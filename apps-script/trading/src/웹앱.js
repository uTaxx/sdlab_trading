/* 웹 앱 입구. 주소 하나에 `동작`으로 갈라 받는다.

   POST 본문은 JSON 글자다. 화면은 Content-Type을 text/plain으로 보내야
   한다. application/json으로 보내면 브라우저가 사전 요청(OPTIONS)을 먼저
   보내는데 Apps Script는 거기에 답하지 못한다.

   HTTP 상태 코드는 늘 200이다. Apps Script 웹 앱은 상태 코드를 정할 수
   없다. 그래서 거절도 200으로 가고, 본문의 `상태` 칸에 401·403·404·501을
   적는다. 화면 쪽 `부르기()`가 지금은 HTTP 상태를 보고 있어서 최종 연결
   때 그 부분을 같이 고친다.

   동작
     상태      누구나. 살아 있는지와 판, 시각.
     계좌      열쇠 필요. 증권사 잔고를 화면 모양으로.
     자료      열쇠 필요. 무엇·인자를 자료처리로.
     텔레그램  비밀 필요. 텔레그램 갱신을 GitHub으로. */

function doGet(e) {
  const 동작 = (e && e.parameter && e.parameter.동작) || '상태';
  if (동작 === '상태') return 답하기(상태());
  return 답하기({ 오류: 'GET으로는 상태만 물을 수 있습니다.', 상태: 405 });
}

function doPost(e) {
  let 몸 = {};
  try { 몸 = JSON.parse((e && e.postData && e.postData.contents) || '{}') || {}; } catch (err) { 몸 = {}; }
  const 동작 = 몸.동작 || (e && e.parameter && e.parameter.동작) || '';
  return 답하기(동작처리(동작, 몸, (e && e.parameter) || {}));
}

/** 동작을 가른다. 순수하게 두려고 바깥 일은 전부 함수로 받는다. Node에서 시험한다. */
function 동작처리(동작, 몸, 질의, 바깥) {
  const 밖 = 바깥 || { 화면열쇠: 화면열쇠, 계좌조회: 계좌조회, 자료처리: 자료처리, 텔레그램받기: 텔레그램받기, 비밀: function () { return 속성(속성이름.텔레그램비밀); }, 상태: 상태 };
  if (동작 === '상태') return 밖.상태();
  if (동작 === '텔레그램') {
    const 비밀 = 밖.비밀();
    if (!열쇠맞나(질의.비밀, 비밀)) return { 오류: '웹훅 비밀값이 맞지 않습니다.', 상태: 403 };
    return 밖.텔레그램받기(몸);
  }
  if (동작 === '계좌' || 동작 === '자료') {
    if (!열쇠맞나(몸.열쇠 || 몸.token, 밖.화면열쇠())) return { 오류: '열쇠가 맞지 않습니다.', 상태: 403 };
    if (동작 === '계좌') {
      try { return 밖.계좌조회(); } catch (err) { return { 오류: String(err && err.message || err), 상태: 502 }; }
    }
    const 인자 = {};
    Object.keys(몸).forEach(function (k) { if (k !== '동작' && k !== '열쇠' && k !== 'token' && k !== '무엇') 인자[k] = 몸[k]; });
    return 밖.자료처리(몸.무엇, 인자);
  }
  return { 오류: '모르는 동작입니다: ' + String(동작), 상태: 404 };
}

function 답하기(값) {
  return ContentService.createTextOutput(JSON.stringify(값)).setMimeType(ContentService.MimeType.JSON);
}
