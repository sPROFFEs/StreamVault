(function () {
    "use strict";
    const $ = (selector) => document.querySelector(selector);
    const lang = window.APP_LANG === "es" ? "es" : "en";
    let status = null;

    async function request(url, options = {}) {
        const response = await fetch(url, {
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", ...(options.headers || {}) },
            ...options,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        return data;
    }

    function setBusy(busy) {
        ["#tunnel-save", "#tunnel-start", "#tunnel-stop", "#tunnel-create", "#tunnel-enabled", "#tunnel-refresh"]
            .forEach((selector) => { const element = $(selector); if (element) element.disabled = busy; });
    }

    function syncMode() {
        const managed = $("#tunnel-mode").value === "managed";
        $("#tunnel-managed-fields").hidden = !managed;
        $("#tunnel-token-field").hidden = !managed;
    }

    function render(data) {
        status = data;
        $("#tunnel-enabled").checked = !!data.enabled;
        $("#tunnel-mode").value = data.mode || "quick";
        $("#tunnel-origin").value = data.origin || "http://nginx:80";
        syncMode();
        const badge = $("#tunnel-status");
        badge.className = `badge ${data.connected ? "badge--ok" : data.running || !data.installed ? "badge--warn" : "badge--neutral"}`;
        badge.textContent = data.connected
            ? (lang === "es" ? "Conectado" : "Connected")
            : data.running ? (lang === "es" ? "Conectando" : "Connecting")
            : data.installed ? (lang === "es" ? "Detenido" : "Stopped") : (lang === "es" ? "No instalado" : "Not installed");
        const result = $("#tunnel-result");
        result.setAttribute("role", "status");
        result.classList.remove("tunnel-result--error");
        if (data.public_url) {
            result.hidden = false;
            result.innerHTML = "";
            const label = document.createElement("span");
            label.textContent = lang === "es" ? "Dirección pública" : "Public address";
            const link = document.createElement("a");
            link.href = data.public_url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = data.public_url;
            result.append(label, link);
        } else {
            result.hidden = true;
        }
        const logs = $("#tunnel-logs");
        logs.textContent = (data.recent_logs || []).join("\n");
        logs.hidden = !logs.textContent;
    }

    function tunnelError(error) {
        const result = $("#tunnel-result");
        result.hidden = false;
        result.setAttribute("role", "alert");
        result.textContent = error.message;
        result.classList.add("tunnel-result--error");
    }

    async function action(url, body) {
        setBusy(true);
        try {
            render(await request(url, { method: "POST", body: body ? JSON.stringify(body) : undefined }));
        } catch (error) {
            tunnelError(error);
        } finally {
            setBusy(false);
        }
    }

    function initTabs() {
        const tabs = [...document.querySelectorAll("[data-admin-tab]")];
        const panels = [...document.querySelectorAll("[data-admin-panel]")];
        function activate(key, push = false) {
            if (!tabs.some(tab => tab.dataset.adminTab === key)) key = "overview";
            tabs.forEach(tab => {
                const selected = tab.dataset.adminTab === key;
                tab.setAttribute("aria-selected", String(selected));
                tab.tabIndex = selected ? 0 : -1;
            });
            panels.forEach(panel => { panel.hidden = panel.dataset.adminPanel !== key; });
            if (push) {
                const url = new URL(location.href);
                url.searchParams.set("tab", key);
                url.searchParams.delete("manage_users");
                url.hash = "";
                history.pushState({}, "", url);
            }
            document.dispatchEvent(new CustomEvent("admin:tab-change", {detail: {tab: key}}));
        }
        function restore() {
            const params = new URLSearchParams(location.search);
            const legacyFiles = location.hash === "#downloaded-content-title" || [...params.keys()].some(key => key.startsWith("download_"));
            activate(params.get("tab") || (params.has("manage_users") ? "users" : legacyFiles ? "files" : "overview"));
        }
        tabs.forEach((tab, index) => {
            tab.addEventListener("click", event => {
                if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                event.preventDefault();
                activate(tab.dataset.adminTab, true);
            });
            tab.addEventListener("keydown", event => {
                let next;
                if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
                else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
                else if (event.key === "Home") next = 0;
                else if (event.key === "End") next = tabs.length - 1;
                else return;
                event.preventDefault();
                tabs[next].focus();
                activate(tabs[next].dataset.adminTab, true);
            });
        });
        window.addEventListener("popstate", restore);
        restore();
    }

    function boot() {
        initTmdbKey();
        initTabs();
        const refreshTunnel = async (force = false) => {
            if ($("#admin-panel-remote").hidden || document.hidden ||
                (force !== true && document.activeElement?.closest(".tunnel-config, .tunnel-create"))) return;
            try { render(await request("/admin/api/tunnel/status")); }
            catch (error) {
                $("#tunnel-status").textContent = lang === "es" ? "No disponible" : "Unavailable";
                tunnelError(error);
            }
        };
        document.addEventListener("admin:tab-change", () => void refreshTunnel());
        void refreshTunnel();
        $("#tunnel-refresh").addEventListener("click", () => void refreshTunnel(true));
        $("#tunnel-mode").addEventListener("change", syncMode);
        $("#tunnel-save").addEventListener("click", async () => {
            setBusy(true);
            try {
                render(await request("/admin/api/tunnel/config", {
                    method: "PUT",
                    body: JSON.stringify({
                        mode: $("#tunnel-mode").value,
                        origin: $("#tunnel-origin").value.trim(),
                        token: $("#tunnel-token").value.trim() || undefined,
                    }),
                }));
                $("#tunnel-token").value = "";
                if (!status.public_url) {
                    $("#tunnel-result").hidden = false;
                    $("#tunnel-result").textContent = lang === "es" ? "Configuración guardada." : "Configuration saved.";
                }
            } catch (error) {
                tunnelError(error);
            } finally { setBusy(false); }
        });
        $("#tunnel-start").addEventListener("click", () => action("/admin/api/tunnel/start"));
        $("#tunnel-stop").addEventListener("click", () => action("/admin/api/tunnel/stop"));
        $("#tunnel-enabled").addEventListener("change", async (event) => {
            setBusy(true);
            try {
                render(await request("/admin/api/tunnel/config", {
                    method: "PUT", body: JSON.stringify({ enabled: event.target.checked }),
                }));
            } catch (error) {
                event.target.checked = !event.target.checked;
                tunnelError(error);
            } finally { setBusy(false); }
        });
        $("#tunnel-create").addEventListener("click", () => action("/admin/api/tunnel/create", {
            account_id: $("#tunnel-account").value.trim(),
            zone_id: $("#tunnel-zone").value.trim(),
            api_token: $("#tunnel-api-token").value,
            tunnel_name: $("#tunnel-name").value.trim(),
            hostname: $("#tunnel-hostname").value.trim(),
            origin: $("#tunnel-origin").value.trim(),
        }));
        window.setInterval(refreshTunnel, 10000);
    }

    // ── TMDB API key management ──────────────────────────────────────────────
    function initTmdbKey() {
        const statusEl = $("#tmdb-status");
        const input = $("#tmdb-key-input");
        const saveBtn = $("#tmdb-save");
        const clearBtn = $("#tmdb-clear");
        const resultEl = $("#tmdb-result");
        if (!statusEl) return;

        async function checkStatus() {
            try {
                const data = await request("/admin/api/tmdb-key");
                if (data.configured) {
                    statusEl.className = "badge badge--ok";
                    statusEl.textContent = lang === "es"
                        ? `Configurado (${data.source === "file" ? "archivo" : "env"})`
                        : `Configured (${data.source})`;
                } else {
                    statusEl.className = "badge badge--warn";
                    statusEl.textContent = lang === "es" ? "No configurado" : "Not set";
                }
            } catch (_) {
                statusEl.className = "badge badge--warn";
                statusEl.textContent = lang === "es" ? "Error" : "Error";
            }
        }

        saveBtn?.addEventListener("click", async () => {
            const key = input.value.trim();
            if (!key) {
                resultEl.textContent = lang === "es" ? "Introduce una clave." : "Enter a key.";
                input.setAttribute("aria-invalid", "true");
                input.focus();
                return;
            }
            input.removeAttribute("aria-invalid");
            saveBtn.disabled = true;
            clearBtn.disabled = true;
            saveBtn.textContent = lang === "es" ? "Guardando…" : "Saving…";
            try {
                await request("/admin/api/tmdb-key", { method: "PUT", body: JSON.stringify({ api_key: key }) });
                resultEl.style.color = "var(--success)";
                resultEl.textContent = lang === "es" ? "Clave guardada y recargada." : "Key saved and reloaded.";
                input.value = "";
                checkStatus();
            } catch (e) {
                input.setAttribute("aria-invalid", "true");
                resultEl.style.color = "var(--danger)";
                resultEl.textContent = e.message;
            } finally {
                saveBtn.disabled = false;
                clearBtn.disabled = false;
                saveBtn.textContent = lang === "es" ? "Guardar y recargar" : "Save & reload";
            }
        });

        clearBtn?.addEventListener("click", async () => {
            clearBtn.disabled = true;
            saveBtn.disabled = true;
            try {
                await request("/admin/api/tmdb-key", { method: "DELETE" });
                resultEl.style.color = "var(--text3)";
                resultEl.textContent = lang === "es" ? "Clave eliminada." : "Key cleared.";
                checkStatus();
            } catch (e) {
                resultEl.style.color = "var(--danger)";
                resultEl.textContent = e.message;
            } finally {
                clearBtn.disabled = false;
                saveBtn.disabled = false;
            }
        });

        document.addEventListener("admin:tab-change", event => {
            if (event.detail.tab === "catalog") void checkStatus();
        });
    }

    document.addEventListener("DOMContentLoaded", boot);
})();
