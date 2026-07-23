import {
  LOCAL_IMPORT_URL,
  buildImportPayload,
  isAllowedHost,
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

async function cookiesForActiveStore(tab) {
  const stores = await chrome.cookies.getAllCookieStores();
  const store = selectCookieStore(stores, tab.id);
  if (!store) throw new Error('未找到当前标签页的 Cookie Store');
  return chrome.cookies.getAll({ storeId: store.id });
}

async function importCookies() {
  importButton.disabled = true;
  setStatus('正在读取当前闲鱼标签页的登录状态…');
  try {
    const pairing = parsePairingBundle(pairingInput.value);
    const tab = await activeTab();
    if (!tab?.url) throw new Error('请先打开闲鱼官方网站');
    const url = new URL(tab.url);
    if (url.protocol !== 'https:' || !isAllowedHost(url.hostname)) {
      throw new Error('当前标签页不是闲鱼或淘宝官方页面');
    }

    const cookies = await cookiesForActiveStore(tab);
    const payload = buildImportPayload(pairing, cookies, navigator.userAgent);
    if (!payload.cookies.some((cookie) => cookie.name === 'unb')) {
      throw new Error('未检测到有效登录态，请在当前 Chrome 登录闲鱼后重试');
    }

    const response = await fetch(LOCAL_IMPORT_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.success) {
      throw new Error(result.detail || result.message || `本机后台返回 ${response.status}`);
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
