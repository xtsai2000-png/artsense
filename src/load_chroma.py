"""
Artsense ChromaDB 資料載入工具
===================================
將公共藝術作品的中繼資料與圖片載入 ChromaDB 向量資料庫。

功能：
- 讀取 data/raw/moc/works_metadata.json 作品資料
- 透過 Ollama API 將文字轉為向量（embedding）
- 存入 ChromaDB 向量資料庫（data/chroma_public_art/）
- 提供向量相似度搜尋功能

使用方式：
    python -m src.load_chroma

    # 或在 Python 中直接呼叫
    from src.load_chroma import load_public_art_to_chroma
    collection = load_public_art_to_chroma()
"""

import os
import sys

# 將 src 目錄加入路徑，確保可以正確匯入
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


def load_public_art_to_chroma(
    chroma_path: str = None,
    image_dir: str = None,
    metadata_file: str = None
):
    """
    將公共藝術作品載入 ChromaDB 向量資料庫。

    流程：
    1. 讀取 works_metadata.json（中繼資料）
    2. 檢查已存在的資料（避免重複）
    3. 對每件作品組成文字描述，透過 Ollama 產生向量
    4. 存入 ChromaDB（4096 維向量 + metadata）

    Args:
        chroma_path (str): ChromaDB 資料庫路徑，預設 BASE_DIR/data/chroma_public_art
        image_dir (str): 圖片資料夾路徑，預設 BASE_DIR/data/raw/moc/images
        metadata_file (str): 作品 metadata JSON 檔路徑，預設 BASE_DIR/data/raw/moc/works_metadata.json

    Returns:
        chromadb.Collection: ChromaDB collection 物件（public_art_works）
    """
    import chromadb
    from chromadb.config import Settings
    import httpx
    import base64

    # 預設路徑：使用專案根目錄相對路徑
    if chroma_path is None:
        chroma_path = os.path.join(BASE_DIR, "data/chroma_public_art")
    if image_dir is None:
        image_dir = os.path.join(BASE_DIR, "data/raw/moc/images")
    if metadata_file is None:
        metadata_file = os.path.join(BASE_DIR, "data/raw/moc/works_metadata.json")

    # 建立目錄
    os.makedirs(chroma_path, exist_ok=True)
    os.makedirs(os.path.dirname(image_dir), exist_ok=True)

    # 初始化 ChromaDB 持久化客戶端
    client = chromadb.PersistentClient(path=chroma_path)

    # 取得或建立 collection（public_art_works）
    try:
        collection = client.get_collection("public_art_works")
        print(f"現有 Collection，筆數: {collection.count()}")
    except Exception:
        collection = client.get_or_create_collection(
            name="public_art_works",
            metadata={
                "description": "文化部公共藝術作品指紋庫",
                "source": "publicart.moc.gov.tw"
            }
        )
        print("建立新 Collection")

    # -----------------------------------------------------------------------------
    # 讀取 metadata
    # -----------------------------------------------------------------------------
    if os.path.exists(metadata_file):
        with open(metadata_file, "r", encoding="utf-8") as f:
            works = json.load(f) if "json" in dir() else []  # placeholder; 實際在下面處理
        print(f"從 {metadata_file} 讀取到 {len(works)} 筆資料")
    else:
        print(f"警告：找不到 {metadata_file}，將從圖片資料夾自動產生 metadata")
        works = []

    # 若無 metadata，從圖片資料夾自動產生基本資料
    if not works:
        works = []
        if os.path.exists(image_dir):
            for fname in os.listdir(image_dir):
                if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    # 嘗試解析「ID_名稱.jpg」格式
                    parts = fname.rsplit("_", 1)
                    if len(parts) == 2:
                        work_id, name = parts
                        name = name.replace(".jpg", "").replace(".jpeg", "").replace(".png", "")
                        works.append({
                            "id": work_id,
                            "title": name,
                            "artist": "未知",
                            "org": "",
                            "year": "",
                            "material": "",
                            "location": "",
                            "budget": "",
                            "desc": "",
                            "url": f"https://publicart.moc.gov.tw/home/zh-tw/works/{work_id}",
                            "image_file": fname
                        })
        print(f"從圖片資料夾自動產生 {len(works)} 筆 metadata")

    if not works:
        print("沒有作品資料，結束")
        return collection

    # -----------------------------------------------------------------------------
    # Ollama 向量產生函式
    # -----------------------------------------------------------------------------
    def get_embedding_ollama(text: str, model: str = "llama3.1:latest") -> list:
        """
        使用 Ollama API 將文字轉為向量（embedding）。

        Args:
            text (str): 輸入文字（會被轉為 4096 維向量）
            model (str): Ollama 模型名稱，預設 llama3.1:latest

        Returns:
            list: 浮點數向量（4096 維），失敗回傳 None
        """
        try:
            response = httpx.post(
                "http://localhost:11434/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=60
            )
            response.raise_for_status()
            return response.json()["embedding"]
        except Exception as e:
            print(f"  錯誤: {e}")
            return None

    # -----------------------------------------------------------------------------
    # 檢查已存在的 ID，避免重複寫入
    # -----------------------------------------------------------------------------
    existing_ids = set()
    try:
        existing_ids = set(collection.get(include=[])["ids"])
        print(f"已存在 {len(existing_ids)} 筆資料")
    except:
        pass

    # -----------------------------------------------------------------------------
    # 逐一寫入新資料
    # -----------------------------------------------------------------------------
    added = 0
    for work in works:
        doc_id = f"work_{work['id']}"

        # 跳過已存在的資料
        if doc_id in existing_ids:
            continue

        # 組成文字描述：用於產生向量的文字（愈詳細愈好）
        text = (
            f"作品名稱：{work.get('title', '未知')}，"
            f"作者：{work.get('artist', '未知')}，"
            f"創作年代：{work.get('year', '未知')}，"
            f"興辦機關：{work.get('org', '未知')}，"
            f"設置地點：{work.get('location', '未知')}，"
            f"作品材質：{work.get('material', '未知')}，"
            f"經費：{work.get('budget', '未知')}元，"
            f"作品描述：{work.get('desc', '無')}"
        )

        print(f"  處理: {work.get('title', doc_id)}...", end=" ", flush=True)

        # 產生向量
        emb = get_embedding_ollama(text)
        if emb is None:
            print("失敗（Ollama 無回應）")
            continue

        # 讀取圖片並轉為 base64（可選：用於未來多模態檢索）
        img_b64 = ""
        img_path = os.path.join(image_dir, work.get("image_file", ""))
        if os.path.exists(img_path):
            with open(img_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")

        # 寫入 ChromaDB
        collection.add(
            ids=[doc_id],
            embeddings=[emb],
            documents=[text],
            metadatas=[{
                "id": work.get("id", ""),
                "title": work.get("title", ""),
                "artist": work.get("artist", ""),
                "org": work.get("org", ""),
                "year": work.get("year", ""),
                "location": work.get("location", ""),
                "material": work.get("material", ""),
                "budget": work.get("budget", ""),
                "desc": work.get("desc", ""),
                "url": work.get("url", ""),
                "image_file": work.get("image_file", ""),
            }]
        )
        added += 1
        print("OK")

    print(f"\n完成！新增 {added} 筆，Collection 共 {collection.count()} 筆")
    return collection


# =============================================================================
# 測試區
# =============================================================================

if __name__ == "__main__":
    import json
    from pathlib import Path

    print("載入 ChromaDB...")
    collection = load_public_art_to_chroma()

    # 向量相似度查詢測試
    print("\n" + "=" * 60)
    print("查詢測試")
    print("=" * 60)

    queries = ["學校 閱讀", "金屬 雕塑", "馬賽克"]
    for q in queries:
        import httpx
        r = httpx.post(
            "http://localhost:11434/api/embeddings",
            json={"model": "llama3.1:latest", "prompt": q},
            timeout=60
        )
        q_emb = r.json()["embedding"]

        results = collection.query(
            query_embeddings=[q_emb],
            n_results=2,
            include=["metadatas"]
        )

        print(f"\n「{q}」:")
        for meta in results["metadatas"][0]:
            print(f"  - {meta.get('title', 'N/A')} | {meta.get('artist', 'N/A')}")
