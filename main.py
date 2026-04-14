"""
Artsense Web Server
===
主程式：提供公共藝術指紋庫的網站前台與 API

改進項目（v0.2）：
- [安全] 修補 /gallery-img/ Path Traversal 漏洞
- [功能] 完成 approve 路由的 DINOv2 特徵萃取並存入 ChromaDB
- [效能] 首頁統計資料加入記憶體快取（TTL 60 秒）
- [穩定] /api/search 加入基本 rate limiting（每 IP 每分鐘 20 次）
- [修正] reject 路由補上 base_dir（原本有宣告但未使用的 bug）
"""

# === 標準庫 ===
import os
import json
import time
import hashlib
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from threading import Lock

# === FastAPI 相關 ===
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# === FastAPI 實例 ===
app = FastAPI(title="Artsense")

# === 靜態檔案掛載 ===
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# === Jinja2 模板引擎 ===
templates = Jinja2Templates(directory="web/templates")

# =============================================================================
# 快取（記憶體，TTL 60 秒）
# =============================================================================
_cache: dict = {}
_cache_lock = Lock()

def cache_get(key: str):
    with _cache_lock:
        item = _cache.get(key)
        if item and time.time() < item["expires"]:
            return item["value"]
    return None

def cache_set(key: str, value, ttl: int = 60):
    with _cache_lock:
        _cache[key] = {"value": value, "expires": time.time() + ttl}

# =============================================================================
# Rate Limiting（每 IP 每分鐘最多 20 次搜尋）
# =============================================================================
_rate_data: dict = defaultdict(list)
_rate_lock = Lock()

def is_rate_limited(ip: str, limit: int = 20, window: int = 60) -> bool:
    now = time.time()
    with _rate_lock:
        timestamps = _rate_data[ip]
        # 清除過期紀錄
        _rate_data[ip] = [t for t in timestamps if now - t < window]
        if len(_rate_data[ip]) >= limit:
            return True
        _rate_data[ip].append(now)
    return False

# =============================================================================
# 輔助函式
# =============================================================================

def get_image_count() -> int:
    """
    動態取得已收集作品圖片數量（含快取，TTL 60 秒）。
    """
    cached = cache_get("image_count")
    if cached is not None:
        return cached

    base_dir = os.path.dirname(os.path.abspath(__file__))
    images_dir = os.path.join(base_dir, "data/raw/moc/images")
    count = 0
    if os.path.exists(images_dir):
        count = len([f for f in os.listdir(images_dir)
                     if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))])
    cache_set("image_count", count, ttl=60)
    return count


def get_dino_count() -> int:
    """
    動態取得已通過審核的作品數量（含快取，TTL 60 秒）。
    """
    cached = cache_get("dino_count")
    if cached is not None:
        return cached

    base_dir = os.path.dirname(os.path.abspath(__file__))
    status_file = os.path.join(base_dir, "data/raw/moc/review_status.json")
    count = 0
    try:
        if os.path.exists(status_file):
            with open(status_file, "r", encoding="utf-8") as f:
                status = json.load(f)
            count = sum(1 for v in status.values() if v.get("status") == "approved")
    except Exception:
        pass
    cache_set("dino_count", count, ttl=60)
    return count


def safe_filename(filename: str) -> str:
    """
    [安全] 只允許純檔名，禁止路徑穿越。
    例如 '../../etc/passwd' → 'passwd'，並驗證副檔名。
    """
    name = Path(filename).name  # 去除所有目錄部分
    allowed_exts = {'.jpg', '.jpeg', '.png', '.webp'}
    if Path(name).suffix.lower() not in allowed_exts:
        return ""
    return name


# =============================================================================
# 全域專案資料
# =============================================================================

PROJECT_DATA = {
    "name": "Artsense",
    "name_full": "公共藝術指紋庫",
    "tagline": "杜絕抄襲，守护原创",
    "description": "Artsense 是台灣首個 AI 公共藝術指紋庫",
    "image_count": get_image_count(),
    "dino_count": get_dino_count(),
    "target_count": 30000,
    "case_count": 0,
    "start_date": "2026-03-23",
    "version": "MVP v0.2",
}

# =============================================================================
# 主頁面
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    """首頁：動態讀取統計資料（已加快取）"""

    # 每次請求都從快取取最新數值
    image_count = get_image_count()
    dino_count = get_dino_count()

    html_content = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Artsense - 公共藝術指紋庫</title>
<link rel="stylesheet" href="/static/css/style.css">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&display=swap" rel="stylesheet">
</head>
<body>
<nav class="navbar">
  <div class="container">
    <a href="/" class="logo">
      <span class="logo-icon">🖼️</span>
      <span class="logo-text">Artsense</span>
    </a>
    <ul class="nav-links">
      <li><a href="#about">關於</a></li>
      <li><a href="#progress">進度</a></li>
      <li><a href="#demo">展示</a></li>
      <li><a href="/compare" target="_blank">🔍 比對</a></li>
      <li><a href="/admin" target="_blank">🔧 審核</a></li>
    </ul>
  </div>
</nav>

<section class="hero">
  <div class="hero-bg"></div>
  <div class="container">
    <div class="hero-content">
      <div class="hero-badge">MVP v0.2</div>
      <h1 class="hero-title">
        <span class="hero-title-cn">公共藝術指紋庫</span>
        <span class="hero-title-en">Artsense</span>
      </h1>
      <p class="hero-tagline">杜絕抄襲，守护原创</p>
      <p class="hero-desc">Artsense 是台灣首個 AI 公共藝術指紋庫，透過 DINOv2 視覺指紋技術，幫助審查委員在決標前發現潛在抄襲作品。</p>
      <div class="hero-actions">
        <a href="#demo" class="btn btn-primary">查看展示</a>
        <a href="https://github.com/xtsai2000-png/artsense" class="btn btn-secondary" target="_blank">GitHub</a>
      </div>
    </div>
    <div class="hero-visual">
      <img src="/static/images/artsense-hero.png" alt="Artsense AI Art Verification" class="hero-image">
    </div>
  </div>
</section>

<section class="stats">
  <div class="container">
    <div class="stats-grid">
      <a href="/gallery" class="stat-card stat-card-link">
        <div class="stat-icon">📸</div>
        <div class="stat-number" id="imageCount">__IMAGE_COUNT__</div>
        <div class="stat-label">已收集作品</div>
        <div class="stat-target">目標 30,000 件</div>
      </a>
      <div class="stat-card">
        <div class="stat-icon">🔍</div>
        <div class="stat-number" id="dinoCount">__DINO_COUNT__</div>
        <div class="stat-label">已完成AI處理</div>
        <div class="stat-target">協助審查</div>
      </div>
      <div class="stat-card">
        <div class="stat-icon">📅</div>
        <div class="stat-number" id="daysActive">1</div>
        <div class="stat-label">開發天數</div>
        <div class="stat-target">持續更新中</div>
      </div>
      <div class="stat-card">
        <div class="stat-icon">⚡</div>
        <div class="stat-number">3</div>
        <div class="stat-label">月 MVP 期程</div>
        <div class="stat-target">穩步前進</div>
      </div>
    </div>
  </div>
</section>

<section class="section" id="about">
  <div class="container">
    <h2 class="section-title">關於專案</h2>
    <div class="about-content">
      <div class="about-text">
        <h3>🎯 解決什麼問題？</h3>
        <p>公共工程中的「罐頭藝術」與抄襲事件屢見不鮮。一件抄襲作品的拆除費用動輒 200 萬以上，更造成社會觀感不佳。</p>
        <h3>💡 我們的方法</h3>
        <p>透過 <strong>DINOv2 視覺指紋</strong>與<strong>ChromaDB 向量檢索</strong>技術，建立全台公共藝術作品的指紋資料庫。</p>
      </div>
      <div class="about-diagram">
        <div class="flow-diagram">
          <div class="flow-step"><div class="flow-icon">📷</div><div class="flow-label">拍攝作品</div></div>
          <div class="flow-arrow">→</div>
          <div class="flow-step"><div class="flow-icon">🔬</div><div class="flow-label">AI 指紋</div></div>
          <div class="flow-arrow">→</div>
          <div class="flow-step"><div class="flow-icon">📊</div><div class="flow-label">比對報告</div></div>
        </div>
      </div>
    </div>
  </div>
</section>

<section class="section section-alt" id="progress">
  <div class="container">
    <h2 class="section-title">建置進度</h2>
    <p class="section-subtitle">MVP 3 個月里程碑追蹤</p>
    <div class="timeline">
      <div class="timeline-phase">
        <h3 class="phase-title">Phase 1 - 爬蟲系統 (W1-W4)</h3>
        <div class="milestones">
          <div class="milestone"><div class="milestone-check">○</div><div class="milestone-content"><div class="milestone-name">M1-1 研究文化部資料庫</div><div class="milestone-date">2026-03-24 ~ 03-28</div></div></div>
          <div class="milestone"><div class="milestone-check">○</div><div class="milestone-content"><div class="milestone-name">M1-2 建立爬蟲框架</div><div class="milestone-date">2026-03-30 ~ 04-04</div></div></div>
          <div class="milestone in-progress"><div class="milestone-check">●</div><div class="milestone-content"><div class="milestone-name">M1-3 爬蟲 Demo (100件)</div><div class="milestone-date">2026-04-07 ~ 04-11</div></div></div>
          <div class="milestone"><div class="milestone-check">○</div><div class="milestone-content"><div class="milestone-name">M1-4 爬蟲穩定化</div><div class="milestone-date">2026-04-14 ~ 04-25</div></div></div>
        </div>
      </div>
    </div>
  </div>
</section>

<section class="section" id="demo">
  <div class="container">
    <h2 class="section-title">成果展示</h2>
    <p class="section-subtitle">系統功能展示（開發中）</p>
    <div class="demo-grid">
      <div class="demo-card">
        <div class="demo-preview placeholder"><div class="placeholder-text">🔍<br>相似度比對<br>（待實作）</div></div>
        <h3>相似度比對</h3>
        <p>上傳作品圖片，快速搜尋資料庫中相似作品</p>
        <span class="demo-badge">coming soon</span>
      </div>
      <div class="demo-card">
        <div class="demo-preview placeholder"><div class="placeholder-text">📊<br>比對報告<br>（待實作）</div></div>
        <h3>PDF 報告生成</h3>
        <p>一鍵生成標準化審查報告</p>
        <span class="demo-badge">coming soon</span>
      </div>
      <div class="demo-card">
        <div class="demo-preview placeholder"><div class="placeholder-text">🗺️<br>地理分布<br>（待實作）</div></div>
        <h3>作品分布地圖</h3>
        <p>全台公共藝術作品地理分布視覺化</p>
        <span class="demo-badge">coming soon</span>
      </div>
    </div>
  </div>
</section>

<section class="section section-alt">
  <div class="container">
    <h2 class="section-title">技術架構</h2>
    <div class="tech-grid">
      <div class="tech-card"><div class="tech-icon">🧠</div><h3>AI 模型</h3><ul><li>DINOv2 視覺特徵</li><li>CLIP 語義理解</li><li>Sentence-Transformers</li></ul></div>
      <div class="tech-card"><div class="tech-icon">💾</div><h3>向量資料庫</h3><ul><li>ChromaDB</li><li>Faiss 加速檢索</li><li>30,000+ 向量規模</li></ul></div>
      <div class="tech-card"><div class="tech-icon">🕷️</div><h3>爬蟲系統</h3><ul><li>Scrapling 自適應爬蟲</li><li>文化部資料庫整合</li><li>全台 22 縣市覆蓋</li></ul></div>
      <div class="tech-card"><div class="tech-icon">🌐</div><h3>網站介面</h3><ul><li>FastAPI 後端</li><li>響應式設計</li><li>PDF 報告生成</li></ul></div>
      <div class="tech-card"><div class="tech-icon">✂️</div><h3>圖片前處理</h3><ul><li>自動切割主體區域</li><li>rembg 去背（U2-Net）</li><li>DINOv2 特徵萃取</li></ul></div>
    </div>
  </div>
</section>

<footer class="footer">
  <div class="container">
    <div class="footer-content">
      <div class="footer-brand">
        <span class="footer-logo">🖼️ Artsense</span>
        <p>台灣公共藝術指紋庫</p>
      </div>
    </div>
    <div class="footer-bottom">
      <p>&copy; 2026 Artsense. All rights reserved.</p>
      <p class="footer-tech">Built with FastAPI + DINOv2 + ChromaDB</p>
    </div>
  </div>
</footer>

<script>
document.addEventListener('DOMContentLoaded', function() {
  const startDate = new Date('2026-03-23');
  const today = new Date();
  const daysActive = Math.floor((today - startDate) / (1000 * 60 * 60 * 24)) + 1;
  document.getElementById('daysActive').textContent = daysActive;
});
</script>
</body>
</html>
"""
    html_content = html_content.replace("__IMAGE_COUNT__", str(image_count))
    html_content = html_content.replace("__DINO_COUNT__", str(dino_count))
    return HTMLResponse(content=html_content)


# =============================================================================
# 作品圖庫頁面
# =============================================================================

@app.get("/gallery", response_class=HTMLResponse)
async def gallery():
    """作品圖庫頁面：縮圖網格顯示所有已收集作品。"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    images_dir = os.path.join(base_dir, "data/raw/moc/images")
    metadata_file = os.path.join(base_dir, "data/raw/moc/works_metadata.json")

    meta_map = {}
    if os.path.exists(metadata_file):
        with open(metadata_file, "r", encoding="utf-8") as f:
            for item in json.load(f):
                meta_map[item.get("image_file", "")] = item

    images = []
    if os.path.exists(images_dir):
        for fname in sorted(os.listdir(images_dir)):
            if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                fpath = os.path.join(images_dir, fname)
                fsize = os.path.getsize(fpath)
                meta = meta_map.get(fname, {})
                images.append({
                    "file": fname,
                    "title": meta.get("title", fname),
                    "artist": meta.get("artist", ""),
                    "year": meta.get("year", ""),
                    "location": meta.get("location", ""),
                    "material": meta.get("material", ""),
                    "url": meta.get("url", ""),
                    "size_kb": fsize // 1024,
                })

    grid_items = ""
    for img in images:
        grid_items += f"""
<div class="gallery-item">
  <a href="/gallery-img/{img['file']}" target="_blank">
    <img src="/gallery-img/{img['file']}" alt="{img['title']}" loading="lazy">
  </a>
  <div class="gallery-caption">
    <div class="gallery-title">{img['title']}</div>
    <div class="gallery-meta">{img['artist']} · {img['year']}</div>
    <div class="gallery-location">{img['location']}</div>
  </div>
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>作品圖庫 - Artsense</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
body {{ font-family: 'Noto Sans TC', sans-serif; background: #0a0a0f; color: #e0e0e0; margin: 0; padding: 0; }}
.topbar {{ background: #111; padding: 16px 24px; display: flex; align-items: center; gap: 16px; border-bottom: 1px solid #222; }}
.topbar a {{ color: #7dd3fc; text-decoration: none; font-size: 14px; }}
.topbar a:hover {{ text-decoration: underline; }}
.page-title {{ color: #fff; font-size: 20px; font-weight: 700; margin: 0; }}
.count {{ color: #aaa; font-size: 14px; }}
.gallery-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 20px; padding: 24px; max-width: 1400px; margin: 0 auto; }}
.gallery-item {{ background: #16161d; border-radius: 12px; overflow: hidden; transition: transform 0.2s, box-shadow 0.2s; }}
.gallery-item:hover {{ transform: translateY(-4px); box-shadow: 0 8px 24px rgba(125,211,252,0.15); }}
.gallery-item img {{ width: 100%; aspect-ratio: 4/5; object-fit: cover; display: block; }}
.gallery-caption {{ padding: 12px; }}
.gallery-title {{ font-weight: 600; font-size: 15px; color: #fff; margin-bottom: 4px; }}
.gallery-meta {{ font-size: 12px; color: #7dd3fc; margin-bottom: 2px; }}
.gallery-location {{ font-size: 12px; color: #888; }}
</style>
</head>
<body>
<div class="topbar">
  <a href="/">← 返回首頁</a>
  <h1 class="page-title">📸 作品圖庫</h1>
  <span class="count">共 {len(images)} 件作品</span>
</div>
<div class="gallery-grid">
  {grid_items if grid_items else '<p style="color:#888;text-align:center;padding:40px;">尚無作品資料</p>'}
</div>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/gallery-img/{filename}")
async def gallery_img(filename: str):
    """
    [安全修補] 提供作品圖片檔案。
    使用 safe_filename() 防止 Path Traversal 攻擊。
    """
    # [修補] 驗證檔名，禁止路徑穿越
    safe_name = safe_filename(filename)
    if not safe_name:
        return JSONResponse({"error": "invalid filename"}, status_code=400)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    search_dirs = [
        os.path.join(base_dir, "data/processed/moc/images_nobg_final"),
        os.path.join(base_dir, "data/processed/moc/images"),
        os.path.join(base_dir, "data/raw/moc/images"),
    ]

    for search_dir in search_dirs:
        fpath = os.path.join(search_dir, safe_name)
        if os.path.exists(fpath):
            return FileResponse(fpath)

    return JSONResponse({"error": "not found"}, status_code=404)


# =============================================================================
# API 端點
# =============================================================================

@app.get("/api/status")
async def api_status():
    """系統狀態 API（統計資料來自快取）"""
    return {
        "status": "online",
        "version": "MVP v0.2",
        "image_count": get_image_count(),
        "dino_count": get_dino_count(),
        "target_count": 30000,
    }


@app.get("/api/search")
async def api_search(request: Request, q: str = "", limit: int = 5):
    """
    向量相似度搜尋 API（加入 Rate Limiting）
    每個 IP 每分鐘最多 20 次請求。
    """
    # [新增] Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if is_rate_limited(client_ip):
        return JSONResponse(
            {"error": "請求過於頻繁，請稍後再試", "results": []},
            status_code=429
        )

    if not q or len(q) < 2:
        return {"error": "查詢字詞太短（至少2個字）", "results": []}

    base_dir = os.path.dirname(os.path.abspath(__file__))
    chroma_path = os.path.join(base_dir, "data/chroma_public_art")

    if not os.path.exists(chroma_path):
        return {"error": "向量資料庫尚未建立", "results": []}

    try:
        import chromadb
        client = chromadb.PersistentClient(path=chroma_path)
        collection = client.get_collection("public_art_works")

        try:
            import httpx
            r = httpx.post(
                "http://localhost:11434/api/embeddings",
                json={"model": "llama3.1:latest", "prompt": q},
                timeout=30
            )
            q_emb = r.json()["embedding"]
        except Exception as e:
            return {"error": f"Ollama 連線失敗: {e}", "results": []}

        results = collection.query(
            query_embeddings=[q_emb],
            n_results=min(limit, 20),  # 限制最大回傳數量
            include=["documents", "metadatas"]
        )

        artworks = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            artworks.append({
                "id": meta.get("id", ""),
                "title": meta.get("title", ""),
                "artist": meta.get("artist", ""),
                "year": meta.get("year", ""),
                "location": meta.get("location", ""),
                "material": meta.get("material", ""),
                "budget": meta.get("budget", ""),
                "desc": meta.get("desc", ""),
                "url": meta.get("url", ""),
                "image_file": meta.get("image_file", ""),
            })

        return {"query": q, "count": len(artworks), "results": artworks}

    except Exception as e:
        return {"error": str(e), "results": []}


@app.get("/api/works")
async def api_works():
    """取得作品列表 API"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    images_dir = os.path.join(base_dir, "data/raw/moc/images")
    metadata_file = os.path.join(base_dir, "data/raw/moc/works_metadata.json")

    meta_map = {}
    if os.path.exists(metadata_file):
        with open(metadata_file, "r", encoding="utf-8") as f:
            for item in json.load(f):
                meta_map[item.get("image_file", "")] = item

    works = []
    if os.path.exists(images_dir):
        for fname in sorted(os.listdir(images_dir)):
            if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                meta = meta_map.get(fname, {})
                works.append({
                    "file": fname,
                    "title": meta.get("title", fname),
                    "artist": meta.get("artist", ""),
                    "year": meta.get("year", ""),
                    "location": meta.get("location", ""),
                    "material": meta.get("material", ""),
                    "url": meta.get("url", ""),
                })

    return {"count": len(works), "works": works}


# =============================================================================
# 圖片審核後台
# =============================================================================

def get_review_status_file():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "data/raw/moc/review_status.json")


def load_review_status():
    status_file = get_review_status_file()
    if os.path.exists(status_file):
        with open(status_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_review_status(status):
    status_file = get_review_status_file()
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


def _index_to_chromadb(work_id: str, processed_path: str, meta: dict):
    """
    [新增] 將已審核通過的作品萃取 DINOv2 特徵並存入 ChromaDB。

    Args:
        work_id: 作品 ID
        processed_path: 處理後圖片完整路徑
        meta: 作品 metadata dict
    """
    import torch
    import numpy as np
    from PIL import Image as PILImage
    import chromadb

    base_dir = os.path.dirname(os.path.abspath(__file__))
    chroma_path = os.path.join(base_dir, "data/chroma_public_art")

    # 載入圖片
    img = PILImage.open(processed_path).convert("RGB")

    # 載入 DINOv2 模型（lazy, 只載一次）
    global _dino_model, _dino_transform
    if "_dino_model" not in globals() or _dino_model is None:
        _dino_model = torch.hub.load("facebookresearch/dinov2", "dinov2_vitb14")
        _dino_model.eval()
        from torchvision import transforms
        _dino_transform = transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                 std=[0.229, 0.224, 0.225]),
        ])

    with torch.no_grad():
        tensor = _dino_transform(img).unsqueeze(0)
        embedding = _dino_model(tensor).squeeze(0).numpy().tolist()

    # 存入 ChromaDB
    os.makedirs(chroma_path, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=chroma_path)
    try:
        collection = chroma_client.get_collection("public_art_works")
    except Exception:
        collection = chroma_client.create_collection("public_art_works")

    collection.upsert(
        ids=[work_id],
        embeddings=[embedding],
        metadatas=[{
            "id": work_id,
            "title": meta.get("title", ""),
            "artist": meta.get("artist", ""),
            "year": meta.get("year", ""),
            "location": meta.get("location", ""),
            "material": meta.get("material", ""),
            "url": meta.get("url", ""),
            "image_file": meta.get("file", ""),
        }],
        documents=[meta.get("title", work_id)],
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """圖片審核後台頁面"""
    with open("web/templates/admin.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/api/admin/works")
async def api_admin_works():
    """取得待審核作品列表 API"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    images_dir = os.path.join(base_dir, "data/raw/moc/images")
    processed_dir = os.path.join(base_dir, "data/processed/moc/images_nobg_final")
    metadata_file = os.path.join(base_dir, "data/raw/moc/works_metadata.json")

    review_status = load_review_status()

    meta_map = {}
    if os.path.exists(metadata_file):
        with open(metadata_file, "r", encoding="utf-8") as f:
            for item in json.load(f):
                meta_map[item.get("image_file", "")] = item

    works = []
    if os.path.exists(images_dir):
        for fname in sorted(os.listdir(images_dir)):
            if not fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                continue

            base_name = Path(fname).stem
            cropped_file = f"{base_name}_crop3.jpg"
            nobg_file = f"{base_name}_nobg_final.png"

            processed_file = None
            if os.path.exists(os.path.join(processed_dir, nobg_file)):
                processed_file = nobg_file
            elif os.path.exists(os.path.join(processed_dir, cropped_file)):
                processed_file = cropped_file

            work_id = base_name
            status = review_status.get(work_id, {}).get("status", "pending")
            meta = meta_map.get(fname, {})

            works.append({
                "id": work_id,
                "file": fname,
                "original_file": fname,
                "cropped_file": processed_file or cropped_file,
                "title": meta.get("title", base_name),
                "artist": meta.get("artist", ""),
                "year": meta.get("year", ""),
                "location": meta.get("location", ""),
                "material": meta.get("material", ""),
                "dimensions": meta.get("dimensions", ""),
                "review_status": status,
            })

    return works


@app.post("/api/admin/approve/{work_id}")
async def api_admin_approve(work_id: str):
    """
    [修補] 通過審核：更新狀態並觸發 DINOv2 特徵萃取存入 ChromaDB。
    原本只寫入 JSON 狀態，向量庫未更新的 bug 已修正。
    """
    import asyncio

    base_dir = os.path.dirname(os.path.abspath(__file__))
    review_status = load_review_status()

    # 找到對應圖片與 metadata
    images_dir = os.path.join(base_dir, "data/raw/moc/images")
    processed_dir = os.path.join(base_dir, "data/processed/moc/images_nobg_final")
    metadata_file = os.path.join(base_dir, "data/raw/moc/works_metadata.json")

    meta_map = {}
    if os.path.exists(metadata_file):
        with open(metadata_file, "r", encoding="utf-8") as f:
            for item in json.load(f):
                meta_map[item.get("image_file", "")] = item

    # 找處理後圖片路徑
    nobg_path = os.path.join(processed_dir, f"{work_id}_nobg_final.png")
    crop_path = os.path.join(processed_dir, f"{work_id}_crop3.jpg")

    processed_path = None
    if os.path.exists(nobg_path):
        processed_path = nobg_path
    elif os.path.exists(crop_path):
        processed_path = crop_path
    else:
        # fallback: 用原始圖片
        for ext in ['.jpg', '.jpeg', '.png', '.webp']:
            raw_path = os.path.join(images_dir, f"{work_id}{ext}")
            if os.path.exists(raw_path):
                processed_path = raw_path
                break

    # 更新審核狀態
    review_status[work_id] = {
        "status": "approved",
        "updated_at": datetime.now().isoformat()
    }
    save_review_status(review_status)

    # 使快取失效，讓首頁統計立即更新
    cache_set("dino_count", None, ttl=0)

    # [修補] 觸發 DINOv2 特徵萃取並存入 ChromaDB
    if processed_path:
        # 找對應 metadata
        meta = {}
        for fname, m in meta_map.items():
            if Path(fname).stem == work_id:
                meta = m
                meta["file"] = fname
                break

        try:
            await asyncio.to_thread(_index_to_chromadb, work_id, processed_path, meta)
            return {"success": True, "message": "已通過審核並完成向量建檔"}
        except Exception as e:
            # 審核狀態已存，但向量建檔失敗時回報警告
            return {"success": True, "message": f"審核已通過，但向量建檔失敗：{e}"}

    return {"success": True, "message": "已通過審核（找不到處理後圖片，向量建檔略過）"}


@app.post("/api/admin/reject/{work_id}")
async def api_admin_reject(work_id: str):
    """拒絕審核：將作品標記為需要重新處理。"""
    review_status = load_review_status()
    review_status[work_id] = {
        "status": "rejected",
        "updated_at": datetime.now().isoformat()
    }
    save_review_status(review_status)
    return {"success": True, "message": "已標記為需要重新處理"}


# =============================================================================
# 相似度比對流程（YOLO → SAM → DINOv2）
# =============================================================================

import shutil
import uuid
import asyncio
import numpy as np
from PIL import Image
from pathlib import Path
from fastapi import UploadFile

_yolo_model = None
_sam_predictor = None
_model_lock = asyncio.Lock()
_dino_model = None
_dino_transform = None


def _get_yolo():
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO
        _yolo_model = YOLO("yolov8n.pt")
    return _yolo_model


def _get_sam_predictor():
    global _sam_predictor
    if _sam_predictor is None:
        from segment_anything import sam_model_registry, SamPredictor
        cache = Path.home() / ".cache" / "torch" / "hub" / "facebook_sam_vit_b"
        ckpt = cache / "sam_vit_b_01ec64.pth"
        if not ckpt.exists():
            cache.mkdir(parents=True, exist_ok=True)
            import urllib.request
            print("⬇ 下載 SAM ViT-B checkpoint...")
            urllib.request.urlretrieve(
                "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth",
                ckpt
            )
        sam = sam_model_registry["vit_b"](checkpoint=str(ckpt))
        sam.to("cpu").eval()
        _sam_predictor = SamPredictor(sam)
    return _sam_predictor


def _run_yolo_sam_pipeline(orig_path: str, out_path: str) -> str:
    """
    同步執行 YOLO → SAM pipeline。
    - YOLO 偵測主體 bounding box
    - 若偵測到：用 SAM 以 bbox 分割
    - 若未偵測到：用 rembg 去背後再以 SAM 強化
    回傳處理後圖片路徑。
    """
    from rembg import remove

    pil_raw = Image.open(orig_path).convert("RGB")
    yolo_model = _get_yolo()
    sam_pred = _get_sam_predictor()

    # Step 1: YOLO 偵測
    boxes = yolo_model(orig_path, verbose=False)[0].boxes
    yolo_box = None
    if len(boxes) > 0:
        best = max(boxes, key=lambda b: float(b.conf[0]))
        conf = float(best.conf[0])
        if conf >= 0.25:
            x1, y1, x2, y2 = map(int, best.xyxy[0].tolist())
            if (x2 - x1) >= 50 and (y2 - y1) >= 50:
                yolo_box = (x1, y1, x2, y2)

    # Step 2a: SAM 以 YOLO bbox 分割
    if yolo_box is not None:
        img_np = np.array(pil_raw)
        sam_pred.set_image(img_np)
        mask, _, _ = sam_pred.predict(
            point_coords=None, point_labels=None,
            box=np.array([[yolo_box[0], yolo_box[1]],
                          [yolo_box[2], yolo_box[3]]]),
            multimask_output=False,
        )
        alpha = np.zeros(img_np.shape[:2], dtype=np.uint8)
        alpha[mask[0]] = 255
        rgba = np.dstack([img_np, alpha])
        result = Image.fromarray(rgba).convert("RGBA")
        result.save(out_path, "PNG")
        return out_path

    # Step 2b: rembg fallback → SAM 強化
    rembg_result = remove(pil_raw)
    rembg_np = np.array(rembg_result)
    if rembg_np.ndim == 2:
        rembg_np = np.dstack([rembg_np, rembg_np, rembg_np,
                               np.ones_like(rembg_np) * 255])
    elif rembg_np.shape[2] == 3:
        rembg_np = np.dstack([rembg_np, np.ones_like(rembg_np[:, :, 0]) * 255])

    alpha = rembg_np[:, :, 3]
    rows = np.any(alpha > 127, axis=1)
    cols = np.any(alpha > 127, axis=0)

    if np.any(rows) and np.any(cols):
        y1, y2 = np.where(rows)[0][[0, -1]]
        x1, x2 = np.where(cols)[0][[0, -1]]
        pad = 20
        y1, y2 = max(0, y1 - pad), min(alpha.shape[0], y2 + pad)
        x1, x2 = max(0, x1 - pad), min(alpha.shape[1], x2 + pad)
        crop = np.array(pil_raw)[y1:y2, x1:x2]
        sam_pred.set_image(crop)
        h, w = crop.shape[:2]
        m, _, _ = sam_pred.predict(
            point_coords=np.array([[w // 2, h // 2]]),
            point_labels=np.array([1]),
            multimask_output=True,
        )
        areas = [np.sum(mm) for mm in m]
        best_idx = areas.index(max(areas))
        full_mask = np.zeros(alpha.shape, dtype=np.uint8)
        full_mask[y1:y2, x1:x2] = (m[best_idx] * 255).astype(np.uint8)
        img_np = np.array(pil_raw.convert("RGB"))
        rgba = np.dstack([img_np, full_mask])
        result = Image.fromarray(rgba).convert("RGBA")
        result.save(out_path, "PNG")
    else:
        rembg_result.save(out_path, "PNG")

    return out_path


@app.get("/compare", response_class=HTMLResponse)
async def compare_page():
    """相似度比對頁面"""
    with open("web/templates/compare.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.post("/api/compare/upload")
async def api_compare_upload(file: UploadFile, remove_bg: bool = True):
    """
    上傳圖片並進行前處理（YOLO → SAM pipeline）
    1. 儲存原始圖片
    2. 若 remove_bg=True，執行 YOLO 偵測 + SAM 分割
    3. 回傳 search_id 供後續使用
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(base_dir, "data/temp_compare")
    os.makedirs(temp_dir, exist_ok=True)

    search_id = str(uuid.uuid4())[:8]
    orig_path = os.path.join(temp_dir, f"{search_id}_orig.jpg")

    with open(orig_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    processed_path = orig_path
    if remove_bg:
        try:
            processed_path = os.path.join(temp_dir, f"{search_id}_processed.png")
            await asyncio.to_thread(_run_yolo_sam_pipeline, str(orig_path), processed_path)
        except Exception as e:
            processed_path = orig_path  # fallback 使用原始圖片
            print(f"前處理失敗，使用原始圖片: {e}")

    return {
        "search_id": search_id,
        "original": f"/api/compare/img/{search_id}_orig.jpg",
        "processed": f"/api/compare/img/{search_id}_processed.png",
        "remove_bg_applied": remove_bg and processed_path != orig_path,
    }


@app.get("/api/compare/img/{filename}")
async def api_compare_img(filename: str):
    """
    [安全] 提供比對用暫存圖片（同樣套用 safe_filename 防護）。
    """
    safe_name = safe_filename(filename)
    if not safe_name:
        return JSONResponse({"error": "invalid filename"}, status_code=400)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    fpath = os.path.join(base_dir, "data/temp_compare", safe_name)
    if os.path.exists(fpath):
        return FileResponse(fpath)
    return JSONResponse({"error": "not found"}, status_code=404)
