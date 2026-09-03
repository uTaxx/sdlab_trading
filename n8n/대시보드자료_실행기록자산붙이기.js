// n8n 워크플로 `무원406 대시보드 자료`(wj3pZvpHJPekTaoI)의
// **「실행기록에 자산 붙이기」 노드 사본**이다.
//
// 진짜로 도는 것은 n8n에 있다. 고칠 때는 두 곳을 같이 고쳐야 한다.
//
// 칸 번호는 파이썬 `cloud/sheet_log.py`의 `회차머리`와 같다. 현금이 9번,
// 평가액이 10번이다. `tests/test_n8n_gateway_fields.py`가 묶어 둔다.
const 앞 = $input.first().json || {};
const 요청 = $('무엇을 물었나').first().json;
if (요청.무엇 !== "실행기록" || 앞.아직없음 || !Array.isArray(앞.회차)) {
  return [{ json: 앞 }];
}

let 줄들 = [];
try {
  줄들 = ((($('시트 읽기').first().json.valueRanges || [])[0]) || {}).values || [];
} catch (e) {
  줄들 = [];
}
const 값 = (줄, i) => String(줄 && 줄[i] != null ? 줄[i] : "").trim();
// 빈 칸은 null로 둔다. 0으로 바꾸면 자산이 0원이었던 날로 그려진다.
const 수 = (글) => {
  const g = String(글 == null ? "" : 글).trim();
  if (!g) return null;
  const n = Number(g.replace(/[^0-9.eE+-]/g, ""));
  return Number.isFinite(n) ? n : null;
};

// 앞 노드가 최근 50줄을 뒤집어 보낸다. 자산 계선은 그보다 길게 봐야 하므로
// 시트 줄을 따로 읽어 날짜순으로 싣는다.
const 몸줄 = 줄들.slice(1).filter((ㅈ) => 값(ㅈ, 0));
const 자산 = 몸줄.slice(-800).map((ㅈ) => ({
  때: 값(ㅈ, 1),
  전략: 값(ㅈ, 2),
  현금: 수(값(ㅈ, 9)),
  평가액: 수(값(ㅈ, 10)),
})).filter((ㄱ) => ㄱ.평가액 !== null);

return [{ json: Object.assign({}, 앞, { 자산: 자산 }) }];
