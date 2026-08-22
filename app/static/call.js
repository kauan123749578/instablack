/**
 * Call — LiveKit (mic sólido + reconnect + auto-reentrar).
 *
 * Mic: createLocalAudioTrack + publish (não confiar só em setMicrophoneEnabled).
 * Queda: reconnect UI; reload → auto-join via sessionStorage.
 * disconnectOnPageLeave: false (só sai no botão Sair).
 */
(function () {
  const root = document.getElementById("call-page");
  if (!root || root.dataset.ready !== "1") return;

  const REJOIN_KEY = "ib_call_rejoin";

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

  function participantMicMuted(p) {
    try {
      if (p === room?.localParticipant) return !micOn;
      if (typeof p.isMicrophoneEnabled === "boolean") return !p.isMicrophoneEnabled;
    } catch (_) {}
    return false;
  }

  function showMicBanner(show) {
    if (micBanner) micBanner.hidden = !show;
  }

  function setMicUi() {
    if (!micBtn) return;
    micBtn.disabled = !room;
    micBtn.classList.toggle("is-muted", !micOn);
    micBtn.innerHTML = micOn
      ? '<i data-lucide="mic"></i>'
      : '<i data-lucide="mic-off"></i>';
    if (myStatus && room) {
      myStatus.textContent = micOn ? "Em voz" : "Em voz · mudo — clique no mic";
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

  function attachScreen(track, who) {
    if (!screenEl || !track?.attach) return;
    track.attach(screenEl);
    if (screenWrap) screenWrap.hidden = false;
    if (screenBadge) screenBadge.textContent = who || "Tela";
  }

  function clearScreen() {
    if (screenEl) {
      try { screenEl.srcObject = null; } catch (_) {}
    }
    if (screenWrap) screenWrap.hidden = true;
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
        "X-CSRF-Token": csrf(),
        "X-Requested-With": "fetch",
        Accept: "application/json",
      },
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
    try {
      const enabled = !!room?.localParticipant?.isMicrophoneEnabled;
      micOn = enabled;
    } catch (_) {}
    setMicUi();
    showMicBanner(!!room && !micOn);
    refreshUi();
  }

  /**
   * Publica mic de forma explícita (createLocalAudioTrack).
   * Deve ser chamado a partir de clique do usuário.
   */
  async function enableMic() {
    if (!room?.localParticipant || !LK) return;
    if (micPublishing) return;
    micPublishing = true;
    setHint("Ativando microfone…");
    try {
      // 1) Prova o device (erro claro se falhar)
      const probe = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
      const deviceId = probe.getAudioTracks()[0]?.getSettings?.().deviceId;
      probe.getTracks().forEach((t) => t.stop());

      // 2) Track do LiveKit
      let audioTrack;
      if (typeof LK.createLocalAudioTrack === "function") {
        audioTrack = await LK.createLocalAudioTrack({
          deviceId,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        });
      } else {
        await room.localParticipant.setMicrophoneEnabled(true);
        micOn = !!room.localParticipant.isMicrophoneEnabled;
        if (!micOn) throw new Error("setMicrophoneEnabled não ligou o mic");
        showMicBanner(false);
        setHint("Microfone ligado.");
        setMicUi();
        refreshUi();
        return;
      }

      // 3) Se já tinha mic, desliga antes
      try {
        await room.localParticipant.setMicrophoneEnabled(false);
      } catch (_) {}

      const source = LK.Track?.Source?.Microphone;
      await room.localParticipant.publishTrack(
        audioTrack,
        source ? { source } : undefined
      );

      // Garante unmuted
      try {
        await room.localParticipant.setMicrophoneEnabled(true);
      } catch (_) {}

      micOn = true;
      showMicBanner(false);
      setHint("Microfone ligado. Fale normalmente.");
      setMicUi();
      refreshUi();

      // Confere 300ms depois (alguns browsers atrasam o estado)
      setTimeout(syncMicFromRoom, 400);
    } catch (err) {
      console.error("enableMic", err);
      micOn = false;
      showMicBanner(true);
      setMicUi();
      const name = err?.name || "";
      const msg = String(err?.message || err);
      if (name === "NotAllowedError" || /Permission|NotAllowed/i.test(msg)) {
        setHint("Permissão negada neste clique. Clique de novo em «Permitir microfone».");
      } else if (name === "NotFoundError") {
        setHint("Nenhum microfone encontrado no dispositivo.");
      } else if (name === "NotReadableError") {
        setHint("Microfone em uso por outro app (Discord/Zoom). Feche e tente de novo.");
      } else {
        setHint("Mic falhou: " + msg);
      }
    } finally {
      micPublishing = false;
    }
  }

  async function muteMic() {
    if (!room?.localParticipant) return;
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
        // false: F5 não “mata” de propósito via API — ainda assim o WS cai;
        // usamos auto-rejoin. Sair só pelo botão.
        disconnectOnPageLeave: false,
        audioCaptureDefaults: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
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
        if (room?.localParticipant) syncMicFromRoom();
        else refreshUi();
      });
      r.on(ev("TrackUnmuted", "trackUnmuted"), () => {
        if (room?.localParticipant) syncMicFromRoom();
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
          attachScreen(track, labelOf(participant));
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
        // Durante reconnect o client às vezes dispara estados — só limpa se for disconnect final
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
        setConnectedUi(false);
        setMicUi();
        refreshUi();
        if (intentionalLeave) {
          rememberJoin(false);
          showMicBanner(false);
          setStatus("Desconectado");
          setHint("");
        } else {
          setStatus("Caiu", "error");
          setHint("Conexão caiu. Reentrando…");
          rememberJoin(true);
          showMicBanner(false);
          // Auto-rejoin após queda
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

      // Mic: NÃO força no auto-rejoin sem gesto (mobile). No clique manual tenta.
      if (!auto) {
        showMicBanner(true);
        setHint("Na sala. Clique em «Permitir microfone» (ou no ícone do mic) para falar.");
        // Tenta já — se o site já tem permissão, liga; senão o banner resolve.
        enableMic().catch(() => {});
      } else {
        showMicBanner(true);
        setHint("Reentrou. Clique no microfone para ligar de novo.");
      }
      setMicUi();

      remoteList(r).forEach((p) => {
        p.trackPublications?.forEach((pub) => {
          if (pub.isSubscribed && pub.track) {
            if (pub.track.kind === "audio") attachRemoteAudio(pub.track, p);
            if (pub.track.kind === "video" && (pub.source === screenSource || String(pub.source).includes("screen"))) {
              attachScreen(pub.track, labelOf(p));
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
    if (!room?.localParticipant) return;
    if (micOn) await muteMic();
    else await enableMic();
  }

  async function toggleScreen() {
    if (!room?.localParticipant) return;
    try {
      const next = !sharing;
      await room.localParticipant.setScreenShareEnabled(next, { audio: false });
      sharing = next;
      screenBtn?.classList.toggle("is-on", sharing);
      if (sharing) {
        room.localParticipant.trackPublications?.forEach((pub) => {
          if (String(pub.source || "").includes("screen") && pub.track) {
            attachScreen(pub.track, "você");
          }
        });
      } else {
        clearScreen();
      }
    } catch (_) {
      sharing = false;
      screenBtn?.classList.remove("is-on");
      setHint("Compartilhar tela cancelado — você continua na sala.");
    }
  }

  function setDeafened(on) {
    deafened = on;
    audioEls.forEach((el) => { el.muted = on; });
    deafenBtn?.classList.toggle("is-muted", on);
  }

  joinBtn?.addEventListener("click", () => join({ auto: false }).catch(console.error));
  document.getElementById("call-channel-btn")?.addEventListener("click", () => {
    if (!room) join({ auto: false }).catch(console.error);
  });
  leaveBtn?.addEventListener("click", () => leave().catch(console.error));
  micBtn?.addEventListener("click", () => toggleMic().catch(console.error));
  enableMicBtn?.addEventListener("click", () => enableMic().catch(console.error));
  screenBtn?.addEventListener("click", () => toggleScreen().catch(console.error));
  deafenBtn?.addEventListener("click", () => setDeafened(!deafened));
  fabChat?.addEventListener("click", () => chatPanel?.classList.add("is-open"));
  chatToggle?.addEventListener("click", () => chatPanel?.classList.remove("is-open"));

  chatForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!room?.localParticipant) return;
    const text = (chatInput.value || "").trim();
    if (!text) return;
    const payload = new TextEncoder().encode(JSON.stringify({ t: "chat", text }));
    await room.localParticipant.publishData(payload, { reliable: true });
    appendChat("você", text);
    chatInput.value = "";
  });

  // Não força disconnect no beforeunload — deixa o WS cair; auto-rejoin se REJOIN_KEY
  window.addEventListener("pagehide", () => {
    if (room && !intentionalLeave) rememberJoin(true);
  });

  setMicUi();
  setConnectedUi(false);
  if (window.lucide) window.lucide.createIcons();

  // Auto-reentrar após F5
  try {
    if (sessionStorage.getItem(REJOIN_KEY) === "1") {
      setTimeout(() => join({ auto: true }).catch(console.error), 400);
    }
  } catch (_) {}
})();
