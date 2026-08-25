/* ─────────────────────────────────────────────────────────────
   sdlab 대시보드

   이 화면은 상태를 들고 있지 않다. 열 때마다 n8n 웹훅에 물어보고 그린다.
   스트림릿이 죽던 병 — 배포가 밀리는 것, DB를 갈아 끼우다 연결이 끊기는
   것 — 은 전부 서버가 상태를 들고 있어서 생겼다. 여기엔 그 병이 없다.

   ## 웹훅 규약

   손익만 기존 웹훅을 그대로 쓴다(이미 돌고 있다).

     POST {바탕}/webhook/muwon-balance   { 열쇠 }

   나머지는 창구 하나로 모은다. 열쇠 검사도, 허용 출처도 한 곳에서만
   보게 하려는 것이다 — 여럿으로 나누면 하나를 빠뜨리고 빠뜨린 줄도 모른다.

     POST {바탕}/webhook/sdlab   { 열쇠, 무엇, ...인자 }

       무엇: "승인목록"  →  { 날짜, 후보: [{종목코드, 종목명, 섹터, 수량, 예상가, 승인}] }
       무엇: "승인"      →  { 종목코드, 값: "Y"|"N"|"" }  →  { 된것: true }
       무엇: "기록"      →  { 승률, 손익비, 최대낙폭, 거래: [...] }
       무엇: "기준"      →  { 매매켜짐, 전략, 전략들: [...], 손절, 비중, 동시보유 }
       무엇: "기준저장"  →  { 바꿀것: {...} }  →  { 된것: true }
       무엇: "최근주문"  →  { 주문: [{때, 종목, 사고팜, 수량, 값, 상태}] }
       무엇: "실행기록"  →  { 회차: [{때, 살펴본종목, 매수신호, 매도신호, 주문수, 막힌이유}] }
       무엇: "알림"      →  { 알림: [{때, 종류, 글}] }
       무엇: "변경이력"  →  { 이력: [{때, 무엇, 이전, 이후}] }

   용어 사전과 전략 설명은 웹훅을 안 탄다. 파이썬 원본에서 뽑아 둔
   자료/*.json 을 그대로 읽는다 — 창구가 없어도 진짜로 동작한다.

   아직 /webhook/sdlab 은 만들어지지 않았다. 그때까지는 예시 자료를 그리고
   **예시라는 것을 화면에 밝힌다.** 조용히 가짜를 보여 주면 그게 제일 나쁘다.
   ───────────────────────────────────────────────────────────── */

(() => {
  "use strict";

  const 저장키 = "sdlab.연결";
  const 기본바탕 = "https://sondullab.app.n8n.cloud";
  const 자동간격 = 60000;   // 증권사가 토큰 발급을 자주 하면 막는다. 1분이면 넉넉하다.

  const $ = (id) => document.getElementById(id);
  const 보이기 = (id, 켤까) => $(id).classList.toggle("숨김", !켤까);

  let 자동 = null;
  let 이번창 = null;        // localStorage가 막혀도 이번 방문은 굴러가야 한다
  let 기준값 = null;        // 되돌리기용 원본

  /* ── 연결 정보 ─────────────────────────────────────── */

  const 읽기 = () => {
    try {
      const ㄱ = JSON.parse(localStorage.getItem(저장키) || "null");
      if (ㄱ && ㄱ.열쇠) return ㄱ;
    } catch { /* 막힌 브라우저 — 이번창으로 간다 */ }
    return 이번창;
  };
  const 쓰기 = (값) => {
    이번창 = 값;
    try { localStorage.setItem(저장키, JSON.stringify(값)); return true; }
    catch { return false; }
  };
  const 지우기 = () => {
    이번창 = null;
    try { localStorage.removeItem(저장키); } catch { /* 무시 */ }
  };

  /* ── 그리기 도구 ───────────────────────────────────── */

  const 안전 = (글) => String(글 ?? "").replace(/[&<>"']/g,
    (ㄱ) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ㄱ]);

  const 돈 = (n) => (n === null || n === undefined || Number.isNaN(Number(n)))
    ? "—" : Math.round(Number(n)).toLocaleString("ko-KR") + "원";
  const 부호돈 = (n) => (Number(n) > 0 ? "+" : "") + 돈(n);
  const 퍼센트 = (n, 자리 = 2) => (Number(n) > 0 ? "+" : "") + Number(n).toFixed(자리) + "%";
  // 한국 증시 관례 — 오르면 빨강, 내리면 파랑
  const 색 = (n) => (Number(n) > 0 ? "오름" : Number(n) < 0 ? "내림" : "중립");
  const 지금 = () => new Date().toLocaleTimeString("ko-KR");

  function 알림(자리, 종류, 제목, 설명) {
    $(자리).innerHTML = 종류
      ? `<div class="알림 ${종류 === "순한" ? "순한" : ""}">
           <strong>${제목}</strong>${설명 ? "<br>" + 설명 : ""}
         </div>`
      : "";
  }

  function 탈났다(제목, 설명) {
    $("탈남").innerHTML = `<strong>${제목}</strong><br>${설명}`;
    보이기("탈남", true);
  }

  /* ── 웹훅 ──────────────────────────────────────────── */

  const 바탕주소 = () => (읽기()?.주소 || 기본바탕).replace(/\/+$/, "");

  async function 부르기(길, 몸 = {}) {
    const 연결 = 읽기();
    if (!연결 || !연결.열쇠) throw Object.assign(new Error("열쇠 없음"), { 열쇠없음: true });

    const 답 = await fetch(`${바탕주소()}${길}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ 열쇠: 연결.열쇠, ...몸 }),
    });

    if (답.status === 401 || 답.status === 403) {
      throw Object.assign(new Error("열쇠가 맞지 않습니다"), { 열쇠틀림: true });
    }
    if (답.status === 404) {
      throw Object.assign(new Error("아직 안 만들어진 창구"), { 없는창구: true });
    }
    if (!답.ok) throw new Error(`HTTP ${답.status}`);

    const 자료 = await 답.json();
    if (자료 && 자료.오류) throw new Error(자료.오류);
    return 자료;
  }

  const 창구 = (무엇, 인자 = {}) => 부르기("/webhook/sdlab", { 무엇, ...인자 });

  /* 브라우저가 주는 영어 오류를 그대로 화면에 내보내면 안 된다.

     `Failed to fetch`가 그대로 나가고 있었다. 처음 보는 사람에게는
     무슨 일이 난 것인지도, 무엇을 해야 하는지도 알 수 없는 말이다.
     원문은 접어 두고(고치는 사람에게는 필요하다) 사람 말을 앞에 둔다. */
  function 무슨오류(e) {
    const 원문 = 안전(e && e.message ? e.message : String(e));
    const 접기 =
      `<details style="margin-top:8px"><summary>자세한 내용</summary>` +
      `<code>${원문}</code></details>`;

    if (/Failed to fetch|NetworkError|TypeError/i.test(원문)) {
      return [
        "지금 계좌에 연결되지 않습니다.",
        "인터넷이 끊겼거나, 숫자를 받아 오는 서버가 잠깐 멈춘 것입니다. " +
          "잠시 뒤 <b>🔄 지금 다시 조회</b>를 눌러 보세요. 계속 이러면 " +
          "이 시스템을 설치한 분에게 알려 주세요." + 접기,
      ];
    }
    if (/403|429|rate|한도|too many/i.test(원문)) {
      return [
        "증권사가 잠깐 조회를 막았습니다.",
        "짧은 시간에 여러 번 부르면 증권사가 막습니다. 잘못된 것은 " +
          "아니니 <b>1분쯤 뒤</b>에 다시 눌러 보세요." + 접기,
      ];
    }
    return [
      "계좌 숫자를 가져오지 못했습니다.",
      "잠시 뒤 <b>🔄 지금 다시 조회</b>를 눌러 보세요. 계속 이러면 " +
        "이 시스템을 설치한 분에게 알려 주세요." + 접기,
    ];
  }

  /* 예시로 돌고 있다는 것을 **맨 위에 한 번만** 알린다.

     예전에는 구역마다 같은 경고 상자를 띄웠다. 한 화면에 여섯 번 나왔고,
     문구도 `/webhook/sdlab`, `docs/대시보드_통합.md`처럼 만든 사람만
     아는 말이었다. 처음 여는 사람에게는 화면이 고장 난 것처럼 보인다.

     조용히 가짜를 보여 주는 것은 여전히 안 된다. 대신 **크게 한 번** 말하고,
     구역에는 작은 표시만 남긴다. */
  let 예시알렸나 = false;

  function 예시배너켜기() {
    if (예시알렸나) return;
    예시알렸나 = true;
    $("예시배너").innerHTML = `
      <div class="알림">
        <strong>지금 보이는 숫자는 전부 예시입니다 — 진짜 계좌 자료가 아닙니다.</strong><br>
        화면이 어떻게 생겼는지 보여 주려고 넣어 둔 값입니다.
        <b>무엇을 누르거나 저장해도 실제로는 아무 일도 일어나지 않습니다.</b><br>
        진짜 자료를 보려면 연결을 하나 더 만들어야 합니다 —
        이 시스템을 설치한 분에게 문의하세요.
        <details style="margin-top:8px">
          <summary>직접 설치하셨다면</summary>
          n8n에 <code>/webhook/sdlab</code> 창구를 아직 안 만든 상태입니다.
          만드는 법은 저장소의 <code>docs/대시보드_통합.md</code>에 있습니다.
        </details>
      </div>`;
    보이기("예시배너", true);
  }

  async function 창구또는예시(무엇, 예시, 알림자리) {
    try {
      const 자료 = await 창구(무엇);
      알림(알림자리, null);
      return { 자료, 진짜: true };
    } catch (e) {
      if (e.열쇠틀림) throw e;
      예시배너켜기();
      // 구역마다 붙는 것은 한 줄짜리 꼬리표다. 위 배너가 자세한 설명을
      // 이미 하고 있으니 여기서 되풀이하지 않는다.
      $(알림자리).innerHTML = '<div class="곁말">↑ 예시 자료입니다</div>';
      return { 자료: 예시, 진짜: false };
    }
  }

  /* ── 파이썬에서 뽑아 둔 자료 ────────────────────────
     원본은 glossary.py / strategy_rules.py 하나씩이고, 여기 오는 것은
     scripts/export_dashboard_data.py 가 기계적으로 옮겨 적은 것이다.
     웹훅을 안 타므로 창구가 없어도 진짜로 동작한다. */

  let 용어들 = [];
  let 전략표 = [];

  async function 자료읽기() {
    const 한개 = async (이름) => {
      try {
        const 답 = await fetch(`./자료/${이름}`);
        return 답.ok ? await 답.json() : [];
      } catch { return []; }
    };
    [용어들, 전략표] = await Promise.all([한개("용어사전.json"), 한개("전략설명.json")]);
  }

  /* ── 탭 ────────────────────────────────────────────── */

  const 탭들 = ["손익", "승인", "기록", "기준", "도움말"];
  const 불러온적 = new Set();

  function 탭보이기(이름) {
    탭들.forEach((ㄱ) => {
      보이기(`쪽-${ㄱ}`, ㄱ === 이름);
      const 단추 = document.querySelector(`.탭[data-탭="${ㄱ}"]`);
      단추.setAttribute("aria-selected", String(ㄱ === 이름));
    });
    if (!불러온적.has(이름)) {
      불러온적.add(이름);
      ({ 손익: 손익불러오기, 승인: 승인불러오기, 기록: 기록불러오기,
         기준: 기준불러오기, 도움말: 용어그리기 })[이름]();
    }
  }

  document.querySelectorAll(".탭").forEach((단추) => {
    단추.addEventListener("click", () => 탭보이기(단추.dataset.탭));
  });

  /* ── 탭 1 · 지금 손익 ──────────────────────────────── */

  async function 손익불러오기() {
    $("다시").disabled = true;
    $("다시").textContent = "조회 중…";

    // **계좌 조회와 무관한 것을 먼저 그린다.** 예전에는 이 둘이 try 안에
    // 있어서, 증권사가 한 번 막히면(오늘 아침 403처럼) 웹훅이 필요 없는
    // 시계표까지 통째로 빈 채로 남았다. 한 칸이 안 된다고 나머지가 같이
    // 사라지면, 사람은 화면 전체가 죽은 줄 안다.
    다음실행그리기();
    최근주문불러오기();

    try {
      const 자료 = await 부르기("/webhook/muwon-balance");
      알림("손익알림", null);
      보이기("탈남", false);
      손익그리기(자료);
    } catch (e) {
      if (e.열쇠없음) { 첫화면(); return; }
      if (e.열쇠틀림) {
        알림("손익알림", "경고", "열쇠가 맞지 않아 숫자를 못 가져옵니다.",
          "아래 <b>연결 지우기</b>를 누르면 열쇠를 다시 넣는 화면으로 돌아갑니다. " +
          "기다린다고 저절로 되지는 않습니다. 열쇠가 바뀌었을 수도 있으니 " +
          "설치한 분에게 확인해 보세요.");
        return;
      }
      const [무슨일, 할일] = 무슨오류(e);
      알림("손익알림", "경고", 무슨일, 할일);
    } finally {
      $("다시").disabled = false;
      $("다시").textContent = "🔄 지금 다시 조회";
    }
  }

  function 손익그리기(자료) {
    const 손익 = Number(자료.평가손익 ?? 0);
    const 원가 = Number(자료.원가 ?? 0);
    $("손익").textContent = 부호돈(손익);
    $("손익").className = "값 " + 색(손익);
    $("수익률").textContent = 원가
      ? `원가 ${돈(원가)} 대비 ${퍼센트(손익 / 원가 * 100)}`
      : "보유 종목이 없습니다";
    $("순자산").textContent = 돈(자료.순자산);
    $("현금").textContent = 돈(자료.현금);

    const 줄들 = Array.isArray(자료.종목) ? 자료.종목 : [];
    $("보유몸").innerHTML = 줄들.length === 0
      ? `<tr><td colspan="7" class="빔">보유 종목이 없습니다</td></tr>`
      : 줄들.map((s) => {
          const ㅅ = Number(s.평가손익 ?? 0);
          const ㅇ = Number(s.평균매입가 ?? 0);
          const ㅈ = Number(s.현재가 ?? 0);
          const 률 = ㅇ ? (ㅈ / ㅇ - 1) * 100 : 0;
          const c = 색(ㅅ);
          return `<tr>
            <td>${안전(s.종목 ?? s.symbol ?? "")}</td>
            <td class="${c}">${부호돈(ㅅ)}</td>
            <td class="${c}">${퍼센트(률)}</td>
            <td>${Number(s.수량 ?? 0).toLocaleString("ko-KR")}</td>
            <td>${돈(ㅇ)}</td><td>${돈(ㅈ)}</td>
            <td>${돈(s.평가금액)}</td>
          </tr>`;
        }).join("");

    $("때").textContent = `${지금()} 조회` +
      (자료.조회시각 ? ` · 증권사 기준 ${안전(자료.조회시각)}` : "");
  }

  /* 다음에 저절로 도는 것 — n8n 시계에 걸린 그대로다.
     웹훅이 필요 없는 자료라 창구와 무관하게 늘 맞는다. */
  // **시각 순으로 정렬해 둔다.** 예전에는 08:40이 08:30보다 앞에 적혀 있어서,
  // 08:00에 보면 "다음은 08:40"이라고 했다 — 배열 순서대로 첫 번째를 골랐기
  // 때문이다. 아래에서 한 번 더 정렬하므로 적는 순서가 틀려도 화면은 맞는다.
  const 시계 = [
    ["08:30", "매수 후보 제안", "오늘 살 만한 것을 골라 알림 — 여기서 승인하세요"],
    ["09:05", "승인된 것만 매수", "체크한 것만 실제로 주문"],
    ["15:40", "30분봉 수집", "장중 시세를 쌓는다"],
    ["17:30", "장 마감 정산", "그날 주문을 최종 체결로 바로잡는다 — 부분 체결이 여기서 맞는다"],
    ["17:40", "기록을 시트로", "오늘 있었던 일을 옮겨 적는다"],
    ["20:00", "시장·섹터 리포트", "오늘 장이 어땠는지 — 내일 후보를 보기 전에 읽으세요"],
  ].sort((ㄱ, ㄴ) => ㄱ[0].localeCompare(ㄴ[0]));

  function 다음실행그리기() {
    const 이제 = new Date();
    const 분 = 이제.getHours() * 60 + 이제.getMinutes();
    const 평일 = 이제.getDay() >= 1 && 이제.getDay() <= 5;
    const 다음 = 시계.find(([때]) => {
      const [ㅅ, ㅂ] = 때.split(":").map(Number);
      return 평일 && ㅅ * 60 + ㅂ > 분;
    });

    $("다음실행").innerHTML = 시계.map(([때, 무엇, 왜]) => {
      const 차례 = 다음 && 다음[0] === 때;
      return `<div class="줄" style="margin:0;padding:7px 0;justify-content:flex-start;
                   ${차례 ? "" : "opacity:.55"}">
        <span style="font-variant-numeric:tabular-nums;font-weight:600;min-width:52px">${때}</span>
        <span>${안전(무엇)}${차례 ? ' <span class="딱지 켜짐">다음</span>' : ""}
          <div class="곁말">${안전(왜)}</div></span>
      </div>`;
    }).join("");

    if (!다음) {
      $("다음실행").insertAdjacentHTML("beforeend",
        // 첫 시각을 손으로 적으면 시계를 고칠 때마다 여기를 잊는다.
        `<div class="곁말" style="margin-top:8px">오늘 예정된 것은 다 끝났습니다 —
         다음은 ${평일 ? "내일" : "돌아오는 평일"} ${시계[0][0]}입니다.</div>`);
    }
  }

  /* 표 하나를 창구에서 받아 그리는 공통 절차. 넷이 모양만 다르고
     하는 일이 같아서 한 곳에 모은다. */
  async function 표불러오기({ 무엇, 예시, 알림자리, 몸, 칸수, 빈말, 줄그리기 }) {
    try {
      const { 자료 } = await 창구또는예시(무엇, 예시, 알림자리);
      const 줄들 = Object.values(자료)[0] || [];
      $(몸).innerHTML = 줄들.length === 0
        ? `<tr><td colspan="${칸수}" class="빔">${빈말}</td></tr>`
        : 줄들.map(줄그리기).join("");
    } catch (e) {
      알림(알림자리, "경고", "불러오지 못했습니다.", 안전(e.message));
    }
  }

  const 주문예시 = { 주문: [
    { 때: "08-24 09:05", 종목: "HPSP(403870)", 사고팜: "매수", 수량: 2, 값: 45050, 상태: "체결" },
  ] };

  const 최근주문불러오기 = () => 표불러오기({
    무엇: "최근주문", 예시: 주문예시, 알림자리: "주문알림", 몸: "주문몸", 칸수: 6,
    빈말: "아직 낸 주문이 없습니다",
    줄그리기: (ㅈ) => `<tr>
      <td>${안전(ㅈ.때)}</td><td>${안전(ㅈ.종목)}</td>
      <td class="가운데 ${ㅈ.사고팜 === "매수" ? "오름" : "내림"}">${안전(ㅈ.사고팜)}</td>
      <td>${Number(ㅈ.수량 ?? 0).toLocaleString("ko-KR")}</td>
      <td>${돈(ㅈ.값)}</td><td class="가운데">${안전(ㅈ.상태)}</td></tr>`,
  });

  function 자동맞추기(켤까) {
    if (자동) { clearInterval(자동); 자동 = null; }
    if (켤까) 자동 = setInterval(손익불러오기, 자동간격);
    $("자동전환").textContent = 켤까 ? "자동 갱신 끄기 (1분)" : "자동 갱신 켜기";
  }

  /* ── 탭 2 · 오늘 승인 ──────────────────────────────── */

  const 승인예시 = {
    날짜: new Date().toISOString().slice(0, 10),
    후보: [
      { 종목코드: "042700", 종목명: "한미반도체", 섹터: "반도체", 수량: 3, 예상가: 98500, 승인: "" },
      { 종목코드: "064350", 종목명: "현대로템",   섹터: "방산",   수량: 2, 예상가: 121000, 승인: "Y" },
      { 종목코드: "196170", 종목명: "알테오젠",   섹터: "바이오", 수량: 1, 예상가: 342000, 승인: "N" },
    ],
  };

  async function 승인불러오기() {
    $("승인다시").disabled = true;
    try {
      const { 자료, 진짜 } = await 창구또는예시("승인목록", 승인예시, "승인알림");
      승인그리기(자료, 진짜);
      $("승인때").textContent = `${지금()} 조회` +
        (자료.날짜 ? ` · ${안전(자료.날짜)} 후보` : "");
    } catch (e) {
      알림("승인알림", "경고", "승인 목록을 불러오지 못했습니다.", 안전(e.message));
    } finally {
      $("승인다시").disabled = false;
    }
  }

  function 승인그리기(자료, 진짜) {
    const 후보 = Array.isArray(자료.후보) ? 자료.후보 : [];
    $("승인몸").innerHTML = 후보.length === 0
      ? `<div class="빔">오늘 고른 종목이 없습니다 — 살 만한 신호가 없었다는 뜻입니다</div>`
      : 후보.map((ㅎ) => {
          const 수량 = Number(ㅎ.수량 ?? 0);
          const 정해짐 = ㅎ.승인 === "Y" || ㅎ.승인 === "N";
          return `
          <div class="후보${정해짐 ? " 정해짐" : ""}" data-코드="${안전(ㅎ.종목코드)}">
            <div class="누구">
              <div class="이름">${안전(ㅎ.종목명)}</div>
              <div class="잔글">
                ${안전(ㅎ.종목코드)}${ㅎ.섹터 ? " · " + 안전(ㅎ.섹터) : ""}
                &nbsp;|&nbsp; ${수량.toLocaleString("ko-KR")}주 × ${돈(ㅎ.예상가)}
              </div>
            </div>
            <div class="고르기">
              <button class="작게 고름" data-값="Y" aria-pressed="${ㅎ.승인 === "Y"}"
                ${진짜 ? "" : "disabled"}>산다</button>
              <button class="작게 거름" data-값="N" aria-pressed="${ㅎ.승인 === "N"}"
                ${진짜 ? "" : "disabled"}>안 산다</button>
            </div>
          </div>`;
        }).join("");

    if (!진짜) return;
    $("승인몸").querySelectorAll("button[data-값]").forEach((단추) => {
      단추.addEventListener("click", () => 승인누름(단추));
    });
  }

  async function 승인누름(단추) {
    const 줄 = 단추.closest(".후보");
    const 코드 = 줄.dataset.코드;
    const 켜짐 = 단추.getAttribute("aria-pressed") === "true";
    const 값 = 켜짐 ? "" : 단추.dataset.값;    // 같은 것을 다시 누르면 되돌린다

    const 이전 = [...줄.querySelectorAll("button[data-값]")]
      .map((ㄴ) => [ㄴ, ㄴ.getAttribute("aria-pressed")]);

    // 먼저 화면을 바꾼다 — 누른 게 먹었는지 몰라서 또 누르는 일을 막는다
    줄.querySelectorAll("button[data-값]").forEach((ㄴ) => {
      ㄴ.setAttribute("aria-pressed", String(ㄴ.dataset.값 === 값));
      ㄴ.disabled = true;
    });

    try {
      await 창구("승인", { 종목코드: 코드, 값 });
      줄.classList.toggle("정해짐", 값 !== "");
      $("승인때").textContent = `${지금()} 저장됨`;
    } catch (e) {
      이전.forEach(([ㄴ, ㅅ]) => ㄴ.setAttribute("aria-pressed", ㅅ));
      알림("승인알림", "경고", "저장하지 못했습니다 — 되돌렸습니다.",
        `${안전(e.message)}<br>화면에 보이는 것이 실제와 다르면 안 되므로 원래대로 돌려놨습니다.`);
    } finally {
      줄.querySelectorAll("button[data-값]").forEach((ㄴ) => { ㄴ.disabled = false; });
    }
  }

  /* ── 탭 3 · 기록 ───────────────────────────────────── */

  const 기록예시 = {
    승률: 41.7, 손익비: 1.9, 최대낙폭: -12.4,
    거래: [
      { 종목: "HPSP(403870)", 손익: -2200, 수익률: -2.44, 수량: 2, 산값: 45050, 판값: 43950, 기간: "3일" },
      { 종목: "리노공업(058470)", 손익: 41500, 수익률: 6.10, 수량: 5, 산값: 136000, 판값: 144300, 기간: "11일" },
    ],
  };

  async function 기록불러오기() {
    $("기록다시").disabled = true;
    try {
      const { 자료 } = await 창구또는예시("기록", 기록예시, "기록알림");
      const ㅅ = Number(자료.승률 ?? 0), ㅂ = Number(자료.손익비 ?? 0), ㄴ = Number(자료.최대낙폭 ?? 0);
      $("승률").textContent = ㅅ.toFixed(1) + "%";
      $("손익비").textContent = ㅂ.toFixed(2);
      $("최대낙폭").textContent = ㄴ.toFixed(1) + "%";
      $("최대낙폭").className = "값 " + (ㄴ < 0 ? "내림" : "중립");

      const 거래 = Array.isArray(자료.거래) ? 자료.거래 : [];
      $("기록몸").innerHTML = 거래.length === 0
        ? `<tr><td colspan="7" class="빔">아직 청산까지 끝난 거래가 없습니다</td></tr>`
        : 거래.map((ㄱ) => {
            const c = 색(ㄱ.손익);
            return `<tr>
              <td>${안전(ㄱ.종목)}</td>
              <td class="${c}">${부호돈(ㄱ.손익)}</td>
              <td class="${c}">${퍼센트(ㄱ.수익률)}</td>
              <td>${Number(ㄱ.수량 ?? 0).toLocaleString("ko-KR")}</td>
              <td>${돈(ㄱ.산값)}</td><td>${돈(ㄱ.판값)}</td>
              <td>${안전(ㄱ.기간 || "—")}</td>
            </tr>`;
          }).join("");
      $("기록때").textContent = `${지금()} 조회`;
      실행기록불러오기();
      알림이력불러오기();
    } catch (e) {
      알림("기록알림", "경고", "기록을 불러오지 못했습니다.", 안전(e.message));
    } finally {
      $("기록다시").disabled = false;
    }
  }

  const 회차예시 = { 회차: [
    { 때: "08-24 09:05", 살펴본종목: 45, 매수신호: 3, 매도신호: 0, 주문수: 1,
      막힌이유: "비중 상한 2건" },
    { 때: "08-23 09:05", 살펴본종목: 45, 매수신호: 0, 매도신호: 0, 주문수: 0, 막힌이유: "" },
  ] };

  const 실행기록불러오기 = () => 표불러오기({
    무엇: "실행기록", 예시: 회차예시, 알림자리: "회차알림", 몸: "회차몸", 칸수: 6,
    빈말: "실행 기록이 없습니다 — 한 번도 안 돌았거나 기록을 남기기 전 회차입니다",
    줄그리기: (ㅎ) => `<tr>
      <td>${안전(ㅎ.때)}</td>
      <td>${Number(ㅎ.살펴본종목 ?? 0)}</td>
      <td>${Number(ㅎ.매수신호 ?? 0)}</td>
      <td>${Number(ㅎ.매도신호 ?? 0)}</td>
      <td>${Number(ㅎ.주문수 ?? 0)}</td>
      <td style="text-align:left">${안전(ㅎ.막힌이유 || "—")}</td></tr>`,
  });

  const 알림예시 = { 알림: [
    { 때: "08-24 09:05", 종류: "체결", 글: "HPSP 2주를 45,050원에 샀습니다." },
    { 때: "08-25 08:30", 종류: "제안", 글: "오늘 매수 후보 3종목 — 화면에서 골라 주세요." },
  ] };

  async function 알림이력불러오기() {
    try {
      const { 자료 } = await 창구또는예시("알림", 알림예시, "알림알림");
      const 줄들 = Array.isArray(자료.알림) ? 자료.알림 : [];
      $("알림몸").innerHTML = 줄들.length === 0
        ? `<div class="빔">아직 알림이 없습니다</div>`
        : 줄들.map((ㅇ) => `
            <div class="후보">
              <div class="누구">
                <div class="이름">${안전(ㅇ.글)}</div>
                <div class="잔글">${안전(ㅇ.때)} · ${안전(ㅇ.종류 || "알림")}</div>
              </div>
            </div>`).join("");
    } catch (e) {
      알림("알림알림", "경고", "알림을 불러오지 못했습니다.", 안전(e.message));
    }
  }

  /* ── 탭 4 · 전략과 기준 ────────────────────────────── */

  const 기준예시 = {
    매매켜짐: false,
    매도켜짐: true,
    전략: "volume_surge_5d",
    손절: 7, 비중: 20, 동시보유: 5,
  };

  async function 기준불러오기() {
    try {
      const { 자료, 진짜 } = await 창구또는예시("기준", 기준예시, "기준알림");
      기준값 = 자료;
      기준그리기(자료, 진짜);
    } catch (e) {
      알림("기준알림", "경고", "기준을 불러오지 못했습니다.", 안전(e.message));
    }
  }

  /* ── 머리의 매수·매도 스위치 ──────────────────────────
     어느 탭에 있든 늘 보인다. 하루 중 제일 급할 때 찾는 것이라
     탭을 옮겨 다니게 하면 안 된다. */

  function 스위치그리기(살까, 팔까, 진짜) {
    const 칠 = (id, 켤까, 위험할까) => {
      const 입력 = $(id + "스위치");
      입력.checked = 켤까;
      입력.disabled = !진짜;
      입력.setAttribute("aria-checked", String(켤까));
      $(id + "상태").textContent = 켤까 ? "ON" : "OFF";
      // 매도가 꺼진 것은 매수가 꺼진 것보다 위험하다 — 손절이 안 걸린다.
      입력.closest(".스위치").classList.toggle("위험", 위험할까 && !켤까);
    };
    칠("매수", 살까, false);
    칠("매도", 팔까, true);
    보이기("스위치들", true);
    보이기("매도경고", 진짜 && !팔까);
  }

  //: 스위치 하나를 바꾼다. 머리의 토글과 기준 탭의 단추가 같이 쓴다 —
  //: 두 군데에 따로 적으면 한쪽만 고치고 다른 쪽을 잊는다.
  async function 스위치바꾸기(무엇, 켤까) {
    const 물음 = {
      매수: 켤까
        ? "매수를 켭니다. 다음 실행부터 승인된 종목을 실제로 삽니다. 계속할까요?"
        : "매수를 끕니다. 새로 사는 것이 전부 멈춥니다(손절은 계속 돕니다). 계속할까요?",
      매도: 켤까
        ? "매도를 켭니다. 손절·익절·청산이 다시 걸립니다. 계속할까요?"
        : "🛑 매도를 끕니다.\n\n손절·익절·청산이 **전부 멈춥니다.** 들고 있는 "
          + "종목의 값이 얼마나 빠지든 자동으로는 아무 일도 일어나지 않습니다.\n\n"
          + "정말 끌까요?",
    }[무엇];
    if (!confirm(물음)) return false;

    const 칸 = 무엇 === "매수" ? "매매켜짐" : "매도켜짐";
    try {
      await 창구("기준저장", { 바꿀것: { [칸]: 켤까 } });
      기준값[칸] = 켤까;
      기준그리기(기준값, true);
      $("기준때").textContent = `${지금()} 저장됨`;
      return true;
    } catch (e) {
      알림("기준알림", "경고", "바꾸지 못했습니다.", 안전(e.message));
      // 창구가 거절했으면 화면도 원래대로 돌려놔야 한다. 안 돌리면
      // 스위치는 바뀐 채로 보이는데 실제로는 안 바뀐 상태가 된다.
      기준그리기(기준값, true);
      return false;
    }
  }

  function 기준그리기(자료, 진짜) {
    const 켜짐 = Boolean(자료.매매켜짐);
    $("킬값").textContent = 켜짐 ? "매매 켜짐" : "매매 꺼짐";
    $("킬값").className = "값 " + (켜짐 ? "오름" : "중립");
    $("킬곁말").textContent = 켜짐
      ? "새로 사는 것이 돕니다"
      : "새로 사는 것이 전부 멈춰 있습니다. 손절은 그대로 돕니다";
    $("킬전환").textContent = 켜짐 ? "끄기" : "켜기";

    // 매도는 없으면 켜진 것으로 본다 — 창구가 옛 형식이어서 이 칸이
    // 안 올 수도 있는데, 그때 화면이 "꺼짐"이라고 하면 사람이 손절이
    // 멈춘 줄 안다. 모를 때 기울 쪽은 매수와 반대다.
    const 팜 = 자료.매도켜짐 !== false;
    $("매도값").textContent = 팜 ? "매도 켜짐" : "매도 꺼짐";
    $("매도값").className = "값 " + (팜 ? "오름" : "내림");
    $("매도곁말").textContent = 팜
      ? "손절·익절·청산이 그대로 돕니다"
      : "손절·익절·청산이 전부 멈춰 있습니다";
    $("매도전환").textContent = 팜 ? "끄기" : "켜기";

    스위치그리기(켜짐, 팜, 진짜);

    // 전략 목록은 파이썬 원본에서 뽑은 것을 쓴다. 창구는 "지금 무엇을
    // 쓰는지"만 알려 주면 된다 — 목록까지 창구가 나르면 원본이 둘이 된다.
    const 계열들 = [...new Set(전략표.map((ㅈ) => ㅈ.계열))];
    $("전략").innerHTML = 계열들.map((계열) => `
      <optgroup label="${안전(계열)}">
        ${전략표.filter((ㅈ) => ㅈ.계열 === 계열).map((ㅈ) =>
          `<option value="${안전(ㅈ.키)}" ${ㅈ.키 === 자료.전략 ? "selected" : ""}
            >${안전(ㅈ.이름)}</option>`).join("")}
      </optgroup>`).join("");
    전략설명갱신();
    변경이력불러오기();

    $("손절").value = 자료.손절 ?? "";
    $("비중").value = 자료.비중 ?? "";
    $("동시보유").value = 자료.동시보유 ?? "";

    // 전략 드롭다운은 잠그지 않는다. 목록과 규칙은 정적 자료라 창구가
    // 없어도 진짜고, 골라 보는 것만으로는 아무것도 안 바뀐다.
    ["킬전환", "매도전환", "손절", "비중", "동시보유", "기준저장"].forEach((id) => {
      $(id).disabled = !진짜;
    });
  }

  function 전략설명갱신() {
    const ㅈ = 전략표.find((ㄱ) => ㄱ.키 === $("전략").value);
    $("전략한줄").textContent = ㅈ ? ㅈ.한줄 : "";
    if (!ㅈ) { $("전략규칙").innerHTML = ""; return; }

    const 묶음 = (제목, 색깔, 줄들) => (줄들 && 줄들.length)
      ? `<div class="칸" style="margin-top:10px">
           <div class="이름표" style="color:var(--${색깔});font-weight:600">${제목}</div>
           <ul style="margin:6px 0 0;padding-left:18px">
             ${줄들.map((ㄱ) => `<li>${굵게(ㄱ)}</li>`).join("")}
           </ul>
         </div>`
      : "";

    $("전략규칙").innerHTML =
      묶음("🔴 이럴 때 삽니다", "오름", ㅈ.산다) +
      묶음("🔵 이럴 때 팝니다", "내림", ㅈ.판다) +
      묶음("알아 둘 점", "흐린글", ㅈ.참고) +
      (ㅈ.설명있음 ? "" :
        `<div class="알림" style="margin-top:10px">
           <strong>이 전략은 아직 설명을 안 붙였습니다.</strong><br>
           파라미터만 나열됩니다 — 무엇을 보고 사는지 화면에서 알 수 없다는 뜻이라,
           고르기 전에 코드를 확인하세요.
         </div>`);
  }

  // 원본 설명에 **강조**가 들어 있다. 그대로 두면 별표가 화면에 보인다.
  const 굵게 = (글) => 안전(글).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  $("전략").addEventListener("change", 전략설명갱신);

  $("킬전환").addEventListener("click", async () => {
    $("킬전환").disabled = true;
    await 스위치바꾸기("매수", !기준값.매매켜짐);
    $("킬전환").disabled = false;
  });

  $("매도전환").addEventListener("click", async () => {
    $("매도전환").disabled = true;
    await 스위치바꾸기("매도", 기준값.매도켜짐 === false);
    $("매도전환").disabled = false;
  });

  // 머리의 토글. change로 받는 이유는 키보드(스페이스)로도 바꿀 수 있어야
  // 해서다. 사람이 물음에서 취소하면 스위치를 원래대로 되돌린다.
  [["매수", "매매켜짐"], ["매도", "매도켜짐"]].forEach(([무엇, 칸]) => {
    $(무엇 + "스위치").addEventListener("change", async (ㅇ) => {
      const 켤까 = ㅇ.target.checked;
      ㅇ.target.disabled = true;
      const 됐나 = await 스위치바꾸기(무엇, 켤까);
      ㅇ.target.disabled = false;
      if (!됐나) ㅇ.target.checked = 칸 === "매도" ? 기준값.매도켜짐 !== false
                                                : Boolean(기준값.매매켜짐);
    });
  });

  $("기준저장").addEventListener("click", async () => {
    const 바꿀것 = {
      전략: $("전략").value,
      손절: Number($("손절").value),
      비중: Number($("비중").value),
      동시보유: Number($("동시보유").value),
    };
    for (const [이름, 값] of Object.entries(바꿀것)) {
      if (이름 !== "전략" && (!Number.isFinite(값) || 값 <= 0)) {
        알림("기준알림", "경고", `${이름} 값이 이상합니다.`, "0보다 큰 숫자를 넣어 주세요.");
        return;
      }
    }
    $("기준저장").disabled = true;
    try {
      await 창구("기준저장", { 바꿀것 });
      Object.assign(기준값, 바꿀것);
      알림("기준알림", null);
      $("기준때").textContent = `${지금()} 저장됨 · 다음 실행부터 적용됩니다`;
    } catch (e) {
      알림("기준알림", "경고", "저장하지 못했습니다.", 안전(e.message));
    } finally {
      $("기준저장").disabled = false;
    }
  });

  const 이력예시 = { 이력: [
    { 때: "08-19 21:10", 무엇: "손절 폭", 이전: "5%", 이후: "7%" },
    { 때: "08-18 09:02", 무엇: "쓰는 전략", 이전: "ma_rsi_v1", 이후: "volume_surge_5d" },
  ] };

  const 변경이력불러오기 = () => 표불러오기({
    무엇: "변경이력", 예시: 이력예시, 알림자리: "이력알림", 몸: "이력몸", 칸수: 4,
    빈말: "아직 바꾼 것이 없습니다",
    줄그리기: (ㅇ) => `<tr>
      <td>${안전(ㅇ.때)}</td><td style="text-align:left">${안전(ㅇ.무엇)}</td>
      <td>${안전(ㅇ.이전)}</td><td><b>${안전(ㅇ.이후)}</b></td></tr>`,
  });

  /* ── 탭 5 · 용어 ───────────────────────────────────── */

  function 용어그리기() {
    const 찾는말 = $("용어찾기").value.trim().toLowerCase();
    const 걸린것 = 용어들.filter((ㄴ) => !찾는말 ||
      [ㄴ.이름, ㄴ.뜻, ㄴ.읽는법, ㄴ.영문].join(" ").toLowerCase().includes(찾는말));

    $("용어수").textContent = 찾는말
      ? `${걸린것.length}개 찾음 (전체 ${용어들.length}개)`
      : `${용어들.length}개`;

    $("용어몸").innerHTML = 걸린것.length === 0
      ? `<div class="빔">그런 말은 사전에 없습니다. 화면에서 본 그대로 넣어 보세요.</div>`
      // 65개를 다 펼치면 폰에서 서른 화면이 넘는다. 이름만 죽 훑다가
      // 궁금한 것만 열게 한다. 찾는 말이 있을 때는 미리 열어 둔다 —
      // 찾아 놓고 또 눌러야 하면 찾은 보람이 없다.
      : 걸린것.map((ㄴ) => `
          <details class="후보 낱말" ${찾는말 ? "open" : ""}>
            <summary>
              <span class="이름">${안전(ㄴ.이름)}</span>
              ${ㄴ.영문 ? `<span class="잔글">${안전(ㄴ.영문)}</span>` : ""}
            </summary>
            <p style="margin:8px 0 0">${굵게(ㄴ.뜻)}</p>
            ${ㄴ.읽는법 ? `<p style="margin:8px 0 0;color:var(--흐린글);font-size:14px">
              <b style="color:var(--강조)">읽는 법</b> — ${굵게(ㄴ.읽는법)}</p>` : ""}
          </details>`).join("");
  }
  $("용어찾기").addEventListener("input", 용어그리기);

  $("기준다시").addEventListener("click", 기준불러오기);
  $("승인다시").addEventListener("click", 승인불러오기);
  $("기록다시").addEventListener("click", 기록불러오기);
  $("다시").addEventListener("click", 손익불러오기);
  $("자동전환").addEventListener("click", () => 자동맞추기(!자동));

  /* ── 첫 화면 ───────────────────────────────────────── */

  function 첫화면() {
    보이기("설정", true);
    보이기("본문", false);
    보이기("스위치들", false);
    보이기("매도경고", false);
  }

  // 붙여 넣고 엔터를 치는 것이 사람의 기본 동작이다. <form>이 아니라
  // 엔터가 아무 일도 안 했다 — 첫 화면에서 그러면 길이 막힌 것과 같다.
  $("열쇠").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); $("저장").click(); }
  });

  $("저장").addEventListener("click", () => {
    const 열쇠 = $("열쇠").value.trim();
    if (!열쇠) {
      알림("열쇠알림", "경고", "열쇠를 넣어 주세요.",
        "어디서 얻는지는 입력칸 바로 아래에 적어 뒀습니다.");
      $("열쇠").focus();
      return;
    }
    알림("열쇠알림", null);
    const 외웠나 = 쓰기({ 주소: $("주소").value.trim(), 열쇠 });
    보이기("기억못함", !외웠나);   // 못 외웠어도 길을 막지는 않는다
    보이기("설정", false);
    보이기("본문", true);
    불러온적.clear();
    (용어들.length ? Promise.resolve() : 자료읽기()).then(() => {
      탭보이기("손익");
      // 머리 스위치는 어느 탭에서든 보인다. 기준 탭을 안 열어도 맞아야
      // 하므로 여기서 한 번 읽는다 — 안 그러면 "—"인 채로 남는다.
      기준불러오기();
      불러온적.add("기준");
    });
  });

  $("연결풀기").addEventListener("click", () => {
    지우기();
    예시알렸나 = false;
    보이기("예시배너", false);
    자동맞추기(false);
    불러온적.clear();
    보이기("기억못함", false);
    보이기("탈남", false);
    첫화면();
  });

  // 정적 자료를 먼저 읽고 화면을 연다. 전략 목록과 용어 사전이 여기
  // 들어 있어서, 못 읽으면 그 두 탭이 빈 채로 열린다.
  자동맞추기(false);
  자료읽기().then(() => {
    if (읽기()?.열쇠) {
      보이기("본문", true);
      탭보이기("손익");
    } else {
      첫화면();
    }
  });
})();
