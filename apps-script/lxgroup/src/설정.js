/* 회사(LX) 쪽 Apps Script 설정.

   API 키는 스크립트 속성이 아니라 시트 `운영설정` 탭에 있다. n8n이 그렇게
   쓰고 있었고, 스크립트가 주인 계정으로 실행되므로 그 탭을 그대로 읽으면
   된다. 코드와 저장소에는 키가 들어가지 않는다. */

const 주시트ID = '1emQDc0-bWdSIjQm61yvP2eu38JUJqx3VSzQhFQEAboI';
const 부문매핑시트ID = '1vKP1Wuzd_OQenyXhMeOctUrtdkxb47Sw8U_HDOwkyTI';
const 경쟁사폴더ID = '1QCQyD9IRK4VDcMbDpcpnmTZV2VZlLL6h';
const 시장지표폴더ID = '1hSI7BuESsF31DfxlDtvZUx9ZSFLsZ_4V';

const 탭이름 = {
  설정: '설정', 운영설정: '운영설정', 경쟁사: '경쟁사', 수집작업: '수집작업'
};

/** 운영설정 탭에서 이름으로 값 하나. 없으면 빈 글자. 값은 로그에 찍지 않는다. */
function 운영값(이름) {
  const 줄들 = 탭읽기(주시트ID, 탭이름.운영설정);
  for (let i = 0; i < 줄들.length; i++) {
    const 줄 = 줄들[i];
    const 키 = String(줄['키'] || 줄['이름'] || 줄['항목'] || '').trim();
    if (키 === 이름) return String(줄['값'] == null ? '' : 줄['값']).trim();
  }
  return '';
}

/** 식별코드 확인. n8n 조회 웹훅 넷이 쓰던 규칙 그대로다.
    설정 탭에서 식별코드가 있고 상태가 운영중이어야 한다. 순수 함수. */
function 코드쓸수있나(코드, 설정줄들) {
  const 찾는것 = String(코드 == null ? '' : 코드).trim().toUpperCase();
  if (!찾는것) return false;
  return (설정줄들 || []).some(function (줄) {
    return String(줄['식별코드'] || '').trim().toUpperCase() === 찾는것 &&
      String(줄['상태'] || '').trim() === '운영중';
  });
}

function 코드확인(코드) {
  return 코드쓸수있나(코드, 탭읽기(주시트ID, 탭이름.설정));
}
