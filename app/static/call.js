/**
 * Call — Discord-like + LiveKit.
 * Fluxo: ENTRA na sala primeiro → depois pede mic (banner / botão).
 */
(function () {
  const root = document.getElementById("call-page");
  if (!root || root.dataset.ready !== "1") return;

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
  const myAvatar = document.getElementById("call-my-avatar");

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

  function isMicMuted(p) {
    try {
      if (typeof p.isMicrophoneEnabled === "boolean") return !p.isMicrophoneEnabled;
    } catch (_) {}
    return false;
  }

  function showMicBanner(show) {
    if (micBanner) micBanner.hidden = !show;
  }

  function setMicUi() {
    micBtn?.classList.toggle("is-muted", !micOn);
    micBtn?.removeAttribute("disabled");
    if (micBtn) {
      micBtn.disabled = !room;
      micBtn.innerHTML = micOn
        ? '<i data-lucide="mic"></i>'
        : '<i data-lucide="mic-off"></i>';
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
    if (myStatus) myStatus.textContent = on ? (micOn ? "Em voz" : "Em voz · mudo") : "Offline";
    if (fabChat) fabChat.hidden = !on && window.matchMedia("(max-width: 960px)").matches ? false : !on;
    // fab only on mobile when connected
    if (fabChat) {
      const mobile = window.matchMedia("(max-width: 960px)").matches;
      fabChat.hidden = !(on && mobile);
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

    // tiles
    if (tilesEl) {
      if (!r) {
        tilesEl.innerHTML = "";
      } else {
        tilesEl.innerHTML = people
          .map(({ p, self }) => {
            const name = labelOf(p) + (self ? " (você)" : "");
            const speaking = !!p.isSpeaking;
            const muted = isMicMuted(p);
            return `<div class="dc-tile${speaking ? " is-speaking" : ""}" data-id="${escapeHtml(p.identity || "")}">
              <div class="dc-avatar" style="background:${colorFor(p.identity)}">${escapeHtml(initialFor(labelOf(p)))}</div>
              <div class="dc-tile-name">
                ${muted ? '<i data-lucide="mic-off"></i>' : ""}
                <span>${escapeHtml(name)}</span>
              </div>
            </div>`;
          })
          .join("");
      }
    }

    // sidebar voice members
    if (voiceMembersEl) {
      if (!r) {
        voiceMembersEl.innerHTML = "";
      } else {
        voiceMembersEl.innerHTML = people
          .map(({ p, self }) => {
            const name = labelOf(p) + (self ? " (você)" : "");
            const speaking = !!p.isSpeaking;
            const muted = isMicMuted(p);
            return `<div class="dc-voice-member${speaking ? " is-speaking" : ""}">
              <span class="av" style="background:${colorFor(p.identity)}">${escapeHtml(initialFor(labelOf(p)))}</span>
              <span>${escapeHtml(name)}</span>
              ${muted ? '<i data-lucide="mic-off" class="mic-off"></i>' : ""}
            </div>`;
          })
          .join("");
      }
    }

    if (myAvatar && root.dataset.me) {
      myAvatar.style.background = colorFor("u-self");
    }
    if (myStatus && r) {
      myStatus.textContent = micOn ? "Em voz" : "Em voz · mudo";
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
    if (screenBadge) screenBadge.textContent = who ? `${who}` : "Tela";
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
    const p = el.play?.();
    if (p && p.catch) p.catch(() => {});
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
      s.src = "https://cdn.jsdelivr.net/npm/livekit-client@2.9.1/dist/livekit-client.umd.min.js";
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
    if (!data.url || !data.token) {
      throw new Error("Servidor sem url/token LiveKit.");
    }
    return data;
  }

  async function safeDisconnect(r) {
    if (!r) return;
    try { await r.disconnect(); } catch (_) {}
  }

  /** Liga mic DEPOIS de já estar na sala (gesto do usuário). */
  async function enableMic() {
    if (!room?.localParticipant) return;
    try {
      await room.localParticipant.setMicrophoneEnabled(true);
      micOn = true;
      showMicBanner(false);
      setHint("");
      setMicUi();
      refreshUi();
    } catch (err) {
      console.warn("mic", err);
      micOn = false;
      showMicBanner(true);
      setMicUi();
      setHint(
        "O navegador bloqueou o mic. Toque em «Permitir microfone» de novo ou libere no cadeado da URL."
      );
    }
  }

  async function join() {
    if (joining || room) return;
    joining = true;
    intentionalLeave = false;
    setStatus("Conectando…", "wait");
    setHint("Entrando na sala…");
    if (joinBtn) joinBtn.disabled = true;

    let r = null;
    try {
      // NÃO pede mic aqui — igual Discord: entra primeiro.
      const [sdk, tokenData] = await Promise.all([
        lkReady.then((x) => x || loadLivekitScript()),
        fetchToken(),
      ]);
      LK = sdk;
      if (!LK?.Room) throw new Error("LiveKit JS não carregou. Verifique a rede/CDN.");

      r = new LK.Room({
        adaptiveStream: true,
        dynacast: true,
        disconnectOnPageLeave: true,
        audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true },
      });
      room = r;

      const screenSource = LK.Track?.Source?.ScreenShare || "screen_share";

      r.on(ev("ParticipantConnected", "participantConnected"), () => refreshUi());
      r.on(ev("ParticipantDisconnected", "participantDisconnected"), (p) => {
        if (p?.identity) detachRemoteAudio(p.identity);
        refreshUi();
      });
      r.on(ev("ActiveSpeakersChanged", "activeSpeakersChanged"), () => refreshUi());
      r.on(ev("TrackMuted", "trackMuted"), () => refreshUi());
      r.on(ev("TrackUnmuted", "trackUnmuted"), () => refreshUi());
      r.on(ev("LocalTrackPublished", "localTrackPublished"), () => refreshUi());
      r.on(ev("LocalTrackUnpublished", "localTrackUnpublished"), () => refreshUi());

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
        console.warn("disconnected", reason);
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
          setStatus("Desconectado");
          setHint("");
        } else {
          setStatus("Caiu", "error");
          setHint("Conexão caiu. Entre na sala de novo.");
        }
        if (joinBtn) joinBtn.disabled = false;
        if (lobbyEl) lobbyEl.hidden = false;
      });

      await r.connect(String(tokenData.url).trim(), String(tokenData.token).trim());

      if (!r.localParticipant) {
        throw new Error("Sem participante local — confira LIVEKIT_API_SECRET.");
      }

      // Já está NA SALA. Agora tenta mic (pode abrir o popup do browser).
      joining = false;
      setConnectedUi(true);
      setStatus("Na sala", "live");
      setHint("");
      refreshUi();

      // Tenta ligar mic; se falhar, fica na sala mudo + banner (gesto explícito no mobile).
      try {
        await r.localParticipant.setMicrophoneEnabled(true);
        micOn = true;
        showMicBanner(false);
      } catch (micErr) {
        console.warn(micErr);
        micOn = false;
        showMicBanner(true);
        setHint("Você já entrou. Toque em «Permitir microfone» abaixo.");
      }
      setMicUi();
      refreshUi();

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
    if (!micOn) {
      await enableMic();
      return;
    }
    try {
      await room.localParticipant.setMicrophoneEnabled(false);
      micOn = false;
      setMicUi();
      refreshUi();
    } catch (err) {
      setHint(String(err.message || err));
    }
  }

  async function toggleScreen() {
    if (!room?.localParticipant) return;
    try {
      const next = !sharing;
      await room.localParticipant.setScreenShareEnabled(next, { audio: true });
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

  joinBtn?.addEventListener("click", () => join().catch(console.error));
  document.getElementById("call-channel-btn")?.addEventListener("click", () => {
    if (!room) join().catch(console.error);
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

  window.addEventListener("beforeunload", () => {
    intentionalLeave = true;
    try { room?.disconnect(); } catch (_) {}
  });

  setMicUi();
  setConnectedUi(false);
  if (window.lucide) window.lucide.createIcons();
})();
