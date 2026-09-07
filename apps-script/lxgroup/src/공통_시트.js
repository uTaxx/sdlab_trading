/* 시트 읽기·쓰기. 머리줄을 칸 이름으로 써서 줄을 객체로 다룬다.
   셀 단위로 쓰지 않는다. 한 번에 setValues로 쓴다. 셀 단위는 수십 배 느려서
   6분 한도에 걸린다. */

function 탭잡기(시트ID, 탭) {
  return SpreadsheetApp.openById(시트ID).getSheetByName(탭);
}

/** 머리줄이 있는 탭을 [{칸: 값}] 로. 없으면 []. */
function 탭읽기(시트ID, 탭) {
  const 시트 = 탭잡기(시트ID, 탭);
  if (!시트 || 시트.getLastRow() < 2) return [];
  const 값들 = 시트.getRange(1, 1, 시트.getLastRow(), 시트.getLastColumn()).getValues();
  return 줄객체로(값들);
}

/** 2차원 배열을 머리줄 기준 객체 목록으로. 순수 함수. */
function 줄객체로(값들) {
  if (!값들 || !값들.length) return [];
  const 머리 = 값들[0].map(function (h) { return String(h == null ? '' : h).trim(); });
  const 결과 = [];
  for (let i = 1; i < 값들.length; i++) {
    const 줄 = {};
    let 빈줄 = true;
    머리.forEach(function (h, j) {
      if (!h) return;
      const v = 값들[i][j];
      줄[h] = v;
      if (v !== '' && v != null) 빈줄 = false;
    });
    if (!빈줄) 결과.push(줄);
  }
  return 결과;
}

/** 탭이 없으면 머리줄과 함께 만든다. 있으면 그대로. */
function 탭보장(시트ID, 탭, 머리) {
  const 문서 = SpreadsheetApp.openById(시트ID);
  let 시트 = 문서.getSheetByName(탭);
  if (!시트) {
    시트 = 문서.insertSheet(탭);
    시트.getRange(1, 1, 1, 머리.length).setValues([머리]);
  }
  return 시트;
}

/** 객체 여러 줄을 머리줄 순서대로 맨 아래에 붙인다. */
function 탭덧붙이기(시트ID, 탭, 머리, 줄들) {
  if (!줄들 || !줄들.length) return 0;
  const 시트 = 탭보장(시트ID, 탭, 머리);
  const 값들 = 줄들.map(function (줄) { return 머리.map(function (h) { return 줄[h] == null ? '' : 줄[h]; }); });
  시트.getRange(시트.getLastRow() + 1, 1, 값들.length, 머리.length).setValues(값들);
  return 값들.length;
}

/** 탭 내용을 통째로 다시 쓴다. 머리줄부터. */
function 탭다시쓰기(시트ID, 탭, 머리, 줄들) {
  const 시트 = 탭보장(시트ID, 탭, 머리);
  시트.clearContents();
  const 값들 = [머리].concat((줄들 || []).map(function (줄) { return 머리.map(function (h) { return 줄[h] == null ? '' : 줄[h]; }); }));
  시트.getRange(1, 1, 값들.length, 머리.length).setValues(값들);
  return 값들.length - 1;
}
