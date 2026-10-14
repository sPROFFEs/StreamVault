/* System telemetry, forge configuration and private node administration. */
(() => {
    "use strict";
    document.addEventListener("DOMContentLoaded", () => {
        const byId = id => document.getElementById(id);
        if (!byId("admin-panel-system")) return;
        const es = window.APP_LANG === "es";
        const t = (en, spanish) => es ? spanish : en;
        let timer, refreshing = false, sourceLoaded = false;
        const visible = tab => !document.hidden && !byId(`admin-panel-${tab}`).hidden;
        const message = (id, value, error = false) => {
            byId(id).textContent = value;
            byId(id).classList.toggle("operations-error", error);
        };
        const node = (tag, text, className) => {
            const element = document.createElement(tag);
            if (text != null) element.textContent = text;
            if (className) element.className = className;
            return element;
        };
        async function request(path, method = "GET", body) {
            const response = await fetch(path, {method, credentials: "same-origin",
                headers: body === undefined ? {} : {"Content-Type": "application/json"},
                body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(120000)});
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
            return data;
        }
        function row(title, description) {
            const element = node("div", null, "operations-row");
            const details = node("div");
            details.append(node("strong", title));
            if (description) details.append(node("p", description));
            element.append(details);
            return element;
        }
        function list(id, items, render) {
            byId(id).replaceChildren(...(items.length ? items.map(render) : [node("p", t("No entries yet.", "Sin entradas todavía."), "muted")]));
        }
        function button(label, action) {
            const element = node("button", label, "button button--compact button--ghost");
            element.type = "button";
            element.addEventListener("click", async () => {
                element.disabled = true;
                try { await action(); } catch (error) { message("federation-feedback", error.message, true); }
                finally { element.disabled = false; }
            });
            return element;
        }
        async function system() {
            const data = await request("/admin/api/system/monitor");
            byId("system-cpu").textContent = `${data.cpu_percent}%`;
            byId("system-memory").textContent = `${data.memory.percent}%`;
            byId("system-disk").textContent = `${data.disk.percent}%`;
            byId("system-uptime").textContent = `${Math.floor(data.process.uptime_seconds / 3600)} h`;
            list("system-services", data.services || [], item => row(item.name,
                [item.state, item.health, item.restarts != null ? `${t("Restarts", "Reinicios")}: ${item.restarts}` : "", item.oom_killed ? "OOM" : ""].filter(Boolean).join(" · ")));
            message("system-feedback", `${t("Server version", "Versión del servidor")}: ${data.version}`);
        }
        async function history() {
            const [history, backups] = await Promise.all([request("/admin/system/update/history"), request("/admin/system/update/backups")]);
            list("system-history", [...(history.items || [])].reverse(), item => row(`${item.state} · ${item.operation || t("Check", "Comprobación")}`,
                `${new Date(item.updated_at * 1000).toLocaleString()} · ${item.message}`));
            list("system-backups", backups.items || [], item => {
                const element = row(item.name, `${(item.size_bytes / 1024**2).toFixed(1)} MB`);
                const link = node("a", t("Download private backup", "Descargar copia privada"), "button button--compact button--ghost");
                link.href = `/admin/system/update/backups/${encodeURIComponent(item.name)}`;
                element.append(link);
                return element;
            });
        }
        async function federation() {
            const [peers, grants] = await Promise.all([request("/admin/api/federation/peers"), request("/admin/api/federation/grants")]);
            list("federation-peers", peers.items || [], peer => {
                const element = row(peer.name, `${peer.syncing ? t("Synchronizing…", "Sincronizando…") : peer.status} · ${peer.base_url}${peer.last_error ? " · " + peer.last_error : ""}`);
                const actions = node("div", null, "inline-actions");
                actions.append(button(t("Synchronize", "Sincronizar"), async () => {
                    await request(`/admin/api/federation/peers/${peer.id}/sync`, "POST");
                    message("federation-feedback", t("Synchronization queued. Status refreshes automatically.", "Sincronización iniciada. El estado se actualiza automáticamente."));
                }), button(peer.enabled ? t("Disable", "Desactivar") : t("Enable", "Activar"), async () => {
                    await request(`/admin/api/federation/peers/${peer.id}`, "PATCH", {enabled: !peer.enabled});
                    await federation();
                }), button(t("Remove", "Eliminar"), async () => {
                    if (!confirm(t("Remove this node and its imported metadata? No remote files will be deleted.", "¿Eliminar este nodo y sus metadatos importados? No se borrarán archivos remotos."))) return;
                    await request(`/admin/api/federation/peers/${peer.id}`, "DELETE");
                    await federation();
                }));
                element.append(actions);
                return element;
            });
            list("federation-grants", grants.items || [], grant => {
                const scopes = [grant.share_media ? t("Media", "Vídeos") : "", grant.share_profiles ? t("Profiles", "Perfiles") : ""].filter(Boolean).join(" · ");
                const element = row(grant.name, scopes);
                element.append(button(t("Revoke", "Revocar"), async () => {
                    if (!confirm(t("Revoke this node's access immediately?", "¿Revocar inmediatamente el acceso de este nodo?"))) return;
                    await request(`/admin/api/federation/grants/${grant.id}`, "DELETE");
                    await federation();
                }));
                return element;
            });
        }
        function bindForm(id, feedback, submit) {
            const form = byId(id);
            form.addEventListener("submit", async event => {
                event.preventDefault();
                const submitButton = form.querySelector("button[type=submit]");
                if (submitButton.disabled) return;
                submitButton.disabled = true;
                try { await submit(form); } catch (error) { message(feedback, error.message, true); }
                finally { submitButton.disabled = false; }
            });
        }
        function populateSource(data) {
            const form = byId("update-source-form");
            ["mode", "provider", "api_url", "repository", "check_interval_hours"].forEach(key => { form.elements[key].value = data[key] ?? ""; });
            form.elements.automatic_checks.checked = !!data.automatic_checks;
            message("update-source-result", data.has_token ? t("API credential saved (hidden).", "Credencial API guardada (oculta).") : "");
        }
        bindForm("update-source-form", "update-source-result", async form => {
            const body = Object.fromEntries(["mode", "provider", "api_url", "repository"].map(key => [key, form.elements[key].value.trim()]));
            body.check_interval_hours = Number(form.elements.check_interval_hours.value);
            body.automatic_checks = form.elements.automatic_checks.checked;
            if (form.elements.api_token.value.trim()) body.api_token = form.elements.api_token.value.trim();
            populateSource(await request("/admin/system/update/config", "PUT", body));
            form.elements.api_token.value = "";
            message("update-source-result", t("Source saved. Check for updates before installing.", "Origen guardado. Comprueba actualizaciones antes de instalar."));
            byId("btn-update-check").click();
        });
        bindForm("federation-config", "federation-feedback", async form => {
            await request("/admin/api/federation/config", "PUT", {node_name: form.elements.node_name.value.trim(), enabled: form.elements.enabled.checked});
            message("federation-feedback", t("Publication settings saved.", "Publicación guardada."));
        });
        bindForm("federation-peer", "federation-feedback", async form => {
            await request("/admin/api/federation/peers", "POST", {name: form.elements.name.value.trim(), base_url: form.elements.base_url.value.trim(),
                token: form.elements.token.value.trim(), allow_private: form.elements.allow_private.checked});
            form.reset();
            message("federation-feedback", t("Node added. Synchronize to import its available movies and episodes.", "Nodo añadido. Sincroniza para importar sus películas y episodios disponibles."));
            await federation();
        });
        bindForm("federation-grant", "federation-feedback", async form => {
            const data = await request("/admin/api/federation/grants", "POST", {name: form.elements.name.value.trim(),
                share_media: form.elements.share_media.checked, share_profiles: form.elements.share_profiles.checked});
            byId("federation-issued").hidden = false;
            byId("federation-token").value = data.token;
            byId("federation-token").focus();
            byId("federation-token").select();
            await federation();
        });
        const hideToken = () => { byId("federation-token").value = ""; byId("federation-issued").hidden = true; };
        byId("federation-token-hide").addEventListener("click", hideToken);
        let activeTab, refreshAgain = false;
        async function refresh() {
            clearTimeout(timer);
            if (refreshing) { refreshAgain = true; return; }
            refreshing = true;
            try {
                if (visible("system")) {
                    await system();
                    if (activeTab !== "system") await history();
                    activeTab = "system";
                } else if (visible("federation")) {
                    if (activeTab !== "federation") {
                        const config = await request("/admin/api/federation/config");
                        byId("federation-config").elements.node_name.value = config.node_name;
                        byId("federation-config").elements.enabled.checked = config.enabled;
                    }
                    await federation();
                    activeTab = "federation";
                } else {
                    activeTab = null;
                    hideToken();
                    if (visible("overview") && !sourceLoaded) {
                        populateSource(await request("/admin/system/update/config"));
                        sourceLoaded = true;
                    }
                }
            } catch (error) {
                message(visible("federation") ? "federation-feedback" : visible("system") ? "system-feedback" : "update-source-result", error.message, true);
            } finally {
                refreshing = false;
                if (refreshAgain) { refreshAgain = false; void refresh(); }
                else if (visible("system") || visible("federation")) timer = setTimeout(refresh, 10000);
            }
        }
        byId("system-refresh").addEventListener("click", () => { activeTab = null; void refresh(); });
        byId("system-backup").addEventListener("click", async () => {
            if (!confirm(t("Create a private database/configuration backup? Videos are not included.", "¿Crear una copia privada de bases de datos/configuración? No se incluyen vídeos."))) return;
            byId("system-backup").disabled = true;
            try { await request("/admin/system/update/backups", "POST"); await history(); }
            catch (error) { message("system-feedback", error.message, true); }
            finally { byId("system-backup").disabled = false; }
        });
        document.addEventListener("admin:tab-change", refresh);
        document.addEventListener("visibilitychange", refresh);
        window.addEventListener("pagehide", () => { clearTimeout(timer); hideToken(); });
        void refresh();
    });
})();
