from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from src.sources import GLOBAL_DOMAINS, KR_DOMAINS

BLOCKED_HOSTS = (
    "news.google.com",
    "news.google.co.kr",
    "google.com",
    "google.co.kr",
    "consent.google.com",
)


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_kr_domain(url: str) -> bool:
    host = host_of(url)
    return host.endswith(".kr") or _matches(host, KR_DOMAINS)


def is_global_domain(url: str) -> bool:
    return _matches(host_of(url), GLOBAL_DOMAINS)


def is_trusted_url(url: str, region: str | None = None) -> bool:
    if region == "kr":
        return is_kr_domain(url)
    if region == "global":
        return is_global_domain(url)
    return is_kr_domain(url) or is_global_domain(url)


def is_foreign_only_story(title: str, summary: str) -> bool:
    """해외 단독 이슈(미국 CISA/NSA 등)인데 국내 맥락이 없으면 True."""
    text = f"{title} {summary}"
    kr_markers = (
        "한국",
        "국내",
        "대한민국",
        "KISA",
        "금융감독",
        "과기정통",
        "행정안전",
        "행안부",
        "지니언스",
        "Genians",
        "서울",
        "국회",
        "공공기관",
        "금융권",
    )
    if any(m in text for m in kr_markers):
        return False
    foreign_markers = (
        "CISA",
        "NSA",
        "FBI",
        "미국,",
        "미국 ",
        "미국이",
        "미국의",
        "유럽",
        "프랑스",
        "영국",
        "EU ",
        "European",
        "White House",
    )
    return any(m in text for m in foreign_markers)


def is_kr_domestic(url: str, title: str, summary: str = "") -> bool:
    """대한민국 관련 국내 보안 기사인지 판별."""
    if not is_kr_domain(url):
        return False
    if is_foreign_only_story(title, summary):
        return False
    return True


def classify_region(url: str, title: str, summary: str = "") -> str:
    return "kr" if is_kr_domestic(url, title, summary) else "global"


def is_homepage_url(url: str) -> bool:
    """도메인 루트·인덱스만 있는 링크(언론사 메인)인지 판별."""
    parsed = urlparse(url.strip())
    path = (parsed.path or "").rstrip("/").lower()
    if not path:
        return True
    return path in ("/index.html", "/index.php", "/index.htm", "/main", "/home")


def is_valid_article_url(url: str) -> bool:
    """Slack에 넣을 원문 링크인지 검증한다. Google News 래퍼 URL은 거부한다."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc or "." not in parsed.netloc:
        return False
    host = host_of(url)
    if any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTS):
        return False
    if parsed.path.startswith("/url") and "q" in parse_qs(parsed.query):
        return False
    if is_homepage_url(url):
        return False
    return is_trusted_url(url)
