/* 바깥 API 호출. DART·ECOS·FRED·관세청·KOSIS·KCCI가 전부 이 함수를 지난다.

   키는 운영설정 탭에서 읽는다. 로그에 주소를 찍을 때는 키를 가린다.
   Apps Script 실행 기록은 GitHub에 안 나오지만 같은 규칙을 지킨다. */

function 키(이름) {
  const 값 = 운영값(이름);
  if (!값) throw new Error('운영설정 탭에 ' + 이름 + ' 값이 없습니다.');
  return 값;
}

/** GET으로 받아 글자로. 5xx와 시간 초과는 두 번까지 다시 보낸다. */
function 받기(주소, 옵션) {
  const 설정 = 옵션 || {};
  let 마지막오류 = '';
  for (let 시도 = 0; 시도 < 3; 시도++) {
    try {
      const 답 = UrlFetchApp.fetch(주소, {
        method: 설정.method || 'get', muteHttpExceptions: true,
        headers: 설정.headers || {}, payload: 설정.payload, contentType: 설정.contentType
      });
      const 상태 = 답.getResponseCode();
      if (상태 >= 200 && 상태 < 300) return 설정.이진 ? 답.getBlob() : 답.getContentText();
      마지막오류 = 'HTTP ' + 상태;
      if (상태 < 500) break;
    } catch (e) {
      마지막오류 = String(e);
    }
    Utilities.sleep(1500 * (시도 + 1));
  }
  throw new Error('받지 못했습니다: ' + 주소가리기(주소) + ' · ' + 마지막오류);
}

function 받기JSON(주소, 옵션) {
  return JSON.parse(받기(주소, 옵션) || 'null');
}

/** 주소 안의 키 값을 가린다. 순수 함수. */
function 주소가리기(주소) {
  return String(주소).replace(/((?:crtfc_key|api_key|apiKey|serviceKey|authKey|key)=)[^&]+/gi, '$1(가림)');
}

/** DART 공시 목록 주소. corp_code가 없으면 기간만으로 부른다(딜 공시). */
function DART목록주소(인자) {
  const 조각 = ['crtfc_key=' + encodeURIComponent(키('DART'))];
  Object.keys(인자 || {}).forEach(function (k) {
    if (인자[k] != null && 인자[k] !== '') 조각.push(k + '=' + encodeURIComponent(인자[k]));
  });
  return 'https://opendart.fss.or.kr/api/list.json?' + 조각.join('&');
}
