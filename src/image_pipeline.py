"""
Artsense 圖片處理管線 v2
===
優化項目：
- 品質過濾（解析度 + 模糊偵測）
- pHash 去重
- MobileSAM 替換 SAM ViT-B（速度 5x）
- 多尺度 DINOv2 萃取（全圖 + 局部裁切）
- 批次 GPU 推論
- PCA 向量壓縮（768 → 256 維）
"""

import os
import io
import hashlib
import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# =============================================================================
# 設定常數
# =============================================================================

QUALITY_MIN_SIZE   = 300          # 最小邊長（像素）
QUALITY_BLUR_THRESH = 80.0        # Laplacian variance 低於此值 = 模糊
PHASH_THRESH       = 8            # pHash 漢明距離門檻（0=完全一致，8=輕微差異）
DINO_DIM           = 768          # DINOv2 ViT-B 原始維度
PCA_DIM            = 256          # 壓縮後維度
BATCH_SIZE         = 16           # GPU 批次大小
SIMILARITY_THRESH  = 0.82         # 向量相似度閾值（餘弦，可透過 API 覆寫）

# =============================================================================
# 品質過濾
# =============================================================================

def check_image_quality(img_path: str) -> tuple[bool, str]:
    """
    檢查圖片品質，回傳 (通過, 原因)。

    檢查項目：
    1. 解析度：短邊 < QUALITY_MIN_SIZE 直接拒絕
    2. 模糊度：Laplacian variance < QUALITY_BLUR_THRESH 拒絕
    """
    try:
        import cv2
        img_cv = cv2.imread(img_path)
        if img_cv is None:
            return False, "無法讀取圖片"

        h, w = img_cv.shape[:2]
        if min(h, w) < QUALITY_MIN_SIZE:
            return False, f"解析度過低 ({w}×{h}，最小需 {QUALITY_MIN_SIZE}px)"

        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
        if blur_score < QUALITY_BLUR_THRESH:
            return False, f"圖片模糊 (score={blur_score:.1f}，門檻 {QUALITY_BLUR_THRESH})"

        return True, "通過"

    except ImportError:
        # cv2 未安裝時 fallback 用 PIL 只做解析度檢查
        try:
            with Image.open(img_path) as img:
                w, h = img.size
                if min(h, w) < QUALITY_MIN_SIZE:
                    return False, f"解析度過低 ({w}×{h})"
            return True, "通過（模糊偵測略過，cv2 未安裝）"
        except Exception as e:
            return False, f"讀取失敗：{e}"

    except Exception as e:
        return False, f"品質檢查失敗：{e}"


# =============================================================================
# pHash 去重
# =============================================================================

def compute_phash(img_path: str, hash_size: int = 16) -> Optional[str]:
    """
    計算感知雜湊（pHash）。
    回傳 hex 字串，失敗時回傳 None。
    """
    try:
        import imagehash
        with Image.open(img_path) as img:
            h = imagehash.phash(img, hash_size=hash_size)
            return str(h)
    except ImportError:
        # imagehash 未安裝：用 MD5 fallback（只能抓完全相同的圖）
        try:
            with open(img_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return None
    except Exception as e:
        logger.warning(f"pHash 計算失敗 {img_path}: {e}")
        return None


def is_duplicate(new_hash: str, existing_hashes: dict[str, str],
                 thresh: int = PHASH_THRESH) -> Optional[str]:
    """
    比對新圖的 pHash 與現有庫。
    回傳重複的 work_id，無重複回傳 None。
    """
    if new_hash is None:
        return None

    # 如果是 MD5（長度 32），只做精確比對
    if len(new_hash) == 32:
        for work_id, h in existing_hashes.items():
            if h == new_hash:
                return work_id
        return None

    # pHash：計算漢明距離
    try:
        import imagehash
        new_h = imagehash.hex_to_hash(new_hash)
        for work_id, h_str in existing_hashes.items():
            try:
                existing_h = imagehash.hex_to_hash(h_str)
                if (new_h - existing_h) <= thresh:
                    return work_id
            except Exception:
                continue
    except ImportError:
        pass

    return None


# =============================================================================
# 模型 Lazy Load（全域單例）
# =============================================================================

_yolo_model       = None
_mobile_sam       = None
_mobile_sam_pred  = None
_dino_model       = None
_dino_transform   = None
_pca_model        = None   # sklearn PCA，fit 後可序列化


def get_yolo():
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO
        _yolo_model = YOLO("yolov8n.pt")
        logger.info("YOLOv8n 載入完成")
    return _yolo_model


def get_mobile_sam():
    """
    優先使用 MobileSAM（速度 5x），fallback 至 SAM ViT-B。
    """
    global _mobile_sam, _mobile_sam_pred

    if _mobile_sam_pred is not None:
        return _mobile_sam_pred

    # 嘗試 MobileSAM
    try:
        from mobile_sam import sam_model_registry, SamPredictor
        cache_dir = Path.home() / ".cache" / "mobile_sam"
        ckpt = cache_dir / "mobile_sam.pt"
        if not ckpt.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            import urllib.request
            logger.info("下載 MobileSAM checkpoint...")
            urllib.request.urlretrieve(
                "https://github.com/ChaoningZhang/MobileSAM/raw/master/weights/mobile_sam.pt",
                ckpt
            )
        sam = sam_model_registry["vit_t"](checkpoint=str(ckpt))
        sam.eval()
        _mobile_sam_pred = SamPredictor(sam)
        logger.info("MobileSAM 載入完成")
        return _mobile_sam_pred

    except ImportError:
        logger.warning("MobileSAM 未安裝，fallback 至 SAM ViT-B")

    # Fallback: SAM ViT-B
    from segment_anything import sam_model_registry, SamPredictor
    cache_dir = Path.home() / ".cache" / "torch" / "hub" / "facebook_sam_vit_b"
    ckpt = cache_dir / "sam_vit_b_01ec64.pth"
    if not ckpt.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        import urllib.request
        logger.info("下載 SAM ViT-B checkpoint...")
        urllib.request.urlretrieve(
            "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth",
            ckpt
        )
    sam = sam_model_registry["vit_b"](checkpoint=str(ckpt))
    sam.to("cpu").eval()
    _mobile_sam_pred = SamPredictor(sam)
    logger.info("SAM ViT-B 載入完成（fallback）")
    return _mobile_sam_pred


def get_dino():
    """載入 DINOv2 ViT-B/14，回傳 (model, transform)"""
    global _dino_model, _dino_transform
    if _dino_model is None:
        import torch
        from torchvision import transforms

        _dino_model = torch.hub.load("facebookresearch/dinov2", "dinov2_vitb14")
        _dino_model.eval()

        _dino_transform = transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                 std=[0.229, 0.224, 0.225]),
        ])
        logger.info("DINOv2 ViT-B 載入完成")
    return _dino_model, _dino_transform


# =============================================================================
# YOLO + MobileSAM 分割管線
# =============================================================================

def segment_artwork(orig_path: str, out_path: str) -> str:
    """
    執行 YOLOv8 → MobileSAM 分割管線。
    - YOLO 偵測主體 bbox（conf ≥ 0.25，尺寸 ≥ 50px）
    - 找到：用 SAM bbox prompt 分割
    - 找不到：rembg fallback → SAM center prompt 強化
    回傳輸出路徑。
    """
    return segment_artwork_with_bbox(orig_path, out_path, None)


def segment_artwork_with_bbox(orig_path: str, out_path: str, bbox: list = None) -> str:
    """
    以指定或自動偵測的 bbox 執行分割。

    bbox: [x1, y1, x2, y2]（像素），None 表示由 YOLO 自動偵測。
    """
    from rembg import remove

    pil_raw = Image.open(orig_path).convert("RGB")
    img_np  = np.array(pil_raw)

    sam = get_mobile_sam()

    if bbox and len(bbox) == 4:
        # 使用使用者手動框選的區域
        x1, y1, x2, y2 = bbox
        sam.set_image(img_np)
        mask, _, _ = sam.predict(
            point_coords=None, point_labels=None,
            box=np.array([[x1, y1], [x2, y2]]),
            multimask_output=False,
        )
        alpha = (mask[0] * 255).astype(np.uint8)
        rgba  = np.dstack([img_np, alpha])
        Image.fromarray(rgba).convert("RGBA").save(out_path, "PNG")
        return out_path

    # 否則自動偵測（同原本邏輯）
    yolo_box = _detect_yolo_box(orig_path, pil_raw, img_np)
    if yolo_box is not None:
        sam.set_image(img_np)
        mask, _, _ = sam.predict(
            point_coords=None, point_labels=None,
            box=np.array([[yolo_box[0], yolo_box[1]],
                          [yolo_box[2], yolo_box[3]]]),
            multimask_output=False,
        )
        alpha = (mask[0] * 255).astype(np.uint8)
        rgba  = np.dstack([img_np, alpha])
        Image.fromarray(rgba).convert("RGBA").save(out_path, "PNG")
        return out_path

    # fallback: rembg + SAM center
    rembg_np = np.array(remove(pil_raw))
    if rembg_np.ndim == 3 and rembg_np.shape[2] == 4:
        alpha_ch = rembg_np[:, :, 3]
    else:
        alpha_ch = np.ones(img_np.shape[:2], dtype=np.uint8) * 255

    rows = np.any(alpha_ch > 127, axis=1)
    cols = np.any(alpha_ch > 127, axis=0)
    if np.any(rows) and np.any(cols):
        y1, y2 = np.where(rows)[0][[0, -1]]
        x1, x2 = np.where(cols)[0][[0, -1]]
        pad = 20
        y1, y2 = max(0, y1 - pad), min(alpha_ch.shape[0], y2 + pad)
        x1, x2 = max(0, x1 - pad), min(alpha_ch.shape[1], x2 + pad)
        crop = img_np[y1:y2, x1:x2]
        h, w  = crop.shape[:2]
        sam.set_image(crop)
        m, _, _ = sam.predict(
            point_coords=np.array([[w // 2, h // 2]]),
            point_labels=np.array([1]),
            multimask_output=True,
        )
        best_idx   = max(range(len(m)), key=lambda i: np.sum(m[i]))
        full_mask  = np.zeros(alpha_ch.shape, dtype=np.uint8)
        full_mask[y1:y2, x1:x2] = (m[best_idx] * 255).astype(np.uint8)
        rgba = np.dstack([img_np, full_mask])
        Image.fromarray(rgba).convert("RGBA").save(out_path, "PNG")
    else:
        Image.fromarray(rembg_np).save(out_path, "PNG")

    return out_path


def _detect_yolo_box(orig_path, pil_raw, img_np):
    """由 YOLO 偵測主體 bounding box"""
    yolo = get_yolo()
    boxes = yolo(orig_path, verbose=False)[0].boxes
    if len(boxes) > 0:
        best = max(boxes, key=lambda b: float(b.conf[0]))
        if float(best.conf[0]) >= 0.25:
            x1, y1, x2, y2 = map(int, best.xyxy[0].tolist())
            if (x2 - x1) >= 50 and (y2 - y1) >= 50:
                return (x1, y1, x2, y2)
    return None


# =============================================================================
# 多尺度 DINOv2 特徵萃取（批次）
# =============================================================================

def _make_crops(pil_img: Image.Image) -> list[Image.Image]:
    """
    從圖片產生 4 個裁切：全圖 + 左上/右上/中下三個局部。
    局部裁切佔原圖 60%，捕捉細節紋理。
    """
    w, h   = pil_img.size
    cw, ch = int(w * 0.6), int(h * 0.6)
    crops  = [
        pil_img,                                              # 全圖
        pil_img.crop((0,       0,       cw, ch)),             # 左上
        pil_img.crop((w - cw,  0,       w,  ch)),             # 右上
        pil_img.crop((w // 2 - cw // 2, h - ch, w // 2 + cw // 2, h)),  # 中下
    ]
    return crops


def extract_features_batch(img_paths: list[str]) -> np.ndarray:
    """
    批次萃取多尺度 DINOv2 特徵。
    輸入：圖片路徑清單
    輸出：shape (N, DINO_DIM) 的 float32 陣列
    回傳平均多尺度向量，有效捕捉局部抄襲。
    """
    import torch

    model, transform = get_dino()
    device = next(model.parameters()).device

    all_embeddings = []

    for i in range(0, len(img_paths), BATCH_SIZE):
        batch_paths = img_paths[i:i + BATCH_SIZE]
        tensors_per_crop = [[] for _ in range(4)]  # 4 個尺度

        for path in batch_paths:
            try:
                pil = Image.open(path).convert("RGB")
                crops = _make_crops(pil)
                for j, crop in enumerate(crops):
                    tensors_per_crop[j].append(transform(crop))
            except Exception as e:
                logger.warning(f"圖片讀取失敗 {path}: {e}")
                for j in range(4):
                    tensors_per_crop[j].append(
                        torch.zeros(3, 224, 224)
                    )

        # 批次推論（4 個尺度各自一批）
        scale_embeddings = []
        with torch.no_grad():
            for j in range(4):
                batch_tensor = torch.stack(tensors_per_crop[j]).to(device)
                emb = model(batch_tensor).cpu().numpy()  # (B, 768)
                scale_embeddings.append(emb)

        # 平均 4 個尺度的向量
        mean_emb = np.mean(scale_embeddings, axis=0)  # (B, 768)
        all_embeddings.append(mean_emb)

    result = np.vstack(all_embeddings) if all_embeddings else np.zeros((0, DINO_DIM))
    return result.astype(np.float32)


def extract_features_single(img_path: str) -> Optional[np.ndarray]:
    """單張圖片特徵萃取（用於即時比對）"""
    result = extract_features_batch([img_path])
    if len(result) == 0:
        return None
    return result[0]


# =============================================================================
# PCA 向量壓縮
# =============================================================================

def get_pca_model(chroma_path: str):
    """
    載入或訓練 PCA 模型。
    - 若 pca_model.pkl 存在直接載入
    - 否則從 ChromaDB 取出所有向量重新訓練
    """
    global _pca_model
    if _pca_model is not None:
        return _pca_model

    import pickle
    pca_path = os.path.join(chroma_path, "pca_model.pkl")

    if os.path.exists(pca_path):
        with open(pca_path, "rb") as f:
            _pca_model = pickle.load(f)
        logger.info(f"PCA 模型載入完成（{PCA_DIM} 維）")
        return _pca_model

    return None  # 尚未訓練


def train_pca(vectors: np.ndarray, chroma_path: str):
    """
    用現有向量訓練 PCA，並序列化儲存。
    建議在向量數量 > 500 後才執行，否則 PCA 效果有限。
    """
    import pickle
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import normalize

    global _pca_model

    logger.info(f"開始訓練 PCA：{len(vectors)} 筆向量 → {PCA_DIM} 維...")
    vectors_normalized = normalize(vectors)
    pca = PCA(n_components=PCA_DIM, random_state=42)
    pca.fit(vectors_normalized)

    _pca_model = pca
    pca_path = os.path.join(chroma_path, "pca_model.pkl")
    with open(pca_path, "wb") as f:
        pickle.dump(pca, f)

    explained = pca.explained_variance_ratio_.sum()
    logger.info(f"PCA 訓練完成，解釋變異量 {explained:.1%}")
    return pca


def compress_vector(vector: np.ndarray) -> np.ndarray:
    """
    將 768 維向量壓縮至 256 維。
    若 PCA 尚未訓練，回傳原始向量（截斷至 256 維作為 fallback）。
    """
    from sklearn.preprocessing import normalize
    if _pca_model is not None:
        v = normalize(vector.reshape(1, -1))
        return _pca_model.transform(v)[0].astype(np.float32)
    # Fallback：截斷
    return vector[:PCA_DIM].astype(np.float32)


def compress_vectors_batch(vectors: np.ndarray) -> np.ndarray:
    """批次壓縮"""
    from sklearn.preprocessing import normalize
    if _pca_model is not None:
        v = normalize(vectors)
        return _pca_model.transform(v).astype(np.float32)
    return vectors[:, :PCA_DIM].astype(np.float32)


# =============================================================================
# 完整入庫管線（整合所有優化）
# =============================================================================

def process_and_index(
    work_id: str,
    orig_path: str,
    meta: dict,
    chroma_collection,
    existing_phashes: dict[str, str],
    similarity_thresh: float = SIMILARITY_THRESH,
) -> dict:
    """
    完整處理一件作品並入庫 ChromaDB。

    步驟：
    1. 品質過濾
    2. pHash 去重
    3. YOLOv8 → MobileSAM 分割
    4. 多尺度 DINOv2 萃取
    5. PCA 壓縮（若模型已訓練）
    6. ChromaDB upsert

    回傳 dict 包含 status 與 message。
    """
    # ── 1. 品質過濾 ──
    ok, reason = check_image_quality(orig_path)
    if not ok:
        return {"status": "rejected_quality", "message": reason}

    # ── 2. pHash 去重 ──
    phash = compute_phash(orig_path)
    dup   = is_duplicate(phash, existing_phashes)
    if dup:
        return {"status": "duplicate", "message": f"與 {dup} 重複"}

    # ── 3. 分割 ──
    base_dir      = os.path.dirname(os.path.abspath(orig_path))
    processed_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(orig_path))),
        "processed", "moc", "images_nobg_final"
    )
    os.makedirs(processed_dir, exist_ok=True)
    out_path = os.path.join(processed_dir, f"{work_id}_nobg_final.png")

    try:
        segment_artwork(orig_path, out_path)
    except Exception as e:
        logger.warning(f"分割失敗，使用原圖：{e}")
        out_path = orig_path

    # ── 4. 多尺度特徵萃取 ──
    embedding = extract_features_single(out_path)
    if embedding is None:
        return {"status": "error", "message": "特徵萃取失敗"}

    # ── 5. PCA 壓縮 ──
    compressed = compress_vector(embedding)

    # ── 6. ChromaDB upsert ──
    chroma_collection.upsert(
        ids=[work_id],
        embeddings=[compressed.tolist()],
        metadatas=[{
            "id":         work_id,
            "title":      meta.get("title", ""),
            "artist":     meta.get("artist", ""),
            "year":       meta.get("year", ""),
            "location":   meta.get("location", ""),
            "material":   meta.get("material", ""),
            "url":        meta.get("url", ""),
            "image_file": meta.get("image_file", ""),
            "phash":      phash or "",
        }],
        documents=[meta.get("title", work_id)],
    )

    # 更新 phash 快取
    if phash:
        existing_phashes[work_id] = phash

    return {"status": "indexed", "message": "入庫成功"}
