"""
Artsense Web Server v2
===
優化項目：
- 圖片處理管線移至 src/image_pipeline.py
- 搜尋邏輯移至 src/search.py
- JWT Admin 身份驗證（src/auth.py）
- 品質過濾 + pHash 去重
- 多尺度 DINOv2 + MobileSAM
- 相似度閾值可動態調整
- 回饋迴圈 API（標記抄襲/非抄襲）
- PCA 索引重建 API
- 批次 GPU 入庫 API
- Path Traversal 安全防護
- 記憶體快取（TTL 60s）
- Rate Limiting
"""

import os
import sys
import json
import shutil
import uuid
import time
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from threading import Lock

from fastapi import FastAPI, Request, Depends, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.auth import (
    create_token, require_admin,
    get_similarity_thresh, set_similarity_thresh,
    ADMIN_USER, ADMIN_PASS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Artsense", version="2.0")

try:
    app.mount("/static", StaticFiles(directory="web/static"), name="static")
except Exception:
    pass

templates = Jinja2Templates(directory="web/templates")

ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# =============================================================================
# 快取（TTL 60s）
# =============================================================================

_cache: dict = {}
_cache_lock  = Lock()

def cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if item and time.time() < item["expires"]:
            return item["value"]
    return None

def cache_set(key, value, ttl=60):
    with _cache_lock:
        _cache[key] = {"value": value, "expires": time.time() + ttl}

def cache_invalidate(key):
    with _cache_lock:
        _cache.pop(key, None)

# =============================================================================
# Rate Limiting
# =============================================================================

_rate_data: dict = defaultdict(list)
_rate_lock       = Lock()

def is_rate_limited(ip, limit=20, window=60):
    now = time.time()
    with _rate_lock:
        _rate_data[ip] = [t for t in _rate_data[ip] if now - t < window]
        if len(_rate_data[ip]) >= limit:
            return True
        _rate_data[ip].append(now)
    return False

# =============================================================================
# 安全：Path Traversal 防護
# =============================================================================

def safe_filename(filename):
    name = Path(filename).name
    if Path(name).suffix.lower() not in ALLOWED_IMG_EXT:
        return ""
    return name

# =============================================================================
# 輔助函式
# =============================================================================

def get_image_count():
    cached = cache_get("image_count")
    if cached is not None:
        return cached
    images_dir = os.path.join(BASE_DIR, "data/raw/moc/images")
    count = 0
    if os.path.exists(images_dir):
        count = sum(1 for f in os.listdir(images_dir)
                    if f.lower().endswith(tuple(ALLOWED_IMG_EXT)))
    cache_set("image_count", count, ttl=60)
    return count

def get_indexed_count():
    cached = cache_get("indexed_count")
    if cached is not None:
        return cached
    try:
        from src.search import get_chroma
        count = get_chroma(BASE_DIR).count()
    except Exception:
        count = 0
    cache_set("indexed_count", count, ttl=60)
    return count

def get_feedback_count():
    cached = cache_get("feedback_count")
    if cached is not None:
        return cached
    try:
        from src.search import get_feedback_stats
        count = get_feedback_stats(BASE_DIR)["total"]
    except Exception:
        count = 0
    cache_set("feedback_count", count, ttl=120)
    return count

def load_metadata_map():
    metadata_file = os.path.join(BASE_DIR, "data/raw/moc/works_metadata.json")
    meta_map = {}
    if os.path.exists(metadata_file):
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                for item in json.load(f):
                    meta_map[item.get("image_file", "")] = item
        except Exception as e:
            logger.warning(f"metadata 載入失敗：{e}")
    return meta_map

def load_review_status():
    status_file = os.path.join(BASE_DIR, "data/raw/moc/review_status.json")
    if os.path.exists(status_file):
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_review_status(status):
    status_file = os.path.join(BASE_DIR, "data/raw/moc/review_status.json")
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

# =============================================================================
# 首頁
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    image_count    = get_image_count()
    indexed_count  = get_indexed_count()
    feedback_count = get_feedback_count()

    html = f"""<!DOCTYPE html>
<html lang="zh-TW"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Artsense - 公共藝術指紋庫</title>
<link rel="stylesheet" href="/static/css/style.css">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&display=swap" rel="stylesheet">
</head><body>
<nav class="navbar"><div class="container">
  <a href="/" class="logo"><span class="logo-icon">🖼️</span><span class="logo-text">Artsense</span></a>
  <ul class="nav-links">
    <li><a href="/gallery">圖庫</a></li>
    <li><a href="/compare" target="_blank">🔍 比對</a></li>
    <li><a href="/admin" target="_blank">🔧 審核</a></li>
  </ul>
</div></nav>

<section class="hero"><div class="container"><div class="hero-content">
  <div class="hero-badge">MVP v2.0</div>
  <h1 class="hero-title">
    <span class="hero-title-cn">公共藝術指紋庫</span>
    <span class="hero-title-en">Artsense</span>
  </h1>
  <p class="hero-tagline">杜絕抄襲，守护原创</p>
  <p class="hero-desc">台灣首個 AI 公共藝術指紋庫，透過多尺度 DINOv2 + MobileSAM，幫助審查委員在決標前發現潛在抄襲作品。</p>
  <div class="hero-actions">
    <a href="/compare" class="btn btn-primary">開始比對</a>
    <a href="https://github.com/xtsai2000-png/artsense" class="btn btn-secondary" target="_blank">GitHub</a>
  </div>
</div></div></section>

<section class="stats"><div class="container"><div class="stats-grid">
  <a href="/gallery" class="stat-card stat-card-link">
    <div class="stat-icon">📸</div>
    <div class="stat-number">{image_count}</div>
    <div class="stat-label">已收集作品</div>
    <div class="stat-target">目標 30,000 件</div>
  </a>
  <div class="stat-card">
    <div class="stat-icon">🔍</div>
    <div class="stat-number">{indexed_count}</div>
    <div class="stat-label">已建立指紋</div>
    <div class="stat-target">向量已入庫</div>
  </div>
  <div class="stat-card">
    <div class="stat-icon">📋</div>
    <div class="stat-number">{feedback_count}</div>
    <div class="stat-label">審查紀錄</div>
    <div class="stat-target">護城河資產</div>
  </div>
  <div class="stat-card">
    <div class="stat-icon">📅</div>
    <div class="stat-number" id="daysActive">1</div>
    <div class="stat-label">開發天數</div>
    <div class="stat-target">持續更新中</div>
  </div>
</div></div></section>

<section class="section section-alt"><div class="container">
  <h2 class="section-title">技術架構 v2</h2>
  <div class="tech-grid">
    <div class="tech-card"><div class="tech-icon">🧠</div><h3>多尺度 DINOv2</h3><ul><li>全圖 + 3 局部裁切</li><li>批次 GPU 推論</li><li>PCA 壓縮 768→256 維</li></ul></div>
    <div class="tech-card"><div class="tech-icon">✂️</div><h3>MobileSAM</h3><ul><li>速度 5x vs SAM ViT-B</li><li>YOLOv8 bbox 提示</li><li>rembg fallback</li></ul></div>
    <div class="tech-card"><div class="tech-icon">🛡️</div><h3>品質門禁</h3><ul><li>解析度 + 模糊偵測</li><li>pHash 去重</li><li>可調相似度閾值</li></ul></div>
    <div class="tech-card"><div class="tech-icon">🔄</div><h3>回饋迴圈</h3><ul><li>委員標記抄襲案例</li><li>JSONL 累積訓練資料</li><li>護城河核心資產</li></ul></div>
    <div class="tech-card"><div class="tech-icon">🔐</div><h3>安全強化</h3><ul><li>JWT Admin 驗證</li><li>Path Traversal 防護</li><li>Rate Limiting</li></ul></div>
  </div>
</div></section>

<footer class="footer"><div class="container">
  <div class="footer-bottom">
    <p>&copy; 2026 Artsense. All rights reserved.</p>
    <p class="footer-tech">FastAPI + DINOv2 + MobileSAM + ChromaDB</p>
  </div>
</div></footer>

<script>
document.addEventListener('DOMContentLoaded', function() {{
  const days = Math.floor((new Date() - new Date('2026-03-23')) / 86400000) + 1;
  document.getElementById('daysActive').textContent = days;
}});
</script>
</body></html>"""
    return HTMLResponse(content=html)

# =============================================================================
# 圖庫
# =============================================================================

@app.get("/gallery", response_class=HTMLResponse)
async def gallery():
    images_dir = os.path.join(BASE_DIR, "data/raw/moc/images")
    meta_map   = load_metadata_map()
    images = []
    if os.path.exists(images_dir):
        for fname in sorted(os.listdir(images_dir)):
            if fname.lower().endswith(tuple(ALLOWED_IMG_EXT)):
                meta = meta_map.get(fname, {})
                images.append({"file": fname, "title": meta.get("title", fname),
                                "artist": meta.get("artist", ""), "year": meta.get("year", ""),
                                "location": meta.get("location", "")})
    grid = "".join(f"""<div class="gallery-item">
  <a href="/gallery-img/{i['file']}" target="_blank">
    <img src="/gallery-img/{i['file']}" alt="{i['title']}" loading="lazy"></a>
  <div class="gallery-caption">
    <div class="gallery-title">{i['title']}</div>
    <div class="gallery-meta">{i['artist']} · {i['year']}</div>
    <div class="gallery-location">{i['location']}</div>
  </div></div>""" for i in images)
    return HTMLResponse(f"""<!DOCTYPE html><html lang="zh-TW"><head><meta charset="UTF-8">
<title>作品圖庫 - Artsense</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500&display=swap" rel="stylesheet">
<style>body{{font-family:'Noto Sans TC',sans-serif;background:#0a0a0f;color:#e0e0e0;margin:0}}
.topbar{{background:#111;padding:16px 24px;display:flex;align-items:center;gap:16px;border-bottom:1px solid #222}}
.topbar a{{color:#7dd3fc;text-decoration:none;font-size:14px}}
.gallery-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:20px;padding:24px;max-width:1400px;margin:0 auto}}
.gallery-item{{background:#16161d;border-radius:12px;overflow:hidden;transition:transform .2s,box-shadow .2s}}
.gallery-item:hover{{transform:translateY(-4px);box-shadow:0 8px 24px rgba(125,211,252,.15)}}
.gallery-item img{{width:100%;aspect-ratio:4/5;object-fit:cover;display:block}}
.gallery-caption{{padding:12px}}.gallery-title{{font-weight:600;font-size:15px;color:#fff;margin-bottom:4px}}
.gallery-meta{{font-size:12px;color:#7dd3fc;margin-bottom:2px}}.gallery-location{{font-size:12px;color:#888}}
</style></head><body>
<div class="topbar"><a href="/">← 返回首頁</a>
  <span style="color:#fff;font-size:18px;font-weight:700">📸 作品圖庫</span>
  <span style="color:#aaa;font-size:14px">共 {len(images)} 件</span></div>
<div class="gallery-grid">{grid or '<p style="color:#888;text-align:center;padding:40px">尚無作品資料</p>'}</div>
</body></html>""")

@app.get("/gallery-img/{filename}")
async def gallery_img(filename: str):
    safe_name = safe_filename(filename)
    if not safe_name:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    for d in ["data/processed/moc/images_nobg_final", "data/processed/moc/images", "data/raw/moc/images"]:
        fpath = os.path.join(BASE_DIR, d, safe_name)
        if os.path.exists(fpath):
            return FileResponse(fpath)
    return JSONResponse({"error": "not found"}, status_code=404)

# =============================================================================
# 身份驗證
# =============================================================================

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if req.username != ADMIN_USER or req.password != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    return {"token": create_token(req.username), "expires_in": 28800}

# =============================================================================
# 系統狀態
# =============================================================================

@app.get("/api/status")
async def api_status():
    try:
        from src.search import get_feedback_stats
        feedback = get_feedback_stats(BASE_DIR)
    except Exception:
        feedback = {"total": 0, "plagiarism": 0}
    return {
        "status": "online", "version": "2.0",
        "image_count":      get_image_count(),
        "indexed_count":    get_indexed_count(),
        "feedback_count":   feedback["total"],
        "plagiarism_cases": feedback["plagiarism"],
        "similarity_thresh": get_similarity_thresh(),
        "target_count":     30000,
    }

# =============================================================================
# 文字搜尋
# =============================================================================

@app.get("/api/search")
async def api_search(request: Request, q: str = "", limit: int = 5):
    if is_rate_limited(request.client.host if request.client else "unknown"):
        return JSONResponse({"error": "請求過於頻繁", "results": []}, status_code=429)
    if not q or len(q) < 2:
        return {"error": "查詢字詞太短（至少 2 個字）", "results": []}
    from src.search import search_by_text
    results = search_by_text(q, BASE_DIR, limit=min(limit, 20))
    return {"query": q, "count": len(results), "results": results}

# =============================================================================
# 圖片比對
# =============================================================================

@app.get("/compare", response_class=HTMLResponse)
async def compare_page():
    with open("web/templates/compare.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.post("/api/compare/upload")
async def api_compare_upload(request: Request, file: UploadFile,
                              remove_bg: bool = True, thresh: float = None):
    if is_rate_limited(request.client.host if request.client else "unknown", limit=10):
        return JSONResponse({"error": "上傳過於頻繁"}, status_code=429)

    temp_dir  = os.path.join(BASE_DIR, "data/temp_compare")
    os.makedirs(temp_dir, exist_ok=True)
    search_id = str(uuid.uuid4())[:8]
    orig_path = os.path.join(temp_dir, f"{search_id}_orig.jpg")

    with open(orig_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    from src.image_pipeline import check_image_quality, segment_artwork
    ok, reason = check_image_quality(orig_path)
    if not ok:
        os.unlink(orig_path)
        return JSONResponse({"error": f"圖片品質不符：{reason}"}, status_code=400)

    processed_path = orig_path
    if remove_bg:
        try:
            out_path = os.path.join(temp_dir, f"{search_id}_processed.png")
            await asyncio.to_thread(segment_artwork, orig_path, out_path)
            processed_path = out_path
        except Exception as e:
            logger.warning(f"分割失敗，使用原圖：{e}")

    from src.search import search_by_image
    results = await asyncio.to_thread(
        search_by_image, processed_path, BASE_DIR, 10, thresh)

    return {
        "search_id":     search_id,
        "orig_url":      f"/api/compare/img/{search_id}_orig.jpg",
        "processed_url": f"/api/compare/img/{search_id}_processed.png",
        "count":         len(results),
        "results":       results,
        "thresh_used":   thresh or get_similarity_thresh(),
    }

@app.get("/api/compare/img/{filename}")
async def api_compare_img(filename: str):
    safe_name = safe_filename(filename)
    if not safe_name:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    fpath = os.path.join(BASE_DIR, "data/temp_compare", safe_name)
    if os.path.exists(fpath):
        return FileResponse(fpath)
    return JSONResponse({"error": "not found"}, status_code=404)

@app.get("/api/compare/image/{search_id}")
async def api_compare_image(search_id: str):
    """取得處理後的圖片（向後相容）"""
    processed_path = os.path.join(BASE_DIR, "data/temp_compare", f"{search_id}_processed.png")
    if os.path.exists(processed_path):
        return FileResponse(processed_path)
    orig_path = os.path.join(BASE_DIR, "data/temp_compare", f"{search_id}_orig.jpg")
    if os.path.exists(orig_path):
        return FileResponse(orig_path)
    return JSONResponse({"error": "Image not found"}, status_code=404)

@app.get("/api/compare/search/{search_id}")
async def api_compare_search(search_id: str):
    """對處理過的圖片進行相似度比對（SSE）"""
    from fastapi.responses import StreamingResponse
    import asyncio

    processed_path = os.path.join(BASE_DIR, "data/temp_compare", f"{search_id}_processed.png")
    orig_path = os.path.join(BASE_DIR, "data/temp_compare", f"{search_id}_orig.jpg")
    img_path = processed_path if os.path.exists(processed_path) else orig_path

    if not os.path.exists(img_path):
        return JSONResponse({"error": "Image not found"}, status_code=404)

    async def generate():
        try:
            from src.search import search_by_image
            yield f"event: status\ndata: {{\"message\": \"處理中...\"}}\n\n"
            await asyncio.sleep(0.1)
            results = search_by_image(img_path, BASE_DIR, limit=10)
            yield f"event: result\ndata: {json.dumps(results)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {{\"error\": \"{e}\"}}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

# =============================================================================
# 回饋迴圈
# =============================================================================

class FeedbackRequest(BaseModel):
    query_work_id:   str
    matched_work_id: str
    is_plagiarism:   bool
    note:            str = ""

@app.post("/api/feedback")
async def api_feedback(req: FeedbackRequest, admin=Depends(require_admin)):
    from src.search import save_feedback
    save_feedback(BASE_DIR, req.query_work_id, req.matched_work_id,
                  req.is_plagiarism, admin.get("sub", "unknown"), req.note)
    cache_invalidate("feedback_count")
    return {"success": True, "message": "回饋已記錄"}

@app.get("/api/feedback/stats")
async def api_feedback_stats(admin=Depends(require_admin)):
    from src.search import get_feedback_stats
    return get_feedback_stats(BASE_DIR)

# =============================================================================
# Admin 設定
# =============================================================================

class SettingsRequest(BaseModel):
    similarity_thresh: float

@app.post("/api/admin/settings")
async def api_admin_settings(req: SettingsRequest, admin=Depends(require_admin)):
    try:
        set_similarity_thresh(req.similarity_thresh)
        return {"success": True, "similarity_thresh": get_similarity_thresh()}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

@app.post("/api/admin/rebuild-pca")
async def api_rebuild_pca(admin=Depends(require_admin)):
    from src.search import rebuild_pca_index
    result = await asyncio.to_thread(rebuild_pca_index, BASE_DIR)
    cache_invalidate("indexed_count")
    return result

# =============================================================================
# Admin 審核後台
# =============================================================================

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    with open("web/templates/admin.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/admin/works")
async def api_admin_works(admin=Depends(require_admin)):
    images_dir    = os.path.join(BASE_DIR, "data/raw/moc/images")
    processed_dir = os.path.join(BASE_DIR, "data/processed/moc/images_nobg_final")
    meta_map      = load_metadata_map()
    review_status = load_review_status()
    works = []
    if os.path.exists(images_dir):
        for fname in sorted(os.listdir(images_dir)):
            if not fname.lower().endswith(tuple(ALLOWED_IMG_EXT)):
                continue
            base_name = Path(fname).stem
            nobg = f"{base_name}_nobg_final.png"
            crop = f"{base_name}_crop3.jpg"
            pf   = nobg if os.path.exists(os.path.join(processed_dir, nobg)) else \
                   crop if os.path.exists(os.path.join(processed_dir, crop)) else None
            meta = meta_map.get(fname, {})
            works.append({
                "id": base_name, "file": fname, "cropped_file": pf or crop,
                "title": meta.get("title", base_name), "artist": meta.get("artist", ""),
                "year": meta.get("year", ""), "location": meta.get("location", ""),
                "material": meta.get("material", ""), "dimensions": meta.get("dimensions", ""),
                "review_status": review_status.get(base_name, {}).get("status", "pending"),
            })
    return works

@app.post("/api/admin/approve/{work_id}")
async def api_admin_approve(work_id: str, admin=Depends(require_admin)):
    from src.image_pipeline import process_and_index
    from src.search import get_chroma, load_phash_map

    images_dir    = os.path.join(BASE_DIR, "data/raw/moc/images")
    processed_dir = os.path.join(BASE_DIR, "data/processed/moc/images_nobg_final")
    meta_map      = load_metadata_map()

    processed_path = None
    for candidate in [os.path.join(processed_dir, f"{work_id}_nobg_final.png"),
                      os.path.join(processed_dir, f"{work_id}_crop3.jpg")]:
        if os.path.exists(candidate):
            processed_path = candidate
            break
    if not processed_path:
        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            raw = os.path.join(images_dir, f"{work_id}{ext}")
            if os.path.exists(raw):
                processed_path = raw
                break
    if not processed_path:
        return JSONResponse({"error": "找不到圖片"}, status_code=404)

    review_status = load_review_status()
    review_status[work_id] = {
        "status": "approved", "updated_at": datetime.now().isoformat(),
        "reviewer": admin.get("sub", "unknown"),
    }
    save_review_status(review_status)
    cache_invalidate("indexed_count")

    meta = {}
    for fname, m in meta_map.items():
        if Path(fname).stem == work_id:
            meta = {**m, "image_file": fname}
            break

    collection = get_chroma(BASE_DIR)
    phash_map  = load_phash_map(BASE_DIR)
    result = await asyncio.to_thread(
        process_and_index, work_id, processed_path, meta, collection, phash_map)
    return {"success": True, **result}

@app.post("/api/admin/reject/{work_id}")
async def api_admin_reject(work_id: str, admin=Depends(require_admin)):
    review_status = load_review_status()
    review_status[work_id] = {
        "status": "rejected", "updated_at": datetime.now().isoformat(),
        "reviewer": admin.get("sub", "unknown"),
    }
    save_review_status(review_status)
    return {"success": True, "message": "已標記為需要重新處理"}

# =============================================================================
# 批次入庫
# =============================================================================

@app.post("/api/admin/batch-index")
async def api_batch_index(admin=Depends(require_admin)):
    from src.image_pipeline import (check_image_quality, extract_features_batch,
                                     compress_vectors_batch, compute_phash,
                                     is_duplicate, segment_artwork)
    from src.search import get_chroma, load_phash_map

    images_dir    = os.path.join(BASE_DIR, "data/raw/moc/images")
    processed_dir = os.path.join(BASE_DIR, "data/processed/moc/images_nobg_final")
    meta_map      = load_metadata_map()
    review_status = load_review_status()
    collection    = get_chroma(BASE_DIR)
    phash_map     = load_phash_map(BASE_DIR)
    existing_ids  = set(collection.get()["ids"])

    pending = [(Path(f).stem, f) for f in sorted(os.listdir(images_dir))
               if f.lower().endswith(tuple(ALLOWED_IMG_EXT))
               and Path(f).stem not in existing_ids
               and review_status.get(Path(f).stem, {}).get("status") == "approved"]

    if not pending:
        return {"status": "done", "indexed": 0, "message": "沒有待入庫的作品"}

    stats = {"indexed": 0, "skipped_quality": 0, "skipped_duplicate": 0, "error": 0}
    valid_paths, valid_ids, valid_metas = [], [], []

    for work_id, fname in pending:
        raw = os.path.join(images_dir, fname)
        ok, _ = check_image_quality(raw)
        if not ok:
            stats["skipped_quality"] += 1
            continue
        phash = compute_phash(raw)
        if is_duplicate(phash, phash_map):
            stats["skipped_duplicate"] += 1
            continue
        out = os.path.join(processed_dir, f"{work_id}_nobg_final.png")
        os.makedirs(processed_dir, exist_ok=True)
        if not os.path.exists(out):
            try:
                await asyncio.to_thread(segment_artwork, raw, out)
            except Exception:
                out = raw
        meta = {**meta_map.get(fname, {}), "image_file": fname, "phash": phash or ""}
        valid_paths.append(out)
        valid_ids.append(work_id)
        valid_metas.append(meta)
        if phash:
            phash_map[work_id] = phash

    if valid_paths:
        try:
            embeddings = await asyncio.to_thread(extract_features_batch, valid_paths)
            compressed = compress_vectors_batch(embeddings)
            collection.upsert(ids=valid_ids, embeddings=compressed.tolist(),
                               metadatas=valid_metas,
                               documents=[m.get("title", wid) for wid, m
                                          in zip(valid_ids, valid_metas)])
            stats["indexed"] = len(valid_ids)
        except Exception as e:
            logger.error(f"批次入庫失敗：{e}")
            stats["error"] = len(valid_paths)

    cache_invalidate("indexed_count")
    return {"status": "done", **stats, "message": f"成功入庫 {stats['indexed']} 件"}

# =============================================================================
# 作品列表
# =============================================================================

@app.get("/api/works")
async def api_works():
    images_dir = os.path.join(BASE_DIR, "data/raw/moc/images")
    meta_map   = load_metadata_map()
    works = []
    if os.path.exists(images_dir):
        for fname in sorted(os.listdir(images_dir)):
            if fname.lower().endswith(tuple(ALLOWED_IMG_EXT)):
                meta = meta_map.get(fname, {})
                works.append({"file": fname, "title": meta.get("title", fname),
                               "artist": meta.get("artist", ""), "year": meta.get("year", ""),
                               "location": meta.get("location", ""), "url": meta.get("url", "")})
    return {"count": len(works), "works": works}
