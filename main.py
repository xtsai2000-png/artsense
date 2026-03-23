"""
Artsense Web Server
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime
import os

app = FastAPI(title="Artsense")

# Static files
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# Templates
templates = Jinja2Templates(directory="web/templates")

import glob
import os

def get_image_count():
    """動態取得已收集作品數量"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    images_dir = os.path.join(base_dir, "data/raw/moc/images")
    if os.path.exists(images_dir):
        return len([f for f in os.listdir(images_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))])
    return 0

PROJECT_DATA = {
    "name": "Artsense",
    "name_full": "公共藝術指紋庫",
    "tagline": "杜絕抄襲，守护原创",
    "description": "Artsense 是台灣首個 AI 公共藝術指紋庫",
    "image_count": get_image_count(),
    "target_count": 30000,
    "case_count": 0,
    "start_date": "2026-03-23",
    "version": "MVP v0.1",
}

@app.get("/", response_class=HTMLResponse)
async def home():
    """Homepage"""
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
                    <div class="stat-card">
                        <div class="stat-icon">📸</div>
                        <div class="stat-number" id="imageCount">__IMAGE_COUNT__</div>
                        <div class="stat-label">已收集作品</div>
                        <div class="stat-target">目標 30,000 件</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-icon">🔍</div>
                        <div class="stat-number">0</div>
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
    # 動態替換計數
    html_content = html_content.replace("__IMAGE_COUNT__", str(PROJECT_DATA["image_count"]))
    return HTMLResponse(content=html_content)

@app.get("/api/status")
async def api_status():
    """API status"""
    return {
        "status": "online",
        "version": "MVP v0.1",
        "image_count": PROJECT_DATA["image_count"],
        "target_count": 30000,
    }

@app.get("/api/search")
async def api_search(q: str = "", limit: int = 5):
    """搜尋公共藝術作品"""
    if not q or len(q) < 2:
        return {"error": "查詢字詞太短", "results": []}

    import os, sys, httpx
    base_dir = os.path.dirname(os.path.abspath(__file__))
    chroma_path = os.path.join(base_dir, "data/chroma_public_art")

    if not os.path.exists(chroma_path):
        return {"error": "向量資料庫尚未建立", "results": []}

    try:
        import chromadb
        client = chromadb.PersistentClient(path=chroma_path)
        collection = client.get_collection("public_art_works")

        # 取得查詢向量
        try:
            r = httpx.post("http://localhost:11434/api/embeddings", json={"model": "llama3.1:latest", "prompt": q}, timeout=30)
            q_emb = r.json()["embedding"]
        except Exception as e:
            return {"error": f"Ollama 連線失敗: {e}", "results": []}

        # 向量搜尋
        results = collection.query(query_embeddings=[q_emb], n_results=limit, include=["documents", "metadatas"])

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
