from __future__ import annotations

import logging
from typing import Any

import httpx
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.config import settings
from src.curator import _is_duplicate, _mark_seen
from src.links import is_valid_article_url
from src.models import Briefing, CuratedItem

logger = logging.getLogger(__name__)

MAX_BLOCKS = 50


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _story_block(item: CuratedItem, index: int) -> dict[str, Any] | None:
    if not is_valid_article_url(item.url):
        logger.warning("Slack 블록에서 URL 검증 실패로 제외: %s", item.url)
        return None
    title = _clip(item.title_ko, 70)
    text = (
        f"*<{item.url}|{title}>*\n"
        f"• 요약: {_clip(item.summary_ko, 160)}\n"
        f"• 💡 인사이트: {_clip(item.insight, 80)}"
    )
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text},
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": "원문 보기"},
            "url": item.url,
        },
    }


def _unique_items(items: list[CuratedItem], seen_urls: set[str]) -> list[CuratedItem]:
    unique: list[CuratedItem] = []
    for item in items:
        if _is_duplicate(item.url, item.title_ko, seen_urls):
            logger.info("중복 기사 제외: %s", item.title_ko)
            continue
        _mark_seen(item.url, item.title_ko, seen_urls)
        unique.append(item)
    return unique


def _section_blocks(
    title: str,
    items: list[CuratedItem],
    first: bool = False,
    divider_after_header: bool = False,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if not first:
        blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{title}*"},
        }
    )
    if divider_after_header:
        blocks.append({"type": "divider"})
    if not items:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_관련 기사를 충분히 모으지 못했습니다._"},
            }
        )
        return blocks
    for i, item in enumerate(items, start=1):
        block = _story_block(item, i)
        if block:
            blocks.append(block)
    return blocks


def _intro_blocks(briefing: Briefing) -> list[dict[str, Any]]:
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "지니언스 데일리 보안 브리핑",
                "emoji": True,
            },
        },
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": briefing.date_label[:150],
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{_clip(briefing.headline, 40)}*"},
        },
    ]


def build_briefing_blocks(briefing: Briefing) -> list[dict[str, Any]]:
    seen_urls: set[str] = set()
    blocks = _intro_blocks(briefing)
    blocks.extend(
        _section_blocks(
            "🇰🇷 국내 주요 보안 뉴스",
            _unique_items(briefing.kr_security, seen_urls),
            first=True,
        )
    )
    blocks.extend(
        _section_blocks(
            "🌐 해외 주요 보안 뉴스",
            _unique_items(briefing.global_security, seen_urls),
        )
    )
    blocks.extend(
        _section_blocks(
            "📈 국내 보안 시장 동향",
            _unique_items(briefing.kr_market, seen_urls),
        )
    )
    blocks.extend(
        _section_blocks(
            "🌍 해외 보안 시장 동향",
            _unique_items(briefing.global_market, seen_urls),
        )
    )
    blocks.extend(
        _section_blocks(
            "🎯 [Focus] NAC / EDR / SSL VPN 동향",
            _unique_items(briefing.focus_product, seen_urls),
            divider_after_header=True,
        )
    )
    return blocks


def build_domestic_blocks(briefing: Briefing) -> list[dict[str, Any]]:
    return build_briefing_blocks(briefing)


def _chunk_blocks(blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for block in blocks:
        if len(current) >= MAX_BLOCKS:
            chunks.append(current)
            current = []
        current.append(block)
    if current:
        chunks.append(current)
    return chunks


def _post_webhook(url: str, payload: dict[str, Any]) -> None:
    with httpx.Client(timeout=20) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()


def send_slack_message(text: str, blocks: list[dict[str, Any]]) -> None:
    """순수 Block Kit만 전송한다. attachments는 사용하지 않는다."""
    webhook_url = settings.slack_webhook_url
    if not webhook_url:
        raise RuntimeError(
            "SLACK_WEBHOOK_URL이 비어 있습니다. 프로젝트 루트 .env를 확인하세요."
        )
    if not blocks:
        raise ValueError("전송할 blocks가 없습니다.")

    for chunk in _chunk_blocks(blocks):
        payload = {"text": text, "blocks": chunk}
        _post_webhook(webhook_url, payload)
    logger.info("Slack Block Kit 전송 완료 (%s blocks)", len(blocks))


def post_briefing(briefing: Briefing) -> None:
    fallback = f"지니언스 데일리 보안 브리핑 | {briefing.date_label}\n{briefing.headline}"
    blocks = build_briefing_blocks(briefing)

    if settings.slack_webhook_url:
        send_slack_message(fallback, blocks)
        return

    if settings.slack_bot_token and settings.slack_channel_id:
        client = WebClient(token=settings.slack_bot_token)
        for chunk in _chunk_blocks(blocks):
            client.chat_postMessage(channel=settings.slack_channel_id, text=fallback, blocks=chunk)
        return

    raise RuntimeError("Slack 전송 설정이 없습니다. SLACK_WEBHOOK_URL 또는 BOT TOKEN+CHANNEL을 확인하세요.")


def post_error(message: str) -> None:
    text = f":rotating_light: 보안 뉴스 봇 실행 실패\n```{message[:2500]}```"
    payload = {
        "text": text,
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    }
    url = settings.slack_alert_webhook_url or settings.slack_webhook_url
    try:
        if url:
            _post_webhook(url, payload)
            return
        if settings.slack_bot_token and settings.slack_channel_id:
            WebClient(token=settings.slack_bot_token).chat_postMessage(
                channel=settings.slack_channel_id,
                text=text,
                blocks=payload["blocks"],
            )
    except (httpx.HTTPError, SlackApiError):
        logger.exception("에러 알림 전송도 실패했습니다.")


def send_webhook_test() -> None:
    test_url = "https://www.bleepingcomputer.com/"
    test_text = "안녕하세요! 지니언스 보안 뉴스 봇 테스트입니다 🚀"
    send_slack_message(
        text=test_text,
        blocks=[
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "지니언스 보안 뉴스 봇 테스트", "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"1️⃣ *<{test_url}|원문 링크 테스트>*\n"
                        f"{test_text}\n"
                        f"_출처: BleepingComputer_\n"
                        f"*💡 지니언스 인사이트:* Block Kit만 사용하면 메시지가 접히지 않습니다."
                    ),
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "원문 보기"},
                    "url": test_url,
                },
            },
        ],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    send_webhook_test()
    print("테스트 메시지를 Slack 채널로 보냈습니다.")
