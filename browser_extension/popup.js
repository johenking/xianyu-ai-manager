import {
  buildImportPayload,
  collectAllowedCookies,
  formatConsoleError,
  parsePairingBundle,
  selectCookieStore,
} from './lib.mjs';

const pairingInput = document.querySelector('#pairing');
const importButton = document.querySelector('#import');
const openButton = document.querySelector('#open-goofish');
const statusBox = document.querySelector('#status');

function setStatus(message, tone = '') {
  statusBox.textContent = message;
  statusBox.className = `status ${tone}`.trim();
}

async function activeTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] || null;
}

async function cookiesForImport(tab) {
  const stores = await chrome.cookies.getAllCookieStores();
  const store = selectCookieStore(stores, tab?.id);
  if (!store) throw new Error('未找到可用的 Cookie Store');
  return collectAllowedCookies(store.id, (details) => chrome.cookies.getAll(details));
}

async function importCookies() {
  importButton.disabled = true;
  setStatus('正在读取当前浏览器里的闲鱼登录状态…');
  try {
    const pairing = parsePairingBundle(pairingInput.value);
    const tab = await activeTab();
    const cookies = await cookiesForImport(tab);
    const payload = buildImportPayload(pairing, cookies, navigator.userAgent);
    if (!payload.cookies.some((cookie) => cookie.name === 'unb')) {
      throw new Error('未检测到闲鱼登录态。请先在 Chrome 登录闲鱼，然后再导入；不必保持闲鱼为当前标签页。');
    }

    let response;
    let result = {};
    for (let attempt = 0; attempt < 3; attempt += 1) {
      response = await fetch(pairing.importUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      result = await response.json().catch(() => ({}));
      const retryable = response.status === 503
        || result?.detail?.code === 'session_probe_retryable';
      if (!retryable) break;
      setStatus(`平台检查暂时失败，正在重试（${attempt + 1}/3）…`);
      await new Promise((resolve) => setTimeout(resolve, 800 * (attempt + 1)));
    }
    if (!response.ok || !result.success) {
      throw new Error(formatConsoleError(result, response.status));
    }
    pairingInput.value = '';
    setStatus(`导入成功，已验证 ${payload.cookies.length} 个 Cookie 字段。`, 'success');
  } catch (error) {
    setStatus(error instanceof Error ? error.message : '导入失败，请重试', 'error');
  } finally {
    importButton.disabled = false;
  }
}

importButton.addEventListener('click', importCookies);
openButton.addEventListener('click', () => {
  chrome.tabs.create({ url: 'https://www.goofish.com/' });
});
