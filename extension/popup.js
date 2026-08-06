/* Instablack Session Sync — popup */

const DEFAULT_PANEL = "https://instablack-production.up.railway.app";

const $ = (id) => document.getElementById(id);

function panelOrigin() {
  return DEFAULT_PANEL;
}

function setStatus(msg, kind) {
  const el = $("status");
  el.textContent = msg;
  el.className = "status" + (kind ? ` ${kind}` : "");
}

async function loadSettings() {
  $("panel").value = DEFAULT_PANEL;
  $("panel-display").textContent = DEFAULT_PANEL;
  const data = await chrome.storage.local.get(["token", "proxy"]);
  if (data.token) $("token").value = data.token;
  if (data.proxy) $("proxy").value = data.proxy;
}

async function saveSettings() {
  const token = ($("token").value || "").trim();
  const proxy = ($("proxy").value || "").trim();
  await chrome.storage.local.set({
    panelOrigin: DEFAULT_PANEL,
    token,
    proxy,
  });
  setStatus("Token salvo.", "ok");
}

function collectBrowserProfile() {
  const nav = navigator;
  const screenObj = window.screen || {};
  let chromeVersion = "";
  const m = String(nav.userAgent || "").match(/Chrome\/([\d.]+)/);
  if (m) chromeVersion = m[1];
  return {
    user_agent: nav.userAgent || "",
    language: nav.language || "",
    languages: Array.from(nav.languages || []),
    platform: nav.platform || "",
    vendor: nav.vendor || "",
    hardware_concurrency: nav.hardwareConcurrency || null,
    device_memory: nav.deviceMemory || null,
    screen: {
      width: screenObj.width || null,
      height: screenObj.height || null,
      colorDepth: screenObj.colorDepth || null,
    },
    pixel_ratio: window.devicePixelRatio || null,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
    timezone_offset: new Date().getTimezoneOffset(),
    cookie_enabled: !!nav.cookieEnabled,
    do_not_track: nav.doNotTrack || null,
    webdriver: !!nav.webdriver,
    max_touch_points: nav.maxTouchPoints || 0,
    chrome_version: chromeVersion,
    captured_at: new Date().toISOString(),
  };
}

async function getInstagramCookies() {
  const seen = new Map();
  const queries = [
    { domain: "instagram.com" },
    { domain: ".instagram.com" },
    { url: "https://www.instagram.com/" },
    { url: "https://instagram.com/" },
  ];
  for (const q of queries) {
    const list = await chrome.cookies.getAll(q);
    for (const c of list || []) {
      const domain = String(c.domain || "").toLowerCase();
      if (domain && !domain.includes("instagram.com")) continue;
      const key = `${c.name}|${c.domain}|${c.path}`;
      seen.set(key, {
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path,
        secure: !!c.secure,
        httpOnly: !!c.httpOnly,
        sameSite: c.sameSite || "",
        expirationDate: c.expirationDate || null,
        storeId: c.storeId || "",
        hostOnly: !!c.hostOnly,
        session: !!c.session,
      });
    }
  }
  return Array.from(seen.values());
}

async function api(path, options = {}) {
  const token = ($("token").value || "").trim();
  if (!token) {
    throw new Error(
      "Falta o token. Clique em “Abrir painel → gerar token”, copie o ibxt_… e cole aqui."
    );
  }
  const res = await fetch(`${panelOrigin()}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    data = null;
  }
  if (!res.ok) {
    const detail =
      (data && (data.detail || data.error)) || `HTTP ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function reloadAccounts() {
  setStatus("Carregando contas…");
  const data = await api("/api/extension/accounts");
  const sel = $("account");
  const current = sel.value;
  sel.innerHTML = "";
  const optNew = document.createElement("option");
  optNew.value = "";
  optNew.textContent = "+ Nova conta (do zero)";
  sel.appendChild(optNew);
  for (const a of data.accounts || []) {
    const opt = document.createElement("option");
    opt.value = String(a.id);
    opt.textContent = `@${a.username} (${a.status})`;
    sel.appendChild(opt);
  }
  if (current && [...sel.options].some((o) => o.value === current)) {
    sel.value = current;
  }
  const n = (data.accounts || []).length;
  setStatus(
    `OK · ${n} conta(s) · painel @${data.panel_user}\nPode conectar uma nova ou atualizar uma existente.`,
    "ok"
  );
}

async function pushSession() {
  const rawId = ($("account").value || "").trim();
  const accountId = rawId ? Number(rawId) : null;
  const proxy = ($("proxy").value || "").trim();

  if (!accountId && !proxy) {
    throw new Error(
      "Conta nova: cole a proxy residencial (ip:porta:user:senha) antes de enviar."
    );
  }

  setStatus("Lendo cookies do Instagram…");
  const cookies = await getInstagramCookies();
  const names = new Set(cookies.map((c) => c.name));
  if (!names.has("sessionid") || !names.has("csrftoken")) {
    throw new Error(
      "Cookies incompletos. Abra instagram.com logado neste Chrome e tente de novo."
    );
  }

  const browser = collectBrowserProfile();
  setStatus(
    `Enviando ${cookies.length} cookies + fingerprint…\n${
      accountId ? "Atualizando conta #" + accountId : "Criando conta nova"
    }`
  );

  const payload = {
    cookies,
    browser,
    proxy,
  };
  if (accountId) payload.account_id = accountId;

  const result = await api("/api/extension/push-session", {
    method: "POST",
    body: JSON.stringify(payload),
  });

  setStatus(
    `${result.created ? "Conta criada" : "Sessão atualizada"} · @${result.username}\n` +
      `${result.cookies_count} cookies + browser salvos no painel.`,
    "ok"
  );
  await reloadAccounts().catch(() => {});
  if (result.account_id) {
    $("account").value = String(result.account_id);
  }
}

$("open-token").addEventListener("click", () => {
  chrome.tabs.create({ url: `${DEFAULT_PANEL}/accounts/extension` });
});

$("save").addEventListener("click", () => {
  saveSettings().catch((e) => setStatus(String(e.message || e), "err"));
});
$("reload").addEventListener("click", () => {
  saveSettings()
    .then(reloadAccounts)
    .catch((e) => setStatus(String(e.message || e), "err"));
});
$("push").addEventListener("click", () => {
  const btn = $("push");
  btn.disabled = true;
  saveSettings()
    .then(pushSession)
    .catch((e) => setStatus(String(e.message || e), "err"))
    .finally(() => {
      btn.disabled = false;
    });
});

loadSettings().catch(() => {});
