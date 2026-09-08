/* 사람이 편집기에서 실행하는 점검 함수들. */

const 판 = '2026-09-07 기반';

function 상태() {
  return { ok: true, 판: 판, 시계모드: 시계모드(), 시각: Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss') };
}

/** 권한 허용을 한 번에 다 받기 위한 함수. 배포 뒤 주인이 편집기에서 한 번
    실행하고 「허용」을 누른다. 시트, 외부 접속, 트리거, 속성을 전부 건드려서
    나중에 다시 묻지 않게 한다. 아무것도 바꾸지 않는다. */
function 권한확인() {
  const 결과 = {};
  결과.시트 = SpreadsheetApp.openById(설정시트ID).getName();
  결과.외부접속 = UrlFetchApp.fetch('https://api.github.com/zen', { muteHttpExceptions: true }).getResponseCode();
  결과.트리거수 = ScriptApp.getProjectTriggers().length;
  결과.속성 = 빠진속성();
  Logger.log(JSON.stringify(결과));
  return 결과;
}

/** 비어 있는 속성 이름. 값은 절대 찍지 않는다. */
function 빠진속성() {
  return Object.keys(속성이름)
    .filter(function (k) { return k !== 'KIS토큰' && k !== 'KIS토큰만료' && k !== '시계모드'; })
    .map(function (k) { return 속성이름[k]; })
    .filter(function (이름) { return !속성(이름); });
}
