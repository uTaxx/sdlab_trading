import { workflow, node, trigger, sticky, placeholder, newCredential, ifElse, expr } from '@n8n/workflow-sdk';

const 텔레그램에서온것 = trigger({
  type: 'n8n-nodes-base.telegramTrigger',
  version: 1.5,
  config: {
    name: '텔레그램에서 온 것',
    position: [0, 0],
    parameters: {
      updates: ['message', 'callback_query'],
      additionalFields: {
        chatIds: placeholder('내 텔레그램 chat ID (GitHub Secrets의 TELEGRAM_CHAT_ID와 같은 값)')
      }
    },
    credentials: { telegramApi: newCredential('무원406 텔레그램 봇') }
  },
  output: [{ update_id: 1, callback_query: { id: '9', data: 'a|2026-08-20|403870' } }]
});

const 버튼인가 = ifElse({
  version: 2.2,
  config: {
    name: '버튼을 누른 것인가',
    position: [220, 0],
    parameters: {
      conditions: {
        options: { caseSensitive: true, leftValue: '', typeValidation: 'loose' },
        conditions: [
          {
            leftValue: expr('{{ $json.callback_query }}'),
            operator: { type: 'object', operation: 'exists', singleValue: true }
          }
        ],
        combinator: 'and'
      }
    }
  }
});

const 받았습니다 = node({
  type: 'n8n-nodes-base.telegram',
  version: 1.2,
  config: {
    name: '받았습니다 답하기',
    position: [440, -100],
    parameters: {
      resource: 'callback',
      operation: 'answerQuery',
      queryId: expr('{{ $json.callback_query.id }}'),
      additionalFields: { text: '받았습니다. 잠시 뒤 반영됩니다' }
    },
    credentials: { telegramApi: newCredential('무원406 텔레그램 봇') }
  },
  output: [{ ok: true, result: true }]
});

const 깃허브로넘기기 = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.5,
  config: {
    name: '깃허브로 넘기기',
    position: [700, 0],
    parameters: {
      method: 'POST',
      url: 'https://api.github.com/repos/uTaxx/sdlab_trading/actions/workflows/telegram-n8n.yml/dispatches',
      authentication: 'genericCredentialType',
      genericAuthType: 'httpTemplatedCustomAuth',
      sendHeaders: true,
      specifyHeaders: 'keypair',
      headerParameters: {
        parameters: [{ name: 'Accept', value: 'application/vnd.github+json' }]
      },
      sendBody: true,
      contentType: 'json',
      specifyBody: 'json',
      jsonBody: expr('{\n  "ref": "main",\n  "inputs": {\n    "payload": {{ JSON.stringify(JSON.stringify($("텔레그램에서 온 것").item.json)) }}\n  }\n}')
    },
    credentials: { httpTemplatedCustomAuth: newCredential('무원406 깃허브 PAT (Actions 쓰기)') }
  },
  output: [{ success: true }]
});

const 설명 = sticky(
  '## 무원406: 텔레그램 받기\n\n' +
  '버튼을 누르거나 명령을 보내면 여기가 받아 **GitHub Actions로 그대로 넘긴다.**\n\n' +
  '### 규칙: 여기서는 아무 판단도 하지 않는다\n' +
  '무엇을 승인할지, 기준을 어떻게 바꿀지는 전부 저장소 코드가 정한다.\n' +
  '판단이 두 군데로 나뉘면 나중에 왜 그렇게 됐는지 볼 곳이 두 배가 된다.\n\n' +
  '"받았습니다"만 여기서 답한다. 이건 판단이 아니라 인사다.\n' +
  '안 하면 버튼이 도는 표시로 남아 사람이 또 누른다.\n\n' +
  '### ⚠️ 켜기 전에\n' +
  '1. 텔레그램 자격증명은 **무원406 봇으로 새로 만든다.** 뉴스 시스템 봇을\n' +
  '   붙이면 그쪽 워크플로가 통째로 멈춘다.\n' +
  '2. 이 워크플로를 켜면 그 봇에 웹훅이 걸리고, GitHub 쪽 폴링\n' +
  '   (`텔레그램 명령 받기`)은 더 못 받는다. 그쪽 schedule을 꺼야 한다.\n' +
  '3. 되돌리려면 이 워크플로를 끄면 된다.',
  [텔레그램에서온것, 버튼인가, 받았습니다, 깃허브로넘기기],
  { color: 4 }
);

export default workflow('muwon406-telegram', '무원406: 텔레그램 받기')
  .add(텔레그램에서온것)
  .to(버튼인가.onTrue(받았습니다.to(깃허브로넘기기)).onFalse(깃허브로넘기기))
  .add(설명);
