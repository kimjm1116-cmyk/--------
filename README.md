# 지니언스 데일리 보안 뉴스 봇

매일 오전 7:30(KST)에 전 세계 보안 뉴스를 수집하고, LLM으로 한국어 요약·큐레이션한 뒤 Slack 채널에 올립니다.

대상 독자: 지니언스 임직원 (개발 / 기획 / 영업 / 마케팅)

## 1. 아키텍처

```
RSS / NewsAPI  ──► 수집·중복제거  ──► OpenAI 큐레이션  ──► Slack Block Kit
                                          │
                                          └─ 실패 시 에러 알림
```

1. `collectors.py` : BleepingComputer, The Hacker News, Dark Reading, Krebs, SecurityWeek, CISA, Google News RSS (+ 선택 NewsAPI)
2. `curator.py` : 중요도 평가, Top 10 선정, 한국어 2~3줄 요약, NAC/EDR 사업 인사이트
3. `slack_poster.py` : Incoming Webhook 또는 Bot API
4. GitHub Actions cron : KST 07:30 자동 실행

## 2. 추천 스택

| 역할 | 선택 |
| --- | --- |
| 런타임 | Python 3.12 |
| RSS | `feedparser`, `httpx` |
| LLM | `openai` (기본 `gpt-4o-mini`) |
| Slack | Incoming Webhook (`httpx`) 또는 `slack_sdk` |
| 설정 | `pydantic-settings`, `.env` |
| 스케줄 | GitHub Actions (무료 티어로 충분) |

로컬 테스트는 `.env`, 운영은 GitHub Secrets를 사용하세요.

## 3. 로컬 실행

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# .env에 키 입력 후
python -m src.main
```

## 4. 환경 변수

| 변수 | 필수 | 설명 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 예 | OpenAI API 키 |
| `OPENAI_MODEL` | 아니오 | 기본 `gpt-4o-mini` |
| `SLACK_WEBHOOK_URL` | 권장 | Incoming Webhook URL |
| `SLACK_BOT_TOKEN` | 대체 | Bot Token (`xoxb-`) |
| `SLACK_CHANNEL_ID` | Bot 사용 시 | 채널 ID |
| `SLACK_ALERT_WEBHOOK_URL` | 아니오 | 실패 전용 웹훅 |
| `NEWSAPI_KEY` | 아니오 | newsapi.org 키 |
| `LOOKBACK_HOURS` | 아니오 | 기본 24 |
| `TZ` | 아니오 | `Asia/Seoul` |

Slack 앱 설정 요약:

1. api.slack.com에서 앱 생성
2. Incoming Webhooks 활성화 후 채널 연결, URL 복사
3. (Bot 방식) `chat:write` 스코프, 채널에 앱 초대

## 5. GitHub Actions 배포 (매일 07:30)

1. 이 저장소를 GitHub에 push
2. Settings → Secrets and variables → Actions 에 최소 아래 등록
   - `OPENAI_API_KEY`
   - `SLACK_WEBHOOK_URL`
3. `.github/workflows/daily-news.yml` 이 이미 포함되어 있음
4. Actions 탭에서 **Daily Security News Briefing** → Run workflow 로 즉시 테스트

cron은 UTC 기준입니다.

```
30 22 * * *   # UTC 22:30 = 한국시간 07:30
```

서머타임 없는 KST이므로 연중 동일합니다.

월 실행 횟수는 약 30회, 런타임 1~2분이라 GitHub 무료 티어로 충분합니다. OpenAI 비용은 `gpt-4o-mini` 기준 하루 수백 원 수준이 일반적입니다.

## 6. 메시지 구성

- 🔥 글로벌 주요 보안 뉴스 Top 10
- 🛡️ NAC / EDR 및 보안 시장 동향 (지니언스 인사이트 포함)

실패하면 같은 채널(또는 alert 웹훅)로 에러 요약을 보냅니다.

## 7. 운영 팁

- RSS가 막히면 `User-Agent`를 유지한 채 소스만 `src/collectors.py`의 `RSS_FEEDS`에 추가하면 됩니다.
- Top 개수는 `.env`의 `TOP_N`, `MARKET_N`으로 조절합니다.
- 사내 보안정책상 아웃바운드가 막힌 서버보다 GitHub Actions가 안정적입니다.
