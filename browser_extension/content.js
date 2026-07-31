const CONSOLE_ORIGIN = 'https://xianyu.cxywjx.top';
const ALLOWED_COMMANDS = new Set([
  'XMC_GET_DEVICE',
  'XMC_START_LOGIN',
  'XMC_CONFIRM_LOGIN',
  'XMC_CANCEL_LOGIN',
]);

if (window.location.origin === CONSOLE_ORIGIN) {
  window.addEventListener('message', (event) => {
    if (event.source !== window || event.origin !== CONSOLE_ORIGIN) return;
    if (!event.data || !ALLOWED_COMMANDS.has(event.data.type)) return;
    chrome.runtime.sendMessage(event.data, (response) => {
      window.postMessage({
        type: 'XMC_CLIENT_BROWSER_RESULT',
        requestId: event.data.requestId || '',
        response: response || { ok: false, error: '当前设备浏览器连接未响应' },
      }, CONSOLE_ORIGIN);
    });
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (!message?.type?.startsWith('XMC_CLIENT_BROWSER_')) return;
    window.postMessage(message, CONSOLE_ORIGIN);
  });

  window.postMessage({ type: 'XMC_CLIENT_BROWSER_CONTENT_READY' }, CONSOLE_ORIGIN);
}
