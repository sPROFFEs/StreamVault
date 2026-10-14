/* StreamVault keyboard-shortcuts modal. */
(function () {
    "use strict";

    let modal = null;

    const SHORTCUTS = [
        { keys: ["Ctrl", "F"], label: "Open search" },
        { keys: ["Ctrl", "K"], label: "Filter downloads" },
        { keys: ["Ctrl", "Z"], label: "Go back" },
        { keys: ["Ctrl", "R"], label: "Reload" },
        { keys: ["Esc"], label: "Close modal / overlay" },
        { keys: ["?"], label: "Show this help" },
    ];

    function build() {
        modal = document.createElement("div");
        modal.className = "modal-backdrop";
        modal.style.display = "none";
        modal.innerHTML = `
            <div class="modal" role="dialog" aria-modal="true" aria-label="${document.documentElement.lang === 'es' ? 'Atajos de teclado' : 'Keyboard shortcuts'}">
                <div class="modal__header">
                    KEYBOARD SHORTCUTS
                    <button class="icon-btn" id="sv-shortcuts-close" aria-label="Close">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
                    </button>
                </div>
                <div class="modal__body">
                    <div id="sv-shortcuts-list" style="display:grid;gap:10px"></div>
                </div>
            </div>
        `;
        modal.addEventListener("click", (e) => {
            if (e.target === modal) close();
        });
        modal.querySelector("#sv-shortcuts-close").addEventListener("click", close);
        const list = modal.querySelector("#sv-shortcuts-list");
        SHORTCUTS.forEach((s) => {
            const row = document.createElement("div");
            row.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:12px";
            const lbl = document.createElement("span");
            lbl.textContent = s.label;
            lbl.style.cssText = "color:var(--text2);font-size:14px";
            const keys = document.createElement("span");
            keys.style.cssText = "display:flex;gap:4px;flex-wrap:wrap";
            s.keys.forEach((k) => {
                const kbd = document.createElement("kbd");
                kbd.textContent = k;
                keys.appendChild(kbd);
            });
            row.appendChild(lbl);
            row.appendChild(keys);
            list.appendChild(row);
        });
        document.body.appendChild(modal);
    }

    function open() {
        if (!modal) build();
        window.SVShell.open(modal);
    }
    function close() {
        if (modal) window.SVShell.close(modal);
    }
    function toggle() {
        if (!modal) build();
        if (window.SVShell.isOpen(modal)) close();
        else open();
    }

    window.SVShortcutsModal = { open, close, toggle };
})();
