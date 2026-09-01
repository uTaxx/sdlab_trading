# 대시보드를 Streamlit Community Cloud에 올려서 폰/PC 어디서든 보기

지금까지는 대시보드를 보려면 `streamlit run src/muwon/dashboard/app.py`를
직접 실행해야 했다. 매번 터미널을 열어야 하고, 종료하면 화면도 꺼진다.
이 문서대로 하면 **평일 상시로 떠 있는 웹 주소 하나**가 생겨서, 폰이든
PC든 그 주소만 열면 언제든 현재 리스크 정책·보유 종목·매매 기록·전략 리뷰
결과를 볼 수 있다.

**비용은 0원이다**. Streamlit Community Cloud는 공개(public) GitHub
저장소를 무료로 호스팅해준다. 이 저장소(`uTaxx/sdlab_trading`)는 이미 공개
저장소라 바로 쓸 수 있다.

## 왜 이게 되는지 (배경)

Streamlit Cloud도 GitHub Actions와 마찬가지로 **컨테이너가 재배포될 때마다
로컬 디스크가 사라진다**. 그래서 대시보드가 뜰 때마다 구글드라이브에서
`muwon.db`를 내려받고, 화면에서 설정을 바꾸면 즉시 다시 올리도록 이미
코드를 만들어 뒀다(`src/muwon/dashboard/app.py`의 `sync_db_from_drive`/
`sync_db_to_drive`, 30초마다 자동으로도 다시 받아온다). GitHub Actions가
매일 만드는 매매 기록도, 대시보드가 바꾼 설정도 **같은 구글드라이브 폴더
하나**를 거쳐 서로에게 보인다.

즉 이 설정은 [`docs/deploy_github_actions.md`](deploy_github_actions.md)에서
만든 서비스 계정·공유 드라이브를 **그대로 재사용**한다. 그 문서를 먼저
끝냈다면 아래 1번은 건너뛰고 2번부터 하면 된다.

## 1. (아직 안 했다면) 구글 서비스 계정 + 공유 드라이브 준비

[`docs/deploy_github_actions.md`](deploy_github_actions.md)의 1~2번을
그대로 따라간다. 이미 GitHub Actions용으로 만들어 둔 서비스 계정 JSON
키와 공유 드라이브 폴더 ID가 있다면, 그 값을 그대로 다시 쓰면 된다(새로
만들 필요 없음).

## 2. Streamlit Community Cloud 가입 및 배포

1. https://share.streamlit.io 접속 → **Continue with GitHub**로 로그인
   (이 저장소에 접근 권한이 있는 GitHub 계정이어야 함)
2. **Create app** → **Deploy a public app from GitHub**
3. 입력값:
   - **Repository**: `uTaxx/sdlab_trading`
   - **Branch**: `main` (또는 지금 쓰는 브랜치)
   - **Main file path**: `src/muwon/dashboard/app.py`
4. **Advanced settings** 펼치기 → **Python version**을 `3.11`로 지정
5. 아직 **Deploy는 누르지 말고** 같은 화면(또는 배포 후 앱 설정 → **Secrets**)에서
   3번(시크릿 등록)부터 먼저 채운다. 시크릿 없이 배포하면 첫 실행에서
   DB 연결/암호화 관련 오류가 난다.

## 3. 시크릿 등록

앱 설정(⋮ 메뉴 → **Settings** → **Secrets**)에 아래 내용을 TOML 형식으로
붙여넣는다. GitHub Secrets에 등록해 둔 값과 **완전히 같은 값**을 써야 한다
(다른 `MUWON_MASTER_KEY`를 쓰면 KIS 앱키·텔레그램 토큰이 화면에서
"복호화 불가"로 뜬다).

```toml
DATABASE_URL = "sqlite:///./muwon.db"
MUWON_MASTER_KEY = "여기에 GitHub Secrets와 동일한 값"
GDRIVE_FOLDER_ID = "공유 드라이브 폴더 ID"
GDRIVE_SA_KEY_JSON = '''
{"type": "service_account", "project_id": "...", "private_key": "...", ...}
'''
```

`GDRIVE_SA_KEY_JSON`은 서비스 계정 JSON 키 파일의 **내용 전체**를 삼중
따옴표(`'''`) 사이에 그대로 붙여넣는다. JSON 안에 큰따옴표가 많아서
일반 따옴표로 감싸면 깨진다.

## 4. 배포 & 첫 접속 확인

1. **Deploy** 클릭: 몇 분 정도 걸린다(의존성 설치)
2. 배포가 끝나면 `https://<앱이름>.streamlit.app` 같은 주소가 생긴다.
   이 주소를 즐겨찾기/홈 화면에 추가해두면 폰에서도 바로 열림
3. 화면 상단에 "☁️ 구글드라이브 동기화: HH:MM:SS"가 보이면 정상 연결된 것
4. 리스크 정책이나 활성 전략을 하나 바꿔보고, 몇 분 뒤 GitHub Actions
   실행 로그(또는 다음 자동매매 실행)에서 그 값이 반영됐는지 확인하면
   양방향 동기화까지 검증 끝

## 알아두면 좋은 것

- **동기화는 30초 주기**다. GitHub Actions가 방금 끝낸 매매를 대시보드가
  바로 보려면 최대 30초 정도 걸릴 수 있다.
- **설정을 바꾸면 즉시 업로드된다**. 화면에서 저장 버튼을 누른 순간
  구글드라이브에도 반영되므로, 그 직후 GitHub Actions가 돌아도 최신
  설정을 받아간다.
- **동시에 여러 곳에서 쓰기 충돌은 크게 신경 안 써도 된다**. 개인용
  단일 계좌 도구라 GitHub Actions(하루 1회)와 대시보드(가끔 설정 변경)가
  같은 순간에 동시에 쓸 일이 거의 없다. 다만 GitHub Actions가 한창 매매
  중일 때 대시보드에서 설정을 저장하면, 그 실행이 이미 내려받은 옛 상태
  기준으로 끝나고 그 다음에 대시보드가 올린 값을 덮어쓸 수 있다. 장중
  자동매매(`run_realtime_trading.py`)와는 무관하고, 하루 1회 배치
  (`run_paper_trading.py`) 실행 중(평일 15:30 KST 직후 몇 분)에만 해당.

## 문제가 생기면

- **"MUWON_MASTER_KEY가 설정되어 있지 않습니다" 경고**: 시크릿에
  `MUWON_MASTER_KEY`가 없거나 오타. Settings → Secrets에서 확인.
- **KIS/텔레그램 값이 "복호화 불가"로 표시**: `MUWON_MASTER_KEY`가
  GitHub Secrets에 등록된 값과 다르다. 두 곳을 동일하게 맞출 것.
- **"구글드라이브 동기화" 캡션이 아예 안 보임**: `GDRIVE_SA_KEY_JSON` 또는
  `GDRIVE_FOLDER_ID` 둘 중 하나가 비어 있으면 동기화 자체가 꺼진 채로
  로컬(컨테이너 내부) DB만 본다. 재배포되면 그 로컬 DB는 사라진다.
- 나머지(서비스 계정 키 생성 오류, `storageQuotaExceeded` 등)는
  [`docs/deploy_github_actions.md`](deploy_github_actions.md)의
  "문제가 생기면" 절과 원인이 같다.
