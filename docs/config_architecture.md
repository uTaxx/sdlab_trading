# 설정 아키텍처: 대시보드에서 값을 바꿀 수 있게 하는 구조

## 왜 `.env` 하나로 안 끝내는가

`.env`는 프로세스가 시작될 때 한 번만 읽힌다. KIS 앱키나 리스크 정책을
`.env`에만 두면, 나중에 만들 대시보드에서 값을 바꿔도 서버 파일을 고치고
프로세스를 재시작해야 반영된다. 그래서 이 프로젝트는 설정을 두 계층으로
나눈다.

## 두 계층

1. **부트스트랩 설정** (`.env`, `src/muwon/config.py`의 `BootstrapSettings`).
   DB에 연결하기 위해 최소한으로 필요한 값만 담습니다. `DATABASE_URL`과
   `MUWON_MASTER_KEY`(비밀값 암호화 키). 이 두 값은 DB 자체에 저장할 수
   없으므로 (닭이 먼저냐 달걀이 먼저냐 문제) `.env`에 남는다.

2. **런타임 설정** (DB `app_settings` 테이블, `src/muwon/settings/`).
   KIS 인증정보, 텔레그램 봇 토큰, 리스크 정책이 여기 있습니다.
   `SettingsService`(`src/muwon/settings/service.py`)가 유일한 접근 지점입니다.

```
.env (DATABASE_URL, MUWON_MASTER_KEY)
        │
        ▼
SettingsStore (DB app_settings 테이블 + TTL 캐시 + 비밀값 암호화)
        │
        ▼
SettingsService (타입 안전한 get/set: 리스크정책 / KIS인증정보 / 텔레그램)
        │
        ├── scripts/configure.py (CLI)
        ├── src/muwon/dashboard/app.py (웹 대시보드, Streamlit)
        ├── RiskManager (매 검사마다 최신 정책을 읽음)
        ├── TelegramNotifier (매 전송마다 최신 토큰을 읽음)
        └── KISClient.from_settings() (실행 시점의 최신 인증정보로 생성)
```

## 값이 바뀌면 언제 반영되나

- 같은 프로세스 안에서 `SettingsService.set_*()`를 호출하면 즉시 반영된다.
- 대시보드가 별도 프로세스로 떠서 DB에 값을 쓰는 경우, 봇 프로세스는 최대
  캐시 TTL(`SettingsStore` 기본 5초) 이내에 새 값을 읽는다. 즉시 반영이
  필요해지면 캐시 무효화용 pub/sub(Redis 등)을 Phase 2+에서 추가할 수 있다.

## 비밀값 암호화

`kis.app_key`, `kis.app_secret`, `kis.account_no`, `telegram.bot_token`은
`MUWON_MASTER_KEY`(Fernet 대칭키)로 암호화되어 DB에 저장된다. 이 키가 없으면
비밀값을 저장하거나 읽을 수 없다. 실수로 평문 저장되는 걸 막기 위한
안전장치다. 리스크 정책 같은 비민감 값은 암호화하지 않는다.

## 값 넣는 법

CLI로:

```bash
python scripts/configure.py kis --env paper --app-key XXX --app-secret YYY \
    --account-no 12345678 --account-product-cd 01
python scripts/configure.py telegram --bot-token XXX --chat-id YYY
python scripts/configure.py risk --max-position-weight 0.15 \
    --stop-loss-pct -0.05 --daily-loss-limit-pct -0.03 \
    --max-concurrent-positions 8
python scripts/configure.py show
```

또는 웹 대시보드로:

```bash
pip install -e ".[dashboard]"
streamlit run src/muwon/dashboard/app.py
```
