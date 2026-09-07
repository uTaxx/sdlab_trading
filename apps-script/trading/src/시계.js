/* 시계. n8n `AutoTrading_Schedule`(t2t8DmJ9d7yB4g4U)이 하던 일이다.

   정해진 시각에 GitHub Actions를 부른다. 여기서는 아무 판단도 하지 않는다.

   ## 어떻게 시각을 맞추나

   Apps Script의 시간 트리거는 분 단위까지만 정할 수 있고 정확한 시각을
   보장하지 않는다. 그래서 1분마다 트리거를 받아, 시간표에서 지금 분에
   해당하는 일을 찾아 부른다. 트리거가 몇 분 늦게 와도 `유예분` 안이면
   그날 아직 안 한 일로 보고 부른다. 같은 일을 두 번 부르지 않도록 그날
   한 것을 스크립트 속성에 적어 둔다.

   ## 관찰 모드

   `CLOCK_MODE`가 `실행`이 아니면 GitHub을 부르지 않는다. 대신 "예정
   09:05, 실제 09:05:07, 7초 늦음"을 `시계기록` 탭에 적는다. 최종 연결
   전에 이 표를 한 주쯤 모아서 늦는 정도를 보고 넘어갈지 정한다.

   ## 시간표는 n8n 것과 같다 (2026-09-07 기준)

   시각은 전부 한국시각이다. inputs는 n8n이 보내던 것과 글자까지 같다.
   dry_run을 "false"라는 글자로 보내는 것은 n8n이 그렇게 보내고 있었고
   그 뒤로 실거래가 그 값으로 돌았기 때문이다. 바꾸려면 워크플로 쪽과
   같이 봐야 한다. */

const 유예분 = 3;

/** 손절 감시 시각. n8n cron `0,30 9-14`와 `0 15`. 마지막이 15:00이다. */
const 손절감시시각 = (function () {
  const 목록 = [];
  for (let 시 = 9; 시 <= 14; 시++) { 목록.push(두자리(시) + ':00'); 목록.push(두자리(시) + ':30'); }
  목록.push('15:00');
  return 목록;
})();

const 시간표 = [
  { 이름: '전략 변경 반영', 요일: '평일', 시각: ['08:20'], 워크플로: 'strategy-apply.yml', 입력: null },
  { 이름: '매수 후보 산출', 요일: '평일', 시각: ['08:30'], 워크플로: 'propose-buys.yml', 입력: { dry_run: 'false' } },
  { 이름: '승인된 것만 매수', 요일: '평일', 시각: ['09:05'], 워크플로: 'execute-approved.yml', 입력: { dry_run: 'false' } },
  { 이름: '장중 손절 감시', 요일: '평일', 시각: 손절감시시각, 워크플로: 'watch-stops.yml', 입력: { dry_run: 'false' } },
  { 이름: '분봉 수집', 요일: '평일', 시각: ['15:40'], 워크플로: 'collect-intraday.yml', 입력: null },
  { 이름: '체결 정산', 요일: '평일', 시각: ['17:30'], 워크플로: 'settle-fills.yml', 입력: { apply: 'true' } },
  { 이름: '기록을 시트로', 요일: '평일', 시각: ['17:40'], 워크플로: 'push-records.yml', 입력: null },
  { 이름: '전략 검토', 요일: '평일', 시각: ['17:50'], 워크플로: 'strategy-review.yml', 입력: null },
  { 이름: '시장·섹터 리포트', 요일: '평일', 시각: ['20:00'], 워크플로: 'market-report.yml', 입력: null },
  { 이름: '기간별 전략 검증', 요일: '토요일', 시각: ['09:00'], 워크플로: 'period-check.yml', 입력: { period: '전부', strategies: '전부' } }
];

function 두자리(n) { return (n < 10 ? '0' : '') + n; }

/** 요일 이름이 그 요일에 맞나. 요일번호는 1(월)~7(일). */
function 요일맞나(요일이름, 요일번호) {
  if (요일이름 === '평일') return 요일번호 >= 1 && 요일번호 <= 5;
  if (요일이름 === '토요일') return 요일번호 === 6;
  if (요일이름 === '매일') return true;
  return false;
}

/** 지금 해야 할 일을 고른다. 순수 함수다.

    지금 = { 날짜: 'yyyy-MM-dd', 요일: 1~7, 시: 0~23, 분: 0~59 }
    이미한것 = { '날짜|시각|워크플로': '실제시각' }

    돌려주는 것은 [{ 항목, 예정시각, 열쇠, 늦은분 }]. 열쇠는 이미한것에
    적을 이름이다. 예정 시각이 지금보다 `유예분` 넘게 앞이면 그날 놓친
    것으로 보고 부르지 않는다. 09:05 매수를 09:40에 부르면 승인한 값과
    다른 값에 산다. 놓친 것은 기록에만 남긴다. */
function 할일찾기(지금, 이미한것, 표) {
  const 목록 = 표 || 시간표;
  const 지금분 = 지금.시 * 60 + 지금.분;
  const 결과 = [];
  목록.forEach(function (항목) {
    if (!요일맞나(항목.요일, 지금.요일)) return;
    항목.시각.forEach(function (시각) {
      const 조각 = 시각.split(':');
      const 예정분 = Number(조각[0]) * 60 + Number(조각[1]);
      const 차이 = 지금분 - 예정분;
      if (차이 < 0 || 차이 > 유예분) return;
      const 열쇠 = 지금.날짜 + '|' + 시각 + '|' + 항목.워크플로;
      if (이미한것 && Object.prototype.hasOwnProperty.call(이미한것, 열쇠)) return;
      결과.push({ 항목: 항목, 예정시각: 시각, 열쇠: 열쇠, 늦은분: 차이 });
    });
  });
  return 결과;
}

/** 놓친 것 찾기. 유예를 넘겨 부르지 못한 일이다. 기록에 남긴다. */
function 놓친것찾기(지금, 이미한것, 표) {
  const 목록 = 표 || 시간표;
  const 지금분 = 지금.시 * 60 + 지금.분;
  const 결과 = [];
  목록.forEach(function (항목) {
    if (!요일맞나(항목.요일, 지금.요일)) return;
    항목.시각.forEach(function (시각) {
      const 조각 = 시각.split(':');
      const 예정분 = Number(조각[0]) * 60 + Number(조각[1]);
      const 차이 = 지금분 - 예정분;
      if (차이 <= 유예분) return;
      const 열쇠 = 지금.날짜 + '|' + 시각 + '|' + 항목.워크플로;
      if (이미한것 && Object.prototype.hasOwnProperty.call(이미한것, 열쇠)) return;
      결과.push({ 항목: 항목, 예정시각: 시각, 열쇠: 열쇠, 늦은분: 차이 });
    });
  });
  return 결과;
}

/* ── 아래는 Apps Script 안에서만 도는 부분 ─────────────────────── */

const 한것속성 = 'CLOCK_DONE';

function 지금한국() {
  const d = new Date();
  const 글 = Utilities.formatDate(d, 'Asia/Seoul', 'yyyy-MM-dd|u|H|m|HH:mm:ss');
  const 조각 = 글.split('|');
  return { 날짜: 조각[0], 요일: Number(조각[1]), 시: Number(조각[2]), 분: Number(조각[3]), 시각글: 조각[4] };
}

function 한것읽기(날짜) {
  let 값 = {};
  try { 값 = JSON.parse(속성(한것속성) || '{}'); } catch (e) { 값 = {}; }
  // 오늘 것만 남긴다. 안 지우면 속성이 계속 자란다.
  const 오늘것 = {};
  Object.keys(값).forEach(function (k) { if (k.indexOf(날짜 + '|') === 0) 오늘것[k] = 값[k]; });
  return 오늘것;
}

function 한것쓰기(값) { 속성쓰기(한것속성, JSON.stringify(값)); }

/** 1분마다 트리거가 부르는 함수. */
function 매분() {
  const 지금 = 지금한국();
  const 한것 = 한것읽기(지금.날짜);
  const 모드 = 시계모드();

  놓친것찾기(지금, 한것).forEach(function (일) {
    한것[일.열쇠] = 지금.시각글;
    시계기록쓰기([지금.날짜, 일.예정시각, 지금.시각글, 일.늦은분 * 60, 일.항목.워크플로, 모드, '놓침. 유예를 넘겨 부르지 않음']);
  });

  할일찾기(지금, 한것).forEach(function (일) {
    한것[일.열쇠] = 지금.시각글;
    let 결과 = '관찰. 부르지 않음';
    if (모드 === '실행') {
      const 답 = 워크플로실행(일.항목.워크플로, 일.항목.입력);
      결과 = 답.ok ? '부름' : ('실패 ' + 답.상태 + ' ' + 답.본문);
      if (!답.ok) 텔레그램보내기('시계가 ' + 일.항목.이름 + '(' + 일.예정시각 + ')을 부르지 못했습니다. ' + 답.상태 + ' ' + 답.본문);
    }
    시계기록쓰기([지금.날짜, 일.예정시각, 지금.시각글, 늦은초(일.예정시각, 지금.시각글), 일.항목.워크플로, 모드, 결과]);
  });

  한것쓰기(한것);
}

function 늦은초(예정, 실제) {
  const a = 예정.split(':').map(Number);
  const b = 실제.split(':').map(Number);
  return (b[0] * 3600 + b[1] * 60 + (b[2] || 0)) - (a[0] * 3600 + a[1] * 60);
}

function 시계기록쓰기(줄) {
  try {
    const 문서 = SpreadsheetApp.openById(설정시트ID);
    let 탭 = 문서.getSheetByName(시계기록탭);
    if (!탭) {
      탭 = 문서.insertSheet(시계기록탭);
      탭.appendRow(['날짜', '예정시각', '실제시각', '늦은초', '워크플로', '모드', '결과']);
    }
    탭.appendRow(줄);
  } catch (e) {
    Logger.log('시계기록을 못 적었습니다: ' + e);
  }
}

/** 1분 트리거를 건다. 이미 있으면 그대로 둔다. 편집기에서 사람이 한 번 실행한다. */
function 시계설치() {
  const 있나 = ScriptApp.getProjectTriggers().some(function (t) { return t.getHandlerFunction() === '매분'; });
  if (있나) return '이미 설치돼 있습니다.';
  ScriptApp.newTrigger('매분').timeBased().everyMinutes(1).create();
  return '설치했습니다. 모드: ' + 시계모드();
}

function 시계해제() {
  let 셈 = 0;
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === '매분') { ScriptApp.deleteTrigger(t); 셈++; }
  });
  return 셈 + '개를 지웠습니다.';
}
