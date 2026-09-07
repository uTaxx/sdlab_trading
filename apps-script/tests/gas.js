/* Apps Script 파일을 Node에서 불러오는 도우미.

   Apps Script 파일은 전역을 같이 쓰는 스크립트라 require로 못 읽는다.
   vm 문맥 하나에 파일들을 차례로 넣고, Apps Script 전역 객체는 시험이 주는
   흉내로 채운다. 순수 함수만 시험한다. 시트·드라이브·UrlFetch를 쓰는
   함수는 Apps Script 안에서 `점검_*`로 확인한다. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const 뿌리 = path.resolve(__dirname, '..');

function 불러오기(프로젝트, 파일들, 전역) {
  const 문맥 = vm.createContext(Object.assign({
    console, Date, JSON, Math, Number, String, Object, Array, Boolean, Error, RegExp, encodeURIComponent, decodeURIComponent,
    Logger: { log() {} },
    PropertiesService: 속성흉내(),
    Utilities: { sleep() {}, formatDate: (d, tz, f) => String(d.toISOString()) },
    ScriptApp: { getProjectTriggers: () => [], newTrigger: () => ({ timeBased: () => ({ everyMinutes: () => ({ create() {} }), after: () => ({ create() {} }) }) }), deleteTrigger() {} },
    UrlFetchApp: { fetch() { throw new Error('시험에서는 UrlFetchApp을 쓰지 않는다'); } },
    SpreadsheetApp: { openById() { throw new Error('시험에서는 SpreadsheetApp을 쓰지 않는다'); } },
    ContentService: { createTextOutput: (t) => ({ 글: t, setMimeType() { return this; } }), MimeType: { JSON: 'json' } },
    CacheService: { getScriptCache: () => ({ get: () => null, put() {} }) },
    LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock() {} }) }
  }, 전역 || {}));
  파일들.forEach((이름) => {
    const 길 = path.join(뿌리, 프로젝트, 'src', 이름);
    // 파일 맨 바깥의 const는 vm 문맥의 전역 속성이 되지 않아 시험에서 못 본다.
    // Apps Script에서는 var와 const가 같은 뜻이라 var로 바꿔 넣는다.
    const 글 = fs.readFileSync(길, 'utf8').replace(/^const /gm, 'var ');
    vm.runInContext(글, 문맥, { filename: 길 });
  });
  return 문맥;
}

function 속성흉내(초기값) {
  const 값 = Object.assign({}, 초기값 || {});
  return {
    getScriptProperties: () => ({
      getProperty: (k) => (Object.prototype.hasOwnProperty.call(값, k) ? 값[k] : null),
      setProperty: (k, v) => { 값[k] = v; }
    }),
    _값: 값
  };
}

/** vm 문맥에서 만든 배열·객체는 프로토타입이 달라 deepStrictEqual이 실패한다.
    JSON으로 한 번 돌려 값만 남긴다. */
function 값(x) { return JSON.parse(JSON.stringify(x)); }

module.exports = { 불러오기, 속성흉내, 뿌리, 값 };
