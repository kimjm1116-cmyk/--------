from __future__ import annotations

import logging
from functools import lru_cache

from googlenewsdecoder import gnewsdecoder

logger = logging.getLogger(__name__)


def is_google_news_url(url: str) -> bool:
    return "news.google.com" in (url or "")


@lru_cache(maxsize=512)
def resolve_google_news_url(url: str) -> str | None:
    """Google News RSS 래퍼 URL을 언론사 원문 URL로 변환한다."""
    if not is_google_news_url(url):
        return url
    try:
        result = gnewsdecoder(url, interval=0)
        if result.get("status") and result.get("decoded_url"):
            return str(result["decoded_url"]).strip()
        logger.warning("Google News 디코딩 실패: %s", result.get("message", "unknown"))
    except Exception:
        logger.exception("Google News URL 디코딩 예외: %s", url[:100])
    return None
