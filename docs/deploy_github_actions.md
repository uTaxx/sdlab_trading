# GitHub Actions + 구글드라이브로 매일 자동매매 실행하기

PC를 계속 켜둘 필요 없이, 매일 장마감 후(15:30 KST) GitHub Actions가 자동으로
`scripts/run_paper_trading.py`를 실행한다. 보유 종목·가상현금 상태는
프로세스가 매번 새로 뜨는 GitHub Actions 특성상 로컬 디스크에 못 남기므로,
`muwon.db` 파일을 구글드라이브에 두고 실행 시작/종료마다 내려받고 올린다.

이 문서는 **사람이 웹 콘솔에서 직접 해야 하는 설정**을 순서대로 안내한다.
코드는 이미 다 준비되어 있다 (`scripts/gdrive_sync.py`,
`.github/workflows/paper-trading.yml`).

## 1. 구글 클라우드 서비스 계정 만들기

서비스 계정은 "사람이 로그인하는 계정"이 아니라 "프로그램이 쓰는 전용 계정"이다.
GitHub Actions는 사람이 브라우저로 로그인할 수 없으니, 이게 필요하다.

1. https://console.cloud.google.com 접속 (구글 계정으로 로그인)
2. 상단 프로젝트 선택 드롭다운 → **새 프로젝트** → 이름 아무거나(예: `muwon406`) → 만들기
3. 만든 프로젝트가 선택된 상태에서, 좌측 상단 ☰ 메뉴 → **API 및 서비스** → **라이브러리**
4. 검색창에 `Google Drive API` 입력 → 클릭 → **사용** 버튼
5. 좌측 ☰ 메뉴 → **IAM 및 관리자** → **서비스 계정** → 상단 **+ 서비스 계정 만들기**
6. 이름 아무거나(예: `muwon406-bot`) 입력 → **만들고 계속하기** → 역할은 건너뛰어도 됨 → **완료**
7. 방금 만든 서비스 계정 클릭 → **키** 탭 → **키 추가** → **새 키 만들기** → **JSON** 선택 → 만들기
   → JSON 파일이 자동으로 다운로드된다. **이 파일을 잘 보관할 것** (나중에 GitHub Secrets에 붙여넣음)
8. 서비스 계정 이메일 주소를 복사해둘 것: `xxx@muwon406.iam.gserviceaccount.com` 같은 형식
   (서비스 계정 목록 페이지에 표시됨)

## 2. 구글드라이브 "공유 드라이브" 만들고 서비스 계정 추가

**일반 "내 드라이브" 폴더로는 안 된다**. 서비스 계정은 자체 저장 공간이
없어서, 개인 폴더에 새 파일을 만들려고 하면 `storageQuotaExceeded` 오류로
막힌다. 구글 워크스페이스 계정이면 파일 소유권이 개인이 아니라 드라이브
자체에 귀속되는 **공유 드라이브(Shared Drive)** 를 쓸 수 있다 (개인 무료
Gmail 계정은 공유 드라이브를 만들 수 없다. 그 경우 워크스페이스 계정으로
진행할 것).

1. https://drive.google.com 접속 → 왼쪽 메뉴 **공유 드라이브** → 상단 **+ 새로 만들기**
2. 이름 아무거나 (예: `muwon406-state`) → 만들기
3. 만든 공유 드라이브 열기 → 우측 상단 사람 추가 아이콘(또는 드라이브 이름 옆 점 세 개 → **회원 관리**)
4. 1번에서 복사한 서비스 계정 이메일 주소 추가 → 권한 **콘텐츠 관리자(Content manager)** 이상 → 보내기
5. 그 공유 드라이브를 연 상태에서 주소창 URL을 본다:
   `https://drive.google.com/drive/folders/`**`이 뒤의 긴 문자열`**
   이 문자열이 "폴더 ID"(공유 드라이브 ID와 동일)다. 복사해둘 것

## 3. GitHub Secrets 등록

`https://github.com/uTaxx/sdlab_trading/settings/secrets/actions` 접속 →
**New repository secret**으로 아래 8개를 하나씩 추가.

| Secret 이름 | 값 |
|---|---|
| `MUWON_MASTER_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`로 새로 생성한 값 (기존에 쓰던 게 있으면 그걸로) |
| `KIS_APP_KEY` | KIS 모의투자 앱키 |
| `KIS_APP_SECRET` | KIS 모의투자 시크릿키 |
| `KIS_ACCOUNT_NO` | KIS 모의투자 계좌번호 (앞 8자리) |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇토큰 |
| `TELEGRAM_CHAT_ID` | 텔레그램 chat ID |
| `GDRIVE_SA_KEY_JSON` | 1번에서 다운로드한 JSON 키 파일을 **텍스트 에디터로 열어서 내용 전체**를 그대로 붙여넣기 |
| `GDRIVE_FOLDER_ID` | 2번에서 복사한 폴더 ID |

## 4. 첫 실행 확인

1. https://github.com/uTaxx/sdlab_trading/actions/workflows/paper-trading.yml 접속
2. **Run workflow** 버튼 → 브랜치 확인 → **Run workflow**
3. 몇 분 뒤 실행 결과 확인: 초록 체크면 성공. 빨간 X면 로그를 열어서 어느 단계에서 실패했는지 확인

첫 실행에선 구글드라이브에 `muwon.db`가 없어서 "새 상태로 시작합니다"라고 뜨는 게 정상이다.
그 다음부턴 매번 이어서 상태가 쌓인다.

## 5. 그 다음부턴

평일 15:30 KST(06:30 UTC)에 자동으로 돈다. 별도로 할 일 없음.

**리스크 정책(종목당 비중, 손절선, 자동매매 on/off 등)을 바꾸고 싶으면**:
대시보드가 이제 구글드라이브를 직접 보고/올린다.
[`docs/deploy_streamlit_cloud.md`](deploy_streamlit_cloud.md)대로 한 번
배포해두면, 폰이든 PC든 그 화면에서 바로 바꾸면 된다(로컬로 내려받았다
다시 올리는 수동 과정 불필요). 로컬에서 잠깐 확인만 하고 싶을 때는
아래처럼 직접 내려받아도 된다:

```bash
python scripts/gdrive_sync.py download --folder-id <폴더ID> --filename muwon.db --out ./muwon.db
streamlit run src/muwon/dashboard/app.py   # 값 확인/수정
python scripts/gdrive_sync.py upload --folder-id <폴더ID> --filename muwon.db --path ./muwon.db
```

(`GDRIVE_SA_KEY_JSON`, `MUWON_MASTER_KEY`를 로컬 `.env`/환경변수에도 설정해야 함.)

## 문제가 생기면

- **1번(서비스 계정 키 만들기)에서 "서비스 계정 키 생성 사용 중지됨" 오류**:
  구글 워크스페이스 조직 정책(`iam.managed.disableServiceAccountKeyCreation`)이
  자동으로 걸려 있는 경우다. 조직 정책 관리자 권한이 있으면
  `https://console.cloud.google.com/iam-admin/orgpolicies/iam-managed-disableServiceAccountKeyCreation?project=<프로젝트ID>`
  에서 "상위 정책 재정의" → 규칙을 "사용 안함"으로 바꿔서 이 프로젝트만 예외
  처리한다. 권한이 없으면 조직에 속하지 않은 개인 Gmail 계정으로 새 프로젝트를
  만드는 게 더 빠르다.
- **`storageQuotaExceeded` 오류**: 일반 "내 드라이브" 폴더를 썼을 때 나는
  오류다. 2번처럼 공유 드라이브를 써야 한다.
- **워크플로우가 KIS 접속 단계에서 `403 Forbidden`**: 서버까지는 도달했지만
  인증이 거부된 것이다. 십중팔구 **모의투자용이 아니라 실전투자용 앱키를
  넣은 경우**다. KIS Developers 포털에서 모의투자 전용으로 발급받은
  앱키/시크릿인지 다시 확인할 것 (실전투자 키와는 완전히 별개로 발급됨).
- **구글드라이브 단계에서 실패**: 서비스 계정 이메일이 공유 드라이브에
  콘텐츠 관리자 이상으로 추가돼 있는지, `GDRIVE_SA_KEY_JSON`에 JSON 전체가
  (따옴표 손상 없이) 들어갔는지 확인.
