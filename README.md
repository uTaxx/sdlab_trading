# muwon406: 코스피/코스닥 자동매매 시스템

한국투자증권(KIS) API 기반, 규칙기반 전략에서 시작해 ML 신호로 고도화하는
자동매매 시스템. 백테스트 → 모의투자 → 소액 실거래 순으로 검증하며 진행합니다.

📖 **처음이라면 [사용설명서](docs/사용설명서.md)부터 읽으세요**. 이 시스템이 무엇을
하는지, 매일 무슨 일이 자동으로 일어나는지, 급할 때 어떻게 끄는지를 용어 해설과 함께
정리했습니다.

🔗 대시보드: https://utaxx.github.io/sdlab_trading/

## 구조

```
src/muwon/
├── config.py       # 부트스트랩 설정 (.env: DB URL, 암호화 키만)
├── settings/       # 런타임 설정 (KIS 인증정보/텔레그램/리스크 정책, DB 저장)
├── domain/         # 공통 타입/인터페이스 (Strategy, MarketDataSource, OrderExecutor)
├── data/           # KIS API 클라이언트: 시세 수집
├── strategy/       # 전략 21종: trend/reversion/breakout/rule_based + registry
├── indicators/     # 기술적 지표 (이동평균·RSI·MACD·볼린저·스토캐스틱·돈치안·ATR·ADX)
├── risk/           # 리스크 매니저: 주문 실행 전 최종 검증
├── execution/       # 주문 실행기 (모의/실전 전환, Phase 2)
├── backtest/       # 백테스트 엔진 (Phase 1)
├── analysis/       # 전략 가설 비교(스윕/일일 리뷰) 공용 로직
├── notify/         # 텔레그램 알림
├── dashboard/      # 설정 관리 웹 대시보드 (Streamlit, 폰/PC 상시 접속 가능)
├── cloud/          # 상태 DB 구글드라이브 동기화 (GitHub Actions·대시보드 공용)
└── db/             # 시세/신호/주문/설정 저장 (SQLite → 운영 시 Postgres 전환 가능)

scripts/
├── configure.py           # 대시보드와 동일한 SettingsService를 쓰는 설정 CLI
├── run_backtest.py        # 과거 데이터로 전략 백테스트
├── run_dry_run.py         # KIS 없이 신호→리스크→체결→알림→기록 파이프라인 검증(가짜 체결)
├── run_paper_trading.py   # KIS 모의투자: 하루 1회 배치 모드 (GitHub Actions용)
├── run_realtime_trading.py  # KIS 모의투자: 장중 실시간 모드 (VPS용, KIS 웹소켓)
├── run_hypothesis_sweep.py  # 등록된 전략 가설을 과거 데이터로 일괄 백테스트·비교
├── update_universe.py     # 매매 대상 종목을 시가총액 상위로 갱신(스냅샷 저장)
├── verify_kis_order.py    # KIS 주문 실행 경로 검증(모의투자 전용, 진단용)
├── run_robustness_check.py  # 전략을 여러 기간에 나눠 검증(과최적화 탐지)
├── run_analysis_report.py   # Claude에 붙여넣을 분석 리포트 생성·텔레그램 발송
├── run_daily_review.py    # 매일 자동매매 직후, "다른 전략이었다면?" 비교 리포트를 텔레그램으로 발송
└── gdrive_sync.py         # GitHub Actions용 상태 DB 구글드라이브 업/다운로드

.github/workflows/
├── paper-trading.yml            # 평일 장마감 후 자동으로 run_paper_trading.py 실행
├── update-universe.yml          # 매주 매매 대상 종목 갱신
├── verify-kis-order.yml         # 주문 경로 진단 (수동 실행)
├── analysis-report.yml          # 주 1회 분석 리포트 텔레그램 발송
└── check-kis-connectivity.yml   # KIS 접속 가능 여부만 확인하는 진단용 (비밀값 불필요)
```

KIS 인증정보·텔레그램 토큰·리스크 정책은 `.env`가 아니라 DB에 저장되어,
재시작 없이 CLI나 웹 대시보드에서 값을 바꿀 수 있습니다. 설계 배경은
[`docs/config_architecture.md`](docs/config_architecture.md) 참고.

## 설정 대시보드

```bash
pip install -e ".[dashboard]"
streamlit run src/muwon/dashboard/app.py
```

한 화면에서 설정 조회/수정, 변경 이력, 개발 로그(git 커밋)까지 다 보이는
통합 도구입니다. `scripts/configure.py`와 동일한 `SettingsService`를
거치므로 저장 위치·형식은 CLI와 완전히 같습니다.

- **자동매매 킬스위치**: 화면 상단 토글로 즉시 on/off: 끄면 `RiskManager`가
  신규 진입 신호를 전부 거부합니다.
- **실시간 갱신**: 변경 이력(5초)·개발 로그(20초)는 `st.fragment(run_every=...)`로
  자동 새로고침되어, 다른 프로세스(CLI, 봇)가 값을 바꿔도 클릭 없이 반영됩니다.
- **변경 이력**: 설정값이 바뀔 때마다 이전값→새값이 자동 기록됩니다. 비밀값은
  `MUWON_MASTER_KEY`가 있을 때만 마스킹 표시됩니다.
- **활성 전략**: 등록된 21개 전략을 계열 필터·"거래가 있었던 전략만" 토글로
  좁혀 보면서, 각 전략의 최근 백테스트 성적(수익률/MDD/승률/거래수)을 한 표에서
  비교하고 화면에서 바로 전환할 수 있습니다
  (`scripts/configure.py strategy --active-key`와 동일한 동작).
- **매매 기록**: 청산까지 끝난 매매를 전략별로 보여줍니다(진입가·청산가·
  손익·진입/청산 사유): `trades` 테이블을 그대로 읽습니다.
- **전략 리뷰 결과**: `scripts/run_daily_review.py`가 매일 쌓아 둔 "다른
  전략이었다면?" 비교 결과를 표로 보여줍니다.

비밀값(앱시크릿, 봇토큰 등)을 저장/조회하려면 `MUWON_MASTER_KEY`가 `.env`에
설정되어 있어야 합니다. 대시보드 하단 "보유 종목 & 최근 주문"에서 실제
매매 파이프라인(아래 참고)이 만든 포지션·주문을 5초마다 자동 갱신되는
표로 볼 수 있습니다.

### 폰/PC 어디서든 상시 접속 (Streamlit Community Cloud)

매번 `streamlit run`을 직접 실행하는 대신, 무료로 상시 호스팅되는 웹 주소
하나를 만들어 둘 수 있습니다. `GDRIVE_SA_KEY_JSON`/`GDRIVE_FOLDER_ID`가
설정되어 있으면 대시보드가 뜰 때·30초마다 구글드라이브에서 최신 `muwon.db`를
받아오고, 화면에서 설정을 바꾸면 즉시 다시 올립니다(`src/muwon/cloud/gdrive_sync.py`):
GitHub Actions가 매일 만드는 매매 기록과 대시보드에서 바꾼 설정이 같은
구글드라이브 폴더를 통해 서로에게 반영됩니다. 배포 방법은
[`docs/deploy_streamlit_cloud.md`](docs/deploy_streamlit_cloud.md) 참고.

## 매매 파이프라인 (Phase 2)

`src/muwon/execution/engine.py`의 `TradingEngine`이 신호 생성 → 리스크
매니저 승인 → 주문 체결 → 텔레그램 알림 → DB 기록을 한 번에 처리합니다.
매일 장 마감 후 1회(`run_once()`) 도는 걸 전제로 설계했습니다.

시세 소스와 주문 실행기를 무엇으로 주입하느냐에 따라 두 가지 경로가 있습니다.

- **`scripts/run_dry_run.py`**. 시세는 Yahoo Finance, 체결은
  `SimulatedOrderExecutor`(KIS 서버를 거치지 않고 로컬에서 체결됐다고
  가정)로 처리합니다. KIS 네트워크 접근이 안 되는 환경에서도 파이프라인
  전체(리스크 검증·텔레그램 알림 문구·DB 기록)를 오늘 바로 검증할 수
  있습니다. **KIS 모의투자가 아닙니다**. 진짜 매매 파이프라인 배관을
  테스트하는 용도입니다.
- **`scripts/run_paper_trading.py`**. 시세·체결 모두 `KISClient`를 거쳐
  KIS 모의투자 서버로 실제 주문을 넣습니다. KIS API가 비표준 포트
  (9443/29443)를 쓰기 때문에 egress 정책에 따라 접근이 막혀 있을 수
  있습니다. 이 저장소를 개발한 로컬 환경은 실제로 막혀 있었지만,
  **GitHub Actions 러너에서는 모의투자 포트(29443) 접속이 확인됐습니다**
  (`check-kis-connectivity.yml` 참고).

두 경로 모두 같은 `TradingEngine`·`RiskManager`·`TelegramNotifier`를
쓰므로, KIS 접근이 열리면 데이터소스/실행기만 바꿔 끼우면 됩니다.

매수/매도가 체결되면 텔레그램으로 `🟢 매수 체결` / `🔴 매도 체결` 메시지가
갑니다(봇토큰 미설정 시엔 로그로만 남습니다). 리스크 매니저가 거부한
신호는 알림을 보내지 않고 실행 결과에만 남습니다.

## 매일 자동 실행 (GitHub Actions)

PC를 계속 켜둘 필요 없이, GitHub Actions가 평일 장마감 후 자동으로
`run_paper_trading.py`를 실행하도록 구성되어 있습니다
(`.github/workflows/paper-trading.yml`). GitHub Actions는 매번 새
가상머신이라 로컬 상태가 안 남기 때문에, 보유 종목·가상현금 상태(`muwon.db`)를
구글드라이브에 두고 실행마다 내려받고/올립니다.

설정 방법(구글 서비스 계정 만들기, GitHub Secrets 등록 등)은
[`docs/deploy_github_actions.md`](docs/deploy_github_actions.md)에
순서대로 정리되어 있습니다.

**PC 없이 폰/브라우저로 할 수 있는 것**: 저장소 Actions 탭 →
"KIS 모의투자 일일 자동매매" → Run workflow로 수동 실행할 수 있습니다.
이때 `trading_enabled` 입력값을 `true`/`false`로 지정하면 다른 리스크
설정은 그대로 두고 자동매매 킬스위치만 즉시 전환됩니다(`scripts/configure.py
kill-switch`): 이상 징후가 보일 때 PC 앞이 아니어도 바로 멈출 수 있게 만든
용도입니다. 값을 "유지"로 두면 지금 저장된 설정 그대로 실행됩니다.

## 장중 실시간 매매 (VPS 또는 상시 켜진 PC)

하루 1회 배치 대신, 장중(09:00~15:30 KST) 체결이 들어올 때마다 반응하는
운영 모드도 있습니다. `src/muwon/execution/realtime_engine.py`의
`RealtimeTradingEngine`이 KIS 웹소켓으로 받은 틱을 분봉(기본 1분,
`src/muwon/data/tick_aggregator.py`)으로 묶고, 봉이 마감될 때마다 신호를
평가합니다. 판단 로직(`Strategy`)과 리스크 검증(`RiskManager`)은 배치
모드와 완전히 동일하게 재사용하고, "언제 판단하느냐"만 다릅니다.

`src/muwon/execution/realtime_runner.py`가 웹소켓 연결이 끊기면 지수
백오프로 자동 재연결합니다. 장중 몇 시간을 붙잡고 있어야 하는 연결이라
네트워크 순단은 예외가 아니라 전제로 설계했습니다. 재연결해도 봉 히스토리
(sma60 계산용 최근 60개 분량)는 메모리에 그대로 남아 있어 지표가 다시
채워질 때까지 기다릴 필요가 없습니다.

이건 장중 내내 떠 있어야 하는 상시 프로세스라 GitHub Actions로는 안 되고
**VPS 또는 계속 켜져 있는 PC가 필요합니다**. 두 운영 모드(배치/실시간)는
같은 계좌에 동시에 쓰는 게 아니라 둘 중 하나를 고르는 대안입니다.

```bash
pip install -e ".[realtime]"
python scripts/run_realtime_trading.py
```

KIS 웹소켓 연동은 이 저장소를 개발한 환경에서 실제 접속 검증을 못 했습니다
(KIS 포트 자체가 막혀 있음): 공식 문서 기준으로 작성했으니 실제 배포 후
첫 실행에서 재검증이 필요합니다. 설정 방법:

- VPS(리눅스, systemd)에 배포 → [`docs/deploy_vps_realtime.md`](docs/deploy_vps_realtime.md)
- 집 윈도우 PC를 상시 서버로 활용 → [`docs/deploy_windows_pc.md`](docs/deploy_windows_pc.md)

## 매매 대상 종목 자동 갱신

`data/universe.py`의 기본 목록은 사람이 골라 고정해 둔 것이라 시간이 지나면
낡습니다(상장폐지·순위 역전·신규 대형주 누락). `scripts/update_universe.py`가
KIS 시가총액 순위로 다시 뽑아 `universe_snapshots` 테이블에 **스냅샷으로
쌓습니다**. 덮어쓰지 않는 이유는, 성과가 달라졌을 때 그게 전략 탓인지 종목이
바뀐 탓인지 구분하려면 그날 무엇을 대상으로 삼았는지가 남아 있어야 하기
때문입니다.

```bash
python scripts/update_universe.py                 # 미리보기(저장 안 함)
python scripts/update_universe.py --apply --size 30
```

ETF·ETN·스팩·우선주는 자동으로 제외합니다. 개별 종목 전략(이동평균·RSI)의
전제가 맞지 않거나(바스켓 상품), 합병 전까지 가격이 거의 고정이거나(스팩),
같은 회사가 중복으로 잡히고 거래량도 적기 때문입니다(우선주).

매매·리뷰 스크립트는 최신 스냅샷을 자동으로 쓰고, 갱신된 적이 없거나 갱신에
실패하면 기존 기본 목록으로 돌아갑니다. 종목 갱신 실패가 매매를 멈추게
해서는 안 되기 때문입니다. 매주 일요일 밤 `update-universe.yml`이 자동 실행되며,
변경된 종목은 텔레그램으로 알려줍니다.

> ⚠️ KIS의 순위 조회 API는 모의투자를 지원하지 않을 수 있습니다. 거부되면
> 사유를 출력하고 종료하며, 기존 종목 목록이 그대로 유지됩니다.

## 전략 진단: 리포트를 받아 Claude에게 물어보기

`scripts/run_analysis_report.py`가 매매 결과·현재 설정·검증 결과를 한 덩어리
텍스트로 만들어 텔레그램으로 보냅니다(금요일 장마감 후 자동, `analysis-report.yml`).
**받은 내용을 그대로 복사해 Claude에게 붙여넣으면 전략 진단을 받을 수 있습니다.**

리포트에 담기는 것:

- 현재 설정(활성 전략, 리스크 정책, 자동매매 on/off, 매매 대상 종목 수)
- 실전 성과(승률, 누적 손익, 평균 이익/손실, **손익비**, 평균 보유일)
- **전략별 / 청산 사유별 / 진입 사유별** 집계: 어디서 잃고 있는지가 보입니다
- 최근 매매 개별 내역(진입가→청산가, 진입·청산 사유)
- 보유 중인 종목
- 최근 리뷰에서 활성 전략의 순위
- 다기간 검증 결과(구간별 수익률, 최악 구간)

숫자만 나열하지 않고 맥락까지 함께 담는 이유는, 리포트 하나만 보고도 판단이
가능해야 진단이 의미 있기 때문입니다 — "승률 33%"만으로는 좋은지 나쁜지
알 수 없지만, 손익비 0.88과 "손절 2건이 손실의 대부분"까지 같이 보이면
무엇을 고쳐야 할지가 드러납니다.

> LLM을 코드에서 직접 호출하지 않는 이유: 키 관리·비용·장애 요소가 늘어나는
> 데 비해, 전략 변경 판단은 어차피 사람이 최종 확인해야 하는 영역이라 얻는
> 게 적습니다. 사람이 붙여넣는 한 단계가 그 확인을 자연스럽게 만듭니다.

```bash
python scripts/run_analysis_report.py --days 30              # 콘솔+텔레그램
python scripts/run_analysis_report.py --days 90 --no-telegram
```

## 과최적화 검증: 여러 기간에서 통하는가

한 구간 성적만 보고 전략을 고르면 "그 시기에만 맞았던 전략"을 고르게 됩니다.
`scripts/run_robustness_check.py`가 같은 전략을 연/반기 단위로 각각 돌려
구간별 성과를 비교합니다.

```bash
python scripts/run_robustness_check.py --from-year 2021 --to-year 2024
python scripts/run_robustness_check.py --from-year 2023 --to-year 2024 --half-year
```

정렬 기준은 평균이 아니라 **최악 구간 수익률**입니다. 평균으로 줄을 세우면
"한 구간 대박 + 다른 구간 폭망" 전략이 위로 올라오는데, 실전에서 중요한 건
못 버티는 구간이 있느냐이기 때문입니다.

실제로 2023~2024 단일 구간에서 +59%로 1위였던 돈치안 돌파는, 2021~2024로
넓혀 보니 2022년 -15.4%·2024년 -7.8%로 시기를 심하게 타는 전략이었습니다.
그 한 구간만 보고 갈아탔다면 상승장에서만 통하는 전략을 고른 셈입니다.

## 전략 가설 검증 & 진화

"단타 가설을 세우고 → 과거 데이터로 검증하고 → 실전에 반영한다"를
코드 배포 없이 반복할 수 있도록 만든 구조입니다.

- **`src/muwon/strategy/registry.py`**. 전략을 `StrategyDefinition`으로 등록하는
  곳. **21개 가설이 4개 계열로 등록되어 있습니다.** 같은 전략 코드라도
  파라미터(이동평균 기간·RSI 기간·거래량 배수 등)만 바꾸면 다른 가설이 되고,
  아예 다른 로직도 같은 인터페이스로 등록됩니다.

  | 계열 | 성격 | 구현 파일 | 예 |
  |---|---|---|---|
  | 추세추종 | 오르는 걸 따라 사고 꺾이면 판다. 승률 낮고 손익비 큼 | `strategy/trend.py` | 골든크로스, EMA교차, MACD, 돈치안(터틀) |
  | 평균회귀 | 많이 빠지면 되돌아온다에 베팅. 승률 높지만 추세장에서 크게 물림 | `strategy/reversion.py` | RSI 반등, RSI(2) 눌림목, 볼린저 하단, 스토캐스틱 |
  | 돌파·모멘텀 | 박스를 뚫으면 그 방향으로 간다. 가짜 돌파가 약점 | `strategy/breakout.py` | 볼린저 상단돌파, 거래량 급증, 종가 신고가 |
  | 복합 | 여러 규칙을 섞은 것 | `strategy/rule_based.py` | 이동평균+RSI |

  각 항목은 `status="live"`(실거래 중) 또는 `"hypothesis"`(검증 중)로 표시됩니다.
- **`scripts/run_hypothesis_sweep.py`**. 등록된 가설 전체(또는 `--keys`로
  고른 일부)를 같은 기간·같은 종목 유니버스로 백테스트하고 수익률/MDD/승률/거래수를
  비교표로 출력합니다. 결과는 `backtest_runs` 테이블에 파라미터 스냅샷과
  함께 누적 저장되므로, 나중에 다시 돌려도 시간에 따른 비교가 가능합니다.

  ```bash
  python scripts/run_hypothesis_sweep.py --start 2023-01-01 --end 2024-12-31
  ```

- **`scripts/configure.py strategy`**. 가설이 마음에 들면 코드를 고치거나
  다시 배포할 필요 없이 설정값 하나로 실거래 전략을 바꿉니다. 이 변경도
  기존 설정 변경 이력에 자동으로 남습니다.

  ```bash
  python scripts/configure.py strategy --list                    # 등록된 가설 + 현재 활성 전략 확인
  python scripts/configure.py strategy --active-key ma_rsi_fast5_20  # 실거래 전략 교체
  ```

- **`scripts/run_daily_review.py`**. 매일 자동매매(GitHub Actions)가 끝날
  때마다 자동으로 붙는 리뷰 단계입니다(`.github/workflows/paper-trading.yml`에
  `run_paper_trading.py` 다음 스텝으로 추가돼 있음). 기준일(오늘)에서
  `--lookback-days`(기본 90일)만큼 거슬러 올라간 최근 구간을 등록된 전략
  전체로 다시 채점해서, "오늘 만약 다른 매수 전략이었다면 수익률이 더
  좋았을지 나빴을지"를 지금 실거래 중인 전략 대비 %p 차이로 텔레그램에
  보내줍니다. 결과는 `backtest_runs`에 `notes="daily_review"`로 쌓여서
  수동 스윕(`notes="manual_sweep"`) 기록과 구분되고, 매일 쌓이므로 어느
  전략이 꾸준히 앞서는지 시간에 따른 추세로도 볼 수 있습니다. 사람이 손대지
  않아도 매일 "지금이 최선인가"를 다시 묻는 루프입니다.
- **매매 결과 학습 기반 (`TradeRow`)**. 실전/모의 매매에서 포지션이 청산될
  때마다 진입가·청산가·손익·진입 사유·청산 사유가 `strategy_key`와 함께
  `trades` 테이블에 기록됩니다(`src/muwon/execution/state_repository.py`의
  `record_trade`). 지금은 이 데이터를 사람이 조회하는 용도지만, 어떤 전략이
  어떤 조건에서 이기고 지는지가 이미 구조화되어 쌓이고 있어서, 향후 AI가
  이 로그를 읽고 전략 파라미터 수정이나 새 가설을 제안하는 단계로 자연스럽게
  이어질 수 있도록 설계했습니다(아직 AI 연동 자체는 구현 전: 데이터 기반만
  마련된 상태).

## 로드맵

1. **Phase 0**: 저장소 구조, 리스크 매니저 기본 로직, KIS API 신청 가이드
2. **Phase 1**: 데이터 수집 파이프라인 + 규칙기반 전략 + 백테스트 엔진
3. **Phase 2** (진행 중): 매매 파이프라인(신호→리스크→체결→알림→기록) +
   설정 대시보드 + 배치(GitHub Actions)/실시간(VPS 웹소켓) 두 실행 모드:
   KIS 모의투자 계좌로 실제 체결 확인이 다음 단계
4. **Phase 3**: ML 신호 고도화
5. **Phase 4**: 소액 실거래 전환

## 시작하기

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env  # DB URL, 암호화 키 채워넣기 (docs/kis_api_setup.md 참고)

pytest
```

## KIS API 신청 및 설정

아직 앱키를 발급받지 않았다면 [`docs/kis_api_setup.md`](docs/kis_api_setup.md)를
따라 진행하세요. 발급 후에는 `python scripts/configure.py kis ...`로
저장합니다.

## 리스크 정책

기본 리스크 규칙은 [`docs/risk_policy.md`](docs/risk_policy.md)에 정리되어
있으며, `python scripts/configure.py risk ...`로 조정할 수 있습니다.
