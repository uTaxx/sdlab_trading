/* 관리자 텔레그램 알림. 봇 토큰과 대화방은 시트 `운영설정` 탭의
   TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_CHAT_ID 줄이다. 주인이 넣어야 한다.
   못 보내도 예외를 내지 않는다. 작업 상태는 이미 시트에 남아 있다. */
function 텔레그램보내기(글) {
  const 토큰 = 운영값('TELEGRAM_BOT_TOKEN');
  const 방 = 운영값('TELEGRAM_ADMIN_CHAT_ID');
  if (!토큰 || !방) { Logger.log('텔레그램 설정이 비어 있어 알림을 못 보냈습니다.'); return false; }
  try {
    const 답 = UrlFetchApp.fetch('https://api.telegram.org/bot' + 토큰 + '/sendMessage', {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      payload: JSON.stringify({ chat_id: 방, text: String(글).slice(0, 3900) })
    });
    return 답.getResponseCode() === 200;
  } catch (e) {
    Logger.log('텔레그램 전송 실패: ' + e);
    return false;
  }
}
