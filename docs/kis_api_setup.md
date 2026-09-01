# 한국투자증권(KIS) 개발자 API 신청 가이드

이 프로젝트는 한국투자증권 KIS Developers API로 시세 조회/주문을 처리합니다.
아래 순서대로 진행하면 됩니다. (증권사 UI는 수시로 바뀔 수 있으니, 메뉴 이름이
다르면 KIS Developers 포털 공지를 참고하세요.)

## 1. 한국투자증권 계좌 개설

- 실전투자 API를 쓰려면 한국투자증권 실계좌가 필요합니다 (비대면 계좌개설 앱
  또는 지점 방문).
- **모의투자만 먼저 시작할 계획이라면** 이 단계는 나중에 진행해도 됩니다.
  이 프로젝트의 로드맵상 Phase 2까지는 모의투자로만 검증합니다.

## 2. KIS Developers 포털 가입

1. https://apiportal.koreainvestment.com 접속
2. 보유 중인 한국투자증권 계좌/HTS 아이디로 로그인 (또는 회원가입)
3. "OAuth 인증키 신청" 또는 "나의 앱" 메뉴로 이동

## 3. 앱키/시크릿키 발급

- "앱 등록"에서 앱 이름을 지정하고 등록하면 **앱키(App Key)**와
  **시크릿키(App Secret)**가 발급됩니다.
- **실전투자용**과 **모의투자용** 키가 별도로 발급됩니다. 반드시 구분해서
  보관하세요. 이 프로젝트는 처음엔 모의투자 키만 사용합니다.

## 4. 모의투자 계좌 신청

- 한국투자증권 앱 또는 HTS에서 "모의투자" 메뉴로 모의투자 계좌를 개설합니다.
- 모의투자 계좌 개설 후, KIS Developers 포털에서 모의투자용 앱키/시크릿키를
  별도로 발급받아야 모의투자 API 호출이 가능합니다.

## 5. 발급받은 값 설정

앱키/시크릿키는 `.env`가 아니라 DB에 암호화되어 저장됩니다 (이유는
[`docs/config_architecture.md`](config_architecture.md) 참고: 나중에
대시보드에서 재시작 없이 값을 바꿀 수 있게 하기 위함입니다).

먼저 `.env`에 DB 접속 정보와 암호화 키만 설정합니다:

```bash
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 출력된 값을 .env의 MUWON_MASTER_KEY= 뒤에 채워 넣기
```

그다음 CLI로 KIS 인증정보를 저장합니다:

```bash
python scripts/configure.py kis --env paper \
    --app-key 발급받은_앱키 --app-secret 발급받은_시크릿키 \
    --account-no 계좌번호_앞8자리 --account-product-cd 01
```

`.env`와 DB 파일(`muwon.db`)은 `.gitignore`에 포함되어 있어 저장소에
커밋되지 않습니다. **절대 앱키/시크릿키를 코드나 커밋에 직접 넣지 마세요.**

## 6. 다음 단계

키 발급이 끝나면 알려주세요. Phase 1에서 `KISClient`의 시세 조회 메서드를
실제 엔드포인트로 완성하고, 발급받은 키로 인증 토큰 발급까지 검증합니다.
