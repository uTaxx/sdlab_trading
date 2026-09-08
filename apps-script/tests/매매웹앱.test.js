const test = require('node:test');
const assert = require('node:assert/strict');
const { 불러오기, 값 } = require('./gas');

const 파일들 = ['설정.js', '계좌.js', '자료.js', '웹앱.js', '점검.js'];

function 바깥(덮기) {
  return Object.assign({
    화면열쇠: () => 'KEY-1',
    계좌조회: () => ({ 현금: 1 }),
    자료처리: (무엇) => ({ 받은것: 무엇 }),
    텔레그램받기: (몸) => ({ ok: true, 받은것: 몸 }),
    비밀: () => 'S',
    상태: () => ({ ok: true })
  }, 덮기 || {});
}

test('열쇠가 틀리면 403 모양으로 거절한다', () => {
  const g = 불러오기('trading', 파일들);
  const 답 = g.동작처리('계좌', { 열쇠: 'nope' }, {}, 바깥());
  assert.equal(답.상태, 403);
});

test('시트에 열쇠가 없으면 무엇을 보내도 거절한다. 옛 값으로 돌아가지 않는다', () => {
  const g = 불러오기('trading', 파일들);
  const 답 = g.동작처리('계좌', { 열쇠: '' }, {}, 바깥({ 화면열쇠: () => '' }));
  assert.equal(답.상태, 403);
});

test('열쇠가 맞으면 계좌와 자료를 넘긴다', () => {
  const g = 불러오기('trading', 파일들);
  assert.deepEqual(값(g.동작처리('계좌', { 열쇠: 'KEY-1' }, {}, 바깥())), { 현금: 1 });
  assert.deepEqual(값(g.동작처리('자료', { 열쇠: 'KEY-1', 무엇: '기준' }, {}, 바깥())), { 받은것: '기준' });
});

test('텔레그램은 주소의 비밀값을 본다', () => {
  const g = 불러오기('trading', 파일들);
  assert.equal(g.동작처리('텔레그램', { update_id: 1 }, { 비밀: 'X' }, 바깥()).상태, 403);
  assert.equal(g.동작처리('텔레그램', { update_id: 1 }, { 비밀: 'S' }, 바깥()).ok, true);
});

test('아직 안 옮긴 자료 갈래는 501과 까닭을 준다', () => {
  const g = 불러오기('trading', 파일들);
  const 답 = g.자료처리('승인목록', {});
  assert.equal(답.상태, 501);
  assert.match(답.오류, /아직/);
  assert.equal(g.자료처리('없는것', {}).상태, 400);
});

test('증권사 응답을 화면 모양으로 바꾼다. n8n 코드 노드와 같은 규칙', () => {
  const g = 불러오기('trading', 파일들);
  const 답 = g.화면모양({
    rt_cd: '0',
    output1: [
      { prdt_name: '삼성전자', pdno: '005930', hldg_qty: '10', evlu_pfls_amt: '1500', pchs_avg_pric: '70000', prpr: '70150', evlu_amt: '701500' },
      { prdt_name: '판것', pdno: '000000', hldg_qty: '0', evlu_pfls_amt: '999' }
    ],
    output2: [{ prvs_rcdl_excc_amt: '5000000', dnca_tot_amt: '9999999', nass_amt: '5701500', pchs_amt_smtl_amt: '700000' }]
  });
  assert.equal(답.현금, 5000000);
  assert.equal(답.종목.length, 1);
  assert.equal(답.평가손익, 1500);
  assert.equal(답.종목[0].종목, '삼성전자(005930)');
});

test('증권사가 실패를 200으로 주면 0원이 아니라 오류로 만든다', () => {
  const g = 불러오기('trading', 파일들);
  const 답 = g.화면모양({ rt_cd: '1', msg1: '토큰 만료', msg_cd: 'EGW00123' });
  assert.match(답.오류, /토큰 만료/);
  assert.equal(답.현금, undefined);
});
