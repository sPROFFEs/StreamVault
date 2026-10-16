/* Device downloads and a deliberately small, server-independent offline library. */
(() => {
    "use strict";
    let savedLang;
    try { savedLang = localStorage.getItem("sv_offline_lang"); } catch (_) {}
    const es = String(window.APP_LANG || savedLang || navigator.language || "en").startsWith("es");
    const text = es ? {
        title: "Biblioteca sin conexión", device: "ESTE DISPOSITIVO", server: "Biblioteca del servidor",
        intro: "Las descargas completadas en este dispositivo funcionan sin Internet ni conexión al servidor.",
        limit: "Límite del dispositivo (GiB)", saveLimit: "Guardar límite", close: "Cerrar reproductor",
        privacy: "Las descargas quedan en este perfil del navegador/dispositivo incluso tras cerrar sesión. Cualquiera que use este perfil podrá verlas. Borrar los datos del sitio, desinstalar la app o cambiar de servidor las elimina. El navegador puede liberar espacio; no son una copia de seguridad.",
        empty: "Aún no hay vídeos guardados aquí. Abre la biblioteca del servidor y elige «Guardar en dispositivo».",
        save: "Guardar en dispositivo", start: "Descargar", cancel: "Cancelar", remove: "Eliminar", play: "Reproducir",
        preparing: "Preparando vídeo…", done: "Descarga completada. Ya puedes verla sin conexión.",
        hint: "Se guardará una copia en este dispositivo, no en el servidor. Mantén esta pantalla abierta hasta terminar. Se guardan el audio seleccionado y los subtítulos incluidos disponibles; una conversión puede tardar.",
        partial: "Incompleta · eliminar y descargar de nuevo", saved: "Límite guardado.", unsupported: "El dispositivo no pudo reproducir este vídeo. Comprueba sus códecs.",
        deleting: "¿Eliminar esta copia del dispositivo? El archivo del servidor no se borrará.", cancelled: "Descarga cancelada.",
    } : {
        title: "Offline library", device: "THIS DEVICE", server: "Server library",
        intro: "Your completed device downloads work without the server or Internet.",
        limit: "Device limit (GiB)", saveLimit: "Save limit", close: "Close player",
        privacy: "Downloads stay in this browser/device profile, even after sign-out. Anyone using this profile can play them. Clearing site data, uninstalling the app or switching servers deletes them. The browser can reclaim space; these are not backups.",
        empty: "No videos saved here yet. Open the server library and choose “Save to device”.",
        save: "Save to device", start: "Download", cancel: "Cancel", remove: "Delete", play: "Play",
        preparing: "Preparing video…", done: "Download complete. Ready to watch offline.",
        hint: "A copy will be saved on this device, not the server. Keep this screen open until it finishes. The selected audio and available included subtitles are saved; conversion may take time.",
        partial: "Incomplete · delete and download again", saved: "Limit saved.", unsupported: "This device could not play the video. Check its codec support.",
        deleting: "Delete this device copy? The server file will not be deleted.", cancelled: "Download cancelled.",
    };
    function bytes(value) { return `${(Number(value || 0) / 1024 ** 3).toFixed(2)} GiB`; }
    function node(tag, label, className) {
        const el = document.createElement(tag);
        if (label) el.textContent = label;
        if (className) el.className = className;
        return el;
    }
    function button(label, callback) {
        const el = node("button", label);
        el.type = "button"; el.addEventListener("click", callback);
        return el;
    }
    function dialog(title, description) {
        const el = node("dialog", "", "offline-dialog");
        const heading = node("h2", title);
        heading.id = `offline-dialog-${crypto.randomUUID()}`;
        el.setAttribute("aria-labelledby", heading.id);
        el.append(heading, node("p", description));
        document.body.append(el);
        el.addEventListener("close", () => el.remove());
        el.showModal();
        return el;
    }

    function download(file, options = {}) {
        if (document.querySelector(".offline-dialog")) return;
        try { if (window.APP_LANG) localStorage.setItem("sv_offline_lang", window.APP_LANG); } catch (_) {}
        const panel = dialog(text.save, `${file.title || file.name || "Video"} · ${text.hint}`);
        const status = node("p", `${text.limit}: ${bytes(SVOffline.limitBytes())}`);
        status.setAttribute("role", "status");
        const progress = document.createElement("progress");
        progress.setAttribute("aria-label", text.save);
        progress.hidden = true;
        const actions = node("div", "", "offline-actions");
        const controller = new AbortController();
        const cancel = button(text.cancel, () => { controller.abort(); panel.close(); });
        panel.addEventListener("cancel", () => controller.abort());
        const start = button(text.start, async () => {
            start.disabled = true; progress.hidden = false; status.textContent = text.preparing;
            window.addEventListener("beforeunload", warn);
            try {
                await SVOffline.save(file, {...options, signal: controller.signal, onProgress: ({loaded, total}) => {
                    status.textContent = `${bytes(loaded)}${total ? ` / ${bytes(total)}` : ""}`;
                    if (total) { progress.max = total; progress.value = loaded; }
                }});
                status.textContent = text.done;
                cancel.textContent = text.close;
                start.remove(); progress.hidden = true;
                const library = node("a", text.title);
                library.href = "/assets/offline.html"; actions.prepend(library);
            } catch (error) {
                status.textContent = error.name === "AbortError" ? text.cancelled : error.message;
                start.disabled = false; progress.hidden = true;
            } finally { window.removeEventListener("beforeunload", warn); }
        });
        function warn(event) { event.preventDefault(); event.returnValue = ""; }
        start.classList.add("offline-primary");
        actions.append(cancel, start); panel.append(status, progress, actions);
        cancel.focus();
    }

    let trackUrls = [];
    function closePlayer() {
        const video = document.getElementById("offline-video");
        video.pause(); video.removeAttribute("src"); video.replaceChildren(); video.load();
        trackUrls.forEach(url => URL.revokeObjectURL(url)); trackUrls = [];
        document.getElementById("offline-player-panel").hidden = true;
    }

    async function refresh() {
        const entries = await SVOffline.list();
        const list = document.getElementById("offline-list");
        list.replaceChildren();
        document.getElementById("offline-empty").hidden = entries.length > 0;
        const used = entries.reduce((sum, entry) => sum + (entry.storage_bytes || entry.size_bytes), 0);
        let storage = `${bytes(used)} / ${bytes(SVOffline.limitBytes())}`;
        try {
            const estimate = await navigator.storage.estimate();
            if (estimate.quota) storage += ` · ${es ? "Espacio disponible" : "Available space"}: ${bytes(Math.max(0, estimate.quota - estimate.usage))}`;
        } catch (_) {}
        document.getElementById("offline-storage").textContent = storage;
        for (const entry of entries.sort((a, b) => b.created_at - a.created_at)) {
            const row = node("article", "", "offline-item"), info = node("div"), actions = node("div", "", "offline-actions");
            info.append(node("strong", `${entry.title}${entry.episode ? ` · ${entry.episode}` : ""}`), node("p", `${entry.origin} · ${bytes(entry.size_bytes)}${entry.status !== "complete" ? ` · ${text.partial}` : ""}`));
            if (entry.status === "complete") actions.append(button(text.play, () => {
                closePlayer();
                const panel = document.getElementById("offline-player-panel"), video = document.getElementById("offline-video");
                document.getElementById("offline-player-title").textContent = entry.title;
                for (const subtitle of entry.subtitles || []) {
                    const track = document.createElement("track");
                    track.kind = "subtitles"; track.label = subtitle.label; track.srclang = subtitle.language;
                    track.src = URL.createObjectURL(new Blob([subtitle.text], {type: "text/vtt"}));
                    trackUrls.push(track.src); video.append(track);
                }
                video.src = SVOffline.mediaUrl(entry.id); panel.hidden = false;
                video.play().catch(error => { document.getElementById("offline-status").textContent = error.name === "NotAllowedError" ? text.play : text.unsupported; });
                panel.scrollIntoView({behavior: "smooth", block: "start"}); video.focus();
            }));
            const remove = button(text.remove, () => {
                const confirm = dialog(text.remove, text.deleting);
                const controls = node("div", "", "offline-actions");
                controls.append(button(text.cancel, () => confirm.close()), button(text.remove, async () => {
                    try { closePlayer(); await SVOffline.remove(entry.id); confirm.close(); await refresh(); }
                    catch (error) { document.getElementById("offline-status").textContent = error.message; confirm.close(); }
                }));
                confirm.append(controls);
            });
            remove.dataset.offlineRemove = entry.id; actions.append(remove);
            row.append(info, actions); list.append(row);
        }
    }

    async function boot() {
        if (!document.getElementById("offline-list")) return;
        document.documentElement.lang = es ? "es" : "en";
        document.querySelectorAll("[data-offline-label]").forEach(el => { el.textContent = text[el.dataset.offlineLabel]; });
        document.getElementById("offline-limit").value = SVOffline.limitBytes() / 1024 ** 3;
        document.getElementById("offline-save-limit").addEventListener("click", async () => {
            try {
                SVOffline.setLimitGiB(Number(document.getElementById("offline-limit").value));
                document.getElementById("offline-status").textContent = text.saved;
                await refresh();
            } catch (error) { document.getElementById("offline-status").textContent = error.message; }
        });
        document.getElementById("offline-close").addEventListener("click", closePlayer);
        document.getElementById("offline-video").addEventListener("error", () => { document.getElementById("offline-status").textContent = text.unsupported; });
        try { await SVOffline.ensureWorker(); await refresh(); }
        catch (error) { document.getElementById("offline-status").textContent = error.message; }
    }
    window.SVOfflineUI = {download, refresh};
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
})();
