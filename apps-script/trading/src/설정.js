/* 매매 쪽 Apps Script의 설정.

   비밀값은 전부 스크립트 속성(Script Properties)에 있다. 코드에는 이름만
   있고 값은 없다. 이 저장소가 공개라서 그렇다. 값은 주인이 Apps Script
   편집기의 프로젝트 설정 → 스크립트 속성에서 한 번 넣는다.

   n8n에서는 같은 값이 자격증명(credential)에 있었다. 옮겨 적는 것은
   사람만 할 수 있다. 나는 n8n 자격증명 안의 값을 읽을 수 없다. */

/** 스크립트 속성 이름. 왼쪽은 코드에서 부르는 이름, 오른쪽은 속성 화면에 적는 이름이다. */
const 속성이름 = {
  깃허브토큰: 'GITHUB_TOKEN',            // sdlab_trading의 workflow_dispatch 권한(actions: write)
  텔레그램토큰: 'TELEGRAM_BOT_TOKEN',     // 무원406 봇. 뉴스 봇을 넣으면 그쪽이 멈춘다(n8n 메모).
  텔레그램방: 'TELEGRAM_CHAT_ID',
  텔레그램비밀: 'TELEGRAM_WEBHOOK_SECRET', // 웹훅 주소에 붙이는 값. Apps Script는 요청 헤더를 못 읽어서 주소에 넣는다.
  KIS앱키: 'KIS_APP_KEY',
  KIS비밀: 'KIS_APP_SECRET',
  KIS계좌: 'KIS_ACCOUNT_NO',              // 8자리
  KIS상품: 'KIS_ACCOUNT_PRODUCT',         // 보통 01
  KIS환경: 'KIS_ENV',                     // paper | real. 비어 있으면 paper.
  시계모드: 'CLOCK_MODE',                 // 관찰 | 실행. 비어 있으면 관찰.
  KIS토큰: 'KIS_TOKEN',                   // 코드가 스스로 적는다. 사람이 안 넣는다.
  KIS토큰만료: 'KIS_TOKEN_UNTIL'
};

/** 매매 설정 시트. n8n의 대시보드 자료·계좌조회가 읽던 것과 같은 파일이다. */
const 설정시트ID = '1zuQ0q4PtU6Arof1zjkn27r7932ZtGPqJcf7IoBTDS9o';
const 저장소 = 'uTaxx/sdlab_trading';

/** 시계 기록이 쌓이는 탭. 없으면 만든다. */
const 시계기록탭 = '시계기록';

function 속성(이름) {
  return String(PropertiesService.getScriptProperties().getProperty(이름) || '').trim();
}

function 속성쓰기(이름, 값) {
  PropertiesService.getScriptProperties().setProperty(이름, String(값 == null ? '' : 값));
}

/** 시계 모드. 관찰이면 아무것도 부르지 않고 "불렀을 시각"만 적는다.

    기본값이 관찰인 것이 중요하다. 이 코드는 최종 연결 전에 먼저 올라가서
    한 주쯤 트리거가 제때 오는지 재는 데 쓰인다. 그동안 GitHub을 부르면
    n8n과 두 번 부르게 된다. */
function 시계모드() {
  return 속성(속성이름.시계모드) === '실행' ? '실행' : '관찰';
}

/** 화면 인증 키. 시트 `설정` 탭의 dashboard_key 칸이다.

    n8n 계좌조회에는 시트를 못 읽었을 때 쓰는 옛 값이 코드에 박혀 있었다.
    여기서는 그 값을 옮기지 않는다. 시트를 못 읽으면 거절한다. 공개
    저장소에 인증 키를 적을 수는 없고, 막는 쪽으로 기울이는 것이 이
    저장소의 규칙이다. */
function 화면열쇠() {
  const 줄들 = 설정탭읽기();
  for (let i = 0; i < 줄들.length; i++) {
    if (String(줄들[i][0] || '').trim() === 'dashboard_key') {
      return String(줄들[i][1] == null ? '' : 줄들[i][1]).trim();
    }
  }
  return '';
}

function 설정탭읽기() {
  try {
    const 탭 = SpreadsheetApp.openById(설정시트ID).getSheetByName('설정');
    if (!탭) return [];
    return 탭.getRange(1, 1, Math.max(탭.getLastRow(), 1), 3).getValues();
  } catch (e) {
    Logger.log('설정 탭을 읽지 못했습니다: ' + e);
    return [];
  }
}

/** 열쇠 대조. 순수 함수라 Node에서 시험한다. */
function 열쇠맞나(받은것, 맞는것) {
  const ㄱ = String(받은것 == null ? '' : 받은것).trim();
  const ㄴ = String(맞는것 == null ? '' : 맞는것).trim();
  return ㄴ !== '' && ㄱ === ㄴ;
}
