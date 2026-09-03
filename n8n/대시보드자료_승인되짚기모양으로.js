// n8n 워크플로 `무원406 대시보드 자료`(wj3pZvpHJPekTaoI)의
// **「승인되짚기 모양으로」 노드 사본**이다.
//
// 진짜로 도는 것은 n8n에 있다. 고칠 때는 두 곳을 같이 고쳐야 한다.
// 칸 순서는 파이썬 `analysis/approval_review.py`의 `머리`와 같고
// `tests/test_approval_review.py`가 둘을 묶어 둔다.
const 줄들 = ((($input.first().json.valueRanges || [])[0]) || {}).values || [];
const 값 = (줄, i) => String(줄 && 줄[i] != null ? 줄[i] : "").trim();
// 못 잰 칸은 빈 글자로 온다. 0으로 바꾸면 안 움직인 것과 섞인다.
const 수 = (글) => {
  const g = String(글 == null ? "" : 글).trim();
  if (!g) return null;
  const n = Number(g.replace(/[^0-9.eE+-]/g, ""));
  return Number.isFinite(n) ? n : null;
};
const 몸줄 = 줄들.slice(1).filter((ㅈ) => 값(ㅈ, 0));

return [{ json: { 승인되짚기: 몸줄.slice(-2000).map((ㅈ) => ({
  잰때: 값(ㅈ, 1),
  결정일: 값(ㅈ, 2),
  종목코드: 값(ㅈ, 3),
  종목명: 값(ㅈ, 4),
  승인: 값(ㅈ, 5) === "예",
  예상가: 수(값(ㅈ, 6)),
  기준가: 수(값(ㅈ, 7)),
  d5: 수(값(ㅈ, 8)),
  d20: 수(값(ㅈ, 9)),
  상태: 값(ㅈ, 10),
})) } }];
