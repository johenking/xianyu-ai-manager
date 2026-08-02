(function installClientBrowserBridge() {
  const CONSOLE_ORIGIN = 'https://xianyu.cxywjx.top';
  const BRIDGE_PROTOCOL_VERSION = 1;
  const BRIDGE_INSTANCE_KEY = '__xmcClientBrowserBridgeV1';
  const ALLOWED_COMMANDS = new Set([
    'XMC_GET_DEVICE',
    'XMC_START_LOGIN',
    'XMC_CONFIRM_LOGIN',
    'XMC_CANCEL_LOGIN',
  ]);

  if (window.location.origin !== CONSOLE_ORIGIN || globalThis[BRIDGE_INSTANCE_KEY]) return;
  Object.defineProperty(globalThis, BRIDGE_INSTANCE_KEY, {
    value: true,
    configurable: false,
    enumerable: false,
    writable: false,
  });

  window.addEventListener('message', (event) => {
    if (event.source !== window || event.origin !== CONSOLE_ORIGIN) return;
    if (!event.data || !ALLOWED_COMMANDS.has(event.data.type)) return;
    chrome.runtime.sendMessage(event.data, (response) => {
      const runtimeError = chrome.runtime.lastError;
      window.postMessage({
        type: 'XMC_CLIENT_BROWSER_RESULT',
        requestId: event.data.requestId || '',
        response: runtimeError
          ? { ok: false, code: 'extension_service_unavailable', error: '扩展后台连接未响应，请刷新扩展后重试' }
          : response || { ok: false, code: 'extension_service_unavailable', error: '扩展后台连接未响应' },
      }, CONSOLE_ORIGIN);
    });
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (!message?.type?.startsWith('XMC_CLIENT_BROWSER_')) return;
    window.postMessage(message, CONSOLE_ORIGIN);
  });

  window.postMessage({
    type: 'XMC_CLIENT_BROWSER_CONTENT_READY',
    extensionVersion: chrome.runtime.getManifest().version,
    protocolVersion: BRIDGE_PROTOCOL_VERSION,
  }, CONSOLE_ORIGIN);
}());
