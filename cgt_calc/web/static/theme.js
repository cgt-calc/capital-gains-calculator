// Loaded in <head>, before the page paints, so a saved choice never flashes.
// With no saved choice the system setting applies (see app.css).
(() => {
  const KEY = "cgt-theme";
  const root = document.documentElement;
  const system = window.matchMedia("(prefers-color-scheme: dark)");

  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  } catch {
    // Storage can be blocked; the system setting still applies.
  }

  const isDark = () =>
    root.dataset.theme ? root.dataset.theme === "dark" : system.matches;

  document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("theme");
    if (!button) return;
    const sync = () => {
      const dark = isDark();
      button.textContent = dark ? "Light mode" : "Dark mode";
      button.setAttribute("aria-pressed", String(dark));
    };
    button.hidden = false;
    sync();
    button.addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      root.dataset.theme = next;
      try {
        localStorage.setItem(KEY, next);
      } catch {
        // Not saved; applies until the page is closed.
      }
      sync();
    });
    system.addEventListener("change", sync);
  });
})();
