"""
Artsense ChromaDB 整合腳本
將爬取的公共藝術作品存入 ChromaDB 向量資料庫
"""

import os
import json
import sys

# 確保路徑正確
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

def load_public_art_to_chroma(
    chroma_path: str = None,
    image_dir: str = None,
    metadata_file: str = None
):
    """將公共藝術作品載入 ChromaDB"""
    import chromadb
    from chromadb.config import Settings
    import httpx

    # 預設路徑
    if chroma_path is None:
        chroma_path = os.path.join(BASE_DIR, "data/chroma_public_art")
    if image_dir is None:
        image_dir = os.path.join(BASE_DIR, "data/raw/moc/images")
    if metadata_file is None:
        metadata_file = os.path.join(BASE_DIR, "data/raw/moc/works_metadata.json")

    os.makedirs(chroma_path, exist_ok=True)
    os.makedirs(os.path.dirname(image_dir), exist_ok=True)

    # 初始化 ChromaDB
    client = chromadb.PersistentClient(path=chroma_path)

    # 嘗試取得現有 collection 或建立新的
    try:
        collection = client.get_collection("public_art_works")
        print(f"現有 Collection，筆數: {collection.count()}")
    except Exception:
        collection = client.get_or_create_collection(
            name="public_art_works",
            metadata={"description": "文化部公共藝術作品指紋庫", "source": "publicart.moc.gov.tw"}
        )
        print("建立新 Collection")

    # 讀取 metadata
    if os.path.exists(metadata_file):
        with open(metadata_file, "r", encoding="utf-8") as f:
            works = json.load(f)
        print(f"從 {metadata_file} 讀取到 {len(works)} 筆資料")
    else:
        # 從圖片檔名自動產生 metadata
        works = []
        if os.path.exists(image_dir):
            for fname in os.listdir(image_dir):
                if fname.lower().endswith(('.jpg', '.jpeg', '.png')):
                    # 嘗試解析檔名格式：ID_名稱.jpg
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
        print("沒有作品資料")
        return collection

    def get_embedding_ollama(text, model="llama3.1:latest"):
        """使用 Ollama 取得文字 embedding"""
        try:
            response = httpx.post(
                "http://localhost:11434/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=60
            )
            response.raise_for_status()
            return response.json()["embedding"]
        except Exception as e:
            print(f"  Embedding 失敗: {e}")
            return None

    def image_to_base64(image_path):
        with open(image_path, "rb") as f:
            import base64
            return base64.b64encode(f.read()).decode("utf-8")

    # 檢查已存在的 ID
    existing = set()
    try:
        existing_ids = collection.get(include=[])["ids"]
        existing = set(existing_ids)
        print(f"已存在 {len(existing)} 筆資料")
    except:
        pass

    # 新增缺失的資料
    added = 0
    for work in works:
        doc_id = f"work_{work['id']}"
        if doc_id in existing:
            continue

        text = f"作品名稱：{work.get('title','')}，作者：{work.get('artist','')或不確定}，創作年代：{work.get('year','')}，興辦機關：{work.get('org','')或不確定}，設置地點：{work.get('location','')或不確定}，作品材質：{work.get('material','')或不確定}，經費：{work.get('budget','')或不確定}，作品描述：{work.get('desc','')或不確定}"

        print(f"  加入 {work.get('title', doc_id)}...", end=" ", flush=True)
        emb = get_embedding_ollama(text)
        if emb is None:
            print("失敗（無法產生 embedding）")
            continue

        # 讀取圖片
        img_b64 = ""
        img_path = os.path.join(image_dir, work.get("image_file", ""))
        if os.path.exists(img_path):
            img_b64 = image_to_base64(img_path)

        collection.add(
            ids=[doc_id],
            embeddings=[emb],
            documents=[text],
            metadatas=[{
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

    print(f"\n完成！新增 {added} 筆，總計 {collection.count()} 筆")
    return collection

if __name__ == "__main__":
    collection = load_public_art_to_chroma()

    # 測試查詢
    print("\n查詢測試：")
    queries = ["學校 閱讀", "金屬 雕塑", "馬賽克"]
    for q in queries:
        import httpx
        def get_emb(text):
            r = httpx.post("http://localhost:11434/api/embeddings", json={"model": "llama3.1:latest", "prompt": text}, timeout=60)
            return r.json()["embedding"]

        q_emb = get_emb(q)
        results = collection.query(query_embeddings=[q_emb], n_results=2, include=["metadatas"])
        print(f"\n「{q}」:")
        for meta in results["metadatas"][0]:
            print(f"  - {meta.get('title', 'N/A')} | {meta.get('artist', 'N/A')}")
