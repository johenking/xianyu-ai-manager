import {
  CONSOLE_ORIGIN,
  buildClientImportPayload,
  browserFamilyFromUserAgent,
  bytesToBase64Url,
  canonicalJson,
  cookieFingerprint,
  isAllowedHost,
  isConsolePageUrl,
  selectCookieStore,
} from './lib.mjs';

const DB_NAME = 'xmc-client-device-v1';
const STORE_NAME = 'device';
const DEVICE_KEY = 'primary';
const LOGIN_URL = 'https://www.goofish.com/login';
const SESSION_KEY = 'clientLoginSessions';
const RENEWAL_KEY = 'clientRenewalSessions';
const importDebounce = new Map();

const decodeB64url = (value) => {
  const normalized = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(normalized + '='.repeat((4 - normalized.length % 4) % 4));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
};

const randomDeviceId = () => `device_${bytesToBase64Url(crypto.getRandomValues(new Uint8Array(24)))}`;

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE_NAME);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function databaseValue(mode, value) {
  const database = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(STORE_NAME, mode === 'read' ? 'readonly' : 'readwrite');
      const store = transaction.objectStore(STORE_NAME);
      const request = mode === 'read' ? store.get(DEVICE_KEY) : store.put(value, DEVICE_KEY);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } finally {
    database.close();
  }
}

async function publicJwk(key) {
  const jwk = await crypto.subtle.exportKey('jwk', key);
  return { kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y };
}

async function deviceIdentity() {
  let device = await databaseValue('read');
  if (!device) {
    const signing = await crypto.subtle.generateKey(
      { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify'],
    );
    const encryption = await crypto.subtle.generateKey(
      { name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveKey', 'deriveBits'],
    );
    device = {
      deviceId: randomDeviceId(),
      signingPrivateKey: signing.privateKey,
      signingPublicKey: signing.publicKey,
      encryptionPrivateKey: encryption.privateKey,
      encryptionPublicKey: encryption.publicKey,
    };
    await databaseValue('write', device);
  }
  const userAgent = navigator.userAgent;
  return {
    ...device,
    browserFamily: browserFamilyFromUserAgent(userAgent),
    signingPublicJwk: await publicJwk(device.signingPublicKey),
    encryptionPublicJwk: await publicJwk(device.encryptionPublicKey),
  };
}

async function sessionMap() {
  const stored = await chrome.storage.session.get(SESSION_KEY);
  return stored[SESSION_KEY] || {};
}

async function renewalMap() {
  const stored = await chrome.storage.session.get(RENEWAL_KEY);
  return stored[RENEWAL_KEY] || {};
}

async function saveRenewalMap(tasks) {
  await chrome.storage.session.set({ [RENEWAL_KEY]: tasks });
}

async function saveSessionMap(sessions) {
  await chrome.storage.session.set({ [SESSION_KEY]: sessions });
}

async function cookieStoreForTab(tabId) {
  const stores = await chrome.cookies.getAllCookieStores();
  const store = selectCookieStore(stores, tabId);
  if (!store) throw new Error('未找到当前登录标签页的 Cookie Store');
  return store.id;
}

async function signChallenge(identity, challenge, binding) {
  const proof = {
    version: 1,
    challenge_id: challenge.challenge_id,
    device_id: challenge.device_id,
    purpose: challenge.purpose,
    nonce: challenge.nonce,
    binding,
  };
  const signature = await crypto.subtle.sign(
    { name: 'ECDSA', hash: 'SHA-256' },
    identity.signingPrivateKey,
    new TextEncoder().encode(canonicalJson(proof)),
  );
  return bytesToBase64Url(signature);
}

async function decryptCredential(identity, task) {
  const encrypted = task.encrypted_payload;
  const ephemeralJwk = JSON.parse(encrypted.ephemeral_public_key);
  const ephemeralKey = await crypto.subtle.importKey(
    'jwk', ephemeralJwk, { name: 'ECDH', namedCurve: 'P-256' }, false, [],
  );
  const sharedBits = await crypto.subtle.deriveBits(
    { name: 'ECDH', public: ephemeralKey }, identity.encryptionPrivateKey, 256,
  );
  const aad = decodeB64url(encrypted.aad);
  const infoPrefix = new TextEncoder().encode('xmc-client-renewal-v1\0');
  const aadHash = new Uint8Array(await crypto.subtle.digest('SHA-256', aad));
  const info = new Uint8Array(infoPrefix.length + aadHash.length);
  info.set(infoPrefix);
  info.set(aadHash, infoPrefix.length);
  const hkdfBase = await crypto.subtle.importKey('raw', sharedBits, 'HKDF', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey({
    name: 'HKDF', hash: 'SHA-256', salt: decodeB64url(encrypted.salt), info,
  }, hkdfBase, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  const plaintext = await crypto.subtle.decrypt({
    name: 'AES-GCM', iv: decodeB64url(encrypted.nonce), additionalData: aad,
  }, key, decodeB64url(encrypted.ciphertext));
  return JSON.parse(new TextDecoder().decode(plaintext));
}

async function jsonRequest(path, body) {
  const response = await fetch(`${CONSOLE_ORIGIN}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.success) {
    const detail = result.detail;
    throw new Error((detail && typeof detail === 'object' ? detail.message : detail)
      || result.message || `监控台返回 ${response.status}`);
  }
  return result.data;
}

async function renewalProof(identity, purpose, binding) {
  const challenge = await jsonRequest('/api/client-browser/renewal/challenge', {
    device_id: identity.deviceId,
    purpose,
  });
  return {
    device_id: identity.deviceId,
    challenge_id: challenge.challenge_id,
    signature: await signChallenge(identity, challenge, binding),
  };
}

async function fillOfficialLogin(tabId, credential) {
  const injection = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'ISOLATED',
    func: (username, password) => {
      const visible = (element) => Boolean(element && element.getClientRects().length);
      const passwordInput = [...document.querySelectorAll('input[type="password"]')].find(visible);
      const accountInput = [...document.querySelectorAll('input')].find((element) => (
        visible(element)
        && element !== passwordInput
        && ['text', 'tel', 'email'].includes(element.type || 'text')
      ));
      if (!passwordInput || !accountInput) return { state: 'login_form_missing' };
      const setValue = (element, value) => {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        setter?.call(element, value);
        element.dispatchEvent(new Event('input', { bubbles: true }));
        element.dispatchEvent(new Event('change', { bubbles: true }));
      };
      setValue(accountInput, username);
      setValue(passwordInput, password);
      const agreement = [...document.querySelectorAll('input[type="checkbox"]')]
        .find((element) => visible(element) && !element.checked);
      agreement?.click();
      const button = [...document.querySelectorAll('button,input[type="submit"]')]
        .find((element) => visible(element) && !element.disabled);
      if (!button) return { state: 'login_submit_missing' };
      button.click();
      return { state: 'submitted' };
    },
    args: [credential.username, credential.password],
  });
  return injection[0]?.result || { state: 'login_form_missing' };
}

async function reportRenewalResult(taskId, outcome, errorCode, cookies, userAgent = navigator.userAgent) {
  const identity = await deviceIdentity();
  const tasks = await renewalMap();
  const task = tasks[taskId];
  if (!task) return;
  const purpose = outcome === 'action_required' ? 'renewal_action_required' : 'renewal_complete';
  const binding = {
    device_id: identity.deviceId,
    task_id: taskId,
    account_id: task.accountId,
    outcome,
  };
  const proof = await renewalProof(identity, purpose, binding);
  await jsonRequest('/api/client-browser/renewal/' + encodeURIComponent(taskId) + '/result', {
    ...proof,
    outcome,
    error_code: errorCode || '',
    cookies,
    user_agent: userAgent,
  });
}

async function startLogin(command, sender) {
  const identity = await deviceIdentity();
  if (command.deviceId !== identity.deviceId) throw new Error('当前页面绑定的不是此浏览器设备');
  const tab = await chrome.tabs.create({ url: LOGIN_URL, active: true });
  if (!tab.id) throw new Error('当前浏览器未能打开登录页');
  const storeId = await cookieStoreForTab(tab.id);
  const sessions = await sessionMap();
  sessions[command.sessionId] = {
    sessionId: command.sessionId,
    mode: command.mode,
    officialTabId: tab.id,
    consoleTabId: sender.tab?.id || null,
    storeId,
    expiresAt: command.expiresAt,
    imported: false,
  };
  await saveSessionMap(sessions);
  return { started: true, sessionId: command.sessionId };
}

function requireConsoleSender(sender) {
  const senderUrl = sender?.url || sender?.tab?.url || '';
  if (!isConsolePageUrl(senderUrl)) throw new Error('当前设备浏览器命令来源无效');
}

async function checkRenewalTask(taskId) {
  const tasks = await renewalMap();
  const task = tasks[taskId];
  if (!task || !['claimed', 'action_required'].includes(task.state)) return;
  const tab = await chrome.tabs.get(task.tabId).catch(() => null);
  if (!tab?.url) return;
  const cookies = await chrome.cookies.getAll({ storeId: task.storeId });
  const hasIdentity = cookies.some((cookie) => cookie.name === 'unb' && cookie.value);
  const hasCore = cookies.some((cookie) => ['cookie2', '_m_h5_tk', 'sgcookie', 't'].includes(cookie.name) && cookie.value);
  const fingerprintChanged = cookieFingerprint(cookies) !== task.baselineFingerprint;
  if (hasIdentity && hasCore && fingerprintChanged) {
    try {
      await reportRenewalResult(taskId, 'completed', '', cookies);
      await chrome.tabs.remove(task.tabId).catch(() => undefined);
      delete tasks[taskId];
      await saveRenewalMap(tasks);
      return;
    } catch (_) {
      // The official platform probe has not accepted this state yet.
    }
  }
  const pageState = (String(tab.title || '') + ' ' + String(tab.url || '')).toLowerCase();
  if (task.state === 'action_required') {
    if (Date.now() / 1000 < task.expiresAt + 14 * 60) {
      setTimeout(() => checkRenewalTask(taskId), 2000);
    }
    return;
  }
  if (/verify|captcha|punish|sec|滑块|验证|人脸|短信/.test(pageState)) {
    await reportRenewalResult(taskId, 'action_required', 'human_verification_required', []);
    task.state = 'action_required';
    tasks[taskId] = task;
    await saveRenewalMap(tasks);
    await chrome.tabs.update(task.tabId, { active: true }).catch(() => undefined);
    return;
  }
  if (Date.now() / 1000 < task.expiresAt) setTimeout(() => checkRenewalTask(taskId), 1500);
}

async function claimRenewalTask() {
  const identity = await deviceIdentity();
  const binding = { device_id: identity.deviceId, operation: 'claim_next' };
  const proof = await renewalProof(identity, 'renewal_claim', binding);
  const task = await jsonRequest('/api/client-browser/renewal/claim', proof);
  if (!task) return;
  const credential = await decryptCredential(identity, task);
  try {
    const tab = await chrome.tabs.create({ url: LOGIN_URL, active: false });
    if (!tab.id) throw new Error('未能打开当前设备续期标签页');
    const storeId = await cookieStoreForTab(tab.id);
    const baselineCookies = await chrome.cookies.getAll({ storeId });
    const tasks = await renewalMap();
    tasks[task.task_id] = {
      taskId: task.task_id,
      accountId: task.account_id,
      tabId: tab.id,
      storeId,
      expiresAt: task.expires_at,
      state: 'claimed',
      baselineFingerprint: cookieFingerprint(baselineCookies),
    };
    await saveRenewalMap(tasks);
    await new Promise((resolve) => {
      const listener = (tabId, info) => {
        if (tabId === tab.id && info.status === 'complete') {
          chrome.tabs.onUpdated.removeListener(listener);
          resolve();
        }
      };
      chrome.tabs.onUpdated.addListener(listener);
      setTimeout(() => {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }, 15000);
    });
    const result = await fillOfficialLogin(tab.id, credential);
    if (result.state !== 'submitted') {
      await reportRenewalResult(task.task_id, 'action_required', result.state, []);
      tasks[task.task_id].state = 'action_required';
      await saveRenewalMap(tasks);
      await chrome.tabs.update(tab.id, { active: true }).catch(() => undefined);
    } else {
      setTimeout(() => checkRenewalTask(task.task_id), 2500);
    }
  } finally {
    credential.username = '';
    credential.password = '';
  }
}

async function notifyConsole(session, message) {
  if (!session.consoleTabId) return;
  await chrome.tabs.sendMessage(session.consoleTabId, message).catch(() => undefined);
}

async function tryImport(sessionId) {
  const sessions = await sessionMap();
  const session = sessions[sessionId];
  if (!session || session.imported || session.expiresAt <= Date.now() / 1000) return;
  const tab = await chrome.tabs.get(session.officialTabId).catch(() => null);
  if (!tab?.url) return;
  const host = new URL(tab.url).hostname;
  if (!isAllowedHost(host)) return;
  const cookies = await chrome.cookies.getAll({ storeId: session.storeId });
  const hasIdentity = cookies.some((cookie) => cookie.name === 'unb' && cookie.value);
  const hasCore = cookies.some((cookie) => ['cookie2', '_m_h5_tk', 'sgcookie', 't'].includes(cookie.name) && cookie.value);
  if (!hasIdentity || !hasCore) return;

  const identity = await deviceIdentity();
  const challenge = await jsonRequest(
    `/api/client-browser/sessions/${encodeURIComponent(sessionId)}/challenge`,
    { device_id: identity.deviceId, mode: session.mode },
  );
  const binding = { session_id: sessionId, mode: session.mode, device_id: identity.deviceId };
  const signature = await signChallenge(identity, challenge, binding);
  const payload = buildClientImportPayload({
    sessionId,
    deviceId: identity.deviceId,
    mode: session.mode,
    challengeId: challenge.challenge_id,
    signature,
  }, cookies, navigator.userAgent);
  const imported = await jsonRequest('/api/client-browser/import', payload);
  session.imported = true;
  session.accountId = imported.account_id;
  sessions[sessionId] = session;
  await saveSessionMap(sessions);
  await notifyConsole(session, {
    type: 'XMC_CLIENT_BROWSER_IMPORTED',
    sessionId,
    accountId: imported.account_id,
    state: imported.state,
  });
}

function scheduleImport(sessionId) {
  clearTimeout(importDebounce.get(sessionId));
  importDebounce.set(sessionId, setTimeout(() => {
    tryImport(sessionId).catch(async (error) => {
      const sessions = await sessionMap();
      const session = sessions[sessionId];
      if (session) await notifyConsole(session, {
        type: 'XMC_CLIENT_BROWSER_PROGRESS',
        sessionId,
        message: error instanceof Error ? error.message : '正在等待登录完成',
      });
    });
  }, 700));
}

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (changeInfo.status !== 'complete') return;
  const sessions = await sessionMap();
  Object.values(sessions).forEach((session) => {
    if (session.officialTabId === tabId) scheduleImport(session.sessionId);
  });
  const renewals = await renewalMap();
  Object.values(renewals).forEach((task) => {
    if (task.tabId === tabId) setTimeout(() => checkRenewalTask(task.taskId), 500);
  });
});

chrome.cookies.onChanged.addListener(async (changeInfo) => {
  if (!isAllowedHost(changeInfo.cookie.domain)) return;
  const sessions = await sessionMap();
  Object.values(sessions).forEach((session) => scheduleImport(session.sessionId));
  const renewals = await renewalMap();
  Object.values(renewals).forEach((task) => setTimeout(() => checkRenewalTask(task.taskId), 500));
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create('client-renewal-poll', { periodInMinutes: 1 });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create('client-renewal-poll', { periodInMinutes: 1 });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== 'client-renewal-poll') return;
  claimRenewalTask().catch(() => undefined);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    requireConsoleSender(sender);
    if (message?.type === 'XMC_GET_DEVICE') {
      const identity = await deviceIdentity();
      return {
        deviceId: identity.deviceId,
        browserFamily: identity.browserFamily,
        signingPublicJwk: identity.signingPublicJwk,
        encryptionPublicJwk: identity.encryptionPublicJwk,
      };
    }
    if (message?.type === 'XMC_START_LOGIN') return startLogin(message, sender);
    if (message?.type === 'XMC_CONFIRM_LOGIN') {
      const sessions = await sessionMap();
      const session = sessions[message.sessionId];
      if (!session || session.accountId !== message.accountId) throw new Error('登录确认不匹配');
      await chrome.tabs.remove(session.officialTabId).catch(() => undefined);
      delete sessions[message.sessionId];
      await saveSessionMap(sessions);
      return { confirmed: true };
    }
    if (message?.type === 'XMC_CANCEL_LOGIN') {
      const sessions = await sessionMap();
      const session = sessions[message.sessionId];
      if (session) await chrome.tabs.remove(session.officialTabId).catch(() => undefined);
      delete sessions[message.sessionId];
      await saveSessionMap(sessions);
      return { cancelled: true };
    }
    throw new Error('未知当前设备浏览器命令');
  })().then(
    (data) => sendResponse({ ok: true, data }),
    (error) => sendResponse({ ok: false, error: error instanceof Error ? error.message : '命令失败' }),
  );
  return true;
});
