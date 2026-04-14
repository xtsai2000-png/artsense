"""
Artsense 向量搜尋模組
===
- ChromaDB 相似度搜尋（DINOv2 視覺 + Ollama 語義）
- 回饋迴圈：標記抄襲/非抄襲案例
- PCA 索引重建
- phash 庫管理
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np

from src.image_pipeline import (
    compress_vector, compress_vectors_batch,
    get_pca_model, train_pca,
    SIMILARITY_THRESH, PCA_DIM,
)
from src.auth import get_similarity_thresh

logger = logging.getLogger(__name__)

# =============================================================================
# ChromaDB 連線
# =============================================================================

_chroma_client     = None
_chroma_collection = None


def get_chroma(base_dir: str):
    """取得 ChromaDB collection（Lazy 單例）"""
    global _chroma_client, _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection

    import chromadb
    chroma_path = os.path.join(base_dir, "data", "chroma_public_art")
    os.makedirs(chroma_path, exist_ok=True)

    _chroma_client = chromadb.PersistentClient(path=chroma_path)
    try:
        _chroma_collection = _chroma_client.get_collection("public_art_works")
    except Exception:
        _chroma_collection = _chroma_client.create_collection(
            "public_art_works",
            metadata={"hnsw:space": "cosine"}  # 餘弦距離
        )
    logger.info(f"ChromaDB 連線成功，目前 {_chroma_collection.count()} 筆向量")
    return _chroma_collection


def reset_chroma_singleton():
    """重建索引後呼叫，強制重新連線"""
    global _chroma_client, _chroma_collection
    _chroma_client     = None
    _chroma_collection = None


# =============================================================================
# 視覺相似度搜尋（DINOv2）
# =============================================================================

def search_by_image(
    img_path: str,
    base_dir: str,
    limit: int = 10,
    thresh: Optional[float] = None,
) -> list[dict]:
    """
    以圖搜圖：萃取查詢圖的 DINOv2 特徵後搜尋 ChromaDB。

    回傳相似度高於 thresh 的結果，依相似度降序排列。
    """
    from src.image_pipeline import extract_features_single
    from sklearn.preprocessing import normalize

    thresh = thresh or get_similarity_thresh()

    embedding = extract_features_single(img_path)
    if embedding is None:
        return []

    # L2 正規化（與 ChromaDB 內部向量一致）
    embedding = normalize(embedding.reshape(1, -1)).flatten()

    # 查詢 DINO collection（768維）而非 public_art_works（4096維）
    return _query_chroma(base_dir, embedding.tolist(), limit, thresh, use_dino_collection=True)


# =============================================================================
# 語義搜尋（Ollama text embedding）
# =============================================================================

def search_by_text(
    query: str,
    base_dir: str,
    limit: int = 10,
    thresh: Optional[float] = None,
) -> list[dict]:
    """
    文字搜尋：透過 Ollama 產生查詢向量後搜尋 ChromaDB。
    """
    thresh = thresh or get_similarity_thresh()

    try:
        import httpx
        r = httpx.post(
            "http://localhost:11434/api/embeddings",
            json={"model": "llama3.1:latest", "prompt": query},
            timeout=30,
        )
        raw_emb = np.array(r.json()["embedding"], dtype=np.float32)

        # Ollama 向量維度可能與 DINOv2 不同，壓縮到 PCA_DIM
        if len(raw_emb) > PCA_DIM:
            raw_emb = raw_emb[:PCA_DIM]
        elif len(raw_emb) < PCA_DIM:
            raw_emb = np.pad(raw_emb, (0, PCA_DIM - len(raw_emb)))

        return _query_chroma(base_dir, raw_emb.tolist(), limit, thresh)

    except Exception as e:
        logger.error(f"Ollama 搜尋失敗：{e}")
        return []


def _query_chroma(
    base_dir: str,
    embedding: list,
    limit: int,
    thresh: float,
    use_dino_collection: bool = False,
) -> list[dict]:
    """底層查詢 ChromaDB，過濾低於閾值的結果"""
    import chromadb
    chroma_path = os.path.join(base_dir, "data", "chroma_public_art")
    client = chromadb.PersistentClient(path=chroma_path)

    if use_dino_collection:
        collection = client.get_collection("public_art_dino_features")
    else:
        collection = get_chroma(base_dir)

    if collection.count() == 0:
        return []

    results = collection.query(
        query_embeddings=[embedding],
        n_results=min(limit * 2, collection.count()),  # 多取一些再過濾
        include=["metadatas", "distances"],
    )

    artworks = []
    for meta, dist in zip(
        results["metadatas"][0],
        results["distances"][0],
    ):
        # ChromaDB cosine distance: 0=完全相同, 2=完全相反
        # 轉換為相似度 0~1
        similarity = 1.0 - (dist / 2.0)
        if similarity < thresh:
            continue
        artworks.append({
            "id":         meta.get("id", ""),
            "title":      meta.get("title", ""),
            "artist":     meta.get("artist", ""),
            "year":       meta.get("year", ""),
            "location":   meta.get("location", ""),
            "material":   meta.get("material", ""),
            "url":        meta.get("url", ""),
            "image_file": meta.get("image_file", ""),
            "similarity": round(similarity, 4),
        })

    # 依相似度降序
    artworks.sort(key=lambda x: x["similarity"], reverse=True)
    return artworks[:limit]


# =============================================================================
# 回饋迴圈
# =============================================================================

FEEDBACK_FILE = "data/feedback/plagiarism_cases.jsonl"


def save_feedback(
    base_dir: str,
    query_work_id: str,
    matched_work_id: str,
    is_plagiarism: bool,
    reviewer: str,
    note: str = "",
):
    """
    儲存審查委員的回饋：這兩件作品是否為抄襲關係。

    這些標記是未來微調模型的訓練資料，是護城河的核心資產。
    每筆紀錄寫入 JSONL，方便後續批次讀取。
    """
    feedback_path = os.path.join(base_dir, FEEDBACK_FILE)
    os.makedirs(os.path.dirname(feedback_path), exist_ok=True)

    import time
    record = {
        "query_id":     query_work_id,
        "matched_id":   matched_work_id,
        "is_plagiarism": is_plagiarism,
        "reviewer":     reviewer,
        "note":         note,
        "timestamp":    time.time(),
    }
    with open(feedback_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info(
        f"回饋已記錄：{query_work_id} vs {matched_work_id} "
        f"= {'抄襲' if is_plagiarism else '非抄襲'} (by {reviewer})"
    )


def get_feedback_stats(base_dir: str) -> dict:
    """取得回饋統計（顯示在首頁）"""
    feedback_path = os.path.join(base_dir, FEEDBACK_FILE)
    if not os.path.exists(feedback_path):
        return {"total": 0, "plagiarism": 0, "non_plagiarism": 0}

    total = plagiarism = 0
    with open(feedback_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                total += 1
                if r.get("is_plagiarism"):
                    plagiarism += 1
            except Exception:
                continue

    return {
        "total":          total,
        "plagiarism":     plagiarism,
        "non_plagiarism": total - plagiarism,
    }


# =============================================================================
# pHash 庫管理
# =============================================================================

def load_phash_map(base_dir: str) -> dict[str, str]:
    """
    從 ChromaDB 載入所有 work_id → phash 的對照表，
    用於入庫前的去重檢查。
    """
    collection = get_chroma(base_dir)
    if collection.count() == 0:
        return {}

    results = collection.get(include=["metadatas"])
    phash_map = {}
    for i, meta in enumerate(results["metadatas"]):
        work_id = results["ids"][i]
        phash   = meta.get("phash", "")
        if phash:
            phash_map[work_id] = phash
    return phash_map


# =============================================================================
# PCA 索引重建
# =============================================================================

def rebuild_pca_index(base_dir: str) -> dict:
    """
    從 ChromaDB 取出所有向量，重新訓練 PCA 並更新全部向量。
    建議在：
    1. 首次累積 500+ 件後執行
    2. 每月定期執行一次
    """
    collection = get_chroma(base_dir)
    count      = collection.count()

    if count < 100:
        return {"status": "skip", "message": f"向量數量不足（{count} < 100），略過 PCA 訓練"}

    logger.info(f"開始重建 PCA 索引，共 {count} 筆向量...")

    # 取出所有向量
    results    = collection.get(include=["embeddings", "metadatas", "documents"])
    ids        = results["ids"]
    embeddings = np.array(results["embeddings"], dtype=np.float32)
    metadatas  = results["metadatas"]
    documents  = results["documents"]

    # 訓練 PCA
    chroma_path = os.path.join(base_dir, "data", "chroma_public_art")
    pca         = train_pca(embeddings, chroma_path)

    # 重新壓縮並更新 ChromaDB
    compressed = compress_vectors_batch(embeddings)
    collection.upsert(
        ids=ids,
        embeddings=compressed.tolist(),
        metadatas=metadatas,
        documents=documents,
    )

    reset_chroma_singleton()
    logger.info(f"PCA 索引重建完成，{count} 筆向量已更新至 {PCA_DIM} 維")
    return {"status": "done", "count": count, "dim": PCA_DIM}
