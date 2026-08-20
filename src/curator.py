from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings
from src.links import is_valid_article_url
from src.models import Article, Briefing, CuratedItem

logger = logging.getLogger(__name__)

SECTIONS = (
    "kr_security",
    "global_security",
    "kr_market",
    "global_market",
    "focus_product",
)

SECTION_FILL = {
    "kr_security": {"region": "kr", "topic": None},
    "global_security": {"region": "global", "topic": None},
    "kr_market": {"region": "kr", "topic": None},
    "global_market": {"region": "global", "topic": None},
    "focus_product": {"region": None, "topic": "focus"},
}

SYSTEM_PROMPT = """너는 지니언스(Genians) 사내 뉴스 에디터다.
지니언스는 NAC, EDR, SSL VPN을 주력으로 한다.
독자는 개발, 기획, 영업, 마케팅 임직원이다.

반드시 JSON만 출력한다.
영문은 자연스러운 한국어로 번역한다.
모든 title_ko는 반드시 한국어다. 영문 제목을 그대로 쓰지 마라. 고유명사(기업명, CVE, 제품명)만 원문 표기를 섞을 수 있다.
summary_ko와 insight도 한국어로만 작성한다.
id만 고르고 url은 만들지 마라. 원문 링크는 시스템이 붙인다.

각 후보에 지니언스 비즈니스 중요도 score(1~10 정수)를 매겨 정렬에만 쓴다.
가점: 대규모 사고, 고위험 취약점, 벤더/경쟁사, NAC/EDR/SSL VPN/제로트러스트
감점: 코인 해킹, 가십, 단순 보도자료. 감점 기사는 뒤로 미루되, 섹션이 비면 연관 기사로 채워라.
kr_security와 kr_market에는 region=kr인 후보만 넣어라. 미국·CISA·NSA·FBI 등 해외 단독 뉴스는 global_security로 보내라.

중복 할당 절대 금지. 5개 섹션 전체에서 같은 기사(같은 id, 같은 사건, 같은 원문)는 한 섹션에만 넣어라.
프랑스 국세청 해킹, 구글 양자내성암호처럼 이슈가 겹치면 가장 적합한 섹션 하나에만 두고 나머지는 다른 고유 기사로 채워라.
각 일반 섹션에는 서로 다른 id를 3~4개 후보로 넉넉히 넣어라(최종 2~3개 선별용).
빈 배열 금지.

focus_product는 예외적으로 정확히 3개다.
- product가 edr인 기사 1개
- product가 nac인 기사 1개
- product가 vpn인 기사 1개 (SSL VPN 또는 제로트러스트/ZTNA)
해당 분야가 없으면 엔드포인트(edr 대체) / 네트워크 접근·방화벽(nac 대체) / 원격접속·ZTNA(vpn 대체)로 채워라.
focus_product 항목에는 product 필드를 edr|nac|vpn 중 하나로 넣어라.
제목/요약/인사이트에 EDR, NAC, VPN 같은 분류 라벨을 붙이지 마라. 본문만 자연스럽게 써라.

스키마 예시 문구(한국어 제목, 한국어 요약 등)를 실제 기사 내용으로 절대 복사하지 마라.
title_ko/summary_ko/insight에는 후보 기사의 실제 내용을 번역·요약한 문장만 넣어라.
headline은 짧은 한 줄.
"""


def _articles_payload(articles: list[Article]) -> list[dict]:
    payload = []
    for idx, a in enumerate(articles):
        payload.append(
            {
                "id": idx,
                "title": a.title,
                "url": a.url,
                "source": a.source,
                "region": a.region,
                "topic": a.topic,
                "snippet": a.summary_raw[:320],
            }
        )
    return payload


def _user_prompt(articles: list[Article], date_label: str) -> str:
    return f"""날짜: {date_label}
회사: {settings.company_name}

후보를 채점한 뒤 5개 섹션에 넣어라.
점수는 정렬용이다. 섹션을 비우지 마라.
일반 섹션은 고유 기사 3~4개를 넣고, 최종적으로 최소 2개가 남도록 여유 있게 골라라.
모든 섹션의 title_ko는 한국어로 번역된 제목이어야 한다. 영어 제목 금지.

1) kr_security 국내 주요 보안 뉴스 — region=kr 후보만. 대한민국 침해사고·취약점·KISA·국내 기업·국내 정부 정책. 미국 CISA/NSA/FBI·미국 단독 이슈 절대 금지.
2) global_security 해외 주요 보안 뉴스 — region=global 또는 해외 단독 이슈
3) kr_market 국내 보안 시장 동향 — region=kr 후보만. 국내 IT/보안 시장·국내 벤더
4) global_market 해외 보안 시장 동향 — region=global
5) focus_product 정확히 3개, 구성은 1:1:1
   - EDR 1, NAC 1, SSL VPN/제로트러스트 1
   - 각 항목에 "product": "edr" | "nac" | "vpn"
   - 분류 소제목은 출력하지 말고 기사 본문만 작성

JSON 필드만 맞추고, 값은 후보 기사에서 만들어라. 설명용 문구를 값에 넣지 마라.
{{
  "headline": "",
  "kr_security": [{{"id": 0, "score": 8, "title_ko": "", "summary_ko": "", "insight": ""}}],
  "global_security": [],
  "kr_market": [],
  "global_market": [],
  "focus_product": [{{"id": 0, "product": "edr", "score": 8, "title_ko": "", "summary_ko": "", "insight": ""}}]
}}

후보:
{json.dumps(_articles_payload(articles), ensure_ascii=False)}
"""


@retry(wait=wait_exponential(min=2, max=20), stop=stop_after_attempt(3), reraise=True)
def _call_llm(client: OpenAI, articles: list[Article], date_label: str) -> dict:
    response = client.chat.completions.create(
        model=settings.openai_model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(articles, date_label)},
        ],
    )
    content = response.choices[0].message.content or "{}"
    return json.loads(content)


def _looks_english(text: str) -> bool:
    letters = re.findall(r"[A-Za-z가-힣]", text or "")
    if len(letters) < 8:
        return False
    latin = sum(ch.isascii() and ch.isalpha() for ch in letters)
    return latin / len(letters) >= 0.55


def _iter_items(briefing: Briefing) -> list[CuratedItem]:
    items: list[CuratedItem] = []
    for group in (
        briefing.kr_security,
        briefing.global_security,
        briefing.kr_market,
        briefing.global_market,
        briefing.focus_product,
    ):
        items.extend(group)
    return items


def _translate_english_fields(client: OpenAI, briefing: Briefing) -> None:
    """선정/중복/섹션은 그대로 두고 영문 제목·요약만 한국어로 고친다."""
    targets = []
    for i, item in enumerate(_iter_items(briefing)):
        need_title = _looks_english(item.title_ko)
        need_sum = _looks_english(item.summary_ko)
        if need_title or need_sum:
            targets.append(
                {
                    "i": i,
                    "title": item.title_ko if need_title else "",
                    "summary": item.summary_ko if need_sum else "",
                }
            )
    if not targets:
        return
    response = client.chat.completions.create(
        model=settings.openai_model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": "영문 뉴스 제목/요약을 자연스러운 한국어로만 번역한다. JSON만 출력. URL은 만들지 마라.",
            },
            {
                "role": "user",
                "content": (
                    "각 항목 i에 대해 실제 번역문만 넣어라. 설명 문구를 쓰지 마라. "
                    '{"items":[{"i":0,"title_ko":"...","summary_ko":"..."}]} 형식.\n'
                    "title/summary가 빈 문자열이면 그 필드는 생략.\n"
                    + json.dumps({"items": targets}, ensure_ascii=False)
                ),
            },
        ],
    )
    data = json.loads(response.choices[0].message.content or "{}")
    by_i = {int(row["i"]): row for row in data.get("items", []) if "i" in row}
    for i, item in enumerate(_iter_items(briefing)):
        row = by_i.get(i)
        if not row:
            continue
        if row.get("title_ko") and not _is_placeholder(row["title_ko"]):
            item.title_ko = _clean(row["title_ko"], 70)
        if row.get("summary_ko") and not _is_placeholder(row["summary_ko"]):
            item.summary_ko = _two_lines(row["summary_ko"])


PLACEHOLDER_TEXT = {
    "한국어로 번역된 제목",
    "한국어 제목",
    "한국어 요약",
    "한국어 인사이트",
    "한 줄",
    "실제 한국어 제목",
    "한국어 번역",
}


def _is_placeholder(text: str) -> bool:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t in PLACEHOLDER_TEXT


def _clean(text: str, max_chars: int) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:max_chars].rstrip(" ,;.")


def _real_text(candidate: str | None, fallback: str, max_chars: int) -> str:
    if candidate and not _is_placeholder(candidate):
        return _clean(candidate, max_chars)
    if fallback and not _is_placeholder(fallback):
        return _clean(fallback, max_chars)
    return _clean(fallback or "보안 뉴스", max_chars)


def _two_lines(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    parts = [p.strip() for p in re.split(r"(?<=[.다요])\s+", cleaned) if p.strip()]
    if len(parts) >= 2:
        return " ".join(parts[:2])[:160]
    return cleaned[:160]


def _url_key(url: str) -> str:
    return re.sub(r"/+$", "", (url or "").strip().lower())


def _title_key(title: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", (title or "").lower())[:28]


def _story_keys(url: str, title: str) -> set[str]:
    keys = {_url_key(url)}
    tk = _title_key(title)
    if len(tk) >= 8:
        keys.add("t:" + tk)
    return keys


def _is_duplicate(url: str, title: str, seen: set[str]) -> bool:
    return bool(_story_keys(url, title) & seen)


def _mark_seen(url: str, title: str, seen: set[str]) -> None:
    seen.update(_story_keys(url, title))


def _parse_score(raw: object) -> int:
    try:
        score = int(raw)
    except (TypeError, ValueError):
        return 5
    return max(1, min(10, score))


FOCUS_ORDER = ("edr", "nac", "vpn")

EDR_KEYS = ("edr", "xdr", "endpoint detection", "endpoint security", "엔드포인트")
NAC_KEYS = ("nac", "network access control", "접근제어", "접근 제어", "network access")
VPN_KEYS = ("ssl vpn", "sslvpn", "ztna", "zero trust", "제로트러스트", "원격 접속", "remote access")


def _blob(article: Article) -> str:
    return f"{article.title} {article.summary_raw}".lower()


def _infer_product(article: Article, hint: str = "") -> str:
    hint = (hint or "").lower().strip()
    if hint in FOCUS_ORDER:
        return hint
    text = _blob(article)
    if any(k in text for k in EDR_KEYS):
        return "edr"
    if any(k in text for k in NAC_KEYS):
        return "nac"
    if any(k in text for k in VPN_KEYS):
        return "vpn"
    if "endpoint" in text or "malware" in text:
        return "edr"
    if "network" in text or "firewall" in text or "네트워크" in text:
        return "nac"
    return "vpn"


def _to_item(article: Article, item: dict | None, product: str) -> CuratedItem:
    return CuratedItem(
        title_ko=_real_text((item or {}).get("title_ko"), article.title, 70),
        url=article.url,
        source=article.source,
        summary_ko=_two_lines(
            (item or {}).get("summary_ko")
            if (item or {}).get("summary_ko") and not _is_placeholder((item or {}).get("summary_ko") or "")
            else (article.summary_raw or article.title)
        ),
        insight=_real_text((item or {}).get("insight"), "제품·영업 메시지와 연결해 볼 만하다.", 80),
        score=_parse_score((item or {}).get("score")),
        product_tag=product,
    )


def _map_focus(
    raw_items: list[dict],
    articles: list[Article],
    used_ids: set[int],
    seen: set[str],
) -> list[CuratedItem]:
    picked: dict[str, CuratedItem] = {}
    for item in raw_items or []:
        try:
            idx = int(item["id"])
            article = articles[idx]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if idx in used_ids or not is_valid_article_url(article.url):
            continue
        if _is_duplicate(article.url, article.title, seen):
            continue
        product = _infer_product(article, str(item.get("product") or ""))
        if product in picked:
            continue
        used_ids.add(idx)
        _mark_seen(article.url, article.title, seen)
        picked[product] = _to_item(article, item, product)
        if len(picked) == 3:
            break

    for product in FOCUS_ORDER:
        if product in picked:
            continue
        for idx, article in enumerate(articles):
            if idx in used_ids or not is_valid_article_url(article.url):
                continue
            if _is_duplicate(article.url, article.title, seen):
                continue
            inferred = _infer_product(article)
            if inferred != product:
                continue
            used_ids.add(idx)
            _mark_seen(article.url, article.title, seen)
            picked[product] = _to_item(article, None, product)
            break

    for product in FOCUS_ORDER:
        if product in picked:
            continue
        for idx, article in enumerate(articles):
            if idx in used_ids or not is_valid_article_url(article.url):
                continue
            if _is_duplicate(article.url, article.title, seen):
                continue
            used_ids.add(idx)
            _mark_seen(article.url, article.title, seen)
            picked[product] = _to_item(article, None, product)
            break

    return [picked[p] for p in FOCUS_ORDER if p in picked]


def _map_section(
    raw_items: list[dict],
    articles: list[Article],
    used_ids: set[int],
    seen: set[str],
    limit: int,
    require_region: str | None = None,
) -> list[CuratedItem]:
    ranked: list[CuratedItem] = []
    for item in raw_items or []:
        try:
            idx = int(item["id"])
            article = articles[idx]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if require_region and article.region != require_region:
            continue
        if idx in used_ids or not is_valid_article_url(article.url):
            continue
        if _is_placeholder(item.get("title_ko") or "") and _is_placeholder(item.get("summary_ko") or ""):
            continue
        if _is_duplicate(article.url, item.get("title_ko") or article.title, seen):
            continue
        used_ids.add(idx)
        _mark_seen(article.url, article.title, seen)
        ranked.append(
            CuratedItem(
                title_ko=_real_text(item.get("title_ko"), article.title, 70),
                url=article.url,
                source=article.source,
                summary_ko=_two_lines(
                    item.get("summary_ko")
                    if item.get("summary_ko") and not _is_placeholder(item.get("summary_ko") or "")
                    else (article.summary_raw or article.title)
                ),
                insight=_real_text(item.get("insight"), "제품·영업 메시지와 연결해 볼 만하다.", 80),
                score=_parse_score(item.get("score")),
            )
        )
    ranked.sort(key=lambda x: x.score, reverse=True)
    return ranked[:limit]


def _fill_section(
    mapped: list[CuratedItem],
    articles: list[Article],
    used_ids: set[int],
    seen: set[str],
    minimum: int,
    limit: int,
    region: str | None,
    topic: str | None,
) -> list[CuratedItem]:
    if len(mapped) >= minimum:
        return mapped[:limit]
    for idx, article in enumerate(articles):
        if len(mapped) >= minimum:
            break
        if idx in used_ids or not is_valid_article_url(article.url):
            continue
        if _is_duplicate(article.url, article.title, seen):
            continue
        if topic and article.topic != topic:
            continue
        if region and article.region != region:
            continue
        used_ids.add(idx)
        _mark_seen(article.url, article.title, seen)
        mapped.append(
            CuratedItem(
                title_ko=_clean(article.title, 70),
                url=article.url,
                source=article.source,
                summary_ko=_two_lines(article.summary_raw or article.title),
                insight="관련 맥락으로 원문을 확인하면 좋다.",
                score=5,
            )
        )
    if len(mapped) < minimum and region:
        logger.warning("섹션(region=%s) 후보 부족: %s/%s", region, len(mapped), minimum)
    mapped.sort(key=lambda x: x.score, reverse=True)
    return mapped[:limit]


def curate(articles: list[Article]) -> Briefing:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY가 비어 있습니다. .env를 확인하세요.")
    if not articles:
        raise ValueError("큐레이션할 기사가 없습니다.")

    now = datetime.now(ZoneInfo(settings.tz))
    date_label = now.strftime("%Y-%m-%d (%a)")
    client = OpenAI(api_key=settings.openai_api_key)
    raw = _call_llm(client, articles, date_label)
    used: set[int] = set()
    seen: set[str] = set()
    mapped: dict[str, list[CuratedItem]] = {}

    mapped["focus_product"] = _map_focus(raw.get("focus_product") or [], articles, used, seen)

    for name in ("kr_security", "global_security", "kr_market", "global_market"):
        hint = SECTION_FILL[name]
        items = _map_section(
            raw.get(name) or [],
            articles,
            used,
            seen,
            4,
            require_region=hint["region"],
        )
        items = _fill_section(
            items,
            articles,
            used,
            seen,
            settings.section_min,
            settings.section_max,
            hint["region"],
            hint["topic"],
        )
        mapped[name] = items

    headline = raw.get("headline") or "오늘의 보안 브리핑"
    if _is_placeholder(headline):
        headline = "오늘의 보안 브리핑"
    briefing = Briefing(
        headline=_clean(headline, 40),
        date_label=date_label,
        editor_note="",
        kr_security=mapped["kr_security"],
        global_security=mapped["global_security"],
        kr_market=mapped["kr_market"],
        global_market=mapped["global_market"],
        focus_product=mapped["focus_product"],
    )
    try:
        _translate_english_fields(client, briefing)
    except Exception:
        logger.exception("영문 제목 번역 보정 실패. 기존 제목을 유지합니다.")
    return briefing
