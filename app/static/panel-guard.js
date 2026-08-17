(() => {
  "use strict";

  const stop = (event) => {
    event.preventDefault();
    event.stopPropagation();
    return false;
  };

  document.addEventListener("contextmenu", stop, { capture: true });

  document.addEventListener(
    "keydown",
    (event) => {
      const key = event.key;
      const ctrl = event.ctrlKey || event.metaKey;
      const shift = event.shiftKey;
      const alt = event.altKey;

      if (key === "F12") {
        stop(event);
        return;
      }

      if (shift && ctrl && /^[ijc]$/i.test(key)) {
        stop(event);
        return;
      }

      if (alt && ctrl && /^[ijc]$/i.test(key)) {
        stop(event);
        return;
      }

      if (shift && ctrl && /^k$/i.test(key)) {
        stop(event);
        return;
      }

      if (ctrl && !shift && !alt && /^u$/i.test(key)) {
        stop(event);
        return;
      }
    },
    { capture: true }
  );
})();
