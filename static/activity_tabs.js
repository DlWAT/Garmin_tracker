(() => {
  function init() {
    const root = document.querySelector("[data-tabs]");
    if (!root) return;

    const buttons = Array.from(root.querySelectorAll("[data-tab-button]"));
    const panels = Array.from(document.querySelectorAll("[data-tab-panel]"));

    function activate(key) {
      buttons.forEach((b) => {
        const isActive = b.dataset.tabButton === key;
        b.classList.toggle("tab-btn--active", isActive);
        b.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      panels.forEach((p) => {
        const isActive = p.dataset.tabPanel === key;
        p.classList.toggle("tab-panel--active", isActive);
      });
    }

    buttons.forEach((b) => {
      b.addEventListener("click", () => {
        activate(b.dataset.tabButton);
      });
    });

    // Activate initial
    const initial = (root.dataset.activeTab || "").trim();
    if (initial) {
      activate(initial);
      return;
    }
    if (buttons[0]) activate(buttons[0].dataset.tabButton);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
