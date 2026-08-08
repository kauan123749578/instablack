(() => {
  const grid = document.getElementById("notes-grid");
  const search = document.getElementById("notes-search");
  if (!document.querySelector("[data-page-notes]")) return;

  const csrf =
    document.querySelector('meta[name="csrf-token"]')?.content ||
    document.querySelector('input[name="csrf_token"]')?.value ||
    "";

  function copyText(text) {
    const value = String(text || "");
    if (!value) return Promise.reject(new Error("vazio"));
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(value);
    }
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    return Promise.resolve();
  }

  function flash(btn, okLabel) {
    if (!btn) return;
    const prev = btn.textContent;
    btn.textContent = okLabel || "OK";
    setTimeout(() => {
      btn.textContent = prev;
    }, 1200);
  }

  search?.addEventListener("input", () => {
    const q = (search.value || "").trim().toLowerCase().replace(/^@/, "");
    grid?.querySelectorAll(".notes-card").forEach((card) => {
      const name = card.getAttribute("data-username") || "";
      card.hidden = Boolean(q) && !name.includes(q);
    });
  });

  grid?.addEventListener("click", async (ev) => {
    const copyBtn = ev.target.closest(".notes-copy");
    if (copyBtn) {
      try {
        await copyText(copyBtn.getAttribute("data-copy") || "");
        flash(copyBtn, "Copiado");
      } catch {
        flash(copyBtn, "Erro");
      }
      return;
    }

    const revealBtn = ev.target.closest(".notes-reveal");
    if (revealBtn) {
      const card = revealBtn.closest(".notes-card");
      const noteId = card?.getAttribute("data-note-id");
      const passInput = card?.querySelector(".notes-pass");
      const copyPass = card?.querySelector(".notes-copy-pass");
      if (!noteId || !passInput) return;
      if (passInput.type === "text" && passInput.dataset.plain) {
        passInput.type = "password";
        passInput.value = "••••••••";
        revealBtn.textContent = "Mostrar";
        if (copyPass) copyPass.disabled = true;
        return;
      }
      revealBtn.disabled = true;
      try {
        const res = await fetch(`/accounts/notes/${noteId}/reveal`, {
          headers: { Accept: "application/json", "X-CSRF-Token": csrf },
          credentials: "same-origin",
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "falha");
        passInput.type = "text";
        passInput.value = data.password || "";
        passInput.dataset.plain = data.password || "";
        revealBtn.textContent = "Ocultar";
        if (copyPass) {
          copyPass.disabled = !data.password;
          copyPass.dataset.copy = data.password || "";
        }
        if (data.code) {
          const codeEl = card.querySelector(".notes-code");
          const remainEl = card.querySelector(".notes-remain");
          if (codeEl) codeEl.textContent = data.code;
          if (remainEl && data.remaining != null) remainEl.textContent = `${data.remaining}s`;
          card.dataset.code = data.code;
        }
      } catch (err) {
        revealBtn.textContent = "Erro";
        setTimeout(() => {
          revealBtn.textContent = "Mostrar";
        }, 1400);
      } finally {
        revealBtn.disabled = false;
      }
      return;
    }

    const copyPass = ev.target.closest(".notes-copy-pass");
    if (copyPass && !copyPass.disabled) {
      try {
        await copyText(copyPass.dataset.copy || copyPass.closest(".notes-card")?.querySelector(".notes-pass")?.dataset.plain || "");
        flash(copyPass, "Copiado");
      } catch {
        flash(copyPass, "Erro");
      }
      return;
    }

    const codeBtn = ev.target.closest(".notes-code-btn");
    if (codeBtn) {
      const card = codeBtn.closest(".notes-card");
      const code = (card?.dataset.code || card?.querySelector(".notes-code")?.textContent || "").replace(/\s/g, "");
      if (!/^\d{6}$/.test(code)) return;
      try {
        await copyText(code);
        flash(codeBtn.querySelector(".notes-remain") || codeBtn, "copiado");
      } catch {
        /* ignore */
      }
    }
  });

  async function pollCodes() {
    const cards = [...(grid?.querySelectorAll(".notes-card") || [])].filter((c) =>
      c.querySelector(".notes-code")
    );
    if (!cards.length) return;
    try {
      const res = await fetch("/accounts/notes/codes", {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!res.ok) return;
      const data = await res.json();
      const byId = new Map((data.codes || []).map((row) => [String(row.id), row]));
      cards.forEach((card) => {
        const row = byId.get(String(card.getAttribute("data-note-id")));
        if (!row) return;
        const codeEl = card.querySelector(".notes-code");
        const remainEl = card.querySelector(".notes-remain");
        if (codeEl) codeEl.textContent = row.code;
        if (remainEl) remainEl.textContent = `${row.remaining}s`;
        card.dataset.code = row.code;
      });
    } catch {
      /* ignore */
    }
  }

  pollCodes();
  setInterval(pollCodes, 5000);
  if (window.lucide?.createIcons) window.lucide.createIcons();
})();
