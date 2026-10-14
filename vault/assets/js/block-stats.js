/* Streambert-style blocked-stats modal — server-side counts from
   /api/block-stats (per-worker, in-memory). Triggered via window.SVBlockStats.open(). */
(function () {
    "use strict";

    let modal = null;

    async function fetchStats() {
        try {
            const r = await fetch("/api/block-stats", { credentials: "same-origin" });
            if (!r.ok) return null;
            return await r.json();
        } catch (e) { return null; }
    }

    function build() {
        modal = document.createElement("div");
        modal.className = "modal-backdrop";
        modal.style.display = "none";
        modal.innerHTML = `
            <div class="modal modal--wide" role="dialog" aria-modal="true" aria-label="${document.documentElement.lang === 'es' ? 'Solicitudes bloqueadas' : 'Blocked requests'}">
                <div class="modal__header">
                    BLOCKED REQUESTS
                    <button class="icon-btn" id="bs-close" aria-label="Close">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
                    </button>
                </div>
                <div class="modal__body">
                    <div style="display:flex;gap:14px;margin-bottom:18px">
                        <div class="panel" style="flex:1"><div class="panel__body">
                            <div class="text-muted text-sm">Session</div>
                            <div style="font-family:var(--font-display);font-size:36px;color:var(--red)" id="bs-session">0</div>
                        </div></div>
                        <div class="panel" style="flex:1"><div class="panel__body">
                            <div class="text-muted text-sm">All-time</div>
                            <div style="font-family:var(--font-display);font-size:36px" id="bs-alltime">0</div>
                        </div></div>
                    </div>
                    <div class="section-title" style="font-size:18px">Top blocked hosts</div>
                    <div id="bs-list" style="display:grid;gap:4px;font-size:12px"></div>
                </div>
                <div class="modal__footer">
                    <button class="btn btn-ghost" id="bs-reset">Reset session</button>
                    <button class="btn btn-secondary" id="bs-refresh">Refresh</button>
                </div>
            </div>
        `;
        modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
        modal.querySelector("#bs-close").addEventListener("click", close);
        modal.querySelector("#bs-refresh").addEventListener("click", refresh);
        modal.querySelector("#bs-reset").addEventListener("click", async () => {
            await fetch("/api/block-stats/reset-session", { method: "POST", credentials: "same-origin" });
            refresh();
        });
        document.body.appendChild(modal);
    }

    async function refresh() {
        const data = await fetchStats();
        if (!data) return;
        modal.querySelector("#bs-session").textContent = data.session_total;
        modal.querySelector("#bs-alltime").textContent = data.all_time_total;
        const list = modal.querySelector("#bs-list");
        list.innerHTML = "";
        Object.entries(data.session_by_host || {}).forEach(([host, count]) => {
            const row = document.createElement("div");
            row.style.cssText = "display:flex;justify-content:space-between;padding:6px 10px;border:1px solid var(--border);border-radius:5px;background:var(--surface2)";
            row.innerHTML = `<span>${host || "?"}</span><span style="color:var(--red);font-weight:600">${count}</span>`;
            list.appendChild(row);
        });
        if (!Object.keys(data.session_by_host || {}).length) {
            list.innerHTML = `<div class="text-muted" style="padding:20px;text-align:center">No blocks recorded yet.</div>`;
        }
    }

    function open() {
        if (!modal) build();
        window.SVShell.open(modal);
        refresh();
    }
    function close() { if (modal) window.SVShell.close(modal); }

    window.SVBlockStats = { open, close };
})();
