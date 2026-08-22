/**
 * Call — LiveKit (voz + screen share + chat via data messages).
 */
(function () {
  const root = document.getElementById("call-page");
  if (!root || root.dataset.ready !== "1") return;

  const statusEl = document.getElementById("call-status");
  const peopleEl = document.getElementById("call-people");
  const stageEl = document.getElementById("call-stage");
  const screenEl = document.getElementById("call-screen");
  const emptyEl = document.getElementById("call-stage-empty");
  const hintEl = document.getElementById("call-hint");
  const chatLog = document.getElementById("call-chat-log");
  const chatForm = document.getElementById("call-chat-form");
  const chatInput = document.getElementById("call-chat-input");
  const chatSend = document.getElementById("call-chat-send");

  const joinBtn = document.getElementById("call-join-btn");
  const micBtn = document.getElementById("call-mic-btn");
  const screenBtn = document.getElementById("call-screen-btn");
  const leaveBtn = document.getElementById("call-leave-btn");

  /** @type {import('livekit-client').Room | null} */
  let room = null;
  let micOn = true;
  let sharing = false;
  let joining = false;

  function csrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content || "";
  }

  function setStatus(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.classList.toggle("is-live", kind === "live");
    statusEl.classList.toggle("is-error", kind === "error");
  }

  function setConnected(on) {
    if (joinBtn) joinBtn.disabled = on || joining;
    if (micBtn) micBtn.disabled = !on;
    if (screenBtn) screenBtn.disabled = !on;
    if (leaveBtn) leaveBtn.disabled = !on;
    if (chatInput) chatInput.disabled = !on;
    if (chatSend) chatSend.disabled = !on;
  }

  function participantLabel(p) {
    return (p && (p.name || p.identity)) || "Alguém";
  }

  function remoteList(r) {
    const out = [];
    if (!r || !r.remoteParticipants) return out;
    if (typeof r.remoteParticipants.forEach === "function") {
      r.remoteParticipants.forEach((p) => out.push(p));
    } else if (typeof r.remoteParticipants.values === "function") {
      for (const p of r.remoteParticipants.values()) out.push(p);
    }
    return out;
  }

  function publicationsOf(participant) {
    const out = [];
    const pubs = participant && participant.trackPublications;
    if (!pubs) return out;
    if (typeof pubs.forEach === "function") {
      pubs.forEach((pub) => out.push(pub));
    } else if (typeof pubs.values === "function") {
      for (const pub of pubs.values()) out.push(pub);
    }
    return out;
  }

  function isScreenPub(pub, screenSource) {
    if (!pub) return false;
    const src = pub.source;
    return src === screenSource || src === "screen_share" || String(src || "").toLowerCase().includes("screen");
  }

  function refreshPeople() {
    const r = room;
    if (!r || !peopleEl) return;
    try {
      const list = [];
      if (r.localParticipant) list.push({ p: r.localParticipant, self: true });
      remoteList(r).forEach((p) => list.push({ p, self: false }));
      if (!list.length) {
        peopleEl.innerHTML = '<li class="muted">Ninguém ainda</li>';
        return;
      }
      peopleEl.innerHTML = list
        .map(({ p, self }) => {
          const speaking = !!p.isSpeaking;
          const name = participantLabel(p) + (self ? " (você)" : "");
          return `<li class="${speaking ? "is-speaking" : ""}" data-id="${escapeHtml(p.identity || "")}">
            <span class="dot" aria-hidden="true"></span>
            <span>${escapeHtml(name)}</span>
          </li>`;
        })
        .join("");
    } catch (err) {
      console.warn("refreshPeople", err);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function appendChat(from, text) {
    if (!chatLog) return;
    const line = document.createElement("div");
    line.className = "call-chat-line";
    line.innerHTML = `<strong>${escapeHtml(from)}</strong>${escapeHtml(text)}`;
    chatLog.appendChild(line);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function attachScreenTrack(track) {
    if (!screenEl || !track || typeof track.attach !== "function") return;
    track.attach(screenEl);
    stageEl?.classList.add("has-screen");
    if (emptyEl) emptyEl.hidden = true;
  }

  function clearScreen() {
    if (screenEl) {
      try {
        screenEl.srcObject = null;
      } catch (_) {}
      screenEl.removeAttribute("src");
    }
    stageEl?.classList.remove("has-screen");
    if (emptyEl) emptyEl.hidden = false;
  }

  function loadLivekitScript() {
    return new Promise((resolve, reject) => {
      if (window.LivekitClient && window.LivekitClient.Room) {
        resolve(window.LivekitClient);
        return;
      }
      const existing = document.querySelector("script[data-livekit-client]");
      if (existing) {
        existing.addEventListener("load", () => resolve(window.LivekitClient));
        existing.addEventListener("error", () => reject(new Error("Falha ao carregar livekit-client")));
        return;
      }
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/livekit-client@2.9.1/dist/livekit-client.umd.min.js";
      s.async = true;
      s.dataset.livekitClient = "1";
      s.onload = () => {
        if (!window.LivekitClient || !window.LivekitClient.Room) {
          reject(new Error("LivekitClient.Room ausente após load"));
          return;
        }
        resolve(window.LivekitClient);
      };
      s.onerror = () => reject(new Error("Falha ao carregar livekit-client (CDN)"));
      document.head.appendChild(s);
    });
  }

  function ev(LK, name, fallback) {
    return (LK.RoomEvent && LK.RoomEvent[name]) || fallback;
  }

  async function safeDisconnect(r) {
    if (!r) return;
    try {
      await r.disconnect();
    } catch (_) {}
  }

  async function join() {
    if (joining || room) return;
    joining = true;
    setConnected(false);
    if (joinBtn) joinBtn.disabled = true;
    setStatus("Conectando…");

    let LK;
    try {
      LK = await loadLivekitScript();
    } catch (err) {
      joining = false;
      setStatus("Falha ao entrar", "error");
      if (hintEl) hintEl.textContent = String(err.message || err);
      if (joinBtn) joinBtn.disabled = false;
      return;
    }

    let r = null;
    try {
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
        throw new Error(msg || data.message || `Token HTTP ${res.status}`);
      }
      if (!data.url || !data.token) {
        throw new Error("Servidor não devolveu url/token LiveKit. Confira LIVEKIT_* no Railway.");
      }

      r = new LK.Room({
        adaptiveStream: true,
        dynacast: true,
        audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true },
      });
      room = r;

      const screenSource = (LK.Track && LK.Track.Source && LK.Track.Source.ScreenShare) || "screen_share";

      r.on(ev(LK, "ParticipantConnected", "participantConnected"), () => refreshPeople());
      r.on(ev(LK, "ParticipantDisconnected", "participantDisconnected"), () => refreshPeople());
      r.on(ev(LK, "ActiveSpeakersChanged", "activeSpeakersChanged"), () => refreshPeople());
      r.on(ev(LK, "TrackSubscribed", "trackSubscribed"), (track, publication) => {
        if (track && track.kind === "video" && isScreenPub(publication, screenSource)) {
          attachScreenTrack(track);
        }
      });
      r.on(ev(LK, "TrackUnsubscribed", "trackUnsubscribed"), (_track, publication) => {
        if (isScreenPub(publication, screenSource)) clearScreen();
      });
      r.on(ev(LK, "DataReceived", "dataReceived"), (payload, participant) => {
        try {
          const msg = JSON.parse(new TextDecoder().decode(payload));
          if (msg && msg.t === "chat" && msg.text) {
            appendChat(participantLabel(participant) || "?", msg.text);
          }
        } catch (_) {}
      });
      r.on(ev(LK, "Disconnected", "disconnected"), () => {
        if (room !== r) return;
        room = null;
        sharing = false;
        joining = false;
        clearScreen();
        setConnected(false);
        setStatus("Desconectado");
        if (peopleEl) peopleEl.innerHTML = '<li class="muted">Ninguém ainda</li>';
        if (joinBtn) joinBtn.disabled = false;
      });

      await r.connect(String(data.url).trim(), String(data.token).trim());

      if (!r.localParticipant) {
        throw new Error(
          "Conexão LiveKit sem participante local. Revogue a API key, crie outra e atualize LIVEKIT_API_SECRET no Railway."
        );
      }

      try {
        await r.localParticipant.setMicrophoneEnabled(true);
        micOn = true;
        micBtn?.classList.add("is-active");
        micBtn?.classList.remove("is-off");
      } catch (micErr) {
        micOn = false;
        micBtn?.classList.add("is-off");
        micBtn?.classList.remove("is-active");
        console.warn("mic", micErr);
        if (hintEl) {
          hintEl.textContent =
            "Entrou na sala, mas o microfone foi bloqueado pelo navegador. Clique no cadeado da URL e permita o mic.";
        }
      }

      remoteList(r).forEach((p) => {
        publicationsOf(p).forEach((pub) => {
          if (pub.isSubscribed && pub.track && isScreenPub(pub, screenSource)) {
            attachScreenTrack(pub.track);
          }
        });
      });

      joining = false;
      setConnected(true);
      setStatus("Na sala", "live");
      refreshPeople();
      if (hintEl && micOn) hintEl.textContent = "Mic ligado. Use Tela para compartilhar.";
      if (window.lucide) window.lucide.createIcons();
    } catch (err) {
      console.error("call join", err);
      joining = false;
      if (room === r) room = null;
      await safeDisconnect(r);
      clearScreen();
      setConnected(false);
      setStatus("Falha ao entrar", "error");
      if (joinBtn) joinBtn.disabled = false;
      const raw = String((err && err.message) || err || "erro desconhecido");
      if (hintEl) {
        hintEl.textContent = raw.includes("localParticipant")
          ? "Falha LiveKit (token/URL). Confira LIVEKIT_URL, KEY e SECRET no Railway e se o secret é o da chave atual."
          : raw;
      }
    }
  }

  async function leave() {
    const r = room;
    room = null;
    sharing = false;
    await safeDisconnect(r);
    clearScreen();
    setConnected(false);
    setStatus("Desconectado");
    if (peopleEl) peopleEl.innerHTML = '<li class="muted">Ninguém ainda</li>';
    if (joinBtn) joinBtn.disabled = false;
  }

  async function toggleMic() {
    if (!room || !room.localParticipant) return;
    micOn = !micOn;
    await room.localParticipant.setMicrophoneEnabled(micOn);
    micBtn?.classList.toggle("is-active", micOn);
    micBtn?.classList.toggle("is-off", !micOn);
  }

  async function toggleScreen() {
    if (!room || !room.localParticipant) return;
    try {
      sharing = !sharing;
      await room.localParticipant.setScreenShareEnabled(sharing);
      screenBtn?.classList.toggle("is-active", sharing);
      if (sharing) {
        publicationsOf(room.localParticipant).forEach((pub) => {
          if (isScreenPub(pub, "screen_share") && pub.track) {
            attachScreenTrack(pub.track);
          }
        });
      } else {
        clearScreen();
      }
    } catch (err) {
      sharing = false;
      screenBtn?.classList.remove("is-active");
      if (hintEl) hintEl.textContent = "Compartilhar tela cancelado ou bloqueado pelo navegador.";
    }
  }

  joinBtn?.addEventListener("click", () => {
    join().catch(console.error);
  });
  leaveBtn?.addEventListener("click", () => {
    leave().catch(console.error);
  });
  micBtn?.addEventListener("click", () => toggleMic().catch(console.error));
  screenBtn?.addEventListener("click", () => toggleScreen().catch(console.error));

  chatForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!room || !room.localParticipant) return;
    const text = (chatInput.value || "").trim();
    if (!text) return;
    const payload = new TextEncoder().encode(JSON.stringify({ t: "chat", text }));
    await room.localParticipant.publishData(payload, { reliable: true });
    appendChat("você", text);
    chatInput.value = "";
  });

  window.addEventListener("beforeunload", () => {
    try {
      room?.disconnect();
    } catch (_) {}
  });
})();
