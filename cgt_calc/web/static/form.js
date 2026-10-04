// Small conveniences for the options form. It works without this script.
(() => {
  const form = document.getElementById("options");
  if (!form) return;

  form.addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "file") return;
    const field = input.closest(".field");
    const count = input.files ? input.files.length : 0;
    field.classList.toggle("has-file", count > 0 || field.dataset.reused === "true");
    const note = field.querySelector(".count");
    if (note) note.textContent = count > 1 ? `${count} files` : "";
  });

  // Uploads can take a moment; stop a second click starting a second run.
  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    button.textContent = "Uploading…";
  });

  // Coming back with the browser's back button restores the page as it was.
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    const button = form.querySelector("button[type=submit]");
    button.disabled = false;
    button.textContent = "Calculate";
  });
})();
