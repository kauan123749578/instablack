/* Efeitos da tela de login/registro (raios dourados). */
(function () {
  const canvas = document.getElementById("login-rays");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  let w = 0;
  let h = 0;
  let t = 0;
  function resize() {
    w = canvas.width = window.innerWidth;
    h = canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener("resize", resize);
  (function draw() {
    ctx.clearRect(0, 0, w, h);
    const cx = w * 0.5;
    const cy = h * 0.3;
    for (let i = 0; i < 8; i++) {
      const angle = (i / 8) * Math.PI * 2 + t * 0.0003;
      const len = Math.max(w, h) * 1.2;
      const grad = ctx.createLinearGradient(
        cx,
        cy,
        cx + Math.cos(angle) * len,
        cy + Math.sin(angle) * len
      );
      grad.addColorStop(0, "rgba(212,175,55,0.14)");
      grad.addColorStop(0.5, "rgba(212,175,55,0.04)");
      grad.addColorStop(1, "transparent");
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(
        cx + Math.cos(angle - 0.08) * len,
        cy + Math.sin(angle - 0.08) * len
      );
      ctx.lineTo(
        cx + Math.cos(angle + 0.08) * len,
        cy + Math.sin(angle + 0.08) * len
      );
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();
    }
    t++;
    requestAnimationFrame(draw);
  })();
})();
