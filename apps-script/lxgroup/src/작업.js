/* 수집 작업. 화면의 「업데이트」 한 번이 작업 한 줄이다.

   `수집작업` 탭에 줄을 만들고, 1초 뒤 트리거로 `작업진행`을 부른다.
   작업진행은 잠금을 잡고 다음 단계를 한 조각(조각초 안) 실행한 뒤 상태를
   적고, 남았으면 다시 1초 뒤 트리거를 건다. 6분 한도에 걸려 강제 종료되면
   어디까지 했는지 모르게 되므로 4분에서 멈춘다.

   상태는 대기 · 진행중 · 완료 · 실패 넷이다. 실패하면 남은 단계는
   실행하지 않는다. 반쯤 된 상태로 마스터를 다시 쓰는 것보다 멈추는 쪽이
   낫다. */

const 작업머리 = ['작업ID', '요청시각', '요청코드', '대상', '상태', '현재단계', '진행글', '시작시각', '끝시각', '오류'];
const 조각초 = 240;
const 대상순서 = ['지표', '경쟁사', '딜'];

function 지금글() { return Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss'); }

/** 대상 목록을 정리한다. 순수 함수. 모르는 이름은 버리고 순서를 맞춘다. */
function 대상정리(대상) {
  let 목록 = 대상;
  if (typeof 목록 === 'string') 목록 = 목록.split(',');
  if (!Array.isArray(목록) || !목록.length) 목록 = 대상순서.slice();
  const 정리 = 목록.map(function (x) { return String(x || '').trim(); });
  return 대상순서.filter(function (x) { return 정리.indexOf(x) >= 0; });
}

/** 작업의 다음 단계 번호를 정한다. 순수 함수.
    현재단계는 "대상/단계이름" 또는 빈 글자. 끝났으면 null. */
function 다음단계(작업, 목록) {
  const 표 = 목록 || 단계목록;
  const 대상들 = 대상정리(작업.대상);
  const 전부 = [];
  대상들.forEach(function (대상) {
    (표[대상] || []).forEach(function (단계) { 전부.push({ 대상: 대상, 이름: 단계.이름, 실행: 단계.실행, 열쇠: 대상 + '/' + 단계.이름 }); });
  });
  if (!전부.length) return null;
  const 현재 = String(작업.현재단계 || '').trim();
  if (!현재) return 전부[0];
  const 자리 = 전부.map(function (x) { return x.열쇠; }).indexOf(현재);
  if (자리 < 0) return 전부[0];
  return 작업.단계끝났나 ? (전부[자리 + 1] || null) : 전부[자리];
}

/** 진행 중이거나 기다리는 작업이 있으면 그것을, 없으면 null. */
function 열린작업() {
  const 줄들 = 탭읽기(주시트ID, 탭이름.수집작업);
  for (let i = 줄들.length - 1; i >= 0; i--) {
    const 상태 = String(줄들[i]['상태'] || '');
    if (상태 === '대기' || 상태 === '진행중') return 줄들[i];
  }
  return null;
}

function 작업찾기(작업ID) {
  const 줄들 = 탭읽기(주시트ID, 탭이름.수집작업);
  for (let i = 줄들.length - 1; i >= 0; i--) {
    if (String(줄들[i]['작업ID']) === String(작업ID)) return 줄들[i];
  }
  return null;
}

/** 작업 줄의 칸 몇 개를 고친다. 작업ID로 찾는다. */
function 작업고치기(작업ID, 바꿀것) {
  const 시트 = 탭보장(주시트ID, 탭이름.수집작업, 작업머리);
  const 마지막 = 시트.getLastRow();
  if (마지막 < 2) return false;
  const ID들 = 시트.getRange(2, 1, 마지막 - 1, 1).getValues();
  for (let i = ID들.length - 1; i >= 0; i--) {
    if (String(ID들[i][0]) === String(작업ID)) {
      const 행 = i + 2;
      Object.keys(바꿀것).forEach(function (칸) {
        const 열 = 작업머리.indexOf(칸) + 1;
        if (열 > 0) 시트.getRange(행, 열).setValue(바꿀것[칸]);
      });
      return true;
    }
  }
  return false;
}

/** 화면의 수집시작. 열린 작업이 있으면 새로 만들지 않고 그 ID를 준다. */
function 작업만들기(대상, 요청코드) {
  const 열린것 = 열린작업();
  if (열린것) return { ok: true, 작업ID: String(열린것['작업ID']), 이미있음: true, 상태: String(열린것['상태']) };
  const 목록 = 대상정리(대상);
  if (!목록.length) return { ok: false, error: '대상은 지표, 경쟁사, 딜 중에서 골라야 합니다.', 상태: 400 };
  const 작업ID = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyyMMdd-HHmmss');
  탭덧붙이기(주시트ID, 탭이름.수집작업, 작업머리, [{
    작업ID: 작업ID, 요청시각: 지금글(), 요청코드: String(요청코드 || '').toUpperCase(), 대상: 목록.join(','),
    상태: '대기', 현재단계: '', 진행글: '기다리는 중', 시작시각: '', 끝시각: '', 오류: ''
  }]);
  다음조각예약();
  return { ok: true, 작업ID: 작업ID, 이미있음: false, 상태: '대기' };
}

/** 화면의 진행 조회. */
function 작업상태(작업ID) {
  const 작업 = 작업ID ? 작업찾기(작업ID) : 열린작업();
  if (!작업) return { ok: false, error: '그런 작업이 없습니다.', 상태: 404 };
  return {
    ok: true, 작업ID: String(작업['작업ID']), 상태: String(작업['상태']), 대상: String(작업['대상']),
    현재단계: String(작업['현재단계'] || ''), 진행글: String(작업['진행글'] || ''),
    시작: String(작업['시작시각'] || ''), 끝: String(작업['끝시각'] || ''), 오류: String(작업['오류'] || '')
  };
}

/* ── 트리거 ─────────────────────────────────────────────────── */

function 진행트리거지우기() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === '작업진행') ScriptApp.deleteTrigger(t);
  });
}

function 다음조각예약() {
  진행트리거지우기();
  ScriptApp.newTrigger('작업진행').timeBased().after(1000).create();
}

/** 트리거가 부르는 함수. 한 조각을 실행한다. */
function 작업진행() {
  진행트리거지우기();
  const 잠금 = LockService.getScriptLock();
  if (!잠금.tryLock(5000)) return;
  try {
    const 작업 = 열린작업();
    if (!작업) return;
    const 시작 = Date.now();
    const 문맥 = {
      시간남았나: function () { return (Date.now() - 시작) / 1000 < 조각초; },
      진행쓰기: function (글) { 작업고치기(작업['작업ID'], { 진행글: 글 }); }
    };
    if (String(작업['상태']) === '대기') {
      작업고치기(작업['작업ID'], { 상태: '진행중', 시작시각: 지금글() });
    }
    // 이 조각 안에서 여러 단계를 이어 간다. 시간이 남는 동안만.
    let 단계끝났나 = false;
    let 현재 = String(작업['현재단계'] || '');
    while (문맥.시간남았나()) {
      const 다음 = 다음단계({ 대상: 작업['대상'], 현재단계: 현재, 단계끝났나: 단계끝났나 });
      if (!다음) {
        작업고치기(작업['작업ID'], { 상태: '완료', 끝시각: 지금글(), 진행글: '끝', 현재단계: 현재 });
        캐시비우기();
        return;
      }
      현재 = 다음.열쇠;
      작업고치기(작업['작업ID'], { 현재단계: 현재, 진행글: 다음.대상 + ' · ' + 다음.이름 + ' 시작' });
      let 결과;
      try {
        결과 = 다음.실행(문맥);
      } catch (e) {
        const 까닭 = 다음.대상 + ' · ' + 다음.이름 + ' 단계에서 실패: ' + String(e && e.message || e);
        작업고치기(작업['작업ID'], { 상태: '실패', 끝시각: 지금글(), 오류: 까닭, 진행글: '실패' });
        텔레그램보내기('LX 수집 작업 ' + 작업['작업ID'] + '이(가) 실패했습니다. ' + 까닭);
        return;
      }
      단계끝났나 = Boolean(결과 && 결과.끝났나);
      작업고치기(작업['작업ID'], { 진행글: (결과 && 결과.진행글) || (다음.이름 + (단계끝났나 ? ' 끝' : ' 진행 중')) });
      if (!단계끝났나) break;
    }
    // 시간이 다 됐거나 단계가 덜 끝났다. 다음 조각을 건다.
    다음조각예약();
  } finally {
    잠금.releaseLock();
  }
}
