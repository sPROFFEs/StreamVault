(function () {
    "use strict";
    document.addEventListener("DOMContentLoaded", () => {
        const panel = document.getElementById("system-update");
        if (!panel) return;
        const es = window.APP_LANG === "es";
        const text = (en, spanish) => es ? spanish : en;
        const element = id => document.getElementById(id);
        const labels = {
            idle: text("Not checked", "Sin comprobar"),
            checking: text("Checking…", "Comprobando…"),
            available: text("Update available", "Actualización disponible"),
            current: text("Up to date", "Actualizado"),
            updating: text("Updating…", "Actualizando…"),
            success: text("Completed", "Completado"),
            failure: text("Failed", "Error"),
        };
        let data = {state: "idle"};
        let busy = false;
        let timer;
        let deadline = 0;
        let loaded = false;

        function controls() {
            const blocked = busy || ["checking", "updating"].includes(data.state);
            panel.setAttribute("aria-busy", String(blocked));
            ["check", "pull", "restart", "recreate"].forEach(name => {
                element(`btn-update-${name}`).disabled = blocked ||
                    (name === "pull" && data.state !== "available" && !data.requires_rebuild);
            });
            element("btn-update-pull").textContent = data.requires_rebuild
                ? text("Retry deployment", "Reintentar despliegue") : text("Install update", "Instalar actualización");
        }

        function render(result) {
            data = {state: "idle", ...result};
            panel.dataset.state = data.state;
            const badge = element("update-badge");
            badge.textContent = labels[data.state] || labels.idle;
            if (data.requires_rebuild && ["current", "success"].includes(data.state)) {
                badge.textContent = text("Rebuild required", "Reconstrucción pendiente");
            }
            badge.className = "badge " + (["current", "success"].includes(data.state) ? "badge--ok"
                : ["failure", "checking", "updating", "available"].includes(data.state) ? "badge--warn" : "badge--neutral");
            element("update-result").textContent = data.message || text("Check for updates.", "Comprueba las actualizaciones.");
            element("update-time").hidden = !data.updated_at;
            element("update-time").textContent = data.updated_at ? text("Status recorded: ", "Estado registrado: ") +
                new Date(data.updated_at * 1000).toLocaleString(es ? "es" : "en") : "";
            element("update-version").hidden = !data.repository;
            element("update-repository").textContent = data.repository || "";
            element("update-branch").textContent = [data.remote, data.branch].filter(Boolean).join(" / ");
            element("update-local").textContent = data.local_commit || "—";
            element("update-remote").textContent = data.remote_commit || "—";
            element("update-release").hidden = !data.release_tag;
            element("update-release").textContent = data.release_tag
                ? `${text("Installed", "Instalado")}: ${data.installed_version || "—"} · Release: ${data.release_tag}` : "";
            element("update-notes").hidden = !data.release_notes;
            element("update-notes").textContent = data.release_notes || "";
            controls();
        }

        async function request(path, method = "GET") {
            const response = await fetch(`/admin/system/update/${path}`, {method, credentials: "same-origin"});
            const result = await response.json();
            if (!response.ok) {
                const error = new Error(result.detail || result.message || `HTTP ${response.status}`);
                error.result = result;
                throw error;
            }
            return result;
        }

        function schedule() {
            clearTimeout(timer);
            if (["checking", "updating"].includes(data.state)) {
                if (!deadline) deadline = Date.now() + 35 * 60 * 1000;
                if (Date.now() < deadline) timer = setTimeout(refresh, 2000);
                else render({state: "failure", message: text(
                    "Deployment status could not be verified in time. Check again; a worker may still be running.",
                    "No se pudo verificar el despliegue a tiempo. Comprueba de nuevo; el proceso puede seguir activo.")});
            } else deadline = 0;
        }

        async function refresh() {
            try {
                render(await request("status"));
                loaded = true;
            } catch (error) {
                if (["checking", "updating"].includes(data.state)) {
                    element("update-result").textContent = text(
                        "Waiting for the server to return; deployment completion is not yet confirmed…",
                        "Esperando al servidor; el despliegue aún no está confirmado…");
                } else render({state: "failure", message: error.message});
            }
            schedule();
        }

        async function action(path) {
            if (busy || ["checking", "updating"].includes(data.state)) return;
            busy = true;
            render({...data, state: path === "check" ? "checking" : "updating", message: path === "check"
                ? text("Checking the configured source…", "Comprobando el origen configurado…")
                : text("Starting the deployment worker…", "Iniciando el proceso de despliegue…")});
            try {
                render(await request(path, "POST"));
            } catch (error) {
                if (error.result) {
                    render({...error.result, state: error.result.state || "failure", message: error.message});
                } else {
                    // A lost POST response can mean the server was recreated.
                    // Query the persistent job state before offering another update.
                    element("update-result").textContent = text("Connection lost. Checking deployment status…", "Conexión perdida. Comprobando el estado…");
                    await refresh();
                }
            } finally {
                busy = false;
                controls();
                schedule();
            }
        }

        element("btn-update-check").addEventListener("click", () => action("check"));
        const operations = {
            pull: ["git-pull", text("Install the update and recreate services? Service may be interrupted.", "¿Instalar la actualización y recrear los servicios? El servicio puede interrumpirse.")],
            restart: ["docker-restart", text("Restart StreamVault core?", "¿Reiniciar StreamVault core?")],
            recreate: ["docker-recreate", text("Rebuild and recreate deployment services? Service may be interrupted.", "¿Reconstruir y recrear los servicios? El servicio puede interrumpirse.")],
        };
        Object.entries(operations).forEach(([name, [path, prompt]]) => {
            element(`btn-update-${name}`).addEventListener("click", () => {
                if (!busy && confirm(prompt)) void action(path);
            });
        });
        const loadOverview = () => {
            if (!element("admin-panel-overview").hidden && !loaded) void refresh();
        };
        document.addEventListener("admin:tab-change", loadOverview);
        window.addEventListener("pagehide", () => clearTimeout(timer));
        loadOverview();
    });
})();
