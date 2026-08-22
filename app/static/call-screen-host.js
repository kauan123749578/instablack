/**
 * Janela auxiliar: mantém screen share LiveKit vivo quando a aba principal dá F5.
 * Identity separada: u{id}-{device}-screen (não expulsa voz na aba principal).
 */
(function () {
  const root = document.getElementById("screen-host-page");
  if (!root || root.dataset.ready !== "1") return;

  const CHANNEL = "ib_call_screen_host";
  const DEVICE_KEY = "ib_call_device";
  const RES_MAP = {
    source: null,
    "1440": { width: 2560, height: 1440 },
    "1080": { width: 1920, height: 1080 },
    "720": { width: 1280, height: 720 },
    "480": { width: 854, height: 480 },
  };

  const statusEl = document.getElementById("sh-status");
  const titleEl = document.getElementById("sh-title");
  const stopBtn = document.getElementById("sh-stop-btn");
  const channel = new BroadcastChannel(CHANNEL);

  let room = null;
  let LK = null;
  let sharing = false;
  let busy = false;
  let connectedSlug = "";
  let roomPassword = "";

  const defaultRoomSlug = (root.dataset.roomSlug || "").trim();

  function csrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content || "";
  }

  function deviceId() {
    try {
      let id = localStorage.getItem(DEVICE_KEY);
      if (!id || !/^[a-zA-Z0-9_-]{4,24}$/.test(id)) {
        id = "d" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
        localStorage.setItem(DEVICE_KEY, id);
      }
      return id;
    } catch (_) {
      return "d" + Math.random().toString(36).slice(2, 12);
    }
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text || "";
  }

  function broadcastState(extra) {
    channel.postMessage({
      type: "state",
      sharing,
      ...(extra || {}),
    });
  }

  function loadLivekitScript() {
    return new Promise((resolve, reject) => {
      if (window.LivekitClient?.Room) {
        resolve(window.LivekitClient);
        return;
      }
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/livekit-client@2.15.4/dist/livekit-client.umd.min.js";
      s.async = true;
      s.onload = () => {
        if (window.LivekitClient?.Room) resolve(window.LivekitClient);
        else reject(new Error("LiveKit não carregou"));
      };
      s.onerror = () => reject(new Error("CDN LiveKit falhou"));
      document.head.appendChild(s);
    });
  }

  async function fetchToken(roomSlug, password) {
    const body = { device_id: deviceId(), role: "screen", room_slug: roomSlug || "" };
    if (password) body.password = password;
    const res = await fetch("/call/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf(),
        "X-Requested-With": "fetch",
        Accept: "application/json",
      },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Token HTTP ${res.status}`);
    if (!data.url || !data.token) throw new Error("Sem url/token LiveKit.");
    return data;
  }

  function captureOpts(resKey, fps) {
    const res = RES_MAP[resKey] || RES_MAP["1080"];
    const opts = { audio: false, contentHint: "detail" };
    if (res) {
      opts.resolution = { width: res.width, height: res.height, frameRate: fps };
    }
    return opts;
  }

  function publishOpts(resKey, fps) {
    const bitrate =
      resKey === "1440" ? 10_000_000
        : resKey === "1080" ? 8_000_000
          : resKey === "720" ? 4_500_000
            : resKey === "480" ? 2_000_000
              : 8_000_000;
    return {
      simulcast: false,
      screenShareEncoding: { maxBitrate: bitrate, maxFramerate: fps },
    };
  }

  async function ensureRoom(roomSlug, password) {
    const slug = (roomSlug || defaultRoomSlug || "").trim();
    if (room?.localParticipant && connectedSlug === slug) return room;
    if (room) {
      try { await room.disconnect(); } catch (_) {}
      room = null;
    }
    LK = await loadLivekitScript();
    const tokenData = await fetchToken(slug, password);
    const r = new LK.Room({
      disconnectOnPageLeave: false,
      publishDefaults: {
        screenShareEncoding: { maxBitrate: 8_000_000, maxFramerate: 30 },
        screenShareSimulcastLayers: [],
      },
    });
    await r.connect(String(tokenData.url).trim(), String(tokenData.token).trim(), {
      autoSubscribe: false,
    });
    room = r;
    connectedSlug = slug;
    return r;
  }

  async function startShare(resKey, fps, roomSlug, password) {
    if (busy) return;
    busy = true;
    setStatus("Pedindo permissão da tela…");
    if (stopBtn) stopBtn.hidden = true;
    const slug = (roomSlug || defaultRoomSlug || "").trim();
    const pass = password || roomPassword || "";
    if (pass) roomPassword = pass;
    try {
      await ensureRoom(slug, pass);
      if (sharing) {
        try { await room.localParticipant.setScreenShareEnabled(false); } catch (_) {}
        sharing = false;
      }
      await room.localParticipant.setScreenShareEnabled(
        true,
        captureOpts(resKey || "1080", fps || 30),
        publishOpts(resKey || "1080", fps || 30)
      );
      sharing = true;
      if (titleEl) titleEl.textContent = "Transmitindo";
      setStatus(`${resKey || "1080"}p @ ${fps || 30}fps — sala ${slug || "global"}`);
      if (stopBtn) stopBtn.hidden = false;
      broadcastState({ hint: "Transmissão ativa. Pode atualizar a página principal." });
    } catch (err) {
      console.error("[screen-host] start", err);
      sharing = false;
      setStatus("Falhou: " + String(err.message || err));
      broadcastState({ hint: String(err.message || err) });
    } finally {
      busy = false;
    }
  }

  async function stopShare() {
    if (busy) return;
    busy = true;
    try {
      if (room) {
        try { await room.localParticipant.setScreenShareEnabled(false); } catch (_) {}
      }
    } finally {
      sharing = false;
      busy = false;
      if (titleEl) titleEl.textContent = "Transmissão instablack";
      setStatus("Transmissão parada.");
      if (stopBtn) stopBtn.hidden = true;
      broadcastState({ hint: "Transmissão parada." });
    }
  }

  channel.onmessage = (e) => {
    const msg = e.data || {};
    if (msg.type === "ping") {
      channel.postMessage({ type: "pong", sharing, busy });
      return;
    }
    if (msg.type === "start") {
      startShare(msg.res, msg.fps, msg.room_slug, msg.password).catch(console.error);
      return;
    }
    if (msg.type === "stop") {
      stopShare().catch(console.error);
    }
  };

  stopBtn?.addEventListener("click", () => stopShare().catch(console.error));

  window.addEventListener("pagehide", () => {
    channel.postMessage({ type: "closed", sharing });
  });

  window.addEventListener("beforeunload", () => {
    channel.postMessage({ type: "closed", sharing });
  });

  setStatus("Pronta — inicie pelo botão na página principal.");
  if (window.lucide) window.lucide.createIcons();
  channel.postMessage({ type: "ready" });
})();
