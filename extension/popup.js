/* Instablack Sync — fluxo único: token + proxy + conectar */

const DEFAULT_PANEL = "https://instablack-production.up.railway.app";

const $ = (id) => document.getElementById(id);

function setStatus(msg, kind) {
  const el = $("status");
  el.textContent = msg;
  el.className = "status" + (kind ? ` ${kind}` : "");
}

function refreshStep() {
  const token = ($("token").value || "").trim();
  const proxy = ($("proxy").value || "").trim();
  const step = $("step-now");
  if (!token) {
    step.textContent = "Passo 1: cole o token (botão abaixo abre o site)";
  } else if (!proxy) {
    step.textContent = "Passo 2: cole a proxy residencial";
  } else {
    step.textContent = "Passo 3: aperte Conectar (Instagram já logado)";
  }
}

async function loadSettings() {
  const data = await chrome.storage.local.get(["token", "proxy"]);
  if (data.token) $("token").value = data.token;
  if (data.proxy) $("proxy").value = data.proxy;
  refreshStep();
  if (($("token").value || "").trim()) {
    setStatus("Token ok. Falta a proxy e apertar Conectar.", "ok");
  }
}

async function saveSettings() {
  await chrome.storage.local.set({
    panelOrigin: DEFAULT_PANEL,
    token: ($("token").value || "").trim(),
    proxy: ($("proxy").value || "").trim(),
  });
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
  for (const q of [
    { domain: "instagram.com" },
    { domain: ".instagram.com" },
    { url: "https://www.instagram.com/" },
  ]) {
    const list = await chrome.cookies.getAll(q);
    for (const c of list || []) {
      const domain = String(c.domain || "").toLowerCase();
      if (domain && !domain.includes("instagram.com")) continue;
      seen.set(`${c.name}|${c.domain}|${c.path}`, {
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

async function pushSession() {
  const token = ($("token").value || "").trim();
  const proxy = ($("proxy").value || "").trim();
  if (!token) {
    throw new Error("Cole o token primeiro (ou abra o site e gere um).");
  }
  if (!proxy) {
    throw new Error("Cole a proxy residencial (ip:porta:usuario:senha).");
  }

  await saveSettings();
  setStatus("Lendo cookies do Instagram…");

  const cookies = await getInstagramCookies();
  const names = new Set(cookies.map((c) => c.name));
  if (!names.has("sessionid") || !names.has("csrftoken")) {
    throw new Error(
      "Não achei sessão do Instagram. Abra instagram.com logado neste Chrome e tente de novo."
    );
  }

  setStatus(`Enviando ${cookies.length} cookies pro painel…`);

  const res = await fetch(`${DEFAULT_PANEL}/api/extension/push-session`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      cookies,
      browser: collectBrowserProfile(),
      proxy,
    }),
  });

  let data = null;
  try {
    data = await res.json();
  } catch (_) {}

  if (!res.ok) {
    const detail = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }

  $("step-now").textContent = "Pronto — conta no painel";
  setStatus(
    `Conectou @${data.username}\n` +
      `${data.created ? "Conta nova criada" : "Conta atualizada"} · ${data.cookies_count} cookies\n` +
      "Olha em Contas conectadas no Instablack.",
    "ok"
  );
}

$("open-token").addEventListener("click", () => {
  chrome.tabs.create({ url: `${DEFAULT_PANEL}/accounts/extension` });
});

$("token").addEventListener("input", refreshStep);
$("proxy").addEventListener("input", refreshStep);

$("push").addEventListener("click", () => {
  const btn = $("push");
  btn.disabled = true;
  pushSession()
    .catch((e) => setStatus(String(e.message || e), "err"))
    .finally(() => {
      btn.disabled = false;
    });
});

loadSettings().catch(() => {});
