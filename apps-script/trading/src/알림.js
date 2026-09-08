/* 관리자 텔레그램으로 한 줄 보내기. 못 보내도 예외를 내지 않는다.
   알림 실패가 본래 일의 실패를 가리면 안 된다. 대신 로그에 남긴다. */
function 텔레그램보내기(글) {
  const 방 = 속성(속성이름.텔레그램방);
  if (!방) { Logger.log('TELEGRAM_CHAT_ID가 비어 있어 알림을 못 보냈습니다: ' + 글); return false; }
  const 답 = 텔레그램API('sendMessage', { chat_id: 방, text: String(글).slice(0, 3900) });
  return Boolean(답 && 답.ok);
}
