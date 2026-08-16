from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트의 .env를 명시적으로 로드 (python-dotenv)
ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT_DIR / ".env"), extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    slack_webhook_url: str = ""
    slack_bot_token: str = ""
    slack_channel_id: str = ""
    slack_alert_webhook_url: str = ""

    newsapi_key: str = ""

    lookback_hours: int = 72
    max_collected_articles: int = 120
    section_min: int = 2
    section_max: int = 3
    kr_security_n: int = 3
    global_security_n: int = 3
    kr_market_n: int = 3
    global_market_n: int = 3
    focus_n: int = 3
    min_score: int = 1
    top_n: int = 3
    market_n: int = 3
    tz: str = "Asia/Seoul"
    company_name: str = "지니언스(Genians)"

    http_timeout: float = 20.0
    user_agent: str = (
        "GeniansSecurityNewsBot/1.0 (+internal daily briefing)"
    )


settings = Settings()
