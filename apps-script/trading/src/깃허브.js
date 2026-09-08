/* GitHub Actions 부르기. n8n HTTP 노드가 하던 것과 같은 요청이다.

   POST https://api.github.com/repos/uTaxx/sdlab_trading/actions/workflows/<파일>/dispatches
   본문 { ref: 'main', inputs: {...} }

   성공이면 GitHub이 204를 준다. inputs 값은 전부 글자여야 한다. */

function 워크플로실행(파일, 입력) {
  const 토큰 = 속성(속성이름.깃허브토큰);
  if (!토큰) return { ok: false, 상태: 0, 본문: 'GITHUB_TOKEN 속성이 비어 있습니다.' };
  const 몸 = { ref: 'main' };
  if (입력) 몸.inputs = 입력;
  const 주소 = 'https://api.github.com/repos/' + 저장소 + '/actions/workflows/' + 파일 + '/dispatches';
  let 마지막 = { ok: false, 상태: 0, 본문: '' };
  for (let 시도 = 0; 시도 < 2; 시도++) {
    try {
      const 답 = UrlFetchApp.fetch(주소, {
        method: 'post',
        contentType: 'application/json',
        headers: { Authorization: 'Bearer ' + 토큰, Accept: 'application/vnd.github+json' },
        payload: JSON.stringify(몸),
        muteHttpExceptions: true
      });
      const 상태 = 답.getResponseCode();
      마지막 = { ok: 상태 === 204, 상태: 상태, 본문: 상태 === 204 ? '' : String(답.getContentText() || '').slice(0, 300) };
      // 5xx만 한 번 더 보낸다. 4xx는 다시 보내도 같다.
      if (마지막.ok || 상태 < 500) return 마지막;
    } catch (e) {
      마지막 = { ok: false, 상태: 0, 본문: String(e).slice(0, 300) };
    }
    Utilities.sleep(2000);
  }
  return 마지막;
}
