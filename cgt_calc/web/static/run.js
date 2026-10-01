// Live log for a run that is still going. Without JavaScript the page
// still works: reload it to see progress.
(() => {
  const log = document.getElementById("log");
  const status = document.getElementById("status");
  const copy = document.getElementById("copy");

  if (copy && navigator.clipboard) {
    copy.hidden = false;
    copy.addEventListener("click", () => {
      const text = document.getElementById("command").textContent.trim();
      navigator.clipboard.writeText(text).then(() => {
        copy.textContent = "Copied";
        setTimeout(() => { copy.textContent = "Copy"; }, 1500);
      });
    });
  }

  if (!log || log.dataset.live !== "true") return;

  const levelOf = (line) => {
    if (line.startsWith("ERROR") || line.startsWith("CRITICAL")) return "error";
    if (line.startsWith("WARNING")) return "warning";
    return "info";
  };

  const source = new EventSource(log.dataset.events);

  source.addEventListener("log", (event) => {
    const text = JSON.parse(event.data);
    const line = document.createElement("span");
    line.className = levelOf(text);
    line.textContent = text + "\n";
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
    log.appendChild(line);
    if (atBottom) log.scrollTop = log.scrollHeight;
    status.textContent = "running";
    status.className = "status running";
  });

  source.addEventListener("done", () => {
    source.close();
    // The server renders the result: report, downloads and final status.
    window.location.reload();
  });

  source.onerror = () => {
    // The browser retries by itself; give up only if the run is gone.
    if (source.readyState === EventSource.CLOSED) {
      status.textContent = "connection lost, reload to check";
    }
  };
})();
