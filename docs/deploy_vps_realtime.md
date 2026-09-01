# VPS에서 장중 실시간 매매 실행하기

`scripts/run_realtime_trading.py`는 GitHub Actions처럼 하루 한 번 돌고
끝나는 배치가 아니라, **장이 열려 있는 동안(09:00~15:30 KST) 계속 떠서
KIS 웹소켓으로 체결가를 받는** 프로세스다. GitHub Actions는 이런 상시
연결 유지에 안 맞으므로, 24시간 켜둘 수 있는 VPS가 필요하다.

**GitHub Actions 배치 자동매매(`docs/deploy_github_actions.md`)와는 별개
운영 모드다. 같은 KIS 계좌에 두 개를 동시에 실행하지 말 것.** 어느 쪽을
쓸지 하나만 고르면 된다.

## 0. 이 문서를 믿기 전에

KIS 웹소켓 연동(`src/muwon/data/kis_websocket.py`)은 이 프로젝트를 개발한
환경에서 KIS 포트 자체가 막혀 있어 **실제 접속 검증을 못 했다.**
URL(`ws://ops.koreainvestment.com:21000`/`:31000`)과 메시지 필드 순서는
KIS Developers 공식 문서를 기준으로 작성한 최선의 추정이다. VPS에 처음
배포하면 로그부터 확인해서 실제로 맞는지 봐야 한다.

## 1. VPS 준비

아무 리눅스 VPS나 된다 (AWS Lightsail, 오라클 클라우드 무료 티어, 가비아/
카페24 등). 사양은 크게 필요 없다. 1 vCPU, 1GB RAM이면 충분하다.

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv git
git clone https://github.com/uTaxx/sdlab_trading.git
cd sdlab_trading
git checkout main   # 아직 병합 전이면
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,realtime]"
```

## 2. 설정

```bash
cp .env.example .env
# .env에 DATABASE_URL(기본 sqlite:///./muwon.db 그대로 둬도 됨), MUWON_MASTER_KEY 채우기

python scripts/configure.py kis --env paper \
    --app-key ... --app-secret ... --account-no ... --account-product-cd 01
python scripts/configure.py telegram --bot-token ... --chat-id ...
```

VPS는 로컬 디스크가 계속 살아있으므로(GitHub Actions와 달리), 구글드라이브
동기화가 필요 없다. `muwon.db`가 그 자리에 계속 남는다.

## 3. 첫 실행(수동으로 로그 보면서)

```bash
python scripts/run_realtime_trading.py
```

장중에 실행하고 로그를 지켜본다:
- 연결/구독 단계에서 에러가 나면 URL·포트가 실제로 다른지 의심할 것
  (KIS Developers 포털에서 최신 웹소켓 접속 가이드 대조)
- 틱은 들어오는데 매매가 전혀 안 되면 `src/muwon/data/kis_websocket.py`의
  `_parse_price_message` 필드 인덱스(`_FIELD_SYMBOL`/`_FIELD_PRICE`/`_FIELD_VOLUME`)가
  실제 메시지 구조와 맞는지 확인: 필요하면 `print(raw)`로 원본 메시지를
  한 번 찍어보고 인덱스를 맞출 것

## 4. 상시 서비스로 등록 (systemd)

수동 실행이 잘 되면, 장중 내내 떠 있고 죽으면 자동 재시작되도록 등록한다.

`/etc/systemd/system/muwon-realtime.service`:

```ini
[Unit]
Description=muwon406 realtime trading
After=network.target

[Service]
Type=simple
User=<본인 리눅스 사용자명>
WorkingDirectory=/home/<사용자명>/sdlab_trading
ExecStart=/home/<사용자명>/sdlab_trading/.venv/bin/python scripts/run_realtime_trading.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable muwon-realtime
sudo systemctl start muwon-realtime
sudo journalctl -u muwon-realtime -f   # 로그 실시간 확인
```

## 5. 장 시간에만 실행하기

지금은 프로세스를 계속 띄워둬도 장외 시간엔 KIS가 체결 데이터를 안 보내니
자연히 아무 일도 안 한다. 굳이 시간 맞춰 껐다 켰다 안 해도 동작상
문제는 없다. 다만 자원을 아끼거나 웹소켓 연결을 깔끔하게 유지하고
싶으면, cron으로 장 시작 전 `systemctl start`, 장 마감 후
`systemctl stop`을 걸 수 있다:

```bash
# crontab -e (KST 기준, 서버 시간대에 맞게 조정)
25 9 * * 1-5 systemctl start muwon-realtime
35 15 * * 1-5 systemctl stop muwon-realtime
```

## 6. 리스크 정책/킬스위치 확인

대시보드(`streamlit run src/muwon/dashboard/app.py`)를 VPS에서 같이
띄우면, 같은 로컬 `muwon.db`를 보므로 실시간으로 보유 종목·자동매매
on/off를 그 자리에서 조정할 수 있다 (GitHub Actions 방식과 달리 구글드라이브
동기화 없이 바로 반영됨).
