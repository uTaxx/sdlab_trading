/* 텔레그램 받기. n8n `AutoTrading_Telegram`(7tSSThvQIsLiIsLk)이 하던 일이다.

   버튼을 누르거나 명령을 보내면 받아서 GitHub Actions `telegram-n8n.yml`로
   그대로 넘긴다. 여기서는 아무 판단도 하지 않는다. 버튼이면 "받았습니다"
   만 답한다. 안 하면 버튼이 도는 표시로 남아 사람이 또 누른다.

   ## 연결은 최종 단계에서만 한다

   텔레그램 봇의 웹훅을 이 웹 앱 주소로 바꾸는 순간(setWebhook) n8n의
   텔레그램 트리거는 더 못 받는다. 그래서 `텔레그램웹훅걸기()`는 사람이
   편집기에서 직접 실행할 때만 돈다. 기반 단계에서는 부르지 않는다.

   ## 비밀값은 주소에 넣는다

   Apps Script의 doPost는 요청 헤더를 못 읽는다. 텔레그램이 주는
   X-Telegram-Bot-Api-Secret-Token 헤더를 확인할 수 없어서, 웹훅 주소에
   `?비밀=...`을 붙여 두고 그 값을 대조한다. 이 값은 스크립트 속성
   TELEGRAM_WEBHOOK_SECRET이다. */

function 텔레그램받기(갱신) {
  if (!갱신 || typeof 갱신 !== 'object') return { ok: false, 오류: '텔레그램 갱신이 비어 있습니다.' };
  if (갱신.callback_query && 갱신.callback_query.id) {
    텔레그램API('answerCallbackQuery', { callback_query_id: 갱신.callback_query.id, text: '받았습니다. 잠시 뒤 반영됩니다' });
  }
  const 답 = 워크플로실행('telegram-n8n.yml', { payload: JSON.stringify(갱신) });
  if (!답.ok) {
    텔레그램보내기('텔레그램 명령을 GitHub으로 넘기지 못했습니다. ' + 답.상태 + ' ' + 답.본문);
  }
  return { ok: 답.ok, 상태: 답.상태 };
}

function 텔레그램API(방법, 몸) {
  const 토큰 = 속성(속성이름.텔레그램토큰);
  if (!토큰) { Logger.log('TELEGRAM_BOT_TOKEN이 비어 있습니다.'); return null; }
  try {
    const 답 = UrlFetchApp.fetch('https://api.telegram.org/bot' + 토큰 + '/' + 방법, {
      method: 'post', contentType: 'application/json', payload: JSON.stringify(몸), muteHttpExceptions: true
    });
    return JSON.parse(답.getContentText() || 'null');
  } catch (e) {
    Logger.log('텔레그램 ' + 방법 + ' 실패: ' + e);
    return null;
  }
}

/** 최종 연결 때 사람이 편집기에서 실행한다. 웹 앱 배포 주소를 넣어야 한다. */
function 텔레그램웹훅걸기(웹앱주소) {
  if (!웹앱주소) throw new Error('웹 앱 주소를 넣어야 합니다. https://script.google.com/macros/s/<배포ID>/exec');
  const 비밀 = 속성(속성이름.텔레그램비밀);
  if (!비밀) throw new Error('TELEGRAM_WEBHOOK_SECRET 속성이 비어 있습니다. 먼저 넣으세요.');
  const 주소 = 웹앱주소 + '?동작=텔레그램&비밀=' + encodeURIComponent(비밀);
  return 텔레그램API('setWebhook', { url: 주소, allowed_updates: ['message', 'callback_query'] });
}

function 텔레그램웹훅풀기() {
  return 텔레그램API('deleteWebhook', {});
}
