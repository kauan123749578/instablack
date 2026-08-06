/* Instablack Session Sync — popup */

const $ = (id) => document.getElementById(id);

function setStatus(msg, kind) {
  const el = $("status");
  el.textContent = msg;
  el.className = "status" + (kind ? ` ${kind}` : "");
}

async function loadSettings() {
  const data = await chrome.storage.local.get(["panelOrigin", "token"]);
  if (data.panelOrigin) $("panel").value = data.panelOrigin;
  if (data.token) $("token").value = data.token;
}

async function saveSettings() {
  const panelOrigin = ($("panel").value || "").trim().replace(/\/$/, "");
  const token = ($("token").value || "").trim();
  await chrome.storage.local.set({ panelOrigin, token });
  if (panelOrigin) {
    try {
      await chrome.permissions.request({ origins: [`${panelOrigin}/*`] });
    } catch (_) {
      /* optional */
    }
  }
  setStatus("Configuração salva.", "ok");
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
  const panelOrigin = ($("panel").value || "").trim().replace(/\/$/, "");
  const token = ($("token").value || "").trim();
  if (!panelOrigin || !token) {
    throw new Error("Informe URL do painel e token.");
  }
  const res = await fetch(`${panelOrigin}${path}`, {
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
  sel.innerHTML = "";
  const accounts = data.accounts || [];
  if (!accounts.length) {
    sel.innerHTML = '<option value="">Nenhuma conta web no painel</option>';
    setStatus("Nenhuma conta instagrapi/web encontrada.", "err");
    return;
  }
  for (const a of accounts) {
    const opt = document.createElement("option");
    opt.value = String(a.id);
    opt.textContent = `@${a.username} (${a.status})`;
    sel.appendChild(opt);
  }
  setStatus(`Contas: ${accounts.length} · painel @${data.panel_user}`, "ok");
}

async function pushSession() {
  const accountId = Number(($("account").value || "").trim());
  if (!accountId) throw new Error("Selecione uma conta.");

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
    `Enviando ${cookies.length} cookies + fingerprint…\nUA: ${browser.user_agent.slice(0, 60)}…`
  );

  const result = await api("/api/extension/push-session", {
    method: "POST",
    body: JSON.stringify({
      account_id: accountId,
      cookies,
      browser,
    }),
  });

  setStatus(
    `OK · @${result.username}\n${result.cookies_count} cookies + browser salvos.\nSessão ativa no painel.`,
    "ok"
  );
}

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
