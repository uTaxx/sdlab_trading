const test = require('node:test');
const assert = require('node:assert/strict');
const { 불러오기, 값 } = require('./gas');

const 파일들 = ['설정.js', '공통_시트.js', '단계.js', '작업.js', '웹앱.js', '점검.js'];

test('식별코드는 설정 탭에서 상태가 운영중일 때만 통과한다', () => {
  const g = 불러오기('lxgroup', 파일들);
  const 줄들 = [{ 식별코드: 'LXHOLDINGS', 상태: '운영중' }, { 식별코드: 'OLD', 상태: '중지' }];
  assert.equal(g.코드쓸수있나('lxholdings', 줄들), true);
  assert.equal(g.코드쓸수있나('OLD', 줄들), false);
  assert.equal(g.코드쓸수있나('', 줄들), false);
  assert.equal(g.코드쓸수있나('X', []), false);
});

test('머리줄로 줄을 객체로 만들고 빈 줄은 버린다', () => {
  const g = 불러오기('lxgroup', 파일들);
  const 줄 = g.줄객체로([['회사명', '사용', ''], ['A', 'Y', 'x'], ['', '', ''], ['B', '', '']]);
  assert.deepEqual(값(줄), [{ 회사명: 'A', 사용: 'Y' }, { 회사명: 'B', 사용: '' }]);
});

test('대상은 정해진 순서로 정리되고 모르는 이름은 버린다', () => {
  const g = 불러오기('lxgroup', 파일들);
  assert.deepEqual(값(g.대상정리(['딜', '엉뚱', '지표'])), ['지표', '딜']);
  assert.deepEqual(값(g.대상정리('경쟁사,지표')), ['지표', '경쟁사']);
  assert.deepEqual(값(g.대상정리([])), ['지표', '경쟁사', '딜']);
});

test('다음 단계는 대상 순서와 단계 순서를 따라간다', () => {
  const g = 불러오기('lxgroup', 파일들);
  const 표 = { '지표': [{ 이름: 'a' }, { 이름: 'b' }], '딜': [{ 이름: 'c' }] };
  assert.equal(g.다음단계({ 대상: '지표,딜', 현재단계: '' }, 표).열쇠, '지표/a');
  assert.equal(g.다음단계({ 대상: '지표,딜', 현재단계: '지표/a', 단계끝났나: false }, 표).열쇠, '지표/a');
  assert.equal(g.다음단계({ 대상: '지표,딜', 현재단계: '지표/a', 단계끝났나: true }, 표).열쇠, '지표/b');
  assert.equal(g.다음단계({ 대상: '지표,딜', 현재단계: '지표/b', 단계끝났나: true }, 표).열쇠, '딜/c');
  assert.equal(g.다음단계({ 대상: '지표,딜', 현재단계: '딜/c', 단계끝났나: true }, 표), null);
});

test('등록된 단계는 아직 옮기지 않았다고 분명히 실패한다', () => {
  const g = 불러오기('lxgroup', 파일들);
  Object.keys(g.단계목록).forEach((대상) => {
    g.단계목록[대상].forEach((단계) => {
      assert.throws(() => 단계.실행({}), /아직 Apps Script로 옮기지 않았습니다/, 대상 + '/' + 단계.이름);
    });
  });
});

test('웹 앱은 코드가 없으면 거절하고, 있으면 수집시작과 진행을 넘긴다', () => {
  const g = 불러오기('lxgroup', 파일들);
  const 밖 = {
    코드확인: (c) => c === 'LXHOLDINGS',
    작업만들기: (대상, 코드) => ({ ok: true, 대상, 코드 }),
    작업상태: (id) => ({ ok: true, id }),
    상태: () => ({ ok: true }),
    조회: (이름) => ({ ok: true, 이름 })
  };
  assert.equal(g.동작처리('지표', { code: 'nope' }, {}, 밖).상태, 404);
  assert.equal(g.동작처리('엉뚱', { code: 'LXHOLDINGS' }, {}, 밖).상태, 404);
  assert.deepEqual(값(g.동작처리('수집시작', { code: 'lxholdings', 대상: ['지표'] }, {}, 밖)), { ok: true, 대상: ['지표'], 코드: 'LXHOLDINGS' });
  assert.deepEqual(값(g.동작처리('진행', {}, { code: 'LXHOLDINGS', 작업ID: '1' }, 밖)), { ok: true, id: '1' });
  assert.deepEqual(값(g.동작처리('딜', { code: 'LXHOLDINGS' }, {}, 밖)), { ok: true, 이름: '딜' });
});

test('주소 안의 키는 가려진다', () => {
  const g = 불러오기('lxgroup', ['설정.js', '공통_시트.js', '공통_dart.js']);
  assert.equal(g.주소가리기('https://x/api?crtfc_key=abc123&bgn_de=20260101'), 'https://x/api?crtfc_key=(가림)&bgn_de=20260101');
});
