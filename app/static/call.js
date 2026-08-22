/**
 * Call — LiveKit (setMicrophoneEnabled / setCameraEnabled / setScreenShareEnabled).
 * Mic = API oficial LiveKit (equivalente ao useTrackToggle, UI custom sem React).
 */
(function () {
  const root = document.getElementById("call-page");
  if (!root || root.dataset.ready !== "1") return;

  const REJOIN_KEY = "ib_call_rejoin";
  const REJOIN_SHARE_KEY = "ib_call_rejoin_share";
  const DEVICE_KEY = "ib_call_device";
  const SCREEN_HOST_CHANNEL = "ib_call_screen_host";
  const SCREEN_HOST_SUFFIX = "-screen";

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
  const camBtn = document.getElementById("call-cam-btn");
  const screenBtn = document.getElementById("call-screen-btn");
  const deafenBtn = document.getElementById("call-deafen-btn");
  const leaveBtn = document.getElementById("call-leave-btn");
  const myStatus = document.getElementById("call-my-status");
  const mobileDock = document.getElementById("call-mobile-dock");
  const mobileStatus = document.getElementById("call-mobile-status");
  const reshareBanner = document.getElementById("call-reshare-banner");
  const reshareBtn = document.getElementById("call-reshare-btn");
  const sidebarToggle = document.getElementById("call-sidebar-toggle");
  const sidebarBackdrop = document.getElementById("call-sidebar-backdrop");
  const channelsPanel = document.querySelector(".dc-channels");
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
  let camOn = false;
  let sharing = false;
  let deafened = false;
  let joining = false;
  let intentionalLeave = false;
  let micPublishing = false;
  let camPublishing = false;
  let micGraceUntil = 0;
  let activeScreenOwner = null;
  let lastMicTapMs = 0;
  let sidebarOpen = false;
  let screenHostSharing = false;
  let screenHostWin = null;
  const screenHostChannel = new BroadcastChannel(SCREEN_HOST_CHANNEL);
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

  function isMobileUi() {
    return window.matchMedia("(max-width: 960px)").matches;
  }

  function isIOS() {
    return /iPad|iPhone|iPod/i.test(navigator.userAgent)
      || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }

  function isAndroid() {
    return /Android/i.test(navigator.userAgent);
  }

  function canDeviceScreenShare() {
    return typeof navigator.mediaDevices?.getDisplayMedia === "function" && !isIOS();
  }

  function openSidebar() {
    if (!isMobileUi()) return;
    sidebarOpen = true;
    channelsPanel?.classList.add("is-open");
    if (sidebarBackdrop) {
      sidebarBackdrop.hidden = false;
      sidebarBackdrop.classList.add("is-open");
    }
  }

  function closeSidebar() {
    sidebarOpen = false;
    channelsPanel?.classList.remove("is-open");
    if (sidebarBackdrop) {
      sidebarBackdrop.hidden = true;
      sidebarBackdrop.classList.remove("is-open");
    }
  }

  function initMobileSidebar() {
    sidebarToggle?.addEventListener("click", (e) => {
      e.preventDefault();
      if (sidebarOpen) closeSidebar();
      else openSidebar();
    });
    sidebarBackdrop?.addEventListener("click", () => closeSidebar());

    let touchStartX = 0;
    let touchStartY = 0;
    document.addEventListener("touchstart", (e) => {
      if (!isMobileUi()) return;
      touchStartX = e.touches[0]?.clientX ?? 0;
      touchStartY = e.touches[0]?.clientY ?? 0;
    }, { passive: true });
    document.addEventListener("touchend", (e) => {
      if (!isMobileUi()) return;
      const x = e.changedTouches[0]?.clientX ?? 0;
      const y = e.changedTouches[0]?.clientY ?? 0;
      const dx = x - touchStartX;
      const dy = Math.abs(y - touchStartY);
      if (dy > 80) return;
      if (!sidebarOpen && touchStartX < 28 && dx > 55) openSidebar();
      if (sidebarOpen && dx < -55) closeSidebar();
    }, { passive: true });
  }

  function isScreenHostIdentity(id) {
    return String(id || "").endsWith(SCREEN_HOST_SUFFIX);
  }

  function myScreenHostIdentity() {
    if (!inRoom()) return null;
    return String(room.localParticipant.identity || "") + SCREEN_HOST_SUFFIX;
  }

  function isMyScreenHostParticipant(p) {
    const hostId = myScreenHostIdentity();
    return !!(hostId && p?.identity === hostId);
  }

  function openScreenHostWindow() {
    const features = "width=400,height=260,popup=yes";
    if (screenHostWin && !screenHostWin.closed) {
      try { screenHostWin.focus(); } catch (_) {}
      return screenHostWin;
    }
    screenHostWin = window.open("/call/screen-host", "ib_call_screen_host", features);
    return screenHostWin;
  }

  function pingScreenHost(timeoutMs) {
    const ms = timeoutMs ?? 900;
    return new Promise((resolve) => {
      let done = false;
      const finish = (data) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        screenHostChannel.removeEventListener("message", onMsg);
        resolve(data);
      };
      const onMsg = (e) => {
        const msg = e.data || {};
        if (msg.type === "pong" || msg.type === "ready") finish(msg);
      };
      const timer = setTimeout(() => finish(null), ms);
      screenHostChannel.addEventListener("message", onMsg);
      screenHostChannel.postMessage({ type: "ping" });
    });
  }

  function applyScreenHostState(msg) {
    if (!msg) return;
    if (msg.sharing) {
      sharing = true;
      screenHostSharing = true;
      rememberShare(true);
      showReshareBanner(false);
      screenBtn?.classList.add("is-on");
      setHint(msg.hint || "Transmissão ativa na janela auxiliar — F5 não para a tela.");
    } else if (screenHostSharing) {
      sharing = false;
      screenHostSharing = false;
      rememberShare(false);
      screenBtn?.classList.remove("is-on");
      clearScreen();
      setHint(msg.hint || "Transmissão parada.");
    }
    syncMobileDock();
    refreshUi();
  }

  function initScreenHostBridge() {
    screenHostChannel.onmessage = (e) => {
      const msg = e.data || {};
      if (msg.type === "state") {
        applyScreenHostState(msg);
        return;
      }
      if (msg.type === "closed" && msg.sharing) {
        sharing = false;
        screenHostSharing = false;
        rememberShare(false);
        screenBtn?.classList.remove("is-on");
        clearScreen();
        syncMobileDock();
        setHint("Janela de transmissão fechou — compartilhe de novo se precisar.");
        refreshUi();
      }
    };
  }

  async function syncScreenHostAfterJoin() {
    const host = await pingScreenHost(1200);
    if (host?.sharing) {
      applyScreenHostState({
        sharing: true,
        hint: "Transmissão continua (janela auxiliar). Pode usar F5 à vontade.",
      });
      scanAllRemoteMedia();
      setTimeout(scanAllRemoteMedia, 500);
    }
  }

  async function startScreenShareViaHost() {
    if (!inRoom()) return;
    saveQualityPrefs();
    closeQualityModal();

    const win = openScreenHostWindow();
    if (!win) {
      setHint("Pop-up bloqueado. Permita pop-ups para a transmissão sobreviver ao F5.");
      return startScreenShareInline();
    }

    setHint("Abrindo janela de transmissão…");
    await pingScreenHost(4000);

    screenHostChannel.postMessage({
      type: "start",
      res: screenRes,
      fps: screenFps,
    });

    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      const state = await pingScreenHost(1500);
      if (state?.sharing) {
        applyScreenHostState({
          sharing: true,
          hint: "Transmitindo. Mantenha a janela auxiliar aberta — F5 aqui não para.",
        });
        scanAllRemoteMedia();
        setTimeout(scanAllRemoteMedia, 600);
        return;
      }
      if (!screenHostWin || screenHostWin.closed) break;
      await new Promise((r) => setTimeout(r, 400));
    }
    setHint("Transmissão não iniciou — tente de novo ou permita pop-ups.");
  }

  function rememberShare(on) {
    try {
      if (on) sessionStorage.setItem(REJOIN_SHARE_KEY, "1");
      else sessionStorage.removeItem(REJOIN_SHARE_KEY);
    } catch (_) {}
  }

  function showReshareBanner(show) {
    if (reshareBanner) reshareBanner.hidden = !show;
  }

  function participantIsSharingScreen(p) {
    if (!p) return false;
    let sharingScreen = false;
    const pubs = p.videoTrackPublications || p.trackPublications;
    pubs?.forEach?.((pub) => {
      if (pub.kind && pub.kind !== "video") return;
      if (!isScreenSource(pub.source)) return;
      if (pub.track && pub.isMuted !== true) sharingScreen = true;
    });
    if (p === room?.localParticipant && sharing) sharingScreen = true;
    if (screenHostSharing && isMyScreenHostParticipant(p)) sharingScreen = true;
    return sharingScreen;
  }

  function getScreenPublication(participant) {
    if (!participant) return null;
    let found = null;
    const pubs = participant.videoTrackPublications || participant.trackPublications;
    pubs?.forEach?.((pub) => {
      if (found) return;
      if (pub.kind && pub.kind !== "video") return;
      if (!isScreenSource(pub.source)) return;
      if (pub.track) found = pub;
    });
    return found;
  }

  /** Quem entra depois vê tela/câmera/áudio já publicados. */
  function scanAllRemoteMedia() {
    if (!room) return;
    const r = room;
    const screenSource = LK?.Track?.Source?.ScreenShare || "screen_share";

    const handleParticipant = (p) => {
      const pubs = p.videoTrackPublications || p.trackPublications;
      pubs?.forEach?.((pub) => {
        if (pub.kind === "audio" || (pub.track && pub.track.kind === "audio")) {
          if (pub.isSubscribed !== false && pub.track) attachRemoteAudio(pub.track, p);
          return;
        }
        if (pub.kind !== "video" && pub.track?.kind !== "video") return;
        if (isScreenSource(pub.source) || pub.source === screenSource) {
          if (pub.track) {
            const selfScreen = isMyScreenHostParticipant(p);
            attachScreen(pub.track, selfScreen ? "você (tela)" : labelOf(p), pub);
            activeScreenOwner = p.identity;
            if (p === r.localParticipant || selfScreen) {
              sharing = true;
              if (selfScreen) screenHostSharing = true;
              screenBtn?.classList.add("is-on");
              syncMobileDock();
            }
          }
        }
      });
    };

    if (r.localParticipant) handleParticipant(r.localParticipant);
    remoteList(r).forEach(handleParticipant);
    refreshUi();
  }

  function participantMicMuted(p) {
    try {
      if (p === room?.localParticipant) return !micOn;
      if (typeof p.isMicrophoneEnabled === "boolean") return !p.isMicrophoneEnabled;
    } catch (_) {}
    return false;
  }

  function showMicBanner(show) {
    const should = !!(show && inRoom() && !micOn);
    if (micBanner) micBanner.hidden = !should;
    if (should) syncMicBannerText();
  }

  function syncMicBannerText() {
    const textEl = document.getElementById("call-mic-banner-text");
    if (!textEl) return;
    textEl.textContent = isMobileUi()
      ? "Toque no microfone na barra inferior para falar."
      : "Clique no microfone na barra lateral (ou no botão abaixo) para falar.";
  }

  function mapMicError(err) {
    const name = err?.name || "";
    const msg = String(err?.message || err);
    if (name === "NotAllowedError" || /Permission|NotAllowed/i.test(msg)) {
      return "Permissão negada — permita o microfone no navegador.";
    }
    if (name === "NotFoundError") return "Nenhum microfone encontrado.";
    if (name === "NotReadableError") return "Microfone em uso por outro app.";
    return "Microfone: " + msg;
  }

  function setMicUiPending(pending) {
    [micBtn, enableMicBtn, ...document.querySelectorAll('[data-call-action="mic"]')].forEach((btn) => {
      if (!btn) return;
      btn.classList.toggle("is-pending", pending);
      btn.disabled = pending || !inRoom();
    });
  }

  function setMicUi() {
    const on = inRoom();
    const statusText = !on
      ? "Fora da sala"
      : [micOn ? "Em voz" : "Em voz · mudo", camOn ? "câmera" : "", sharing ? "transmitindo" : ""]
          .filter(Boolean)
          .join(" · ");

    [micBtn, ...document.querySelectorAll('[data-call-action="mic"]')].forEach((btn) => {
      if (!btn) return;
      btn.disabled = !on;
      btn.classList.toggle("is-muted", !micOn);
      btn.innerHTML = micOn
        ? '<i data-lucide="mic"></i>'
        : '<i data-lucide="mic-off"></i>';
    });

    if (myStatus) myStatus.textContent = statusText;
    if (mobileStatus) mobileStatus.textContent = statusText;
    syncMobileDock();
    if (window.lucide) window.lucide.createIcons();
  }

  function syncMobileDock() {
    const on = inRoom();
    const mobile = isMobileUi();
    if (mobileDock) mobileDock.hidden = !(on && mobile);
    if (connEl && mobile) connEl.hidden = true;

    document.querySelectorAll('[data-call-action="cam"]').forEach((btn) => {
      btn.disabled = !on;
      btn.classList.toggle("is-on", camOn);
      btn.innerHTML = camOn ? '<i data-lucide="video"></i>' : '<i data-lucide="video-off"></i>';
    });
    document.querySelectorAll('[data-call-action="screen"]').forEach((btn) => {
      btn.disabled = !on;
      btn.classList.toggle("is-on", sharing);
      if (isIOS()) btn.title = "Tela: use Android ou PC";
      else if (isMobileUi()) btn.title = sharing ? "Parar tela" : "Compartilhar tela";
    });
    document.querySelectorAll('[data-call-action="deafen"]').forEach((btn) => {
      btn.disabled = !on;
      btn.classList.toggle("is-muted", deafened);
    });
    document.querySelectorAll('[data-call-action="chat"]').forEach((btn) => {
      btn.disabled = !on;
    });
  }

  function setCamUi() {
    if (!camBtn) return;
    camBtn.disabled = !inRoom();
    camBtn.classList.toggle("is-on", camOn);
    camBtn.innerHTML = camOn
      ? '<i data-lucide="video"></i>'
      : '<i data-lucide="video-off"></i>';
    syncMobileDock();
    if (window.lucide) window.lucide.createIcons();
  }

  function setConnectedUi(on) {
    if (lobbyEl) lobbyEl.hidden = on;
    if (connEl) connEl.hidden = !on || isMobileUi();
    if (leaveBtn) leaveBtn.disabled = !on;
    if (screenBtn) screenBtn.disabled = !on;
    if (camBtn) camBtn.disabled = !on;
    if (deafenBtn) deafenBtn.disabled = !on;
    if (chatInput) chatInput.disabled = !on;
    if (chatSend) chatSend.disabled = !on;
    if (micBtn) micBtn.disabled = !on;
    const mobile = isMobileUi();
    if (fabChat) fabChat.hidden = !(on && mobile);
    syncMobileDock();
    if (!on) {
      showMicBanner(false);
      showReshareBanner(false);
      collapseScreen();
      camOn = false;
      setCamUi();
    }
  }

  function peopleList(r) {
    const people = [];
    if (r?.localParticipant) people.push({ p: r.localParticipant, self: true });
    remoteList(r).forEach((p) => {
      if (isScreenHostIdentity(p.identity)) return;
      people.push({ p, self: false });
    });
    return people;
  }

  function isScreenSource(src) {
    return String(src || "").toLowerCase().includes("screen");
  }

  function isCameraSource(src) {
    const s = String(src || "").toLowerCase();
    if (isScreenSource(s)) return false;
    return s.includes("camera") || s === "2" || s === "";
  }

  function getCameraTrack(participant) {
    if (!participant) return null;
    let found = null;
    const pubs = participant.videoTrackPublications || participant.trackPublications;
    pubs?.forEach?.((pub) => {
      if (found) return;
      if (pub.kind && pub.kind !== "video") return;
      if (isScreenSource(pub.source)) return;
      if (pub.track && pub.isMuted !== true) found = pub.track;
    });
    return found;
  }

  function participantHasCam(p) {
    try {
      if (p === room?.localParticipant) return !!camOn || !!getCameraTrack(p);
      if (typeof p.isCameraEnabled === "boolean" && p.isCameraEnabled) return true;
      return !!getCameraTrack(p);
    } catch (_) {
      return false;
    }
  }

  function attachCamsToTiles() {
    if (!tilesEl || !room) return;
    peopleList(room).forEach(({ p, self }) => {
      const video = tilesEl.querySelector(`video[data-cam="${CSS.escape(String(p.identity))}"]`);
      const tile = tilesEl.querySelector(`[data-pid="${CSS.escape(String(p.identity))}"]`);
      const track = getCameraTrack(p);
      if (!video) return;
      if (track?.attach) {
        track.attach(video);
        video.muted = true;
        video.playsInline = true;
        video.play?.().catch(() => {});
        video.classList.add("is-live");
        tile?.classList.add("has-cam");
      } else {
        try { video.srcObject = null; } catch (_) {}
        video.classList.remove("is-live");
        tile?.classList.remove("has-cam");
      }
      if (self) {
        // espelho local
        video.style.transform = track ? "scaleX(-1)" : "";
      }
    });
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
              const hasCam = participantHasCam(p);
              const onScreen = participantIsSharingScreen(p);
              const pid = escapeHtml(String(p.identity || ""));
              return `<div class="dc-tile${speaking ? " is-speaking" : ""}${hasCam ? " has-cam" : ""}${onScreen ? " is-sharing" : ""}" data-pid="${pid}">
                <video class="dc-tile-cam" data-cam="${pid}" autoplay playsinline muted></video>
                <div class="dc-avatar" style="background:${colorFor(p.identity)}">${escapeHtml(initialFor(labelOf(p)))}</div>
                <div class="dc-tile-name">
                  ${muted ? '<i data-lucide="mic-off"></i>' : ""}
                  ${hasCam ? '<i data-lucide="video"></i>' : ""}
                  ${onScreen ? '<i data-lucide="monitor-up"></i>' : ""}
                  <span>${escapeHtml(name)}</span>
                </div>
              </div>`;
            })
            .join("");
      attachCamsToTiles();
    }

    if (voiceMembersEl) {
      voiceMembersEl.innerHTML = !r
        ? ""
        : people
            .map(({ p, self }) => {
              const name = labelOf(p) + (self ? " (você)" : "");
              const speaking = !!p.isSpeaking;
              const muted = participantMicMuted(p);
              const onScreen = participantIsSharingScreen(p);
              return `<div class="dc-voice-member${speaking ? " is-speaking" : ""}${onScreen ? " is-sharing" : ""}">
                <span class="av" style="background:${colorFor(p.identity)}">${escapeHtml(initialFor(labelOf(p)))}</span>
                <span>${escapeHtml(name)}</span>
                ${onScreen ? '<i data-lucide="monitor-up" class="share-icon"></i>' : ""}
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
    if (Date.now() < micGraceUntil) return;
    micOn = !!room.localParticipant.isMicrophoneEnabled;
    setMicUi();
    showMicBanner(!micOn);
    refreshUi();
  }

  /** LiveKit oficial — equivalente ao useTrackToggle, mantendo botões customizados. */
  async function toggleMic() {
    if (!inRoom()) {
      setHint("Entre na sala primeiro.");
      return;
    }
    if (micPublishing) return;
    micPublishing = true;

    const lp = room.localParticipant;
    const wantOn = !lp.isMicrophoneEnabled;

    setMicUiPending(true);
    setHint(wantOn ? "Ativando microfone…" : "Mutando…");

    try {
      if (wantOn) micGraceUntil = Date.now() + 2500;
      await lp.setMicrophoneEnabled(
        wantOn,
        wantOn
          ? {
              echoCancellation: true,
              noiseSuppression: true,
              autoGainControl: true,
            }
          : undefined
      );
      micOn = !!lp.isMicrophoneEnabled;
      showMicBanner(!micOn);
      setHint(micOn ? "Microfone ligado." : "Microfone mutado.");
      setMicUi();
      refreshUi();
    } catch (err) {
      console.error("[call] setMicrophoneEnabled", err);
      syncMicFromRoom();
      showMicBanner(true);
      setHint(mapMicError(err));
    } finally {
      micPublishing = false;
      setMicUiPending(false);
    }
  }

  function onMicTap(e) {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    if (micPublishing) return;
    const now = Date.now();
    if (now - lastMicTapMs < 500) return;
    lastMicTapMs = now;
    toggleMic().catch(console.error);
  }

  function bindMicButtons() {
    root.addEventListener("pointerdown", (e) => {
      const btn = e.target.closest("#call-mic-btn, #call-enable-mic-btn, [data-call-action=\"mic\"]");
      if (!btn || btn.disabled) return;
      if (e.pointerType === "mouse" && e.button !== 0) return;
      onMicTap(e);
    }, true);
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
          videoEncoding: {
            maxBitrate: 1_700_000,
            maxFramerate: 24,
          },
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
        if (inRoom()) {
          syncMicFromRoom();
          try {
            camOn = !!room.localParticipant.isCameraEnabled || !!getCameraTrack(room.localParticipant);
          } catch (_) {}
          setCamUi();
        }
        refreshUi();
      });
      r.on(ev("TrackUnmuted", "trackUnmuted"), () => {
        if (inRoom()) {
          syncMicFromRoom();
          try {
            camOn = !!room.localParticipant.isCameraEnabled || !!getCameraTrack(room.localParticipant);
          } catch (_) {}
          setCamUi();
        }
        refreshUi();
      });
      r.on(ev("LocalTrackPublished", "localTrackPublished"), () => {
        syncMicFromRoom();
        try {
          camOn = !!room.localParticipant.isCameraEnabled || !!getCameraTrack(room.localParticipant);
        } catch (_) {}
        setCamUi();
        refreshUi();
      });
      r.on(ev("LocalTrackUnpublished", "localTrackUnpublished"), () => {
        syncMicFromRoom();
        try {
          camOn = !!room.localParticipant.isCameraEnabled || !!getCameraTrack(room.localParticipant);
        } catch (_) {}
        setCamUi();
        refreshUi();
      });

      r.on(ev("Reconnecting", "reconnecting"), () => {
        setStatus("Reconectando…", "wait");
        setHint("Rede instável — reconectando automaticamente…");
      });
      r.on(ev("Reconnected", "reconnected"), () => {
        setStatus("Na sala", "live");
        setHint("Reconectado.");
        syncMicFromRoom();
        refreshUi();
      });

      r.on(ev("TrackSubscribed", "trackSubscribed"), (track, publication, participant) => {
        if (track.kind === "audio") {
          attachRemoteAudio(track, participant);
          refreshUi();
          return;
        }
        if (track.kind !== "video") return;
        const src = publication?.source;
        if (src === screenSource || isScreenSource(src)) {
          const selfScreen = isMyScreenHostParticipant(participant);
          attachScreen(track, selfScreen ? "você (tela)" : labelOf(participant), publication);
          activeScreenOwner = participant?.identity || null;
          if (selfScreen) {
            sharing = true;
            screenHostSharing = true;
            screenBtn?.classList.add("is-on");
            syncMobileDock();
          }
          refreshUi();
          return;
        }
        refreshUi();
      });
      r.on(ev("TrackPublished", "trackPublished"), (publication, participant) => {
        if (publication?.kind === "audio" && publication.track) {
          attachRemoteAudio(publication.track, participant);
        }
        if (publication?.kind === "video" && isScreenSource(publication.source) && publication.track) {
          const selfScreen = isMyScreenHostParticipant(participant);
          attachScreen(
            publication.track,
            selfScreen ? "você (tela)" : labelOf(participant),
            publication
          );
          activeScreenOwner = participant?.identity || null;
          if (selfScreen) {
            sharing = true;
            screenHostSharing = true;
            screenBtn?.classList.add("is-on");
            syncMobileDock();
          }
        }
        refreshUi();
      });
      r.on(ev("TrackUnsubscribed", "trackUnsubscribed"), (track, publication, participant) => {
        if (track.kind === "audio" && participant?.identity) {
          detachRemoteAudio(participant.identity);
        }
        if (publication?.source === screenSource || isScreenSource(publication?.source)) {
          if (!participant?.identity || participant.identity === activeScreenOwner) {
            clearScreen();
            activeScreenOwner = null;
            if (participant === room?.localParticipant || participant?.identity === room?.localParticipant?.identity) {
              sharing = false;
              rememberShare(false);
              screenBtn?.classList.remove("is-on");
              syncMobileDock();
            }
          }
        }
        refreshUi();
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
        camOn = false;
        wipeAudio();
        clearScreen();
        showMicBanner(false);
        setConnectedUi(false);
        setMicUi();
        setCamUi();
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

      scanAllRemoteMedia();
      setTimeout(scanAllRemoteMedia, 600);
      setTimeout(scanAllRemoteMedia, 1500);

      let wantReshare = false;
      try {
        wantReshare = sessionStorage.getItem(REJOIN_SHARE_KEY) === "1";
      } catch (_) {}
      await syncScreenHostAfterJoin();
      if (wantReshare && auto && !screenHostSharing) {
        showReshareBanner(true);
        setHint("Você reentrou. Clique «Compartilhar tela» — ou use a janela auxiliar.");
      } else if (!screenHostSharing) {
        showReshareBanner(false);
      }

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
    rememberShare(false);
    const r = room;
    room = null;
    sharing = false;
    micOn = false;
    camOn = false;
    await safeDisconnect(r);
    wipeAudio();
    clearScreen();
    showMicBanner(false);
    setConnectedUi(false);
    setMicUi();
    setCamUi();
    setStatus("Desconectado");
    setHint("");
    if (joinBtn) joinBtn.disabled = false;
    if (lobbyEl) lobbyEl.hidden = false;
    refreshUi();
  }

  function handleCallAction(action) {
    switch (action) {
      case "cam":
        toggleCamera().catch(console.error);
        break;
      case "screen":
        onScreenBtnClick();
        break;
      case "deafen":
        setDeafened(!deafened);
        break;
      case "leave":
        leave().catch(console.error);
        break;
      case "chat":
        chatPanel?.classList.add("is-open");
        break;
      default:
        break;
    }
  }

  async function toggleCamera() {
    if (!inRoom()) {
      setHint("Entre na sala primeiro.");
      return;
    }
    if (camPublishing) return;
    camPublishing = true;
    try {
      const next = !camOn;
      // API oficial LiveKit (doc: setCameraEnabled)
      await room.localParticipant.setCameraEnabled(next, {
        resolution: { width: 1280, height: 720, frameRate: 24 },
      });
      camOn = !!room.localParticipant.isCameraEnabled || (next && !!getCameraTrack(room.localParticipant));
      if (next && !camOn) camOn = true;
      if (!next) camOn = false;
      setCamUi();
      setMicUi();
      refreshUi();
      setHint(camOn ? "Câmera ligada." : "Câmera desligada.");
    } catch (err) {
      console.error("toggleCamera", err);
      camOn = false;
      setCamUi();
      const name = err?.name || "";
      const msg = String(err?.message || err);
      if (name === "NotAllowedError" || /Permission|NotAllowed/i.test(msg)) {
        setHint("Chrome bloqueou a câmera. Clique de novo e escolha Permitir.");
      } else if (name === "NotFoundError") {
        setHint("Nenhuma câmera encontrada.");
      } else if (name === "NotReadableError") {
        setHint("Câmera em uso por outro app. Feche e tente de novo.");
      } else {
        setHint("Câmera falhou: " + msg);
      }
    } finally {
      camPublishing = false;
    }
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

  /** Clique no monitor: mobile Android = share direto; iOS = aviso; desktop = modal. */
  function onScreenBtnClick() {
    if (!inRoom()) return;
    if (sharing && isMobileUi()) {
      stopScreenShare().catch(console.error);
      return;
    }
    if (isMobileUi()) {
      if (!canDeviceScreenShare()) {
        setHint("iPhone: compartilhar tela não funciona no Safari. Use Android ou PC.");
        return;
      }
      startScreenShareMobile().catch(console.error);
      return;
    }
    openQualityModal();
  }

  async function startScreenShareMobile() {
    if (!inRoom()) return;
    if (!canDeviceScreenShare()) {
      setHint("Seu celular não suporta compartilhar tela neste navegador.");
      return;
    }
    closeQualityModal();
    setHint("Escolha a tela ou app para compartilhar…");
    try {
      if (sharing) {
        try { await room.localParticipant.setScreenShareEnabled(false); } catch (_) {}
        sharing = false;
        clearScreen();
      }
      // Mobile: sem resolution fixa (Android rejeita 1080p às vezes)
      await room.localParticipant.setScreenShareEnabled(
        true,
        { audio: false, contentHint: "detail" },
        {
          simulcast: false,
          screenShareEncoding: {
            maxBitrate: 2_500_000,
            maxFramerate: 24,
          },
        }
      );
      sharing = true;
      rememberShare(true);
      showReshareBanner(false);
      screenBtn?.classList.add("is-on");
      syncMobileDock();
      room.localParticipant.trackPublications?.forEach((pub) => {
        if (String(pub.source || "").includes("screen") && pub.track) {
          attachScreen(pub.track, "você", pub);
          activeScreenOwner = room.localParticipant.identity;
        }
      });
      setHint("Tela compartilhada. Toque no monitor de novo para parar.");
      refreshUi();
    } catch (err) {
      console.error("startScreenShareMobile", err);
      sharing = false;
      screenBtn?.classList.remove("is-on");
      syncMobileDock();
      setHint("Tela cancelada ou negada: " + String(err?.message || err));
    }
  }

  async function startScreenShareInline() {
    if (!inRoom()) return;
    saveQualityPrefs();
    closeQualityModal();
    setHint(`Pedindo tela (${screenRes === "source" ? "fonte" : screenRes + "p"} @ ${screenFps}fps)…`);
    try {
      if (sharing) {
        try {
          await room.localParticipant.setScreenShareEnabled(false);
        } catch (_) {}
        sharing = false;
        screenHostSharing = false;
        clearScreen();
      }

      await room.localParticipant.setScreenShareEnabled(
        true,
        screenCaptureOpts(),
        screenPublishOpts()
      );
      sharing = true;
      rememberShare(true);
      showReshareBanner(false);
      screenBtn?.classList.add("is-on");
      syncMobileDock();
      room.localParticipant.trackPublications?.forEach((pub) => {
        if (String(pub.source || "").includes("screen") && pub.track) {
          attachScreen(pub.track, "você", pub);
          activeScreenOwner = room.localParticipant.identity;
        }
      });
      setHint(
        `Tela na mesma aba (${screenRes === "source" ? "fonte" : screenRes + "p"}). F5 para a transmissão — use janela auxiliar.`
      );
    } catch (_) {
      sharing = false;
      screenBtn?.classList.remove("is-on");
      clearScreen();
      setHint("Compartilhar tela cancelado — você continua na sala.");
    }
  }

  async function startScreenShare() {
    if (!inRoom()) return;
    if (isMobileUi()) return startScreenShareMobile();
    return startScreenShareViaHost();
  }

  async function stopScreenShare() {
    if (screenHostSharing) {
      screenHostChannel.postMessage({ type: "stop" });
      return;
    }
    if (!inRoom()) return;
    try {
      await room.localParticipant.setScreenShareEnabled(false);
    } catch (_) {}
    sharing = false;
    screenHostSharing = false;
    rememberShare(false);
    activeScreenOwner = null;
    screenBtn?.classList.remove("is-on");
    syncMobileDock();
    clearScreen();
    setHint("Compartilhamento parado.");
  }

  function setDeafened(on) {
    deafened = on;
    audioEls.forEach((el) => { el.muted = on; });
    deafenBtn?.classList.toggle("is-muted", on);
    syncMobileDock();
  }

  // Delegação + botões diretos (mobile/desktop)
  root.addEventListener("click", (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;

    const actionBtn = t.closest("[data-call-action]");
    if (actionBtn) {
      const action = actionBtn.getAttribute("data-call-action");
      if (action === "mic") return;
      e.preventDefault();
      handleCallAction(action);
      return;
    }

    if (t.closest("#call-enable-mic-btn") || t.closest("#call-mic-btn")) return;
    if (t.closest("#call-reshare-btn")) {
      e.preventDefault();
      showReshareBanner(false);
      if (isMobileUi()) startScreenShareMobile().catch(console.error);
      else openQualityModal();
      return;
    }
    if (t.closest("#call-join-btn") || t.closest("#call-channel-btn")) {
      closeSidebar();
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
    if (t.closest("#call-cam-btn")) {
      e.preventDefault();
      toggleCamera().catch(console.error);
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

  initMobileSidebar();
  initScreenHostBridge();
  bindMicButtons();

  window.addEventListener("resize", () => {
    syncMicBannerText();
    if (!isMobileUi()) closeSidebar();
    if (inRoom()) setConnectedUi(true);
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
  qualityStop?.addEventListener("click", () => {
    closeQualityModal();
    stopScreenShare().catch(console.error);
  });
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
    if (room && !intentionalLeave) {
      rememberJoin(true);
      if (sharing && !screenHostSharing) rememberShare(true);
    }
  });

  window.addEventListener("beforeunload", (e) => {
    if (sharing && !screenHostSharing && inRoom()) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  setMicUi();
  syncMicBannerText();
  setConnectedUi(false);
  showMicBanner(false);
  if (window.lucide) window.lucide.createIcons();

  try {
    if (sessionStorage.getItem(REJOIN_KEY) === "1") {
      setTimeout(() => join({ auto: true }).catch(console.error), 400);
    }
  } catch (_) {}
})();
