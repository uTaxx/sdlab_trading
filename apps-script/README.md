# Apps Script 프로젝트 둘

n8n이 맡던 층을 Google Apps Script로 옮기는 자리다. 설계는
`docs/회사워크플로_앱스스크립트_이관_설계_2026-09-07.md`에 있다.
**지금은 기반만 있다. 어느 것도 실제 시스템에 연결돼 있지 않다.**

| 폴더 | 옮기는 것 | 지금 상태 |
|---|---|---|
| `trading/` | n8n 매매 층 4개. 시계(AutoTrading_Schedule), 텔레그램 중계(AutoTrading_Telegram), 계좌 조회(AutoTrading_계좌조회), 화면 자료 창구(무원406 대시보드 자료) | 시계·텔레그램·계좌는 코드가 있고 관찰 모드다. 자료 창구는 갈래 이름만 있다. |
| `lxgroup/` | 회사(LX) 시간표 워크플로 10개와 조회 웹훅 4개 | 웹 앱 입구, 식별코드 확인, 수집 작업 관리가 있다. 수집 단계와 조회는 자리만 있다. |

파이썬 매매 엔진(백테스트, 증권사 주문)은 옮기지 않는다. GitHub Actions에
그대로 있고, Apps Script는 그것을 정해진 시각에 부르기만 한다.

## 어떻게 올라가나

`.github/workflows/apps-script-deploy.yml`이 clasp로 올린다. `main`의
`apps-script/**`가 바뀌면 코드만 올리고(push), 사람이 `workflow_dispatch`로
`mode: deploy`를 고르면 웹 앱 배포까지 갱신한다.

`.clasp.json`의 `scriptId`가 비어 있는 프로젝트는 건너뛴다. 프로젝트를
만들면 그 ID를 적어 커밋한다. `deployment.json`의 `deploymentId`도 같다.
처음 배포 때 로그에 찍힌 ID를 적어야 주소가 고정된다.

## 사람이 한 번 할 것

1. https://script.google.com/home/usersettings 에서 Google Apps Script API를 켠다.
2. PC에서 `npx @google/clasp login`을 실행한다. 생긴 `.clasprc.json`
   (윈도우 `C:\Users\<이름>\.clasprc.json`) 내용을 이 저장소의 GitHub 비밀값
   `CLASPRC_JSON`에 넣는다. 시트와 드라이브를 가진 구글 계정으로 로그인한다.
3. 첫 배포 뒤 https://script.google.com 에서 프로젝트를 열어 `권한확인`을
   실행하고 「허용」을 누른다. 프로젝트마다 한 번이다.
4. 매매 프로젝트의 스크립트 속성(프로젝트 설정 → 스크립트 속성)에 값을
   넣는다. 이름은 `trading/src/설정.js`의 `속성이름`에 있다. n8n 자격증명
   안에 있는 값이라 나는 읽을 수 없다.
   - `GITHUB_TOKEN`: sdlab_trading의 Actions를 실행할 수 있는 토큰
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: 무원406 봇과 관리자 대화방
   - `TELEGRAM_WEBHOOK_SECRET`: 아무 긴 글자. 텔레그램 웹훅 주소에 붙인다.
   - `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO`, `KIS_ACCOUNT_PRODUCT`, `KIS_ENV`(paper 또는 real)
5. 회사 프로젝트는 시트 `운영설정` 탭에 `TELEGRAM_BOT_TOKEN`과
   `TELEGRAM_ADMIN_CHAT_ID` 줄을 넣는다. API 키는 이미 그 탭에 있다.

## 시계는 관찰 모드로 먼저 올린다

`trading/src/시계.js`는 1분 트리거로 시간표를 보고 GitHub Actions를 부른다.
스크립트 속성 `CLOCK_MODE`가 `실행`이 아니면 부르지 않고 "예정 09:05, 실제
09:05:07"만 시트 `시계기록` 탭에 적는다. 최종 연결 전에 한 주쯤 이 표를
모아 늦는 정도를 본 뒤에 `실행`으로 바꾼다. 그 전에 n8n을 끄면 안 되고,
`실행`으로 바꾸는 날 n8n `AutoTrading_Schedule`을 꺼야 두 번 부르지 않는다.

## 시험

    cd apps-script && npm run check && npm test

Apps Script 파일은 전역을 같이 쓰는 스크립트라 `tests/gas.js`가 vm 문맥에
넣어 읽는다. 순수 함수만 시험한다. 시트·드라이브·바깥 호출을 쓰는 함수는
배포 뒤 편집기에서 `권한확인`으로 본다. `tests/test_apps_script_base.py`가
매니페스트와 비밀값 모양과 이 Node 시험을 파이썬 쪽에서 다시 돈다.

## 지키는 것

- 코드에 비밀값을 적지 않는다. 저장소가 공개다. 매매 쪽은 스크립트 속성,
  회사 쪽은 시트 `운영설정` 탭에서 읽는다.
- 시트에서 읽은 값을 `Logger.log`에 찍지 않는다.
- 화면 쪽 요청은 `text/plain`으로 온다. HTTP 상태 코드를 못 정하므로 거절은
  본문 `상태` 칸에 적는다. 최종 연결 때 화면의 `부르기()`를 그에 맞춘다.
