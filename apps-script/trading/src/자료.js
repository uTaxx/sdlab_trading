/* 화면 자료 창구. n8n `무원406 대시보드 자료`(wj3pZvpHJPekTaoI)의 `sdlab`이
   하던 일이다. 노드 40개짜리라 기반 단계에서는 옮기지 않았다.

   화면(dashboard/app.js)이 `무엇`으로 보내는 값은 아래와 같다. 읽는 쪽은
   n8n 워크플로를 열어 갈래별로 옮길 때 채운다. 갈래를 하나 옮길 때마다
   `옮긴것`에 넣는다. 안 옮긴 갈래를 부르면 501과 까닭을 돌려준다. 화면이
   조용히 빈 표를 그리게 두지 않는다. */

const 자료갈래 = {
  '승인목록': null, '기록': null, '기준': null,
  '승인': null, '기준저장': null, '전략변경': null, '전략예약': null,
  '기간검증실행': null, '상한측정실행': null
};

function 자료처리(무엇, 인자) {
  const 이름 = String(무엇 || '').trim();
  if (!Object.prototype.hasOwnProperty.call(자료갈래, 이름)) {
    return { 오류: '모르는 항목입니다: ' + 이름, 상태: 400 };
  }
  const 처리 = 자료갈래[이름];
  if (typeof 처리 !== 'function') {
    return { 오류: '아직 Apps Script로 옮기지 않은 항목입니다: ' + 이름, 상태: 501 };
  }
  return 처리(인자 || {});
}
