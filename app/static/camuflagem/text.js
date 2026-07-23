/** Camuflagem — texto: homóglifos + zero-width */

const HOMOGLYPHS = {
  a: "а", c: "с", d: "ԁ", e: "е", i: "і", j: "ј", o: "о", p: "р", s: "ѕ", x: "х", y: "у",
  A: "А", B: "В", C: "С", E: "Е", H: "Н", I: "І", J: "Ј", K: "К", M: "М", O: "О", P: "Р",
  S: "Ѕ", T: "Т", X: "Х", Y: "У",
};

const ZERO_WIDTH = ["\u200B", "\u200C", "\u200D", "\u2060", "\u00AD"];

export function cloakText(input, useHomoglyphs, useZeroWidth) {
  let out = "";
  for (let i = 0; i < input.length; i++) {
    let ch = input[i];
    if (useHomoglyphs && HOMOGLYPHS[ch]) ch = HOMOGLYPHS[ch];
    out += ch;
    const isSpace = ch === " " || ch === "\n" || ch === "\r" || ch === "\t";
    if (useZeroWidth && !isSpace && i < input.length - 1 && Math.random() > 0.5) {
      out += ZERO_WIDTH[Math.floor(Math.random() * ZERO_WIDTH.length)];
    }
  }
  return out;
}

export function initTextTab() {
  const input = document.getElementById("camu-text-in");
  const output = document.getElementById("camu-text-out");
  const homo = document.getElementById("camu-homo");
  const zw = document.getElementById("camu-zw");
  const badgeHomo = document.getElementById("badge-homo");
  const badgeZw = document.getElementById("badge-zw");
  const copyBtn = document.getElementById("camu-text-copy");
  if (!input || !output) return;

  function refresh() {
    const useH = !!homo?.checked;
    const useZ = !!zw?.checked;
    badgeHomo?.classList.toggle("is-on", useH);
    badgeZw?.classList.toggle("is-on", useZ);
    output.value = cloakText(input.value || "", useH, useZ);
  }

  input.addEventListener("input", refresh);
  homo?.addEventListener("change", refresh);
  zw?.addEventListener("change", refresh);
  refresh();

  copyBtn?.addEventListener("click", async () => {
    const text = output.value || "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      const prev = copyBtn.textContent;
      copyBtn.textContent = "Copiado!";
      setTimeout(() => {
        copyBtn.innerHTML = '<i data-lucide="copy"></i> Copiar resultado';
        window.lucide?.createIcons?.();
      }, 1200);
    } catch {
      output.select();
      document.execCommand("copy");
    }
  });
}
