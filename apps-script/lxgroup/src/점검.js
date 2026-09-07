const 판 = '2026-09-07 기반';

function 상태() {
  return { ok: true, 판: 판, 시각: Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss') };
}

/** 권한 허용을 한 번에 받기 위한 함수. 배포 뒤 주인이 편집기에서 한 번
    실행하고 「허용」을 누른다. 시트, 드라이브, 외부 접속, 트리거를 건드리고
    아무것도 바꾸지 않는다. */
function 권한확인() {
  const 결과 = {};
  결과.시트 = SpreadsheetApp.openById(주시트ID).getName();
  결과.드라이브폴더 = DriveApp.getFolderById(경쟁사폴더ID).getName();
  결과.외부접속 = UrlFetchApp.fetch('https://opendart.fss.or.kr/', { muteHttpExceptions: true }).getResponseCode();
  결과.트리거수 = ScriptApp.getProjectTriggers().length;
  결과.운영설정에없는것 = ['DART', 'ECOS_API_KEY', 'FRED_API_KEY', 'CUSTOMS_API_KEY', 'KOSIS_API_KEY', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_ADMIN_CHAT_ID']
    .filter(function (이름) { return !운영값(이름); });
  Logger.log(JSON.stringify(결과));
  return 결과;
}
