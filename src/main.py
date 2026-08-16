from __future__ import annotations

import argparse
import logging
import sys
import traceback

from src.collectors import collect_articles
from src.config import settings
from src.curator import curate
from src.models import Article, Briefing
from src.slack_poster import post_briefing, post_error, send_webhook_test

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("newsbot")


def collect_security_news() -> list[Article]:
    """1) 국내/해외 RSS로 최근 24시간 기사를 수집하고 원문 URL을 검증한다."""
    logger.info("1/3 뉴스 수집 시작 (lookback=%sh)", settings.lookback_hours)
    articles = collect_articles()
    if not articles:
        raise RuntimeError("최근 24시간 수집 기사가 0건입니다. RSS를 확인하세요.")
    logger.info("수집 완료: %s건", len(articles))
    return articles


def summarize_with_openai(articles: list[Article]) -> Briefing:
    """2) 중요도 점수로 섹션당 1~2건만 남기고 한 줄 요약한다."""
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


def run() -> int:
    try:
        articles = collect_security_news()
        briefing = summarize_with_openai(articles)
        send_to_slack(briefing)
        return 0
    except Exception as exc:
        err = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(err)
        post_error(str(exc))
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="지니언스 보안 뉴스 봇")
    parser.add_argument(
        "--test-slack",
        action="store_true",
        help="뉴스 수집 없이 웹훅 테스트 메시지만 전송",
    )
    args = parser.parse_args()
    if args.test_slack:
        send_webhook_test()
        print("테스트 메시지를 Slack 채널로 보냈습니다.")
        sys.exit(0)
    sys.exit(run())
