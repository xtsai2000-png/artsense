#!/usr/bin/env python3
"""
Artsense 圖片處理 pipeline：YOLO → SAM → DINOv2

流程：
  1. YOLOv8 偵測圖片中主體（公共藝術品）區域
  2. 若 YOLO 未偵測到 → 使用 rembg 墌背當作主體
  3. SAM (Segment Anything) 根據 YOLO bbox 或 rembg 結果生成精細遮罩
  4. DINOv2 萃取特徵向量，存入 ChromaDB

用法：
  python src/process_pipeline.py [--source DIR]
"""

import os
import sys
import json
import argparse
import numpy as np
from PIL import Image
from pathlib import Path
from tqdm import tqdm

# ==== Path Config ====
BASE_DIR = Path(__file__).parent.parent.resolve()
RAW_DIR = BASE_DIR / "data/raw/moc/images"
OUT_YOLO = BASE_DIR / "data/processed/moc/images_yolo"
OUT_SAM = BASE_DIR / "data/processed/moc/images_sam_final"
OUT_DINO = BASE_DIR / "data/processed/moc/images_dino_final"

# SAM 模型快取
SAM_CACHE = Path.home() / ".cache" / "torch" / "hub" / "facebook_sam_vit_b"


def ensure_dirs():
    for d in [OUT_YOLO, OUT_SAM, OUT_DINO]:
        d.mkdir(parents=True, exist_ok=True)


# ==== Step 1: YOLO Detection ====
def load_yolo():
    from ultralytics import YOLO
    return YOLO("yolov8n.pt")


def detect_with_yolo(model, image_path: Path) -> tuple[dict | None, Image.Image]:
    """YOLOv8 偵測最大物件區域。失敗回傳 None。"""
    img = Image.open(image_path).convert("RGB")
    results = model(str(image_path), verbose=False)[0]
    boxes = results.boxes

    if len(boxes) == 0:
        return None, img

    # 取 confidence 最高的偵測結果（排除極低分）
    best = max(boxes, key=lambda b: float(b.conf[0]))
    conf = float(best.conf[0])
    if conf < 0.25:
        return None, img

    x1, y1, x2, y2 = map(int, best.xyxy[0].tolist())
    # 過濾太小的框（可能是誤檢）
    if (x2 - x1) < 50 or (y2 - y1) < 50:
        return None, img

    cls_id = int(best.cls[0])
    cls_name = results.names[cls_id]
    box = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "conf": conf, "cls_name": cls_name}
    return box, img


# ==== Step 2: rembg Fallback ====
def remove_background_rebg(pil_img: Image.Image) -> Image.Image:
    """使用 rembg U2-Net 墌背（當 YOLO 失敗時的 fallback）"""
    from rembg import remove
    result = remove(pil_img)
    return result


# ==== Step 3: SAM Segmentation ====
def load_sam():
    from segment_anything import sam_model_registry, SamPredictor, SamAutomaticMaskGenerator

    checkpoint = SAM_CACHE / "sam_vit_b_01ec64.pth"
    if not checkpoint.exists():
        print("  ⬇ 下載 SAM ViT-B checkpoint (~375MB)...")
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        import urllib.request
        url = "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth"
        urllib.request.urlretrieve(url, checkpoint)
        print("  ✅ SAM checkpoint 下載完成")

    sam = sam_model_registry["vit_b"](checkpoint=str(checkpoint))
    sam.to("cpu")
    sam.eval()
    predictor = SamPredictor(sam)
    auto_generator = SamAutomaticMaskGenerator(sam)
    return predictor, auto_generator


def segment_with_bbox(predictor, pil_img: Image.Image, yolo_box: dict) -> Image.Image:
    """用 YOLO bbox 提示 SAM 分割"""
    img_np = np.array(pil_img.convert("RGB"))
    predictor.set_image(img_np)

    bbox = np.array([[yolo_box["x1"], yolo_box["y1"]],
                     [yolo_box["x2"], yolo_box["y2"]]])
    mask, _, _ = predictor.predict(
        point_coords=None, point_labels=None,
        box=bbox, multimask_output=False,
    )

    alpha = np.zeros(img_np.shape[:2], dtype=np.uint8)
    alpha[mask[0]] = 255
    rgba = np.dstack([img_np, alpha])
    return Image.fromarray(rgba).convert("RGBA")


def segment_sam_auto(auto_generator, pil_img: Image.Image) -> Image.Image | None:
    """SAM 全自動分割，取最大/最顯眼的遮罩"""
    img_np = np.array(pil_img.convert("RGB"))
    masks = auto_generator.generate(img_np)

    if not masks:
        return None

    # 取面積最大的 mask（排除畫面邊緣的大面積背景殘留）
    def mask_area(m):
        b = m["bbox"]
        return b[2] * b[3]

    masks.sort(key=mask_area, reverse=True)
    best = masks[0]["segmentation"]

    alpha = np.zeros(img_np.shape[:2], dtype=np.uint8)
    alpha[best] = 255
    rgba = np.dstack([img_np, alpha])
    return Image.fromarray(rgba).convert("RGBA")


def refine_with_sam_auto(predictor, pil_img: Image.Image, rembg_rgba: Image.Image) -> Image.Image:
    """
    rembg 墌背後可能邊緣不乾淨，
    再用 SAM auto segmentation 強化一次。
    """
    # 從 rembg mask 找粗糙範圍
    if rembg_rgba.mode != "RGBA":
        rembg_rgba = rembg_rgba.convert("RGBA")
    
    alpha = np.array(rembg_rgba.split()[3])
    rows = np.any(alpha > 127, axis=1)
    cols = np.any(alpha > 127, axis=0)
    
    if not np.any(rows) or not np.any(cols):
        return rembg_rgba  # fallback: 直接用 rembg 結果
    
    y1, y2 = np.where(rows)[0][[0, -1]]
    x1, x2 = np.where(cols)[0][[0, -1]]
    
    # 稍微擴充邊界
    pad = 20
    y1, y2 = max(0, y1-pad), min(alpha.shape[0], y2+pad)
    x1, x2 = max(0, x1-pad), min(alpha.shape[1], x2+pad)
    
    # 用 SAM auto 在這個範圍內生成更精確的 mask
    img_crop = pil_img.crop((x1, y1, x2, y2))
    img_np = np.array(img_crop.convert("RGB"))
    
    predictor.set_image(img_np)
    
    # 以 crop 中心為 prompt point
    h, w = img_np.shape[:2]
    point = np.array([[w//2, h//2]])
    mask, _, _ = predictor.predict(
        point_coords=point, point_labels=np.array([1]),
        multimask_output=True,
    )
    
    # 取最大的一個
    areas = [np.sum(m) for m in mask]
    best_idx = areas.index(max(areas))
    
    full_mask = np.zeros(alpha.shape, dtype=np.uint8)
    full_mask[y1:y2, x1:x2] = mask[best_idx].astype(np.uint8) * 255
    
    img_np_full = np.array(pil_img.convert("RGB"))
    rgba = np.dstack([img_np_full, full_mask])
    return Image.fromarray(rgba).convert("RGBA")


# ==== Step 4: DINOv2 Feature Extraction ====
def load_dino():
    from transformers import AutoImageProcessor, AutoModel
    processor = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
    model = AutoModel.from_pretrained("facebook/dinov2-base")
    model.eval()
    return processor, model


def extract_dino_features(processor, model, pil_img: Image.Image) -> np.ndarray:
    import torch
    img_rgb = pil_img.convert("RGB").resize((224, 224), Image.BILINEAR)
    inputs = processor(images=img_rgb, return_tensors="pt")
    with torch.no_grad():
        feat = model(**inputs).last_hidden_state[:, 0, :].numpy().flatten()
    return feat / np.linalg.norm(feat)


# ==== Crop to mask bounds ====
def crop_to_mask(pil_rgba: Image.Image, margin: int = 5) -> Image.Image:
    """根據 alpha channel 邊界將圖片裁到只剩主體範圍"""
    if pil_rgba.mode != "RGBA":
        pil_rgba = pil_rgba.convert("RGBA")
    alpha = np.array(pil_rgba.split()[3])
    rows = np.any(alpha > 127, axis=1)
    cols = np.any(alpha > 127, axis=0)
    if not np.any(rows) or not np.any(cols):
        return pil_rgba
    y1, y2 = np.where(rows)[0][[0, -1]]
    x1, x2 = np.where(cols)[0][[0, -1]]
    return pil_rgba.crop((x1, y1, x2+1, y2+1))


# ==== ChromaDB ====
def load_work_metadata() -> dict:
    import chromadb
    client = chromadb.PersistentClient(path=str(BASE_DIR / "data/chroma_public_art"))
    try:
        col = client.get_collection("public_art_works")
        items = col.get()
        return {wid: dict(items["metadatas"][i])
                for i, wid in enumerate(items["ids"])}
    except:
        return {}


def update_chromadb(results: list):
    import chromadb
    client = chromadb.PersistentClient(path=str(BASE_DIR / "data/chroma_public_art"))
    meta_map = load_work_metadata()

    # 刪除舊 collection（完整重建）
    try:
        client.delete_collection("public_art_dino_features")
        print("  🔄 已刪除舊 collection")
    except Exception:
        pass

    dino_col = client.create_collection(
        name="public_art_dino_features",
        metadata={"description": "DINOv2-base features with YOLO+SAM preprocessing", "dimension": 768}
    )

    ids, embeds, metas = [], [], []
    for r in results:
        if r is None:
            continue
        wid = r["work_id"]
        base_meta = meta_map.get(wid, {})
        meta = {
            "title": r.get("title", base_meta.get("title", "")),
            "artist": base_meta.get("artist", ""),
            "year": base_meta.get("year", ""),
            "location": base_meta.get("location", ""),
            "material": base_meta.get("material", ""),
            "yolo_file": r.get("yolo_file", ""),
            "sam_file": r.get("sam_file", ""),
            "dino_file": r.get("dino_file", ""),
            "feature_model": "dinov2-base",
            "preprocess": r.get("preprocess", "yolo+sam"),
            "yolo_conf": str(r.get("yolo_conf", "")),
            "yolo_cls": r.get("yolo_cls", ""),
        }
        ids.append(wid)
        embeds.append(r["feat"].tolist())
        metas.append(meta)

    if ids:
        dino_col.add(ids=ids, embeddings=embeds, metadatas=metas)
        print(f"\n✅ ChromaDB 已更新：{len(ids)} 筆")


# ==== Main ====
def process_image(yolo_model, sam_pred, sam_auto, dino_proc, dino_model,
                  raw_path: Path, work_id: str, title: str = ""):
    print(f"\n處理：{raw_path.name}")

    # Step 1: YOLO
    yolo_box, pil_raw = detect_with_yolo(yolo_model, raw_path)
    pil_rgba = None
    preprocess_method = ""

    if yolo_box:
        print(f"  ✓ YOLO: {yolo_box['cls_name']} conf={yolo_box['conf']:.2f}")
        # Step 2a: SAM with YOLO bbox
        try:
            pil_rgba = segment_with_bbox(sam_pred, pil_raw, yolo_box)
            preprocess_method = "yolo+sam"
            print(f"  ✓ SAM (bbox) 完成")
        except Exception as e:
            print(f"  ⚠ SAM (bbox) 失敗: {e}")
            yolo_box = None

    # Fallback: rembg → SAM auto refine
    if pil_rgba is None:
        print(f"  🔄 YOLO 未偵測，使用 rembg 墌背...")
        try:
            rembg_result = remove_background_rebg(pil_raw)
            pil_rgba = refine_with_sam_auto(sam_pred, pil_raw, rembg_result)
            preprocess_method = "rembg+sam"
            print(f"  ✓ rembg + SAM 強化完成")
        except Exception as e:
            print(f"  ⚠ rembg 失敗: {e}，使用原圖")
            pil_rgba = pil_raw.convert("RGBA")
            preprocess_method = "none"

    # 裁切到 mask 範圍
    pil_cropped = crop_to_mask(pil_rgba)

    # Step 3: 存圖
    yolo_path = OUT_YOLO / f"{work_id}_yolo.jpg"
    sam_path = OUT_SAM / f"{work_id}_sam.png"
    dino_path = OUT_DINO / f"{work_id}_dino.jpg"

    # 存 YOLO crop（墌白底）
    yolo_save = Image.new("RGB", pil_cropped.size, (255, 255, 255))
    if pil_cropped.mode == "RGBA":
        yolo_save.paste(pil_cropped, mask=pil_cropped.split()[3])
    else:
        yolo_save = pil_cropped.convert("RGB")
    yolo_save.save(yolo_path, "JPEG", quality=95)

    pil_cropped.save(sam_path, "PNG")

    # Step 4: DINO feature
    feat = extract_dino_features(dino_proc, dino_model, yolo_save)
    print(f"  ✓ DINOv2 特徵萃取（768維）→ {dino_path.name}")

    return {
        "work_id": work_id,
        "title": title or raw_path.stem,
        "yolo_box": yolo_box,
        "feat": feat,
        "yolo_file": yolo_path.name,
        "sam_file": sam_path.name,
        "dino_file": dino_path.name,
        "preprocess": preprocess_method,
        "yolo_conf": yolo_box["conf"] if yolo_box else "",
        "yolo_cls": yolo_box["cls_name"] if yolo_box else "",
    }


def main():
    parser = argparse.ArgumentParser(description="Artsense 圖片處理：YOLO → SAM → DINOv2")
    parser.add_argument("--source", default=str(RAW_DIR), help="原始圖片目錄")
    args = parser.parse_args()

    source_dir = Path(args.source)
    if not source_dir.exists():
        print(f"❌ 目錄不存在: {source_dir}")
        sys.exit(1)

    print("=" * 60)
    print("Artsense 圖片處理 Pipeline")
    print("  1. YOLOv8n  偵測主體")
    print("  2. rembg    墌背（YOLO 失敗時 fallback）")
    print("  3. SAM      精細分割")
    print("  4. DINOv2   特徵萃取")
    print("=" * 60)

    ensure_dirs()

    print("\n載入模型中（首次需要下載）...")
    yolo_model = load_yolo()
    print("  ✅ YOLOv8n ready")

    sam_pred, sam_auto = load_sam()
    print("  ✅ SAM ready")

    dino_proc, dino_model = load_dino()
    print("  ✅ DINOv2 ready\n")

    meta_map = load_work_metadata()

    exts = {".jpg", ".jpeg", ".png", ".webp"}
    raw_files = sorted([f for f in source_dir.iterdir() if f.suffix.lower() in exts])

    print(f"找到 {len(raw_files)} 張圖片，開始處理...\n")

    results = []
    for raw_path in raw_files:
        work_id = raw_path.stem
        # 嘗試對應正式 work_id
        for wid, m in meta_map.items():
            if raw_path.stem in (m.get("final_file", "") or "") or \
               raw_path.stem in wid:
                work_id = wid
                break
        title = meta_map.get(work_id, {}).get("title", raw_path.stem)
        result = process_image(yolo_model, sam_pred, sam_auto,
                               dino_proc, dino_model, raw_path, work_id, title)
        results.append(result)

    update_chromadb(results)
    print("\n🎉 全部完成！")


if __name__ == "__main__":
    main()
