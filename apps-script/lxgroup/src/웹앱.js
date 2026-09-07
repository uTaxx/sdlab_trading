/* 웹 앱 입구. 주소 하나에 `동작`으로 가른다.

   화면(hub.html)이 지금 n8n 웹훅 넷을 부르는 것을 여기로 모은다. POST
   본문은 JSON 글자이고 Content-Type은 text/plain이어야 한다. HTTP 상태
   코드는 늘 200이라 거절은 본문 `ok:false`와 `상태`로 적는다. 화면은 이미
   `d.ok === false`를 보고 있다.

   동작
     상태      누구나.
     지표·경쟁사·딜·원본   code 필요. 지금 n8n 조회 웹훅과 같은 JSON.
     수집시작  code 필요. 대상을 받아 작업을 만든다.
     진행      code 필요. 작업 상태. GET도 된다. */

const 조회처리 = {
  '지표': null,     // LXGroup_지표조회 (YpCv6bPYsEcxE4cS)
  '경쟁사': null,   // LXGroup_경쟁사_조회 (66OHWRcUSok5i0J0)
  '딜': null,       // LXGroup_딜조회 (v2YFEGynGuz0bp6g)
  '원본': null      // LXGroup_원본내려받기 (l1YMWL5bmAZUciMZ)
};

function doGet(e) {
  const 질의 = (e && e.parameter) || {};
  const 동작 = 질의.동작 || '상태';
  return 답하기(동작처리(동작, 질의, 질의));
}

function doPost(e) {
  let 몸 = {};
  try { 몸 = JSON.parse((e && e.postData && e.postData.contents) || '{}') || {}; } catch (err) { 몸 = {}; }
  const 질의 = (e && e.parameter) || {};
  const 동작 = 몸.동작 || 질의.동작 || '';
  return 답하기(동작처리(동작, 몸, 질의));
}

/** 동작을 가른다. 바깥 일은 함수로 받아 Node에서 시험한다. */
function 동작처리(동작, 몸, 질의, 바깥) {
  const 밖 = 바깥 || {
    코드확인: 코드확인, 작업만들기: 작업만들기, 작업상태: 작업상태, 상태: 상태,
    조회: function (이름, 코드, 인자) {
      const 처리 = 조회처리[이름];
      if (typeof 처리 !== 'function') return { ok: false, error: '「' + 이름 + '」 조회는 아직 Apps Script로 옮기지 않았습니다.', 상태: 501 };
      return 캐시조회(이름 + '|' + 코드 + '|' + JSON.stringify(인자 || {}), function () { return 처리(코드, 인자); });
    }
  };
  if (동작 === '상태') return 밖.상태();
  const 코드 = String(몸.code || 몸.식별코드 || 질의.code || '').trim().toUpperCase();
  const 갈래들 = ['지표', '경쟁사', '딜', '원본', '수집시작', '진행'];
  if (갈래들.indexOf(동작) < 0) return { ok: false, error: '모르는 동작입니다: ' + String(동작), 상태: 404 };
  if (!밖.코드확인(코드)) return { ok: false, error: '등록된 식별코드가 아닙니다.', 상태: 404 };
  if (동작 === '수집시작') return 밖.작업만들기(몸.대상, 코드);
  if (동작 === '진행') return 밖.작업상태(몸.작업ID || 질의.작업ID);
  const 인자 = {};
  Object.keys(몸).forEach(function (k) { if (['동작', 'code', '식별코드'].indexOf(k) < 0) 인자[k] = 몸[k]; });
  return 밖.조회(동작, 코드, 인자);
}

/* 조회 응답은 10분 캐시한다. 수집 작업이 끝나면 지운다. */
function 캐시조회(열쇠, 만들기) {
  const 캐시 = CacheService.getScriptCache();
  const 이름 = 'q:' + Utilities.base64EncodeWebSafe(Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, 열쇠));
  const 있던것 = 캐시.get(이름);
  if (있던것) { try { return JSON.parse(있던것); } catch (e) { /* 다시 만든다 */ } }
  const 값 = 만들기();
  const 글 = JSON.stringify(값);
  // 캐시 한 항목은 100KB까지다. 넘으면 그냥 매번 만든다.
  if (글.length < 100000) 캐시.put(이름, 글, 600);
  return 값;
}

function 캐시비우기() {
  // CacheService는 전체 비우기가 없다. 판 번호를 올려 옛 항목을 못 찾게 하는
  // 방식은 열쇠에 판을 섞어야 해서, 옮길 때 조회 열쇠에 `캐시판`을 넣는다.
  속성쓰기('CACHE_GEN', String(Date.now()));
}

function 속성쓰기(이름, 값) {
  PropertiesService.getScriptProperties().setProperty(이름, String(값 == null ? '' : 값));
}

function 답하기(값) {
  return ContentService.createTextOutput(JSON.stringify(값)).setMimeType(ContentService.MimeType.JSON);
}
