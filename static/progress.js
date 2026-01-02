(() => {
  function clamp(n, min, max) {
    return Math.max(min, Math.min(max, n));
  }

  function init() {
    const root = document.getElementById("task-progress");
    if (!root) return;

    const statusUrl = root.dataset.statusUrl;
    const redirectUrl = root.dataset.redirectUrl;

    const bar = root.querySelector("[data-progress-bar]");
    const pctText = root.querySelector("[data-progress-percent]");
    const msg = root.querySelector("[data-progress-message]");
    const err = root.querySelector("[data-progress-error]");

    if (!statusUrl || !bar || !pctText || !msg || !err) return;

    function setError(text) {
      err.textContent = text || "";
    }

    function tick() {
      fetch(statusUrl, { headers: { Accept: "application/json" } })
        .then(async (resp) => {
          let data = null;
          try {
            data = await resp.json();
          } catch {
            data = null;
          }
          return { ok: resp.ok, data };
        })
        .then(({ ok, data }) => {
          if (!ok || !data) {
            setError("Impossible de récupérer la progression.");
            window.setTimeout(tick, 1200);
            return;
          }

          const pct = clamp(Number(data.percent || 0), 0, 100);
          bar.style.width = `${pct}%`;
          pctText.textContent = `${Math.round(pct)}%`;
          msg.textContent = data.message || "";

          if (data.state === "done") {
            msg.textContent = "Terminé. Rechargement…";
            window.setTimeout(() => {
              window.location.href = redirectUrl || window.location.pathname;
            }, 700);
            return;
          }

          if (data.state === "error") {
            setError(data.error || "Erreur pendant la mise à jour.");
            return;
          }

          setError("");
          window.setTimeout(tick, 650);
        })
        .catch(() => {
          setError("Connexion perdue… nouvelle tentative.");
          window.setTimeout(tick, 1500);
        });
    }

    tick();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
