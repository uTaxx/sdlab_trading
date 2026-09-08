const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { 불러오기, 뿌리, 값 } = require('./gas');

const 파일들 = ['설정.js', '시계.js'];

test('정각에 해당 일을 고른다', () => {
  const g = 불러오기('trading', 파일들);
  const 일 = g.할일찾기({ 날짜: '2026-09-08', 요일: 1, 시: 9, 분: 5 }, {});
  assert.equal(일.length, 1);
  assert.equal(일[0].항목.워크플로, 'execute-approved.yml');
  assert.equal(일[0].늦은분, 0);
});

test('유예 안이면 늦게 와도 부르고, 유예를 넘기면 놓친 것으로 적는다', () => {
  const g = 불러오기('trading', 파일들);
  const 늦음 = g.할일찾기({ 날짜: '2026-09-08', 요일: 1, 시: 9, 분: 8 }, {});
  assert.deepEqual(값(늦음.map((x) => x.항목.워크플로)), ['execute-approved.yml']);
  assert.equal(늦음[0].늦은분, 3);
  const 넘김 = g.할일찾기({ 날짜: '2026-09-08', 요일: 1, 시: 9, 분: 9 }, {});
  assert.equal(넘김.length, 0);
  const 놓침 = g.놓친것찾기({ 날짜: '2026-09-08', 요일: 1, 시: 9, 분: 9 }, {});
  assert.ok(놓침.some((x) => x.항목.워크플로 === 'execute-approved.yml'));
});

test('이미 한 것은 다시 고르지 않는다', () => {
  const g = 불러오기('trading', 파일들);
  const 한것 = { '2026-09-08|09:05|execute-approved.yml': '09:05:03' };
  assert.equal(g.할일찾기({ 날짜: '2026-09-08', 요일: 1, 시: 9, 분: 6 }, 한것).length, 0);
});

test('주말에는 평일 일을 고르지 않고 토요일 09:00은 기간 검증만이다', () => {
  const g = 불러오기('trading', 파일들);
  const 토 = g.할일찾기({ 날짜: '2026-09-12', 요일: 6, 시: 9, 분: 0 }, {});
  assert.deepEqual(값(토.map((x) => x.항목.워크플로)), ['period-check.yml']);
  const 일 = g.할일찾기({ 날짜: '2026-09-13', 요일: 7, 시: 9, 분: 5 }, {});
  assert.equal(일.length, 0);
});

test('손절 감시는 09:00부터 1시간마다 15:00까지다', () => {
  const g = 불러오기('trading', 파일들);
  const 항목 = g.시간표.find((x) => x.워크플로 === 'watch-stops.yml');
  assert.equal(항목.시각[0], '09:00');
  assert.equal(항목.시각[항목.시각.length - 1], '15:00');
  assert.equal(항목.시각.length, 7);
  assert.ok(!항목.시각.includes('15:30'));
  assert.ok(!항목.시각.includes('09:30'));
});

test('시간표의 워크플로 파일이 전부 저장소에 있다', () => {
  const g = 불러오기('trading', 파일들);
  const 자리 = path.resolve(뿌리, '..', '.github', 'workflows');
  g.시간표.forEach((항목) => {
    assert.ok(fs.existsSync(path.join(자리, 항목.워크플로)), 항목.워크플로 + ' 이 없습니다');
  });
});

test('inputs 값은 전부 글자다. GitHub API가 글자만 받는다', () => {
  const g = 불러오기('trading', 파일들);
  g.시간표.forEach((항목) => {
    if (!항목.입력) return;
    Object.values(항목.입력).forEach((v) => assert.equal(typeof v, 'string', 항목.이름));
  });
});

test('시계 모드 기본값은 관찰이다', () => {
  const g = 불러오기('trading', 파일들);
  assert.equal(g.시계모드(), '관찰');
});
