/* 계좌 조회. n8n `AutoTrading_계좌조회`(Ew7CMMjSThC1E42B)의 `muwon-balance`가
   하던 일이다. 화면이 지금 평가손익을 받아 간다.

   ## 토큰은 한 번 받아 20시간 쓴다

   증권사(KIS)는 토큰 발급을 자주 하면 403으로 막는다. 실제로 막혔다.
   받은 토큰을 스크립트 속성에 두고 살아 있으면 그대로 쓴다. 유효기간은
   24시간인데 20시간만 믿는다. n8n의 곳간(static data)과 같은 규칙이다.

   ## 확인하지 못한 것

   n8n은 앱키·시크릿·계좌번호를 자격증명 안에 두고 있어서 어느 값이 어느
   자리로 들어가는지 코드에서는 안 보였다. 여기서는 증권사 문서대로
   appkey·appsecret은 헤더로, 계좌번호(CANO)와 상품코드(ACNT_PRDT_CD)는
   질의로 보낸다. 첫 실제 호출에서 확인한다. */

function KIS주소() {
  return 속성(속성이름.KIS환경) === 'real'
    ? 'https://openapi.koreainvestment.com:9443'
    : 'https://openapivts.koreainvestment.com:29443';
}

function KIS잔고거래ID() {
  return 속성(속성이름.KIS환경) === 'real' ? 'TTTC8434R' : 'VTTC8434R';
}

function KIS토큰() {
  const 살았나 = 속성(속성이름.KIS토큰) && Number(속성(속성이름.KIS토큰만료) || 0) > Date.now();
  if (살았나) return 속성(속성이름.KIS토큰);
  const 답 = UrlFetchApp.fetch(KIS주소() + '/oauth2/tokenP', {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    payload: JSON.stringify({ grant_type: 'client_credentials', appkey: 속성(속성이름.KIS앱키), appsecret: 속성(속성이름.KIS비밀) })
  });
  let 본문 = {};
  try { 본문 = JSON.parse(답.getContentText() || '{}'); } catch (e) { 본문 = {}; }
  if (!본문.access_token) throw new Error('토큰을 받지 못했습니다 (' + 답.getResponseCode() + ')');
  속성쓰기(속성이름.KIS토큰, 본문.access_token);
  속성쓰기(속성이름.KIS토큰만료, Date.now() + 20 * 60 * 60 * 1000);
  return 본문.access_token;
}

function 계좌조회() {
  const 토큰 = KIS토큰();
  const 질의 = {
    CANO: 속성(속성이름.KIS계좌), ACNT_PRDT_CD: 속성(속성이름.KIS상품) || '01',
    AFHR_FLPR_YN: 'N', OFL_YN: '', INQR_DVSN: '02', UNPR_DVSN: '01', FUND_STTL_ICLD_YN: 'N',
    FNCG_AMT_AUTO_RDPT_YN: 'N', PRCS_DVSN: '00', CTX_AREA_FK100: '', CTX_AREA_NK100: ''
  };
  const 질의글 = Object.keys(질의).map(function (k) { return k + '=' + encodeURIComponent(질의[k]); }).join('&');
  const 답 = UrlFetchApp.fetch(KIS주소() + '/uapi/domestic-stock/v1/trading/inquire-balance?' + 질의글, {
    method: 'get', muteHttpExceptions: true,
    headers: {
      authorization: 'Bearer ' + 토큰, appkey: 속성(속성이름.KIS앱키), appsecret: 속성(속성이름.KIS비밀),
      tr_id: KIS잔고거래ID(), 'content-type': 'application/json; charset=utf-8'
    }
  });
  let 본문 = {};
  try { 본문 = JSON.parse(답.getContentText() || '{}'); } catch (e) { 본문 = {}; }
  return 화면모양(본문);
}

/** 증권사 응답을 화면이 쓰는 모양으로. n8n 코드 노드 `화면이 쓸 모양으로`를
    그대로 옮겼다. 순수 함수라 Node에서 시험한다. */
function 화면모양(답) {
  // 증권사는 실패해도 HTTP 200으로 답하고 rt_cd에 결과를 담는다.
  // 그냥 넘기면 화면에 0원이 뜨는데, 그것은 "돈이 없다"로 읽힌다.
  if (String(답.rt_cd || '') !== '0') {
    return { 오류: (답.msg1 || '조회 실패') + ' (' + (답.msg_cd || '') + ')' };
  }
  const 요약 = (답.output2 && 답.output2[0]) || {};
  // 수량 0으로 남아 오는 줄이 있다. 과거에 들고 있다 판 종목이다.
  const 종목 = (답.output1 || [])
    .filter(function (줄) { return Number(줄.hldg_qty || 0) > 0; })
    .map(function (줄) {
      return {
        종목: (줄.prdt_name || 줄.pdno) + '(' + 줄.pdno + ')',
        평가손익: Number(줄.evlu_pfls_amt || 0),
        수량: Number(줄.hldg_qty || 0),
        평균매입가: Number(줄.pchs_avg_pric || 0),
        현재가: Number(줄.prpr || 0),
        평가금액: Number(줄.evlu_amt || 0)
      };
    });
  // 현금은 가수도정산금액을 쓴다. 결제(T+2)까지 반영된 값이라 오늘 낸
  // 주문이 이미 빠져 있다. 예수금 총액을 쓰면 오늘 산 것을 이틀 동안 못 본다.
  const 현금 = Number(요약.prvs_rcdl_excc_amt || 요약.dnca_tot_amt || 0);
  return {
    현금: 현금,
    순자산: Number(요약.nass_amt || 0),
    원가: Number(요약.pchs_amt_smtl_amt || 0),
    평가손익: 종목.reduce(function (합, 줄) { return 합 + 줄.평가손익; }, 0),
    조회시각: 조회시각글(),
    종목: 종목
  };
}

function 조회시각글() {
  if (typeof Utilities !== 'undefined' && Utilities.formatDate) {
    return Utilities.formatDate(new Date(), 'Asia/Seoul', 'a h:mm:ss').replace('AM', '오전').replace('PM', '오후');
  }
  return new Date().toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul' });
}
