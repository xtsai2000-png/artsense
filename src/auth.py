"""
Artsense 身份驗證模組
===
- JWT Bearer Token 保護 /admin 與 /api/admin/*
- 環境變數設定 SECRET_KEY 與預設帳密
- 相似度閾值可透過 API 動態調整
"""

import os
import time
import logging
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# =============================================================================
# 設定（從環境變數讀取，有預設值方便開發）
# =============================================================================

SECRET_KEY    = os.getenv("ARTSENSE_SECRET",   "change-me-in-production")
ADMIN_USER    = os.getenv("ARTSENSE_ADMIN",    "admin")
ADMIN_PASS    = os.getenv("ARTSENSE_PASSWORD", "artsense2026")
TOKEN_TTL_SEC = int(os.getenv("TOKEN_TTL",     str(60 * 60 * 8)))  # 預設 8 小時

# 相似度閾值（可透過 /api/admin/settings 動態調整）
_similarity_thresh: float = float(os.getenv("SIMILARITY_THRESH", "0.82"))

security = HTTPBearer()

# =============================================================================
# 簡易 HMAC Token（不依賴 jose/pyjwt，減少依賴）
# =============================================================================

import hmac
import hashlib
import base64
import json


def _sign(payload: dict) -> str:
    """產生 HMAC-SHA256 簽名的 token"""
    body    = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode()
    sig     = hmac.new(
        SECRET_KEY.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    return f"{body}.{sig}"


def _verify(token: str) -> Optional[dict]:
    """驗證 token，回傳 payload 或 None"""
    try:
        body, sig = token.rsplit(".", 1)
        expected  = hmac.new(
            SECRET_KEY.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=="))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def create_token(username: str) -> str:
    """建立登入 Token"""
    payload = {
        "sub": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_TTL_SEC,
    }
    return _sign(payload)


# =============================================================================
# FastAPI 依賴：驗證 Admin Token
# =============================================================================

async def require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    FastAPI 依賴注入，保護需要 Admin 權限的路由。
    用法：@app.post("/api/admin/...")
          async def endpoint(admin=Depends(require_admin)):
    """
    payload = _verify(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 無效或已過期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


# =============================================================================
# 相似度閾值管理
# =============================================================================

def get_similarity_thresh() -> float:
    return _similarity_thresh


def set_similarity_thresh(value: float):
    global _similarity_thresh
    if not 0.5 <= value <= 1.0:
        raise ValueError("閾值需介於 0.5 ~ 1.0")
    _similarity_thresh = value
    logger.info(f"相似度閾值更新為 {value}")
