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

  let room = null;
  let micOn = true;
  let sharing = false;
  let screenTrack = null;

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
    joinBtn.disabled = on;
    micBtn.disabled = !on;
    screenBtn.disabled = !on;
    leaveBtn.disabled = !on;
    chatInput.disabled = !on;
    chatSend.disabled = !on;
  }

  function participantLabel(p) {
    return (p && (p.name || p.identity)) || "Alguém";
  }

  function refreshPeople() {
    if (!room || !peopleEl) return;
    const list = [];
    const local = room.localParticipant;
    if (local) list.push({ p: local, self: true });
    room.remoteParticipants.forEach((p) => list.push({ p, self: false }));
    if (!list.length) {
      peopleEl.innerHTML = '<li class="muted">Ninguém ainda</li>';
      return;
    }
    peopleEl.innerHTML = list
      .map(({ p, self }) => {
        const speaking = !!p.isSpeaking;
        const name = participantLabel(p) + (self ? " (você)" : "");
        return `<li class="${speaking ? "is-speaking" : ""}" data-id="${p.identity}">
          <span class="dot" aria-hidden="true"></span>
          <span>${escapeHtml(name)}</span>
        </li>`;
      })
      .join("");
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
    if (!screenEl || !track) return;
    track.attach(screenEl);
    stageEl?.classList.add("has-screen");
    if (emptyEl) emptyEl.hidden = true;
  }

  function clearScreen() {
    if (screenEl) {
      screenEl.srcObject = null;
      screenEl.removeAttribute("src");
    }
    stageEl?.classList.remove("has-screen");
    if (emptyEl) emptyEl.hidden = false;
  }

  function handleTrackSubscribed(track, publication, participant) {
    if (track.kind === "video" && publication.source === "screen_share") {
      attachScreenTrack(track);
    }
  }

  function handleTrackUnsubscribed(track, publication) {
    if (publication?.source === "screen_share" || track?.source === "screen_share") {
      clearScreen();
    }
  }

  function loadLivekitScript() {
    return new Promise((resolve, reject) => {
      if (window.LivekitClient || window.LiveKit || window.livekit) {
        resolve();
        return;
      }
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/livekit-client@2.9.1/dist/livekit-client.umd.min.js";
      s.async = true;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("Falha ao carregar livekit-client"));
      document.head.appendChild(s);
    });
  }

  function lkApi() {
    return window.LivekitClient || window.LiveKit || window.livekit;
  }

  async function join() {
    try {
      await loadLivekitScript();
    } catch (err) {
      setStatus("LiveKit JS não carregou", "error");
      return;
    }
    const LK = lkApi();
    if (!LK || !LK.Room) {
      setStatus("LiveKit JS não carregou", "error");
      return;
    }
    setStatus("Conectando…");
    joinBtn.disabled = true;
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
        throw new Error(msg || data.message || `HTTP ${res.status}`);
      }

      room = new LK.Room({
        adaptiveStream: true,
        dynacast: true,
        audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true },
      });

      const screenSource = (LK.Track && LK.Track.Source && LK.Track.Source.ScreenShare)
        || "screen_share";

      room
        .on(LK.RoomEvent.ParticipantConnected, refreshPeople)
        .on(LK.RoomEvent.ParticipantDisconnected, () => {
          refreshPeople();
        })
        .on(LK.RoomEvent.ActiveSpeakersChanged, refreshPeople)
        .on(LK.RoomEvent.TrackSubscribed, (track, publication) => {
          if (track.kind === "video" && (publication.source === screenSource || publication.source === "screen_share")) {
            attachScreenTrack(track);
          }
        })
        .on(LK.RoomEvent.TrackUnsubscribed, (track, publication) => {
          if (publication?.source === screenSource || publication?.source === "screen_share") {
            clearScreen();
          }
        })
        .on(LK.RoomEvent.DataReceived, (payload, participant) => {
          try {
            const msg = JSON.parse(new TextDecoder().decode(payload));
            if (msg && msg.t === "chat" && msg.text) {
              appendChat(participantLabel(participant) || "?", msg.text);
            }
          } catch (_) {}
        })
        .on(LK.RoomEvent.Disconnected, () => {
          setStatus("Desconectado");
          setConnected(false);
          clearScreen();
          sharing = false;
          screenTrack = null;
          room = null;
          if (peopleEl) peopleEl.innerHTML = '<li class="muted">Ninguém ainda</li>';
        });

      await room.connect(data.url, data.token);
      await room.localParticipant.setMicrophoneEnabled(true);
      micOn = true;
      micBtn.classList.add("is-active");
      micBtn.classList.remove("is-off");

      room.remoteParticipants.forEach((p) => {
        p.trackPublications.forEach((pub) => {
          if (pub.isSubscribed && pub.track && (pub.source === screenSource || pub.source === "screen_share")) {
            attachScreenTrack(pub.track);
          }
        });
      });

      setConnected(true);
      setStatus("Na sala", "live");
      refreshPeople();
      if (hintEl) hintEl.textContent = "Mic ligado. Use Tela para compartilhar.";
      if (window.lucide) window.lucide.createIcons();
    } catch (err) {
      console.error(err);
      setStatus("Falha ao entrar", "error");
      setConnected(false);
      joinBtn.disabled = false;
      if (hintEl) hintEl.textContent = String(err.message || err);
    }
  }

  async function leave() {
    try {
      if (sharing && room) {
        await room.localParticipant.setScreenShareEnabled(false);
      }
    } catch (_) {}
    try {
      await room?.disconnect();
    } catch (_) {}
    room = null;
    sharing = false;
    screenTrack = null;
    clearScreen();
    setConnected(false);
    setStatus("Desconectado");
    if (peopleEl) peopleEl.innerHTML = '<li class="muted">Ninguém ainda</li>';
  }

  async function toggleMic() {
    if (!room) return;
    micOn = !micOn;
    await room.localParticipant.setMicrophoneEnabled(micOn);
    micBtn.classList.toggle("is-active", micOn);
    micBtn.classList.toggle("is-off", !micOn);
  }

  async function toggleScreen() {
    if (!room) return;
    try {
      sharing = !sharing;
      await room.localParticipant.setScreenShareEnabled(sharing);
      screenBtn.classList.toggle("is-active", sharing);
      if (sharing) {
        const pubs = room.localParticipant.trackPublications;
        pubs.forEach((pub) => {
          if (pub.source === "screen_share" && pub.track) {
            screenTrack = pub.track;
            attachScreenTrack(pub.track);
          }
        });
      } else {
        clearScreen();
      }
    } catch (err) {
      sharing = false;
      screenBtn.classList.remove("is-active");
      if (hintEl) hintEl.textContent = "Compartilhar tela cancelado ou bloqueado pelo navegador.";
    }
  }

  joinBtn?.addEventListener("click", join);
  leaveBtn?.addEventListener("click", leave);
  micBtn?.addEventListener("click", () => toggleMic().catch(console.error));
  screenBtn?.addEventListener("click", () => toggleScreen().catch(console.error));

  chatForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!room) return;
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
