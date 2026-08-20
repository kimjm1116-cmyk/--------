from __future__ import annotations

import argparse
import logging
import sys
import traceback

from src.collectors import collect_articles
from src.config import settings
from src.curator import curate
from src.models import Article, Briefing
from src.sent_store import load_sent_urls, save_sent_urls
from src.slack_poster import post_briefing, post_error, send_webhook_test

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("newsbot")


def briefing_urls(briefing: Briefing) -> list[str]:
    items = (
        briefing.kr_security
        + briefing.global_security
        + briefing.kr_market
        + briefing.global_market
        + briefing.focus_product
    )
    return [item.url for item in items if item.url]


def collect_security_news() -> list[Article]:
    """1) 최근 24시간 기사만 수집하고, 이미 보낸 URL은 제외한다."""
    sent = load_sent_urls()
    logger.info("1/3 뉴스 수집 시작 (lookback=%sh, 기전송=%s건)", settings.lookback_hours, len(sent))
    articles = collect_articles(exclude_urls=sent)
    if not articles:
        raise RuntimeError("최근 24시간 신규 수집 기사가 0건입니다. RSS 또는 전송 이력을 확인하세요.")
    logger.info("수집 완료: %s건", len(articles))
    return articles


def summarize_with_openai(articles: list[Article]) -> Briefing:
    """2) 기존 큐레이션 로직으로 요약한다."""
    if not settings.openai_api_key or settings.openai_api_key.startswith("sk-your-"):
        raise RuntimeError(
            "OPENAI_API_KEY가 .env에 없거나 예시 값입니다. 실제 키를 넣어 주세요."
        )
    logger.info("2/3 OpenAI 큐레이션 시작 (model=%s)", settings.openai_model)
    briefing = curate(articles)
    logger.info("2/3 큐레이션 완료 (섹션 간 URL/제목 중복 제거 적용)")
    logger.info(
        "큐레이션 완료: 국내보안=%s 해외보안=%s 국내시장=%s 해외시장=%s 포커스=%s",
        len(briefing.kr_security),
        len(briefing.global_security),
        len(briefing.kr_market),
        len(briefing.global_market),
        len(briefing.focus_product),
    )
    return briefing


def send_to_slack(briefing: Briefing) -> None:
    """3) 짧은 Block Kit 브리핑을 한 메시지로 전송한다."""
    if not settings.slack_webhook_url:
        raise RuntimeError("SLACK_WEBHOOK_URL이 .env에 없습니다.")
    logger.info("3/3 Slack 전송 시작")
    post_briefing(briefing)
    logger.info("Slack 전송 완료")


def print_briefing_preview(briefing: Briefing) -> None:
    """Slack 전송 없이 터미널에 브리핑 미리보기를 출력한다."""
    sections = (
        ("🇰🇷 국내 주요 보안 뉴스", briefing.kr_security),
        ("🌐 해외 주요 보안 뉴스", briefing.global_security),
        ("📈 국내 보안 시장 동향", briefing.kr_market),
        ("🌍 해외 보안 시장 동향", briefing.global_market),
        ("🎯 Focus (EDR / NAC / VPN)", briefing.focus_product),
    )
    print()
    print("=" * 60)
    print(f"[DRY-RUN] {briefing.headline}  {briefing.date_label}")
    print("=" * 60)
    for title, items in sections:
        print()
        print(title)
        print("-" * len(title))
        if not items:
            print("  (기사 없음)")
            continue
        for i, item in enumerate(items, 1):
            print(f"  {i}. {item.title_ko}")
            print(f"     {item.url}")
            print(f"     요약: {item.summary_ko}")
            print(f"     인사이트: {item.insight}")
    print()
    print("=" * 60)
    print("[DRY-RUN] Slack 미전송 · sent_articles.json 미저장")
    print("=" * 60)


def run(*, dry_run: bool = False) -> int:
    try:
        articles = collect_security_news()
        briefing = summarize_with_openai(articles)
        if dry_run:
            print_briefing_preview(briefing)
            logger.info("DRY-RUN 완료 (Slack 전송·전송 이력 저장 생략)")
            return 0
        send_to_slack(briefing)
        save_sent_urls(briefing_urls(briefing))
        logger.info("sent_articles.json 저장 완료")
        return 0
    except Exception as exc:
        err = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(err)
        if not dry_run:
            post_error(str(exc))
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="지니언스 보안 뉴스 봇")
    parser.add_argument(
        "--test-slack",
        action="store_true",
        help="뉴스 수집 없이 웹훅 테스트 메시지만 전송",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="수집·큐레이션만 실행하고 터미널에 미리보기 (Slack 전송·전송 이력 저장 안 함)",
    )
    args = parser.parse_args()
    if args.test_slack:
        send_webhook_test()
        print("테스트 메시지를 Slack 채널로 보냈습니다.")
        sys.exit(0)
    sys.exit(run(dry_run=args.dry_run))
