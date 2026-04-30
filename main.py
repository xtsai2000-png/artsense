"""
Artsense Web Server
===
主程式：提供公共藝術指紋庫的網站前台與 API

主要功能：
- 首頁：展示系統概覽與統計數據
- /gallery：作品圖庫頁面，縮圖網格顯示所有已收集作品
- /gallery-img/{filename}：提供作品圖片
- /api/status：系統狀態 API
- /api/search?q=...：向量相似度搜尋 API
- /api/works：取得作品列表 API
"""

# === 標準庫 ===
import os
import json
import glob
from datetime import datetime

# === FastAPI 相關 ===
from fastapi import FastAPI, Body
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# === FastAPI 實例 ===
app = FastAPI(title="Artsense")

# === 靜態檔案掛載 ===
# 將 web/static 目錄掛載到 /static 路徑，供前端取用 CSS、JS、圖片等靜態資源
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# === Jinja2 模板引擎 ===
# 掛載 web/templates 目錄作為 HTML 模板目錄（目前首頁使用 inline HTML，未來可移至模板檔案）
templates = Jinja2Templates(directory="web/templates")


# =============================================================================
# 輔助函式
# =============================================================================

def get_image_count() -> int:
    """
    動態取得已收集作品圖片數量。

    掃描 data/raw/moc/images/ 目錄，計算副檔名為 jpg/jpeg/png/webp 的圖片檔案數量。
    此數字會顯示在首頁的「已收集作品」統計卡片上。

    Returns:
        int: 圖片檔案數量，若目錄不存在則回傳 0
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))          # 取得 main.py 所在目錄
    images_dir = os.path.join(base_dir, "data/raw/moc/images")     # 拼接圖片目錄路徑
    if os.path.exists(images_dir):
        # 過濾圖片副檔名並計算數量
        return len([f for f in os.listdir(images_dir)
                    if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))])
    return 0


def get_dino_count() -> int:
    """
    動態取得已通過審核的作品數量。

    讀取審核狀態檔案 review_status.json，回傳已通過審核的作品數量。
    此數字會顯示在首頁的「已完成AI處理」統計卡片上。

    Returns:
        int: 已通過審核的作品數量
    """
    import json
    base_dir = os.path.dirname(os.path.abspath(__file__))
    status_file = os.path.join(base_dir, "data/raw/moc/review_status.json")
    try:
        if os.path.exists(status_file):
            with open(status_file, "r", encoding="utf-8") as f:
                status = json.load(f)
            return sum(1 for v in status.values() if v.get("status") == "approved")
    except Exception:
        pass
    return 0


# =============================================================================
# 全域專案資料
# =============================================================================

PROJECT_DATA = {
    # 專案基本資訊
    "name": "Artsense",                        # 系統名稱
    "name_full": "公共藝術指紋庫",             # 完整名稱（中文）
    "tagline": "杜絕抄襲，守护原创",           # slogan
    "description": "Artsense 是台灣首個 AI 公共藝術指紋庫",

    # 動態統計數值
    "image_count": get_image_count(),           # 已收集作品數（自動從資料夾讀取）
    "dino_count": get_dino_count(),             # 已完成AI處理（自動從ChromaDB讀取）
    "target_count": 30000,                     # 目標收集數量
    "case_count": 0,                           # 已完成比對案件數（待實作）

    # 專案資訊
    "start_date": "2026-03-23",                # 專案開始日期
    "version": "MVP v0.1",                      # 目前版本
}


# =============================================================================
# 主頁面
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    """
    首頁（Homepage）

    回傳包含以下區塊的 HTML 頁面：
    - 導航列（Navbar）： Logo + 連結
    - 英雄區（Hero）：系統簡介與口號
    - 統計卡片（Stats）：已收集作品、已完成AI處理、開發天數、MVP期程
    - 關於專案（About）：解決什麼問題、方法說明
    - 建置進度（Progress）：Phase 1-3 里程碑時間軸
    - 成果展示（Demo）：相似度比對、PDF報告、地圖三個展示卡（待實作）
    - 技術架構（Tech）：AI模型、向量資料庫、爬蟲系統、網站介面四個技術說明卡
    - 頁尾（Footer）

    計數器會在 Server 啟動時動態計算（get_image_count），
    前端 JavaScript 會自動計算「開發天數」。
    """
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Artsense - 公共藝術指紋庫</title>
        <link rel="stylesheet" href="/static/css/style.css">
        <!-- Google Fonts：Noto Sans TC 中文無襯線字體 -->
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
                    <div class="hero-badge">MVP v0.1</div>
                    <h1 class="hero-title">
                        <span class="hero-title-cn">公共藝術指紋庫</span>
                        <span class="hero-title-en">Artsense</span>
                    </h1>
                    <p class="hero-tagline">杜絕抄襲，守护原创</p>
                    <p class="hero-desc">Artsense 是台灣首個 AI 公共藝術指紋庫，透過 DINOv2 視覺指紋技術，幫助審查委員在決標前發現潛在抄襲作品。</p>
                    <div class="hero-actions">
                        <a href="#demo" class="btn btn-primary">查看展示</a>
                        <a href="https://github.com/artsense" class="btn btn-secondary" target="_blank">GitHub</a>
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
                    <!-- 已收集作品：動態讀取 data/raw/moc/images/ 資料夾數量 -->
                    <a href="/gallery" class="stat-card stat-card-link">
                        <div class="stat-icon">📸</div>
                        <div class="stat-number" id="imageCount">__IMAGE_COUNT__</div>
                        <div class="stat-label">已收集作品</div>
                        <div class="stat-target">目標 30,000 件</div>
                    </a>
                    <!-- 已完成AI處理：自動從ChromaDB讀取 -->
                    <div class="stat-card">
                        <div class="stat-icon">🔍</div>
                        <div class="stat-number" id="dinoCount">__DINO_COUNT__</div>
                        <div class="stat-label">已完成AI處理</div>
                        <div class="stat-target">協助審查</div>
                    </div>
                    <!-- 開發天數：前端 JS 根據 start_date 自動計算 -->
                    <div class="stat-card">
                        <div class="stat-icon">📅</div>
                        <div class="stat-number" id="daysActive">1</div>
                        <div class="stat-label">開發天數</div>
                        <div class="stat-target">持續更新中</div>
                    </div>
                    <!-- MVP 期程：3 個月 -->
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
                            <div class="flow-step">
                                <div class="flow-icon">📷</div>
                                <div class="flow-label">拍攝作品</div>
                            </div>
                            <div class="flow-arrow">→</div>
                            <div class="flow-step">
                                <div class="flow-icon">🔬</div>
                                <div class="flow-label">AI 指紋</div>
                            </div>
                            <div class="flow-arrow">→</div>
                            <div class="flow-step">
                                <div class="flow-icon">📊</div>
                                <div class="flow-label">比對報告</div>
                            </div>
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
                            <div class="milestone">
                                <div class="milestone-check">○</div>
                                <div class="milestone-content">
                                    <div class="milestone-name">M1-1 研究文化部資料庫</div>
                                    <div class="milestone-date">2026-03-24 ~ 03-28</div>
                                </div>
                            </div>
                            <div class="milestone">
                                <div class="milestone-check">○</div>
                                <div class="milestone-content">
                                    <div class="milestone-name">M1-2 建立爬蟲框架</div>
                                    <div class="milestone-date">2026-03-30 ~ 04-04</div>
                                </div>
                            </div>
                            <div class="milestone in-progress">
                                <div class="milestone-check">●</div>
                                <div class="milestone-content">
                                    <div class="milestone-name">M1-3 爬蟲 Demo (100件)</div>
                                    <div class="milestone-date">2026-04-07 ~ 04-11</div>
                                </div>
                            </div>
                            <div class="milestone">
                                <div class="milestone-check">○</div>
                                <div class="milestone-content">
                                    <div class="milestone-name">M1-4 爬蟲穩定化</div>
                                    <div class="milestone-date">2026-04-14 ~ 04-25</div>
                                </div>
                            </div>
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
                        <div class="demo-preview placeholder">
                            <div class="placeholder-text">🔍<br>相似度比對<br>（待實作）</div>
                        </div>
                        <h3>相似度比對</h3>
                        <p>上傳作品圖片，快速搜尋資料庫中相似作品</p>
                        <span class="demo-badge">coming soon</span>
                    </div>

                    <div class="demo-card">
                        <div class="demo-preview placeholder">
                            <div class="placeholder-text">📊<br>比對報告<br>（待實作）</div>
                        </div>
                        <h3>PDF 報告生成</h3>
                        <p>一鍵生成標準化審查報告</p>
                        <span class="demo-badge">coming soon</span>
                    </div>

                    <div class="demo-card">
                        <div class="demo-preview placeholder">
                            <div class="placeholder-text">🗺️<br>地理分布<br>（待實作）</div>
                        </div>
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
                    <div class="tech-card">
                        <div class="tech-icon">🧠</div>
                        <h3>AI 模型</h3>
                        <ul>
                            <li>DINOv2 視覺特徵</li>
                            <li>CLIP 語義理解</li>
                            <li>Sentence-Transformers</li>
                        </ul>
                    </div>
                    <div class="tech-card">
                        <div class="tech-icon">💾</div>
                        <h3>向量資料庫</h3>
                        <ul>
                            <li>ChromaDB</li>
                            <li>Faiss 加速檢索</li>
                            <li>30,000+ 向量規模</li>
                        </ul>
                    </div>
                    <div class="tech-card">
                        <div class="tech-icon">🕷️</div>
                        <h3>爬蟲系統</h3>
                        <ul>
                            <li>Scrapling 自適應爬蟲</li>
                            <li>文化部資料庫整合</li>
                            <li>全台 22 縣市覆蓋</li>
                        </ul>
                    </div>
                    <div class="tech-card">
                        <div class="tech-icon">🌐</div>
                        <h3>網站介面</h3>
                        <ul>
                            <li>FastAPI 後端</li>
                            <li>響應式設計</li>
                            <li>PDF 報告生成</li>
                        </ul>
                    </div>
                    <div class="tech-card">
                        <div class="tech-icon">✂️</div>
                        <h3>圖片前處理</h3>
                        <ul>
                            <li>自動切割主體區域</li>
                            <li>rembg 去背（U2-Net）</li>
                            <li>DINOv2 特徵萃取</li>
                        </ul>
                    </div>
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

        <!-- 前端 JavaScript：計算開發天數（根據 PROJECT_DATA["start_date"]） -->
        <script>
            document.addEventListener('DOMContentLoaded', function() {
                // 從 PROJECT_DATA 取得專案開始日期
                const startDate = new Date('2026-03-23');
                const today = new Date();
                // 計算相差天数 + 1（包含起始日）
                const daysActive = Math.floor((today - startDate) / (1000 * 60 * 60 * 24)) + 1;
                document.getElementById('daysActive').textContent = daysActive;
            });
        </script>
    </body>
    </html>
    """
    # 將 HTML 中的預留位置替換為實際數值（每次渲染時動態抓取）
    html_content = html_content.replace("__IMAGE_COUNT__", str(get_image_count()))
    html_content = html_content.replace("__DINO_COUNT__", str(get_dino_count()))
    return HTMLResponse(content=html_content)


# =============================================================================
# 作品圖庫頁面
# =============================================================================

@app.get("/gallery", response_class=HTMLResponse)
async def gallery():
    """
    作品圖庫頁面（Gallery）

    顯示所有已收集作品的縮圖網格，
    每個作品卡顯示：縮圖、作品名稱、作者、年份、設置地點。
    點擊縮圖可在新分頁開放大圖。

    圖片讀取自：data/raw/moc/images/
    中繼資料讀取自：data/raw/moc/works_metadata.json
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))             # 取得主程式所在目錄
    images_dir = os.path.join(base_dir, "data/raw/moc/images")        # 原始圖片目錄
    metadata_file = os.path.join(base_dir, "data/raw/moc/works_metadata.json")  # 作品中繼資料 JSON

    # 讀取 metadata JSON，建立檔名→資料的對照表（dict）
    meta_map = {}
    if os.path.exists(metadata_file):
        with open(metadata_file, "r", encoding="utf-8") as f:
            for item in json.load(f):
                meta_map[item.get("image_file", "")] = item

    # 掃描圖片目錄，建立作品清單
    images = []
    if os.path.exists(images_dir):
        for fname in sorted(os.listdir(images_dir)):
            # 只處理圖片檔案
            if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                fpath = os.path.join(images_dir, fname)
                fsize = os.path.getsize(fpath)                        # 檔案大小（位元組）
                meta = meta_map.get(fname, {})                       # 查詢對應的 metadata
                images.append({
                    "file": fname,                                    # 檔案名稱
                    "title": meta.get("title", fname),                # 作品名稱
                    "artist": meta.get("artist", ""),                 # 作者
                    "year": meta.get("year", ""),                    # 年代
                    "location": meta.get("location", ""),            # 設置地點
                    "material": meta.get("material", ""),             # 材質
                    "url": meta.get("url", ""),                      # 原始作品 URL
                    "size_kb": fsize // 1024,                        # 檔案大小（KB）
                })

    # 產生每個作品卡的 HTML，組成網格
    grid_items = ""
    for img in images:
        grid_items += f"""
        <div class="gallery-item">
            <!-- 點擊圖片：在新分頁開放大圖 -->
            <a href="/gallery-img/{img['file']}" target="_blank">
                <img src="/gallery-img/{img['file']}" alt="{img['title']}" loading="lazy">
            </a>
            <!-- 作品資訊說明 -->
            <div class="gallery-caption">
                <div class="gallery-title">{img['title']}</div>
                <div class="gallery-meta">{img['artist']} · {img['year']}</div>
                <div class="gallery-location">{img['location']}</div>
            </div>
        </div>"""

    # 組合完整 HTML 頁面
    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>作品圖庫 - Artsense</title>
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Noto Sans TC', sans-serif; background: #0a0a0f; color: #e0e0e0; margin: 0; padding: 0; }}
        /* 頂部導航列 */
        .topbar {{ background: #111; padding: 16px 24px; display: flex; align-items: center; gap: 16px; border-bottom: 1px solid #222; }}
        .topbar a {{ color: #7dd3fc; text-decoration: none; font-size: 14px; }}
        .topbar a:hover {{ text-decoration: underline; }}
        .page-title {{ color: #fff; font-size: 20px; font-weight: 700; margin: 0; }}
        .count {{ color: #aaa; font-size: 14px; }}
        /* 縮圖網格：響應式排列，最小每格 220px */
        .gallery-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 20px; padding: 24px; max-width: 1400px; margin: 0 auto; }}
        /* 作品卡片：暗色背景 + 圓角 + hover 效果 */
        .gallery-item {{ background: #16161d; border-radius: 12px; overflow: hidden; transition: transform 0.2s, box-shadow 0.2s; }}
        .gallery-item:hover {{ transform: translateY(-4px); box-shadow: 0 8px 24px rgba(125,211,252,0.15); }}
        /* 圖片：保持 4:5 比例，object-fit 裁切填滿 */
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
    提供作品圖片檔案（Gallery Image Server）

    根據檔案名稱回傳對應的圖片檔案。
    搜尋順序：
    1. data/processed/moc/images_nobg_final/{filename}
    2. data/processed/moc/images/{filename}
    3. data/raw/moc/images/{filename}

    Args:
        filename: 圖片檔案名稱（URL 路徑參數）

    Returns:
        FileResponse: 圖片檔案（若檔案存在）
        JSON error: 檔案不存在時回傳 404
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 依序搜尋多個目錄
    search_dirs = [
        os.path.join(base_dir, "data/processed/moc/images_nobg_final"),
        os.path.join(base_dir, "data/processed/moc/images"),
        os.path.join(base_dir, "data/raw/moc/images"),
    ]
    
    for search_dir in search_dirs:
        fpath = os.path.join(search_dir, filename)
        if os.path.exists(fpath):
            return FileResponse(fpath)

    # Fallback: 嘗試找同作品 ID 的任何圖檔
    parts = filename_decoded.rsplit(".", 1)
    if len(parts) == 2:
        base, ext = parts
        # 抽出作品 ID（第一段底線之前）
        work_id = base.split("_")[0] if "_" in base else base
        for suffix in ["_nobg_final.png", "_crop3.jpg", "_crop2.jpg", "_crop.jpg"]:
            fallback_name = f"{work_id}{suffix}"
            for search_dir in search_dirs:
                fallback_path = os.path.join(search_dir, fallback_name)
                if os.path.exists(fallback_path):
                    return FileResponse(fallback_path)
        # 最後：搜尋所有包含 work_id 的圖檔
        for search_dir in search_dirs:
            if os.path.exists(search_dir):
                for fname in os.listdir(search_dir):
                    if fname.startswith(work_id) and fname.lower().endswith(('.jpg', '.png', '.jpeg')):
                        return FileResponse(os.path.join(search_dir, fname))

    return {"error": "not found"}


# =============================================================================
# API 端點
# =============================================================================

@app.get("/api/status")
async def api_status():
    """
    系統狀態 API

    回傳目前系統的運行狀態與統計數值，
    供前端 JavaScript 或外部系統查詢使用。

    Returns:
        dict:
            - status (str): 系統狀態，固定 "online"
            - version (str): 目前版本號
            - image_count (int): 已收集作品數（動態）
            - target_count (int): 目標作品數（固定 30000）
    """
    return {
        "status": "online",
        "version": "MVP v0.1",
        "image_count": get_image_count(),
        "dino_count": get_dino_count(),
        "target_count": 30000,
    }


@app.get("/api/search")
async def api_search(q: str = "", limit: int = 5):
    """
    向量相似度搜尋 API

    將查詢文字送至 Ollama 產生文字向量，
    再於 ChromaDB 向量資料庫中搜尋最相似的作品。

    Args:
        q (str): 查詢關鍵字（需至少 2 個字元）
        limit (int): 回傳結果數量上限，預設 5

    Returns:
        dict:
            - query (str): 原始查詢文字
            - count (int): 回傳結果數量
            - results (list): 作品清單（包含 title, artist, year, location 等欄位）
            - error (str): 錯誤訊息（若有）
    """
    # 參數驗證：查詢字詞至少需要 2 個字元
    if not q or len(q) < 2:
        return {"error": "查詢字詞太短（至少2個字）", "results": []}

    base_dir = os.path.dirname(os.path.abspath(__file__))
    chroma_path = os.path.join(base_dir, "data/chroma_public_art")   # ChromaDB 資料庫路徑

    # 檢查向量資料庫是否存在
    if not os.path.exists(chroma_path):
        return {"error": "向量資料庫尚未建立", "results": []}

    try:
        # 連線 ChromaDB，取得 collection
        import chromadb
        client = chromadb.PersistentClient(path=chroma_path)
        collection = client.get_collection("public_art_works")

        # 透過 Ollama API 將文字轉為向量
        try:
            import httpx
            r = httpx.post(
                "http://localhost:11434/api/embeddings",              # Ollama embedding 端點
                json={"model": "llama3.1:latest", "prompt": q},     # 使用 llama3.1 模型
                timeout=30
            )
            q_emb = r.json()["embedding"]                             # 取出 4096 維向量
        except Exception as e:
            return {"error": f"Ollama 連線失敗: {e}", "results": []}

        # 向量相似度搜尋
        results = collection.query(
            query_embeddings=[q_emb],
            n_results=limit,
            include=["documents", "metadatas"]                        # 回傳文件內容與 metadata
        )

        # 整理輸出格式
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
    """
    取得作品列表 API

    回傳所有已收集作品的基本資訊，
    適用於前端下拉選單、列表顯示等情境。

    Returns:
        dict:
            - count (int): 作品總數
            - works (list): 作品清單（file, title, artist, year, location, material, url）
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    images_dir = os.path.join(base_dir, "data/raw/moc/images")
    metadata_file = os.path.join(base_dir, "data/raw/moc/works_metadata.json")

    # 建立 metadata 對照表
    meta_map = {}
    if os.path.exists(metadata_file):
        with open(metadata_file, "r", encoding="utf-8") as f:
            for item in json.load(f):
                meta_map[item.get("image_file", "")] = item

    # 掃描圖片目錄，對應 metadata
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
    """取得審核狀態檔案路徑"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "data/raw/moc/review_status.json")

def load_review_status():
    """載入審核狀態"""
    status_file = get_review_status_file()
    if os.path.exists(status_file):
        with open(status_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_review_status(status):
    """儲存審核狀態"""
    status_file = get_review_status_file()
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """
    圖片審核後台頁面

    提供人類視覺比對原始圖片與處理後圖片的介面，
    支援通過/拒絕操作。
    """
    with open("web/templates/admin.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/api/admin/works")
async def api_admin_works():
    """
    取得待審核作品列表 API

    回傳所有作品的審核狀態，包含：
    - 原始檔案、處理後檔案
    - 作品 metadata
    - 審核狀態（pending/approved/rejected）

    Returns:
        list: 作品清單
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    images_dir = os.path.join(base_dir, "data/raw/moc/images")
    processed_dir = os.path.join(base_dir, "data/processed/moc/images_nobg_final")
    metadata_file = os.path.join(base_dir, "data/raw/moc/works_metadata.json")
    review_status = load_review_status()

    # 建立 metadata 對照表
    meta_map = {}
    if os.path.exists(metadata_file):
        with open(metadata_file, "r", encoding="utf-8") as f:
            for item in json.load(f):
                meta_map[item.get("image_file", "")] = item

    # 掃描圖片目錄
    works = []
    if os.path.exists(images_dir):
        for fname in sorted(os.listdir(images_dir)):
            if not fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                continue

            # 對應的處理後檔案
            base_name = fname.replace('.jpg', '').replace('.jpeg', '').replace('.png', '').replace('.webp', '')
            cropped_file = f"{base_name}_crop3.jpg"
            nobg_file = f"{base_name}_nobg_final.png"

            # 優先使用 nobg_final，否則用 crop3
            processed_file = None
            if os.path.exists(os.path.join(processed_dir, nobg_file)):
                processed_file = nobg_file
            elif os.path.exists(os.path.join(processed_dir, cropped_file)):
                processed_file = cropped_file

            work_id = str(base_name)
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
    通過審核

    將作品標記為已通過審核，寫入 DINOv2 特徵至 ChromaDB。
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    review_status = load_review_status()

    review_status[work_id] = {
        "status": "approved",
        "updated_at": datetime.now().isoformat()
    }
    save_review_status(review_status)

    return {"success": True, "message": "已通過審核"}


@app.post("/api/admin/reject/{work_id}")
async def api_admin_reject(work_id: str):
    """
    拒絕審核

    將作品標記為需要重新處理。
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    review_status = load_review_status()

    review_status[work_id] = {
        "status": "rejected",
        "updated_at": datetime.now().isoformat()
    }
    save_review_status(review_status)

    return {"success": True, "message": "已標記為需要重新處理"}


@app.post("/api/admin/reprocess/{work_id}")
async def api_admin_reprocess(work_id: str, bboxes: list[list[int]] = Body(default=None)):
    """
    以自訂 bboxes 重新處理作品（支援多選）。

    bboxes: [[x1, y1, x2, y2], ...] — 每個區塊一組邊界（像素，圖片自然尺寸）
    將 status 設為 pending，儲存 bboxes 供後續批次處理。
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    review_status = load_review_status()

    # 儲存 bboxes
    entry = review_status.get(work_id, {})
    entry["status"] = "pending"
    entry["updated_at"] = datetime.now().isoformat()
    if bboxes:
        entry["manual_bboxes"] = bboxes  # [[x1,y1,x2,y2], ...]

    review_status[work_id] = entry
    save_review_status(review_status)

    # 執行重新處理（以 SAM bbox prompt，支援多 bbox）
    processed_dir = os.path.join(base_dir, "data/processed/moc/images_nobg_final")
    orig_dir = os.path.join(base_dir, "data/raw/moc/images")
    os.makedirs(processed_dir, exist_ok=True)

    base_name = work_id
    orig_files = [f for f in os.listdir(orig_dir)
                  if f.startswith(base_name) and f.lower().endswith(('.jpg','.jpeg','.png','.webp'))]
    if not orig_files:
        return {"success": False, "error": f"找不到原始圖片：{work_id}"}

    orig_path = os.path.join(orig_dir, orig_files[0])
    out_name = f"{base_name}_nobg_final.png"
    out_path = os.path.join(processed_dir, out_name)

    from src.image_pipeline import segment_artwork_with_bboxes
    cropped_files = segment_artwork_with_bboxes(orig_path, out_path, bboxes)

    return {"success": True, "cropped_files": cropped_files, "count": len(bboxes) if bboxes else 0}


# =============================================================================
# 相似度比對流程
# =============================================================================

import shutil
import uuid
from fastapi import UploadFile

@app.get("/compare", response_class=HTMLResponse)
async def compare_page():
    """相似度比對頁面"""
    with open("web/templates/compare.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.post("/api/compare/upload")
async def api_compare_upload(file: UploadFile, remove_bg: bool = True):
    """
    上傳圖片並進行前處理
    
    1. 儲存原始圖片
    2. 若 remove_bg=True，套用 rembg 去背
    3. 回傳 search_id 供後續使用
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(base_dir, "data/temp_compare")
    os.makedirs(temp_dir, exist_ok=True)
    
    search_id = str(uuid.uuid4())[:8]
    orig_path = os.path.join(temp_dir, f"{search_id}_orig.jpg")
    
    # 儲存原始圖片
    with open(orig_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    
    processed_path = orig_path
    if remove_bg:
        try:
            from rembg import remove
            from PIL import Image
            
            img = Image.open(orig_path)
            result = remove(img)
            processed_path = os.path.join(temp_dir, f"{search_id}_processed.png")
            result.save(processed_path)
        except Exception as e:
            # 如果去背失敗，使用原始圖片
            processed_path = orig_path
    
    return {
        "success": True,
        "search_id": search_id,
        "has_processed": remove_bg
    }


@app.get("/api/compare/image/{search_id}")
async def api_compare_image(search_id: str):
    """取得處理後的圖片"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(base_dir, "data/temp_compare")
    
    # 優先回傳處理過的圖片
    processed_path = os.path.join(temp_dir, f"{search_id}_processed.png")
    if os.path.exists(processed_path):
        return FileResponse(processed_path)
    
    # 否則回傳原始圖片
    orig_path = os.path.join(temp_dir, f"{search_id}_orig.jpg")
    if os.path.exists(orig_path):
        return FileResponse(orig_path)
    
    return {"error": "Image not found"}


@app.get("/api/compare/search/{search_id}")
async def api_compare_search(search_id: str):
    """
    對處理過的圖片進行相似度比對（使用 Server-Sent Events）
    """
    from fastapi.responses import StreamingResponse
    import time
    import asyncio
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(base_dir, "data/temp_compare")
    processed_dir = os.path.join(base_dir, "data/processed/moc/images_nobg_final")
    
    processed_path = os.path.join(temp_dir, f"{search_id}_processed.png")
    orig_path = os.path.join(temp_dir, f"{search_id}_orig.jpg")
    img_path = processed_path if os.path.exists(processed_path) else orig_path if os.path.exists(orig_path) else upload_path if os.path.exists(upload_path) else None
    
    if not os.path.exists(img_path):
        return {"error": "Image not found"}
    
    async def generate():
        # 載入 DINOv2
        yield f"event: status\ndata: {json.dumps({'message': '載入 DINOv2 模型...'})}\n\n"
        await asyncio.sleep(0.1)
        
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModel
            from PIL import Image
            import numpy as np
            
            processor = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
            model = AutoModel.from_pretrained("facebook/dinov2-base")
            model.eval()
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': f'模型載入失敗: {str(e)}'})}\n\n"
            return
        
        # 萃取特徵
        yield f"event: status\ndata: {json.dumps({'message': '萃取圖片特徵...'})}\n\n"
        await asyncio.sleep(0.1)
        
        try:
            img = Image.open(img_path).convert('RGB')
            img_resized = img.resize((224, 224))
            inputs = processor(images=img_resized, return_tensors="pt")
            
            with torch.no_grad():
                outputs = model(**inputs)
                query_emb = outputs.last_hidden_state[:, 0, :].numpy().flatten()
            
            query_normalized = query_emb / np.linalg.norm(query_emb)
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': f'特徵萃取失敗: {str(e)}'})}\n\n"
            return
        
        # 載入資料庫
        yield f"event: status\ndata: {json.dumps({'message': '載入資料庫...'})}\n\n"
        await asyncio.sleep(0.1)
        
        try:
            import chromadb
            client = chromadb.PersistentClient(path=os.path.join(base_dir, "data/chroma_public_art"))
            collection = client.get_collection("public_art_dino_features")
            items = collection.get(include=['embeddings', 'metadatas'])
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': f'資料庫載入失敗: {str(e)}'})}\n\n"
            return
        
        # 計算相似度（所有作品，不過濾審核狀態）
        results = []
        total_items = len(items['ids'])
        
        for i in range(total_items):
            work_id = items['ids'][i]
            meta = items['metadatas'][i]
            final_file = meta.get('final_file', '')
            
            stored_emb = np.array(items['embeddings'][i])
            stored_norm = np.linalg.norm(stored_emb)
            if stored_norm > 0:
                stored_normalized = stored_emb / stored_norm
                cos_sim = np.dot(query_normalized, stored_normalized)
                similarity_pct = cos_sim * 100
            else:
                similarity_pct = 0
            
            # 聰明找圖檔：用 work_id 推測可能的檔名（避免 cropped_file 為空的問題）
            nobg_candidate = f"{work_id}_nobg_final.png"
            crop_candidate = f"{work_id}_crop3.jpg"
            # 優先嘗試 nobg_final，其次 crop3
            cropped_file = nobg_candidate if os.path.exists(os.path.join(processed_dir, nobg_candidate)) else \
                          crop_candidate if os.path.exists(os.path.join(processed_dir, crop_candidate)) else \
                          final_file if final_file else ''

            results.append({
                'work_id': work_id,
                'title': meta.get('title', work_id),
                'artist': meta.get('artist', ''),
                'year': meta.get('year', ''),
                'location': meta.get('location', ''),
                'material': meta.get('material', ''),
                'cropped_file': cropped_file,
                'similarity': round(similarity_pct, 1)
            })
            
            # 發送進度
            progress_data = json.dumps({
                'current': i + 1,
                'total': total_items,
                'current_title': meta.get('title', work_id)[:15]
            })
            yield f"event: progress\ndata: {progress_data}\n\n"
            await asyncio.sleep(0.02)
        
        # 只保留相似度 >= 25% 的作品
        high_match = [r for r in results if r['similarity'] >= 25]
        high_match.sort(key=lambda x: x['similarity'], reverse=True)
        
        complete_data = json.dumps({
            'success': True,
            'total': len(results),
            'matches': len(high_match),
            'results': high_match,
            'query_image': img_path,
            'search_id': search_id
        })
        yield f"event: complete\ndata: {complete_data}\n\n"
    
    return StreamingResponse(
        generate(), 
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# =============================================================================
# 程式進入點
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    # 啟動 FastAPI 開發伺服器
    # host="0.0.0.0"：接受外部連線（同一網域內可透過 IP 存取）
    # port=8000：HTTP 埠號
    # reload=True：偵測程式碼變更時自動重載（開發模式專用）
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
