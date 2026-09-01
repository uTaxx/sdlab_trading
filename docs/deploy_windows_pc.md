# 집 윈도우 PC로 장중 실시간 매매 실행하기

VPS 대신 집 PC를 상시 서버로 쓰는 방법이다. 비용은 0원이지만, VPS와 달리
**PC가 계속 켜져 있고 인터넷이 끊기지 않아야** 하는 책임이 사용자에게
있다. 절전모드/재부팅/정전에 대비한 설정이 핵심이다.

이 스크립트는 KIS 서버로 **나가는(outbound)** 연결만 하므로 (웹소켓 접속,
주문 API 호출), 공유기 포트포워딩이나 고정 공인IP는 필요 없다.

## 1. 절전모드 끄기

PC가 잠들면 그 순간 매매가 멈춘다.

1. **설정** → **시스템** → **전원 및 배터리** → **화면 및 절전**
2. "전원에 연결되어 있을 때 화면을 끄는 시간" / "절전 모드로 전환하는 시간"을
   모두 **"안 함"** 으로 설정
3. 노트북이면 추가로: **제어판** → **전원 옵션** → 왼쪽 메뉴
   **"덮개를 닫았을 때의 동작 설정"** → "전원에 연결됨" 항목을
   **"아무 작업 안 함"** 으로 설정 (덮개 닫아도 안 꺼지게)

## 2. Python·Git 설치

1. https://www.python.org/downloads/ 에서 Python 3.11 다운로드 → 설치
   **설치 화면 맨 아래 "Add python.exe to PATH" 체크박스 꼭 체크할 것**
2. https://git-scm.com/download/win 에서 Git 다운로드 → 기본 설정으로 설치

## 3. 저장소 내려받고 환경 준비

시작 메뉴에서 **PowerShell** 실행 (관리자 권한 아니어도 됨):

```powershell
cd $HOME
git clone https://github.com/uTaxx/sdlab_trading.git
cd sdlab_trading
git checkout main
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,realtime]"
copy .env.example .env
notepad .env
```

메모장이 열리면 `MUWON_MASTER_KEY=` 뒤에 아래 명령으로 만든 값을 채우고 저장:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 4. KIS·텔레그램 설정

```powershell
python scripts\configure.py kis --env paper --app-key "발급받은앱키" --app-secret "발급받은시크릿" --account-no "계좌번호앞8자리" --account-product-cd "01"
python scripts\configure.py telegram --bot-token "봇토큰" --chat-id "챗ID"
```

## 5. 첫 실행 (수동으로 로그 보면서)

```powershell
python scripts\run_realtime_trading.py
```

장중에 실행해서 로그를 지켜본다. 웹소켓 연결/구독 단계 에러, 매매 체결
로그 등을 확인한다. `src/muwon/data/kis_websocket.py`는 이 프로젝트를
개발한 환경(KIS 포트가 막힌 샌드박스)에서 실제 접속 검증을 못 했으니,
여기서 처음으로 진짜 검증하게 된다.

`Ctrl+C`로 종료할 수 있다.

## 6. 로그인할 때 자동 시작 + 죽으면 자동 재시작

수동 실행이 잘 되면, PC를 켜거나 재부팅될 때 자동으로 뜨고, 프로세스가
죽어도 스스로 재시작하도록 배치파일로 감싼다.

`sdlab_trading` 폴더에 `run_forever.bat` 파일을 새로 만든다 (메모장으로 작성 후
파일 형식을 "모든 파일"로, 이름을 `run_forever.bat`로 저장):

```bat
@echo off
cd /d "%~dp0"
:loop
call .venv\Scripts\activate.bat
python scripts\run_realtime_trading.py
echo [%date% %time%] 프로세스 종료됨: 10초 후 재시작
timeout /t 10
goto loop
```

이 파일의 **바로가기**를 만들어서 시작프로그램 폴더에 넣는다:

1. `run_forever.bat` 우클릭 → **바로 가기 만들기**
2. `Win + R` → `shell:startup` 입력 → 엔터 (시작프로그램 폴더가 열림)
3. 방금 만든 바로가기를 그 폴더로 이동

이제 로그인할 때마다 자동으로 뜬다. 확인하려면 로그아웃 후 다시 로그인해보거나,
그 바로가기를 더블클릭해서 검은 창(콘솔)이 뜨고 로그가 찍히는지 본다.

### 정전·재부팅 후에도 자동으로 돌게 하려면 (선택)

기본적으로 윈도우는 재부팅되면 로그인 화면에서 멈춘다. 자동 로그인을
설정해야 사람 개입 없이 다시 매매가 시작된다.

1. `Win + R` → `netplwiz` 입력 → 엔터
2. 본인 계정 선택 → **"사용자가 이 컴퓨터를 사용하려면 사용자 이름과
   암호를 입력해야 합니다"** 체크 해제 → 확인 → 비밀번호 입력

**주의**: 이렇게 하면 PC 부팅 시 비밀번호 없이 바로 로그인된다. 집에
다른 사람도 쓰는 PC거나 도난 위험이 있으면 보안 트레이드오프를 감안할 것.

## 7. 리스크 정책 확인/조정

같은 PC에서 대시보드도 띄울 수 있다 (같은 로컬 `muwon.db`를 보므로 즉시 반영):

```powershell
pip install -e ".[dashboard]"
streamlit run src\muwon\dashboard\app.py
```

## 참고: GitHub Actions 배치 모드와는 별개

이 PC로 실시간 모드를 실행하는 동안은 GitHub Actions 배치
(`docs/deploy_github_actions.md`)를 같은 KIS 계좌에 동시에 실행하지 말 것:
운영 모드는 하나만 고른다.
