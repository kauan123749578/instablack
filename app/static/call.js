/**
 * Call — LiveKit + UI tipo Discord (tiles, falando, dock).
 */
(function () {
  const root = document.getElementById("call-page");
  if (!root || root.dataset.ready !== "1") return;

  const statusEl = document.getElementById("call-status");
  const countEl = document.getElementById("call-count");
  const tilesEl = document.getElementById("call-tiles");
  const emptyEl = document.getElementById("call-empty");
  const joinBtn = document.getElementById("call-join-btn");
  const screenWrap = document.getElementById("call-screen-wrap");
  const screenEl = document.getElementById("call-screen");
  const screenBadge = document.getElementById("call-screen-badge");
  const hintEl = document.getElementById("call-hint");
  const chatLog = document.getElementById("call-chat-log");
  const chatForm = document.getElementById("call-chat-form");
  const chatInput = document.getElementById("call-chat-input");
  const chatSend = document.getElementById("call-chat-send");

  const micBtn = document.getElementById("call-mic-btn");
  const screenBtn = document.getElementById("call-screen-btn");
  const deafenBtn = document.getElementById("call-deafen-btn");
  const leaveBtn = document.getElementById("call-leave-btn");

  const AVATAR_COLORS = [
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
  /** @type {Map<string, HTMLAudioElement>} */
  const audioEls = new Map();

  // Pré-carrega o SDK enquanto a página abre (acelera o 1º join).
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

  function setConnected(on) {
    if (micBtn) micBtn.disabled = !on;
    if (screenBtn) screenBtn.disabled = !on;
    if (deafenBtn) deafenBtn.disabled = !on;
    if (leaveBtn) leaveBtn.disabled = !on;
    if (chatInput) chatInput.disabled = !on;
    if (chatSend) chatSend.disabled = !on;
    if (emptyEl) emptyEl.hidden = on;
    if (joinBtn && emptyEl) {
      // join fica só no empty state
    }
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
    return AVATAR_COLORS[h % AVATAR_COLORS.length];
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
    if (!r?.remoteParticipants) return out;
    r.remoteParticipants.forEach((p) => out.push(p));
    return out;
  }

  function isMicMuted(p) {
    try {
      if (typeof p.isMicrophoneEnabled === "boolean") return !p.isMicrophoneEnabled;
      const pubs = p.audioTrackPublications || p.trackPublications;
      if (!pubs) return false;
      let has = false;
      let muted = true;
      pubs.forEach((pub) => {
        const src = String(pub.source || "");
        if (pub.kind === "audio" || src.includes("mic") || src === "microphone") {
          has = true;
          if (!pub.isMuted) muted = false;
        }
      });
      return has ? muted : false;
    } catch (_) {
      return false;
    }
  }

  function refreshTiles() {
    const r = room;
    if (!tilesEl) return;
    if (!r) {
      if (emptyEl) {
        tilesEl.innerHTML = "";
        tilesEl.appendChild(emptyEl);
        emptyEl.hidden = false;
      }
      if (countEl) countEl.textContent = "0 na sala";
      return;
    }

    const people = [];
    if (r.localParticipant) people.push({ p: r.localParticipant, self: true });
    remoteList(r).forEach((p) => people.push({ p, self: false }));

    if (countEl) {
      const n = people.length;
      countEl.textContent = n === 1 ? "1 na sala" : `${n} na sala`;
    }

    const frag = document.createDocumentFragment();
    if (emptyEl) {
      emptyEl.hidden = true;
      if (emptyEl.parentNode) emptyEl.parentNode.removeChild(emptyEl);
    }

    people.forEach(({ p, self }) => {
      const name = labelOf(p) + (self ? " (você)" : "");
      const speaking = !!p.isSpeaking;
      const muted = isMicMuted(p);
      const tile = document.createElement("div");
      tile.className = "dc-tile" + (speaking ? " is-speaking" : "") + (muted ? " is-muted" : "") + (self ? " dc-tile-self" : "");
      tile.dataset.id = p.identity || "";
      tile.innerHTML = `
        <div class="dc-avatar" style="background:${colorFor(p.identity)}">${escapeHtml(initialFor(labelOf(p)))}</div>
        <div class="dc-tile-name">
          ${muted ? '<i data-lucide="mic-off" class="dc-mic-badge"></i>' : ""}
          <span>${escapeHtml(name)}</span>
        </div>`;
      frag.appendChild(tile);
    });

    tilesEl.innerHTML = "";
    tilesEl.appendChild(frag);
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
    if (screenBadge) screenBadge.textContent = who ? `${who} · tela` : "Compartilhando tela";
  }

  function clearScreen() {
    if (screenEl) {
      try { screenEl.srcObject = null; } catch (_) {}
    }
    if (screenWrap) screenWrap.hidden = true;
  }

  function attachRemoteAudio(track, participant) {
    if (!track || track.kind !== "audio") return;
    const id = participant?.identity || track.sid || Math.random().toString(36);
    let el = audioEls.get(id);
    if (!el) {
      el = document.createElement("audio");
      el.autoplay = true;
      el.playsInline = true;
      el.style.display = "none";
      document.body.appendChild(el);
      audioEls.set(id, el);
    }
    track.attach(el);
    el.muted = deafened;
    el.play?.().catch(() => {});
  }

  function detachRemoteAudio(participantId) {
    const el = audioEls.get(participantId);
    if (!el) return;
    try { el.srcObject = null; } catch (_) {}
    el.remove();
    audioEls.delete(participantId);
  }

  function setDeafened(on) {
    deafened = on;
    audioEls.forEach((el) => { el.muted = on; });
    deafenBtn?.classList.toggle("is-off", on);
    deafenBtn?.classList.toggle("is-on", !on);
  }

  function updateMicUi() {
    micBtn?.classList.toggle("is-off", !micOn);
    micBtn?.classList.toggle("is-on", micOn);
    const icon = micBtn?.querySelector("[data-lucide], .lucide");
    if (micBtn) {
      micBtn.innerHTML = micOn
        ? '<i data-lucide="mic"></i>'
        : '<i data-lucide="mic-off"></i>';
      if (window.lucide) window.lucide.createIcons();
    }
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

  /** Pede mic no browser ANTES do LiveKit (mensagem clara se bloquear). */
  async function ensureMicPermission() {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Este navegador não permite microfone.");
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
        video: false,
      });
      stream.getTracks().forEach((t) => t.stop());
      return true;
    } catch (err) {
      const name = err?.name || "";
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        throw new Error(
          "Microfone bloqueado. No Chrome: cadeado ao lado da URL → Microfone → Permitir → recarregue e entre de novo."
        );
      }
      if (name === "NotFoundError") {
        throw new Error("Nenhum microfone encontrado neste PC.");
      }
      throw new Error(err?.message || "Não foi possível acessar o microfone.");
    }
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
      throw new Error("Servidor sem url/token LiveKit. Confira LIVEKIT_* no Railway.");
    }
    return data;
  }

  async function safeDisconnect(r) {
    if (!r) return;
    try { await r.disconnect(); } catch (_) {}
  }

  function wipeAudio() {
    audioEls.forEach((el) => {
      try { el.srcObject = null; } catch (_) {}
      el.remove();
    });
    audioEls.clear();
  }

  async function join() {
    if (joining || room) return;
    joining = true;
    intentionalLeave = false;
    setStatus("Conectando…", "wait");
    setHint("Pedindo microfone e conectando…");
    if (joinBtn) joinBtn.disabled = true;

    let r = null;
    try {
      // Mic + SDK + token em paralelo (mais rápido).
      const [micOk, sdk, tokenData] = await Promise.all([
        ensureMicPermission(),
        lkReady.then((x) => x || loadLivekitScript()),
        fetchToken(),
      ]);
      LK = sdk;
      if (!LK?.Room) throw new Error("LiveKit JS não carregou.");

      r = new LK.Room({
        adaptiveStream: true,
        dynacast: true,
        disconnectOnPageLeave: true,
        audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true },
      });
      room = r;

      const screenSource = LK.Track?.Source?.ScreenShare || "screen_share";

      r.on(ev("ParticipantConnected", "participantConnected"), () => refreshTiles());
      r.on(ev("ParticipantDisconnected", "participantDisconnected"), (p) => {
        if (p?.identity) detachRemoteAudio(p.identity);
        refreshTiles();
      });
      r.on(ev("ActiveSpeakersChanged", "activeSpeakersChanged"), () => refreshTiles());
      r.on(ev("TrackMuted", "trackMuted"), () => refreshTiles());
      r.on(ev("TrackUnmuted", "trackUnmuted"), () => refreshTiles());
      r.on(ev("LocalTrackPublished", "localTrackPublished"), () => refreshTiles());
      r.on(ev("LocalTrackUnpublished", "localTrackUnpublished"), () => refreshTiles());

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
          // Só limpa se for a tela atual — não sai da sala.
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
        console.warn("LiveKit disconnected", reason);
        room = null;
        sharing = false;
        micOn = false;
        wipeAudio();
        clearScreen();
        setConnected(false);
        updateMicUi();
        refreshTiles();
        if (intentionalLeave) {
          setStatus("Desconectado");
          setHint("");
        } else {
          setStatus("Caiu da sala", "error");
          setHint(
            "A conexão caiu (rede/aba em segundo plano). Clique em Entrar na sala de novo."
          );
        }
        if (joinBtn) joinBtn.disabled = false;
        if (emptyEl && !emptyEl.parentNode && tilesEl) {
          tilesEl.appendChild(emptyEl);
          emptyEl.hidden = false;
        }
      });

      await r.connect(String(tokenData.url).trim(), String(tokenData.token).trim());

      if (!r.localParticipant) {
        throw new Error("Conectou sem participante. Confira LIVEKIT_API_SECRET no Railway.");
      }

      // Liga mic de verdade no LiveKit (permissão já liberada).
      try {
        await r.localParticipant.setMicrophoneEnabled(true);
        micOn = true;
      } catch (micErr) {
        console.warn(micErr);
        micOn = false;
        setHint("Na sala, mas mic falhou. Clique no botão do microfone na barra de baixo.");
      }
      updateMicUi();

      // Áudios / telas já publicados
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

      joining = false;
      setConnected(true);
      setStatus("Na sala", "live");
      if (micOn) setHint("Mic ligado. Use o ícone da tela na barra para compartilhar.");
      refreshTiles();
      if (window.lucide) window.lucide.createIcons();
    } catch (err) {
      console.error(err);
      joining = false;
      if (room === r) room = null;
      await safeDisconnect(r);
      wipeAudio();
      clearScreen();
      setConnected(false);
      micOn = false;
      updateMicUi();
      setStatus("Falha", "error");
      setHint(String(err.message || err));
      if (joinBtn) joinBtn.disabled = false;
      if (emptyEl && tilesEl && !emptyEl.parentNode) {
        tilesEl.appendChild(emptyEl);
        emptyEl.hidden = false;
      }
      refreshTiles();
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
    setConnected(false);
    updateMicUi();
    setStatus("Desconectado");
    setHint("");
    if (joinBtn) joinBtn.disabled = false;
    if (emptyEl && tilesEl) {
      tilesEl.innerHTML = "";
      tilesEl.appendChild(emptyEl);
      emptyEl.hidden = false;
    }
    refreshTiles();
  }

  async function toggleMic() {
    if (!room?.localParticipant) return;
    try {
      if (!micOn) {
        // Tenta liberar de novo no browser se estava bloqueado.
        await ensureMicPermission();
      }
      micOn = !micOn;
      await room.localParticipant.setMicrophoneEnabled(micOn);
      updateMicUi();
      refreshTiles();
      setHint(micOn ? "Mic ligado." : "Mic mutado.");
    } catch (err) {
      micOn = false;
      updateMicUi();
      setHint(String(err.message || err));
    }
  }

  async function toggleScreen() {
    if (!room?.localParticipant) return;
    try {
      const next = !sharing;
      await room.localParticipant.setScreenShareEnabled(next, {
        audio: true,
      });
      sharing = next;
      screenBtn?.classList.toggle("is-on", sharing);
      if (sharing) {
        room.localParticipant.trackPublications?.forEach((pub) => {
          if (String(pub.source || "").includes("screen") && pub.track) {
            attachScreen(pub.track, "você");
          }
        });
        setHint("Compartilhando tela. Pare pelo mesmo botão ou pelo Chrome.");
      } else {
        clearScreen();
        setHint("Compartilhamento parado — você continua na sala.");
      }
    } catch (err) {
      sharing = false;
      screenBtn?.classList.remove("is-on");
      // Cancelar o picker do Chrome NÃO deve derrubar a call.
      setHint("Compartilhar tela cancelado. Você continua na sala.");
    }
  }

  joinBtn?.addEventListener("click", () => join().catch(console.error));
  leaveBtn?.addEventListener("click", () => leave().catch(console.error));
  micBtn?.addEventListener("click", () => toggleMic().catch(console.error));
  screenBtn?.addEventListener("click", () => toggleScreen().catch(console.error));
  deafenBtn?.addEventListener("click", () => setDeafened(!deafened));

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

  updateMicUi();
  if (window.lucide) window.lucide.createIcons();
})();
