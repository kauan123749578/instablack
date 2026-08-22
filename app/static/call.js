/**
 * Call — LiveKit (mic sólido + multi-device + tela nítida).
 *
 * - Mic: setMicrophoneEnabled (gesto do user); sync por track real.
 * - PC + celular: identity = u{id}-{deviceId} (localStorage).
 * - Tela: bitrate alto (~8Mbps / 1080p30), clique → expandir (estilo LANcord).
 */
(function () {
  const root = document.getElementById("call-page");
  if (!root || root.dataset.ready !== "1") return;

  const REJOIN_KEY = "ib_call_rejoin";
  const DEVICE_KEY = "ib_call_device";

  const statusEl = document.getElementById("call-status");
  const countEl = document.getElementById("call-count");
  const tilesEl = document.getElementById("call-tiles");
  const lobbyEl = document.getElementById("call-lobby");
  const joinBtn = document.getElementById("call-join-btn");
  const hintEl = document.getElementById("call-hint");
  const connEl = document.getElementById("call-conn");
  const voiceMembersEl = document.getElementById("call-voice-members");
  const micBanner = document.getElementById("call-mic-banner");
  const enableMicBtn = document.getElementById("call-enable-mic-btn");
  const screenWrap = document.getElementById("call-screen-wrap");
  const screenEl = document.getElementById("call-screen");
  const screenBadge = document.getElementById("call-screen-badge");
  const stageEl = document.querySelector(".dc-stage");
  const chatLog = document.getElementById("call-chat-log");
  const chatForm = document.getElementById("call-chat-form");
  const chatInput = document.getElementById("call-chat-input");
  const chatSend = document.getElementById("call-chat-send");
  const chatPanel = document.getElementById("call-chat-panel");
  const fabChat = document.getElementById("call-fab-chat");
  const chatToggle = document.getElementById("call-chat-toggle");
  const micBtn = document.getElementById("call-mic-btn");
  const screenBtn = document.getElementById("call-screen-btn");
  const deafenBtn = document.getElementById("call-deafen-btn");
  const leaveBtn = document.getElementById("call-leave-btn");
  const myStatus = document.getElementById("call-my-status");
  const qualityModal = document.getElementById("call-quality-modal");
  const qualityGo = document.getElementById("call-quality-go");
  const qualityStop = document.getElementById("call-quality-stop");
  const qualityResBox = document.getElementById("call-quality-res");
  const qualityFpsBox = document.getElementById("call-quality-fps");

  const QUALITY_KEY = "ib_call_screen_quality";
  const RES_MAP = {
    source: null,
    "1440": { width: 2560, height: 1440 },
    "1080": { width: 1920, height: 1080 },
    "720": { width: 1280, height: 720 },
    "480": { width: 854, height: 480 },
  };

  let screenRes = "1080";
  let screenFps = 30;

  const COLORS = [
    "#5865f2", "#57f287", "#fee75c", "#eb459e", "#ed4245",
    "#3ba55d", "#faa81a", "#f47b67", "#9b59b6", "#1abc9c",
  ];

  let room = null;
  let LK = null;
  let micOn = false;
  let sharing = false;
  let deafened = false;
  let joining = false;
  let intentionalLeave = false;
  let micPublishing = false;
  const audioEls = new Map();

  const lkReady = loadLivekitScript().catch((e) => {
    console.warn(e);
    return null;
  });

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

  function setStatus(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.classList.toggle("is-live", kind === "live");
    statusEl.classList.toggle("is-error", kind === "error");
    statusEl.classList.toggle("is-wait", kind === "wait");
  }

  function setHint(msg) {
    if (hintEl) hintEl.textContent = msg || "";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function colorFor(id) {
    let h = 0;
    const s = String(id || "x");
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return COLORS[h % COLORS.length];
  }

  function initialFor(name) {
    const n = String(name || "?").replace(/^@/, "").trim();
    return (n[0] || "?").toUpperCase();
  }

  function labelOf(p) {
    return (p && (p.name || p.identity)) || "Alguém";
  }

  function remoteList(r) {
    const out = [];
    r?.remoteParticipants?.forEach((p) => out.push(p));
    return out;
  }

  function inRoom() {
    return !!(room && room.localParticipant);
  }

  /** Mic realmente publicado e não mutado (isMicrophoneEnabled sozinho mente às vezes). */
  function localMicLive() {
    try {
      const lp = room?.localParticipant;
      if (!lp) return false;
      if (lp.isMicrophoneEnabled === true) return true;
      let live = false;
      const pubs = lp.audioTrackPublications || lp.trackPublications;
      pubs?.forEach?.((pub) => {
        const src = String(pub.source || "");
        const isMic =
          src.includes("microphone") ||
          src === "1" ||
          (pub.kind === "audio" && !src.includes("screen"));
        if (!isMic) return;
        if (pub.track && pub.isMuted !== true) live = true;
      });
      return live;
    } catch (_) {
      return false;
    }
  }

  function participantMicMuted(p) {
    try {
      if (p === room?.localParticipant) return !micOn;
      if (typeof p.isMicrophoneEnabled === "boolean") return !p.isMicrophoneEnabled;
    } catch (_) {}
    return false;
  }

  function showMicBanner(show) {
    // Nunca mostra banner se não estiver realmente na sala (bug do "clico e nada").
    const should = !!(show && inRoom() && !micOn);
    if (micBanner) micBanner.hidden = !should;
  }

  function setMicUi() {
    if (!micBtn) return;
    micBtn.disabled = !inRoom();
    micBtn.classList.toggle("is-muted", !micOn);
    micBtn.innerHTML = micOn
      ? '<i data-lucide="mic"></i>'
      : '<i data-lucide="mic-off"></i>';
    if (myStatus) {
      if (!inRoom()) myStatus.textContent = "Fora da sala";
      else myStatus.textContent = micOn ? "Em voz" : "Em voz · mudo — clique no mic";
    }
    if (window.lucide) window.lucide.createIcons();
  }

  function setConnectedUi(on) {
    if (lobbyEl) lobbyEl.hidden = on;
    if (connEl) connEl.hidden = !on;
    if (leaveBtn) leaveBtn.disabled = !on;
    if (screenBtn) screenBtn.disabled = !on;
    if (deafenBtn) deafenBtn.disabled = !on;
    if (chatInput) chatInput.disabled = !on;
    if (chatSend) chatSend.disabled = !on;
    if (micBtn) micBtn.disabled = !on;
    const mobile = window.matchMedia("(max-width: 960px)").matches;
    if (fabChat) fabChat.hidden = !(on && mobile);
    if (!on) {
      showMicBanner(false);
      collapseScreen();
    }
  }

  function peopleList(r) {
    const people = [];
    if (r?.localParticipant) people.push({ p: r.localParticipant, self: true });
    remoteList(r).forEach((p) => people.push({ p, self: false }));
    return people;
  }

  function refreshUi() {
    const r = room;
    const people = peopleList(r);
    if (countEl) countEl.textContent = String(people.length);

    if (tilesEl) {
      tilesEl.innerHTML = !r
        ? ""
        : people
            .map(({ p, self }) => {
              const name = labelOf(p) + (self ? " (você)" : "");
              const speaking = !!p.isSpeaking;
              const muted = participantMicMuted(p);
              return `<div class="dc-tile${speaking ? " is-speaking" : ""}">
                <div class="dc-avatar" style="background:${colorFor(p.identity)}">${escapeHtml(initialFor(labelOf(p)))}</div>
                <div class="dc-tile-name">
                  ${muted ? '<i data-lucide="mic-off"></i>' : ""}
                  <span>${escapeHtml(name)}</span>
                </div>
              </div>`;
            })
            .join("");
    }

    if (voiceMembersEl) {
      voiceMembersEl.innerHTML = !r
        ? ""
        : people
            .map(({ p, self }) => {
              const name = labelOf(p) + (self ? " (você)" : "");
              const speaking = !!p.isSpeaking;
              const muted = participantMicMuted(p);
              return `<div class="dc-voice-member${speaking ? " is-speaking" : ""}">
                <span class="av" style="background:${colorFor(p.identity)}">${escapeHtml(initialFor(labelOf(p)))}</span>
                <span>${escapeHtml(name)}</span>
                ${muted ? '<i data-lucide="mic-off" class="mic-off"></i>' : ""}
              </div>`;
            })
            .join("");
    }

    if (window.lucide) window.lucide.createIcons();
  }

  function appendChat(from, text) {
    if (!chatLog) return;
    const line = document.createElement("div");
    line.className = "dc-chat-line";
    line.innerHTML = `<strong>${escapeHtml(from)}</strong>${escapeHtml(text)}`;
    chatLog.appendChild(line);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function collapseScreen() {
    screenWrap?.classList.remove("is-expanded");
    stageEl?.classList.remove("has-expanded-screen");
  }

  function toggleScreenExpand() {
    if (!screenWrap || screenWrap.hidden) return;
    const on = !screenWrap.classList.contains("is-expanded");
    screenWrap.classList.toggle("is-expanded", on);
    stageEl?.classList.toggle("has-expanded-screen", on);
  }

  function attachScreen(track, who, publication) {
    if (!screenEl || !track?.attach) return;
    try {
      if (publication?.setVideoQuality && LK?.VideoQuality?.HIGH != null) {
        publication.setVideoQuality(LK.VideoQuality.HIGH);
      }
    } catch (_) {}
    try {
      const mt = track.mediaStreamTrack;
      if (mt && "contentHint" in mt) mt.contentHint = "detail";
    } catch (_) {}
    track.attach(screenEl);
    screenEl.muted = true;
    screenEl.playsInline = true;
    screenEl.play?.().catch(() => {});
    if (screenWrap) {
      screenWrap.hidden = false;
      // Ao começar a ver tela, já abre expandida (qualidade + espaço).
      if (!screenWrap.classList.contains("is-expanded")) {
        screenWrap.classList.add("is-expanded");
        stageEl?.classList.add("has-expanded-screen");
      }
    }
    if (screenBadge) screenBadge.textContent = who || "Tela";
  }

  function clearScreen() {
    if (screenEl) {
      try { screenEl.srcObject = null; } catch (_) {}
    }
    if (screenWrap) screenWrap.hidden = true;
    collapseScreen();
  }

  function attachRemoteAudio(track, participant) {
    if (!track || track.kind !== "audio") return;
    const id = participant?.identity || track.sid || String(Math.random());
    let el = audioEls.get(id);
    if (!el) {
      el = document.createElement("audio");
      el.autoplay = true;
      el.setAttribute("playsinline", "true");
      el.playsInline = true;
      el.style.display = "none";
      document.body.appendChild(el);
      audioEls.set(id, el);
    }
    track.attach(el);
    el.muted = deafened;
    el.volume = 1;
    el.play?.().catch(() => {});
  }

  function detachRemoteAudio(id) {
    const el = audioEls.get(id);
    if (!el) return;
    try { el.srcObject = null; } catch (_) {}
    el.remove();
    audioEls.delete(id);
  }

  function wipeAudio() {
    audioEls.forEach((el) => {
      try { el.srcObject = null; } catch (_) {}
      el.remove();
    });
    audioEls.clear();
  }

  function loadLivekitScript() {
    return new Promise((resolve, reject) => {
      if (window.LivekitClient?.Room) {
        resolve(window.LivekitClient);
        return;
      }
      const existing = document.querySelector("script[data-livekit-client]");
      if (existing) {
        existing.addEventListener("load", () => {
          if (window.LivekitClient?.Room) resolve(window.LivekitClient);
          else reject(new Error("LiveKit não carregou"));
        });
        existing.addEventListener("error", () => reject(new Error("CDN LiveKit falhou")));
        return;
      }
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/livekit-client@2.15.4/dist/livekit-client.umd.min.js";
      s.async = true;
      s.dataset.livekitClient = "1";
      s.onload = () => {
        if (window.LivekitClient?.Room) resolve(window.LivekitClient);
        else reject(new Error("LivekitClient.Room ausente"));
      };
      s.onerror = () => reject(new Error("CDN LiveKit falhou"));
      document.head.appendChild(s);
    });
  }

  function ev(name, fallback) {
    return (LK?.RoomEvent && LK.RoomEvent[name]) || fallback;
  }

  async function fetchToken() {
    const res = await fetch("/call/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf(),
        "X-Requested-With": "fetch",
        Accept: "application/json",
      },
      body: JSON.stringify({ device_id: deviceId() }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail;
      const msg = typeof detail === "string"
        ? detail
        : (Array.isArray(detail) ? detail.map((d) => d.msg || d).join("; ") : null);
      throw new Error(msg || `Token HTTP ${res.status}`);
    }
    if (!data.url || !data.token) throw new Error("Servidor sem url/token LiveKit.");
    return data;
  }

  async function safeDisconnect(r) {
    if (!r) return;
    try { await r.disconnect(true); } catch (_) {
      try { await r.disconnect(); } catch (__) {}
    }
  }

  function rememberJoin(on) {
    try {
      if (on) sessionStorage.setItem(REJOIN_KEY, "1");
      else sessionStorage.removeItem(REJOIN_KEY);
    } catch (_) {}
  }

  function syncMicFromRoom() {
    if (!inRoom()) {
      micOn = false;
      showMicBanner(false);
      setMicUi();
      refreshUi();
      return;
    }
    micOn = localMicLive();
    setMicUi();
    showMicBanner(!micOn);
    refreshUi();
  }

  async function enableMic() {
    if (micPublishing) return;

    if (!inRoom()) {
      showMicBanner(false);
      setHint("Você não está na sala. Clique em «Entrar na sala» primeiro.");
      setStatus("Desconectado");
      return;
    }
    if (!LK) {
      setHint("SDK LiveKit ainda carregando — aguarde 2s e clique de novo.");
      return;
    }

    micPublishing = true;
    if (enableMicBtn) enableMicBtn.disabled = true;
    setHint("Ativando microfone…");

    try {
      // Caminho oficial LiveKit (gesto do clique → permission → publish).
      await room.localParticipant.setMicrophoneEnabled(true, {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });

      micOn = localMicLive();
      if (!micOn && typeof LK.createLocalAudioTrack === "function") {
        const audioTrack = await LK.createLocalAudioTrack({
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        });
        const source = LK.Track?.Source?.Microphone;
        await room.localParticipant.publishTrack(
          audioTrack,
          source ? { source, dtx: true, red: true } : { dtx: true, red: true }
        );
        try {
          await room.localParticipant.setMicrophoneEnabled(true);
        } catch (_) {}
        micOn = localMicLive();
      }

      if (!micOn && !localMicLive()) {
        throw new Error("Microfone não publicou — tente de novo");
      }

      micOn = true;
      showMicBanner(false);
      setHint("Microfone ligado. Fale normalmente.");
      setMicUi();
      refreshUi();
      setTimeout(syncMicFromRoom, 500);
    } catch (err) {
      console.error("enableMic", err);
      micOn = false;
      if (inRoom()) showMicBanner(true);
      else showMicBanner(false);
      setMicUi();
      const name = err?.name || "";
      const msg = String(err?.message || err);
      if (name === "NotAllowedError" || /Permission|NotAllowed/i.test(msg)) {
        setHint("Chrome bloqueou o mic neste clique. Clique de novo em «Ligar microfone» e escolha Permitir.");
      } else if (name === "NotFoundError") {
        setHint("Nenhum microfone encontrado.");
      } else if (name === "NotReadableError") {
        setHint("Microfone em uso por outro app (Discord/Zoom). Feche e tente de novo.");
      } else {
        setHint("Mic falhou: " + msg);
      }
    } finally {
      micPublishing = false;
      if (enableMicBtn) enableMicBtn.disabled = false;
    }
  }

  async function muteMic() {
    if (!inRoom()) return;
    try {
      await room.localParticipant.setMicrophoneEnabled(false);
      micOn = false;
      showMicBanner(true);
      setMicUi();
      refreshUi();
      setHint("Microfone mutado.");
    } catch (err) {
      setHint(String(err.message || err));
    }
  }

  async function join(opts) {
    const auto = !!(opts && opts.auto);
    if (joining || room) return;
    joining = true;
    intentionalLeave = false;
    setStatus("Conectando…", "wait");
    setHint(auto ? "Reentrando na sala…" : "Entrando na sala…");
    if (joinBtn) joinBtn.disabled = true;
    showMicBanner(false);

    let r = null;
    try {
      const [sdk, tokenData] = await Promise.all([
        lkReady.then((x) => x || loadLivekitScript()),
        fetchToken(),
      ]);
      LK = sdk;
      if (!LK?.Room) throw new Error("LiveKit JS não carregou.");

      r = new LK.Room({
        adaptiveStream: true,
        dynacast: true,
        disconnectOnPageLeave: false,
        audioCaptureDefaults: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        // Qualidade tipo LANcord: tela com bitrate alto (default do WebRTC fica borrado).
        publishDefaults: {
          dtx: true,
          red: true,
          screenShareEncoding: {
            maxBitrate: 8_000_000,
            maxFramerate: 30,
          },
          // Uma camada só — evita simulcast “low” na tela.
          screenShareSimulcastLayers: [],
        },
      });
      room = r;

      const screenSource = LK.Track?.Source?.ScreenShare || "screen_share";

      r.on(ev("ParticipantConnected", "participantConnected"), () => refreshUi());
      r.on(ev("ParticipantDisconnected", "participantDisconnected"), (p) => {
        if (p?.identity) detachRemoteAudio(p.identity);
        refreshUi();
      });
      r.on(ev("ActiveSpeakersChanged", "activeSpeakersChanged"), () => refreshUi());
      r.on(ev("TrackMuted", "trackMuted"), () => {
        if (inRoom()) syncMicFromRoom();
        else refreshUi();
      });
      r.on(ev("TrackUnmuted", "trackUnmuted"), () => {
        if (inRoom()) syncMicFromRoom();
        else refreshUi();
      });
      r.on(ev("LocalTrackPublished", "localTrackPublished"), () => syncMicFromRoom());
      r.on(ev("LocalTrackUnpublished", "localTrackUnpublished"), () => syncMicFromRoom());

      r.on(ev("Reconnecting", "reconnecting"), () => {
        setStatus("Reconectando…", "wait");
        setHint("Rede instável — reconectando automaticamente…");
      });
      r.on(ev("Reconnected", "reconnected"), () => {
        setStatus("Na sala", "live");
        setHint("Reconectado.");
        syncMicFromRoom();
      });

      r.on(ev("TrackSubscribed", "trackSubscribed"), (track, publication, participant) => {
        if (track.kind === "audio") {
          attachRemoteAudio(track, participant);
          return;
        }
        if (track.kind === "video" && (publication?.source === screenSource || String(publication?.source).includes("screen"))) {
          attachScreen(track, labelOf(participant), publication);
        }
      });
      r.on(ev("TrackUnsubscribed", "trackUnsubscribed"), (track, publication, participant) => {
        if (track.kind === "audio" && participant?.identity) {
          detachRemoteAudio(participant.identity);
        }
        if (publication?.source === screenSource || String(publication?.source || "").includes("screen")) {
          clearScreen();
          sharing = false;
          screenBtn?.classList.remove("is-on");
        }
      });
      r.on(ev("DataReceived", "dataReceived"), (payload, participant) => {
        try {
          const msg = JSON.parse(new TextDecoder().decode(payload));
          if (msg?.t === "chat" && msg.text) appendChat(labelOf(participant) || "?", msg.text);
        } catch (_) {}
      });
      r.on(ev("Disconnected", "disconnected"), (reason) => {
        if (room !== r) return;
        const state = r.state || r.connectionState;
        const reconnecting =
          state === (LK.ConnectionState && LK.ConnectionState.Reconnecting) ||
          state === "reconnecting";
        if (reconnecting && !intentionalLeave) return;

        console.warn("disconnected", reason, state);
        room = null;
        sharing = false;
        micOn = false;
        wipeAudio();
        clearScreen();
        showMicBanner(false);
        setConnectedUi(false);
        setMicUi();
        refreshUi();
        if (intentionalLeave) {
          rememberJoin(false);
          setStatus("Desconectado");
          setHint("");
        } else {
          setStatus("Caiu", "error");
          setHint("Conexão caiu. Reentrando…");
          rememberJoin(true);
          setTimeout(() => {
            if (!room && !joining && !intentionalLeave) {
              join({ auto: true }).catch(console.error);
            }
          }, 1200);
        }
        if (joinBtn) joinBtn.disabled = false;
        if (lobbyEl) lobbyEl.hidden = false;
      });

      await r.connect(String(tokenData.url).trim(), String(tokenData.token).trim(), {
        autoSubscribe: true,
      });

      if (!r.localParticipant) {
        throw new Error("Sem participante local — confira LIVEKIT_API_SECRET.");
      }

      joining = false;
      rememberJoin(true);
      setConnectedUi(true);
      setStatus("Na sala", "live");
      setHint("");
      refreshUi();

      // Mic só com gesto do usuário (banner). Auto-rejoin não tenta sozinho.
      showMicBanner(true);
      setHint(
        auto
          ? "Reentrou. Clique em «Ligar microfone» para falar."
          : "Na sala. Clique em «Ligar microfone» para publicar o áudio."
      );
      setMicUi();

      remoteList(r).forEach((p) => {
        p.trackPublications?.forEach((pub) => {
          if (pub.isSubscribed && pub.track) {
            if (pub.track.kind === "audio") attachRemoteAudio(pub.track, p);
            if (pub.track.kind === "video" && (pub.source === screenSource || String(pub.source).includes("screen"))) {
              attachScreen(pub.track, labelOf(p), pub);
            }
          }
        });
      });

      if (window.lucide) window.lucide.createIcons();
    } catch (err) {
      console.error(err);
      joining = false;
      if (room === r) room = null;
      await safeDisconnect(r);
      wipeAudio();
      clearScreen();
      showMicBanner(false);
      setConnectedUi(false);
      micOn = false;
      setMicUi();
      setStatus("Falha", "error");
      setHint(String(err.message || err));
      if (joinBtn) joinBtn.disabled = false;
      if (lobbyEl) lobbyEl.hidden = false;
      refreshUi();
    }
  }

  async function leave() {
    intentionalLeave = true;
    rememberJoin(false);
    const r = room;
    room = null;
    sharing = false;
    micOn = false;
    await safeDisconnect(r);
    wipeAudio();
    clearScreen();
    showMicBanner(false);
    setConnectedUi(false);
    setMicUi();
    setStatus("Desconectado");
    setHint("");
    if (joinBtn) joinBtn.disabled = false;
    if (lobbyEl) lobbyEl.hidden = false;
    refreshUi();
  }

  async function toggleMic() {
    if (!inRoom()) {
      setHint("Entre na sala primeiro (botão Entrar na sala).");
      return;
    }
    if (micOn) await muteMic();
    else await enableMic();
  }

  function loadQualityPrefs() {
    try {
      const raw = JSON.parse(localStorage.getItem(QUALITY_KEY) || "{}");
      if (raw.res && RES_MAP[raw.res] !== undefined) screenRes = String(raw.res);
      if ([5, 15, 30, 60].includes(Number(raw.fps))) screenFps = Number(raw.fps);
    } catch (_) {}
  }

  function saveQualityPrefs() {
    try {
      localStorage.setItem(QUALITY_KEY, JSON.stringify({ res: screenRes, fps: screenFps }));
    } catch (_) {}
  }

  function syncQualityUi() {
    qualityResBox?.querySelectorAll("[data-res]").forEach((btn) => {
      btn.classList.toggle("is-on", btn.getAttribute("data-res") === screenRes);
    });
    qualityFpsBox?.querySelectorAll("[data-fps]").forEach((btn) => {
      btn.classList.toggle("is-on", Number(btn.getAttribute("data-fps")) === screenFps);
    });
  }

  function openQualityModal() {
    loadQualityPrefs();
    syncQualityUi();
    if (qualityModal) qualityModal.hidden = false;
  }

  function closeQualityModal() {
    if (qualityModal) qualityModal.hidden = true;
  }

  function bitrateFor(resKey, fps) {
    const base = {
      source: 8_000_000,
      "1440": 10_000_000,
      "1080": 6_000_000,
      "720": 3_500_000,
      "480": 1_500_000,
    }[resKey] || 6_000_000;
    if (fps >= 60) return Math.round(base * 1.35);
    if (fps <= 15) return Math.round(base * 0.65);
    return base;
  }

  function screenCaptureOpts() {
    const res = RES_MAP[screenRes];
    const opts = {
      audio: false,
      contentHint: "detail",
    };
    if (res) {
      opts.resolution = {
        width: res.width,
        height: res.height,
        frameRate: screenFps,
      };
    }
    // "Fonte": sem resolution — browser captura nativo; FPS no publishEncoding.
    return opts;
  }

  function screenPublishOpts() {
    return {
      screenShareEncoding: {
        maxBitrate: bitrateFor(screenRes, screenFps),
        maxFramerate: screenFps,
      },
      simulcast: false,
    };
  }

  function openQualityModal() {
    loadQualityPrefs();
    syncQualityUi();
    if (qualityGo) {
      qualityGo.textContent = sharing ? "Aplicar qualidade" : "Compartilhar tela";
    }
    if (qualityStop) qualityStop.hidden = !sharing;
    if (qualityModal) qualityModal.hidden = false;
  }

  function closeQualityModal() {
    if (qualityModal) qualityModal.hidden = true;
  }

  /** Clique no monitor: sempre abre qualidade; se já compartilha, dá pra reaplicar ou parar no modal. */
  function onScreenBtnClick() {
    if (!inRoom()) return;
    openQualityModal();
  }

  async function startScreenShare() {
    if (!inRoom()) return;
    saveQualityPrefs();
    closeQualityModal();
    setHint(`Pedindo tela (${screenRes === "source" ? "fonte" : screenRes + "p"} @ ${screenFps}fps)…`);
    try {
      // Já compartilhando: reinicia com a nova qualidade.
      if (sharing) {
        try {
          await room.localParticipant.setScreenShareEnabled(false);
        } catch (_) {}
        sharing = false;
        clearScreen();
      }

      await room.localParticipant.setScreenShareEnabled(
        true,
        screenCaptureOpts(),
        screenPublishOpts()
      );
      sharing = true;
      screenBtn?.classList.add("is-on");
      room.localParticipant.trackPublications?.forEach((pub) => {
        if (String(pub.source || "").includes("screen") && pub.track) {
          attachScreen(pub.track, "você", pub);
        }
      });
      setHint(
        `Tela em ${screenRes === "source" ? "fonte" : screenRes + "p"} / ${screenFps}fps — clique no monitor pra parar.`
      );
    } catch (_) {
      sharing = false;
      screenBtn?.classList.remove("is-on");
      clearScreen();
      setHint("Compartilhar tela cancelado — você continua na sala.");
    }
  }

  async function stopScreenShare() {
    if (!inRoom()) return;
    try {
      await room.localParticipant.setScreenShareEnabled(false);
    } catch (_) {}
    sharing = false;
    screenBtn?.classList.remove("is-on");
    clearScreen();
    setHint("Compartilhamento parado.");
  }

  function setDeafened(on) {
    deafened = on;
    audioEls.forEach((el) => { el.muted = on; });
    deafenBtn?.classList.toggle("is-muted", on);
  }

  // Delegação: funciona mesmo se lucide recriar ícones no botão.
  root.addEventListener("click", (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;

    if (t.closest("#call-enable-mic-btn")) {
      e.preventDefault();
      enableMic().catch(console.error);
      return;
    }
    if (t.closest("#call-mic-btn")) {
      e.preventDefault();
      toggleMic().catch(console.error);
      return;
    }
    if (t.closest("#call-join-btn") || t.closest("#call-channel-btn")) {
      if (!room) join({ auto: false }).catch(console.error);
      return;
    }
    if (t.closest("#call-leave-btn")) {
      leave().catch(console.error);
      return;
    }
    if (t.closest("#call-screen-btn")) {
      onScreenBtnClick();
      return;
    }
    if (t.closest("#call-deafen-btn")) {
      setDeafened(!deafened);
      return;
    }
    if (t.closest("#call-screen-wrap")) {
      toggleScreenExpand();
    }
  });

  qualityResBox?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-res]");
    if (!btn) return;
    screenRes = btn.getAttribute("data-res") || "1080";
    syncQualityUi();
  });
  qualityFpsBox?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-fps]");
    if (!btn) return;
    screenFps = Number(btn.getAttribute("data-fps")) || 30;
    syncQualityUi();
  });
  qualityGo?.addEventListener("click", () => startScreenShare().catch(console.error));
  qualityModal?.addEventListener("click", (e) => {
    if (e.target.closest("[data-quality-close]")) closeQualityModal();
  });
  loadQualityPrefs();
  syncQualityUi();

  fabChat?.addEventListener("click", () => chatPanel?.classList.add("is-open"));
  chatToggle?.addEventListener("click", () => chatPanel?.classList.remove("is-open"));

  chatForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!inRoom()) return;
    const text = (chatInput.value || "").trim();
    if (!text) return;
    const payload = new TextEncoder().encode(JSON.stringify({ t: "chat", text }));
    await room.localParticipant.publishData(payload, { reliable: true });
    appendChat("você", text);
    chatInput.value = "";
  });

  window.addEventListener("pagehide", () => {
    if (room && !intentionalLeave) rememberJoin(true);
  });

  setMicUi();
  setConnectedUi(false);
  showMicBanner(false);
  if (window.lucide) window.lucide.createIcons();

  try {
    if (sessionStorage.getItem(REJOIN_KEY) === "1") {
      setTimeout(() => join({ auto: true }).catch(console.error), 400);
    }
  } catch (_) {}
})();
