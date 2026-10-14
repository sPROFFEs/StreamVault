/* StreamVault IPTV — Streambert-style live-TV UI with persistent
   favorites + recents and improved channel browser. */
(function () {
    "use strict";

    const $ = (s, r) => (r || document).querySelector(s);
    const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    const LANG = window.APP_LANG === "es" ? "es" : "en";

    const LAST_CH_KEY = "sv_iptv_last_channel";

    const state = {
        all: [],
        favorites: new Map(),
        recents: [],
        sources: [],
        tab: "featured",
        filters: { search: "", source: "", group: "", country: "", language: "" },
        active: null,
        hls: null,
        reloading: false,
        visibleLimit: 240,
    };

    /* ── Networking ─────────────────────────────────────────────────────── */
    async function api(path, init) {
        const r = await fetch(path, { credentials: "same-origin", ...(init || {}) });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    }

    async function bootstrap() {
        try {
            return await api("/api/iptv/bootstrap", { method: "POST" });
        } catch (error) {
            return { success: false, errors: [{ source: "server", error: error.message }] };
        }
    }
    async function loadSources()  { state.sources = await api("/api/iptv/sources").catch(() => []); }
    async function loadGroups()   { return api("/api/iptv/channels/groups").catch(() => ({})); }
    async function loadCountries(){ return api("/api/iptv/countries").catch(() => []); }
    async function loadLangs()    { return api("/api/iptv/languages").catch(() => []); }
    async function loadChannels(filters) {
        const pageSize = 5000;
        const params = new URLSearchParams();
        if (filters.search) params.set("search", filters.search);
        if (filters.group) params.set("group", filters.group);
        if (filters.country) params.set("country", filters.country);
        if (filters.language) params.set("language", filters.language);
        params.set("limit", String(pageSize));
        const channels = [];
        let offset = 0;
        let total = 1;
        while (offset < total) {
            params.set("offset", String(offset));
            const data = await api(`/api/iptv/channels?${params}`).catch(() => ({ channels: [], total: 0 }));
            const batch = data.channels || [];
            channels.push(...batch);
            total = Number(data.total || channels.length);
            if (!batch.length) break;
            offset += batch.length;
        }
        return channels;
    }
    async function loadFavorites() {
        try {
            const r = await fetch("/api/iptv/favorites", { credentials: "same-origin" });
            if (!r.ok) return new Map();
            const data = await r.json();
            return new Map((data.items || []).map((it) => [it.channel_id, it]));
        } catch (e) { return new Map(); }
    }
    async function loadRecents() {
        try {
            const r = await fetch("/api/iptv/recents", { credentials: "same-origin" });
            if (!r.ok) return [];
            const data = await r.json();
            return data.items || [];
        } catch (e) { return []; }
    }
    async function addFavorite(channel_id) {
        await fetch("/api/iptv/favorites", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ channel_id }),
        });
    }
    async function removeFavorite(channel_id) {
        await fetch(`/api/iptv/favorites/${channel_id}`, {
            method: "DELETE", credentials: "same-origin",
        });
    }
    async function pushRecent(channel_id) {
        await fetch(`/api/iptv/recents/${channel_id}`, {
            method: "POST", credentials: "same-origin",
        });
    }

    /* ── Rendering ──────────────────────────────────────────────────────── */
    function renderGrid(items) {
        const grid = $("#iptv-grid");
        grid.innerHTML = "";
        if (!items.length) {
            if (!state.all.length) return;
            grid.innerHTML = `<div class="empty-state" style="padding:48px"><p class="text-muted">${LANG === "en" ? "No channels match the filters." : "Ningún canal coincide."}</p></div>`;
            return;
        }
        const frag = document.createDocumentFragment();
        items.forEach((ch) => frag.appendChild(renderCard(ch)));
        grid.appendChild(frag);
    }

    function renderCard(ch) {
        const card = document.createElement("div");
        card.className = "iptv-card";
        card.tabIndex = 0;
        card.setAttribute("role", "button");
        card.setAttribute("aria-label", `${LANG === "en" ? "Play" : "Reproducir"} ${ch.name || "?"}`);
        card.dataset.id = ch.id || ch.channel_id;
        const cid = ch.id || ch.channel_id;
        if (state.active && state.active.id === cid) card.classList.add("active");

        const fav = state.favorites.has(cid);
        card.innerHTML = `
            <button class="iptv-card__fav ${fav ? "active" : ""}" title="Favorite" aria-label="favorite">
                <svg viewBox="0 0 24 24" fill="${fav ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z"/></svg>
            </button>
            <span class="iptv-card__health" data-id="${cid}"></span>
            <div class="iptv-card__logo">
                ${ch.logo_url || ch.logo
                    ? `<img src="${escapeAttr(ch.logo_url || ch.logo)}" alt="" loading="lazy">`
                    : `<div class="placeholder">${escapeHTML((ch.name || "?").slice(0, 3).toUpperCase())}</div>`}
            </div>
            <div class="iptv-card__name">${escapeHTML(ch.name || "?")}</div>
            ${ch.group ? `<div class="iptv-card__group">${escapeHTML(ch.group)}</div>` : ""}
        `;
        card.querySelector("img")?.addEventListener("error", (event) => {
            const placeholder = document.createElement("div");
            placeholder.className = "placeholder";
            placeholder.textContent = (ch.name || "?").slice(0, 3).toUpperCase();
            event.target.replaceWith(placeholder);
        }, { once: true });
        card.addEventListener("click", (e) => {
            if (e.target.closest(".iptv-card__fav")) return;
            playChannel(ch);
        });
        card.addEventListener("keydown", (event) => {
            if (event.target === card && ["Enter", " "].includes(event.key)) {
                event.preventDefault();
                playChannel(ch);
            }
        });
        card.querySelector(".iptv-card__fav").addEventListener("click", async (e) => {
            e.stopPropagation();
            await toggleFavorite(cid, ch);
            await refreshFavorites();
            render();
        });
        return card;
    }

    /* ── Tab → items mapping ────────────────────────────────────────────── */
    function applyFilters(list) {
        const f = state.filters;
        const q = (f.search || "").toLowerCase().trim();
        return list.filter((c) => {
            if (f.source && !(c.sources || [c.source_name || c.source]).includes(f.source)) return false;
            if (f.group && c.group !== f.group) return false;
            if (f.country && !((c.countries || [c.country || ""]).map((item) => item.toUpperCase()).includes(f.country))) return false;
            if (f.language && !((c.languages || []).includes(f.language))) return false;
            if (q && !((c.name || "").toLowerCase().includes(q))) return false;
            return true;
        });
    }

    function currentItems() {
        switch (state.tab) {
            case "favorites": {
                const list = Array.from(state.favorites.values()).map((f) => ({
                    id: f.channel_id,
                    name: f.name,
                    logo_url: f.logo,
                    group: f.group,
                    country: f.country,
                    languages: f.language ? [f.language] : [],
                    stream_url: f.stream_url,
                }));
                return applyFilters(list);
            }
            case "recents": {
                const list = state.recents.map((r) => ({
                    id: r.channel_id,
                    name: r.name,
                    logo_url: r.logo,
                    group: r.group,
                    stream_url: r.stream_url,
                }));
                return applyFilters(list);
            }
            case "featured": {
                // Featured = a curated mix: 24 from each major group + favorites
                const byGroup = new Map();
                for (const c of state.all) {
                    const g = c.group || "Misc";
                    if (!byGroup.has(g)) byGroup.set(g, []);
                    if (byGroup.get(g).length < 24) byGroup.get(g).push(c);
                }
                const mixed = [];
                byGroup.forEach((arr) => mixed.push(...arr));
                return applyFilters(mixed).slice(0, 200);
            }
            default: {
                return applyFilters(state.all);
            }
        }
    }

    function render() {
        $("#iptv-pane-empty").hidden = state.all.length > 0;
        $("#iptv-pane-loading").hidden = true;
        $("#iptv-fav-count").textContent = state.favorites.size || "";
        $("#iptv-filters").style.display = ["featured", "browse"].includes(state.tab) ? "flex" : "none";
        const items = currentItems();
        const browseAll = state.tab === "browse";
        renderGrid(browseAll ? items.slice(0, state.visibleLimit) : items);
        const more = $("#iptv-load-more");
        if (more) {
            more.hidden = !browseAll || state.visibleLimit >= items.length;
            more.textContent = LANG === "en"
                ? `Show more · ${Math.min(state.visibleLimit, items.length)} of ${items.length}`
                : `Mostrar más · ${Math.min(state.visibleLimit, items.length)} de ${items.length}`;
        }
        updateNow();
    }

    /* ── Now-playing chrome ─────────────────────────────────────────────── */
    function updateNow() {
        const ch = state.active;
        const logo = $("#iptv-now-logo");
        const name = $("#iptv-now-name");
        const group = $("#iptv-now-group");
        const tags = $("#iptv-now-tags");
        const fav = $("#iptv-fav-toggle");
        const favLabel = $("#iptv-fav-label");

        if (!ch) {
            name.textContent = LANG === "en" ? "Not playing" : "Sin reproducir";
            group.textContent = "";
            tags.innerHTML = "";
            fav.disabled = true;
            $("#iptv-health-check").disabled = true;
            $("#iptv-pip").disabled = true;
            return;
        }
        name.textContent = ch.name || "?";
        group.textContent = ch.group || "";
        logo.innerHTML = "";
        if (ch.logo_url) {
            const img = document.createElement("img");
            img.src = ch.logo_url;
            img.alt = "";
            logo.appendChild(img);
        } else {
            logo.innerHTML = `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="14" width="52" height="36" rx="4"/><path d="M22 50v6h20v-6"/></svg>`;
        }
        const ts = [];
        if (ch.country) ts.push(`<span class="tag tag-blue">${escapeHTML(ch.country)}</span>`);
        if (ch.languages && ch.languages.length) ts.push(`<span class="tag">${escapeHTML(ch.languages.join(", "))}</span>`);
        if (ch.group) ts.push(`<span class="tag">${escapeHTML(ch.group)}</span>`);
        tags.innerHTML = ts.join("");
        fav.disabled = false;
        $("#iptv-health-check").disabled = false;
        $("#iptv-pip").disabled = !document.pictureInPictureEnabled;
        const isFav = state.favorites.has(ch.id);
        fav.classList.toggle("btn-primary", isFav);
        favLabel.textContent = isFav
            ? (LANG === "en" ? "Saved" : "Guardado")
            : (LANG === "en" ? "Save" : "Guardar");
    }

    /* ── Playback ───────────────────────────────────────────────────────── */
    function stopPlayback() {
        const previous = state.hls;
        state.hls = null;
        if (previous) previous.destroy();
        const video = $("#iptv-player");
        video.pause();
        video.removeAttribute("src");
        video.load();
    }

    async function playChannel(ch) {
        state.active = ch;
        $("#iptv-player-empty").style.display = "none";
        $("#iptv-player-error").hidden = true;
        const video = $("#iptv-player");
        stopPlayback();
        const url = ch.stream_url;
        if (!url) return showPlayerError(LANG === "en" ? "No stream URL." : "El canal no tiene URL.");
        const proxyUrl = `/api/local/proxy-stream?${new URLSearchParams({ url })}`;
        try {
            if (/\.m3u8?(?:[?#]|$)/i.test(url) && window.Hls && window.Hls.isSupported()) {
                const hls = new Hls({
                    enableWorker: true, lowLatencyMode: false, capLevelToPlayerSize: true,
                    maxBufferLength: 30, maxBufferSize: 24 * 1024 * 1024, backBufferLength: 30,
                });
                state.hls = hls;
                let networkRetries = 0, mediaRetries = 0;
                hls.on(Hls.Events.MANIFEST_PARSED, () => {
                    if (state.hls === hls) video.play().catch(() => {});
                });
                hls.on(Hls.Events.ERROR, (_, data) => {
                    if (state.hls !== hls || !data.fatal) return;
                    if (data.type === Hls.ErrorTypes.NETWORK_ERROR && networkRetries++ < 1) {
                        hls.startLoad();
                    } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR && mediaRetries++ < 1) {
                        hls.recoverMediaError();
                    } else {
                        stopPlayback();
                        showPlayerError(LANG === "en"
                            ? `Channel unavailable or incompatible (${data.details}). Try another source.`
                            : `Canal no disponible o incompatible (${data.details}). Prueba otra fuente.`);
                    }
                });
                hls.loadSource(proxyUrl);
                hls.attachMedia(video);
            } else {
                video.src = proxyUrl;
                await video.play().catch(() => {});
            }
            try { localStorage.setItem(LAST_CH_KEY, ch.id); } catch (e) {}
            pushRecent(ch.id).catch(() => {});
            loadRecents().then((r) => { state.recents = r; if (state.tab === "recents") render(); });
        } catch (e) {
            showPlayerError(e.message);
        }
        render();
    }

    function showPlayerError(msg) {
        $("#iptv-player-error").hidden = false;
        $("#iptv-player-error-msg").textContent = msg || "";
    }

    /* ── Actions ────────────────────────────────────────────────────────── */
    async function toggleFavorite(cid, channelMeta) {
        if (state.favorites.has(cid)) {
            await removeFavorite(cid);
            state.favorites.delete(cid);
        } else {
            // Need the channel to be in cache for backend to look up details.
            const ch = state.all.find((c) => c.id === cid) || channelMeta;
            if (!ch) return;
            await addFavorite(cid);
            state.favorites.set(cid, {
                channel_id: cid,
                name: ch.name,
                logo: ch.logo_url,
                group: ch.group,
                language: (ch.languages || [])[0],
                country: ch.country,
                stream_url: ch.stream_url,
            });
        }
    }
    async function refreshFavorites() { state.favorites = await loadFavorites(); }

    async function runHealthCheck() {
        if (!state.active) return;
        try {
            const r = await fetch(`/api/iptv/channel/${state.active.id}/health`);
            const data = await r.json();
            const dot = document.querySelector(`.iptv-card__health[data-id="${state.active.id}"]`);
            if (dot) dot.classList.toggle("alive", data.working === true), dot.classList.toggle("dead", data.working === false);
            toast(data.working ? (LANG === "en" ? "Stream is alive" : "Canal disponible") : (LANG === "en" ? "Stream is down" : "Canal caído"));
        } catch (e) { toast("Health check failed", true); }
    }

    async function enterPiP() {
        const video = $("#iptv-player");
        try { if (document.pictureInPictureElement) await document.exitPictureInPicture(); else await video.requestPictureInPicture(); } catch (e) {}
    }

    /* ── Filters wiring ─────────────────────────────────────────────────── */
    function buildSelectors(groups, countries, languages) {
        const src = $("#iptv-source-sel");
        src.length = 1;
        for (const s of state.sources) {
            const o = document.createElement("option");
            o.value = s.key; o.textContent = s.name || s.key;
            src.appendChild(o);
        }
        src.value = state.filters.source;
        const importSource = $("#iptv-import-source");
        if (importSource) {
            const selected = importSource.value;
            importSource.length = 1;
            for (const source of state.sources) {
                const option = document.createElement("option");
                option.value = source.key;
                option.textContent = source.name || source.key;
                importSource.appendChild(option);
            }
            importSource.value = selected;
        }
        const gs = $("#iptv-group-sel");
        gs.length = 1;
        Object.entries(groups)
            .sort((a, b) => b[1] - a[1])
            .forEach(([g, n]) => {
                const o = document.createElement("option");
                o.value = g; o.textContent = `${g} (${n})`;
                gs.appendChild(o);
            });
        gs.value = state.filters.group;
        const cs = $("#iptv-country-sel");
        cs.length = 1;
        countries.forEach((c) => {
            const o = document.createElement("option");
            o.value = c.code; o.textContent = `${c.code} (${c.count})`;
            cs.appendChild(o);
        });
        cs.value = state.filters.country;
        const ls = $("#iptv-lang-sel");
        ls.length = 1;
        languages.slice(0, 50).forEach((l) => {
            const o = document.createElement("option");
            o.value = l.code; o.textContent = `${l.code} (${l.count})`;
            ls.appendChild(o);
        });
        ls.value = state.filters.language;
    }

    /* ── Imports ────────────────────────────────────────────────────────── */
    function openImport() {
        window.SVShell.open($("#iptv-import-modal"), {
            trigger: $("#iptv-open-import"), initialFocus: $("#iptv-import-source"),
        });
    }
    function closeImport() { window.SVShell.close($("#iptv-import-modal")); }
    async function doImport() {
        const source = $("#iptv-import-source")?.value;
        const url = $("#iptv-import-url").value.trim();
        const name = $("#iptv-import-name").value.trim() || "custom";
        if (!url && !source) return;
        const button = $("#iptv-import-go");
        button.disabled = true;
        try {
            await api(source ? `/api/iptv/import/${encodeURIComponent(source)}` : "/api/iptv/import", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                ...(source ? {} : { body: JSON.stringify({ source: url, source_name: name }) }),
            });
            closeImport();
            await fullReload();
            toast(LANG === "en" ? "Imported" : "Importado");
        } catch (e) { toast(LANG === "en" ? "Import failed" : "No se pudo importar", true); }
        finally { button.disabled = false; }
    }

    /* ── Boot ───────────────────────────────────────────────────────────── */
    async function fullReload() {
        if (state.reloading) return;
        state.reloading = true;
        try {
            $("#iptv-pane-loading").hidden = false;
            const [, bootstrapResult] = await Promise.all([loadSources(), bootstrap()]);
            const [all, fav, recents, groups, countries, languages] = await Promise.all([
                loadChannels({}), loadFavorites(), loadRecents(),
                loadGroups(), loadCountries(), loadLangs(),
            ]);
            state.all = all;
            state.favorites = fav;
            state.recents = recents;
            buildSelectors(groups, countries, languages);
            if (!all.length && bootstrapResult.errors?.length) {
                const failed = bootstrapResult.errors.map((item) => item.source).join(", ");
                $("#iptv-empty-message").textContent = LANG === "en"
                    ? `Could not load public channel lists (${failed}). Check server network access and retry.`
                    : `No se pudieron cargar las listas públicas (${failed}). Comprueba la red del servidor y reintenta.`;
            }
            render();
        } catch (error) {
            toast(LANG === "en" ? `Could not reload channels: ${error.message}` : `No se pudieron recargar los canales: ${error.message}`, true);
        } finally {
            state.reloading = false;
            $("#iptv-pane-loading").hidden = true;
        }
    }

    function wire() {
        $$("#iptv-tabs [data-tab]").forEach((a) =>
            a.addEventListener("click", (e) => {
                e.preventDefault();
                $$("#iptv-tabs [data-tab]").forEach((x) => x.classList.remove("active"));
                a.classList.add("active");
                state.tab = a.dataset.tab;
                state.visibleLimit = 240;
                render();
            }));

        let searchTimer = null;
        $("#iptv-search").addEventListener("input", (e) => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => { state.filters.search = e.target.value; render(); }, 180);
        });
        $("#iptv-source-sel").addEventListener("change", (e) => { state.filters.source = e.target.value; render(); });
        $("#iptv-group-sel").addEventListener("change", (e) => { state.filters.group = e.target.value; render(); });
        $("#iptv-country-sel").addEventListener("change", (e) => { state.filters.country = e.target.value; render(); });
        $("#iptv-lang-sel").addEventListener("change", (e) => { state.filters.language = e.target.value; render(); });
        $("#iptv-reload").addEventListener("click", () => fullReload());
        $("#iptv-bootstrap-btn").addEventListener("click", () => fullReload());

        $("#iptv-fav-toggle").addEventListener("click", async () => {
            if (!state.active) return;
            await toggleFavorite(state.active.id, state.active);
            await refreshFavorites();
            render();
        });
        $("#iptv-health-check").addEventListener("click", runHealthCheck);
        $("#iptv-pip").addEventListener("click", enterPiP);
        $("#iptv-retry").addEventListener("click", () => state.active && playChannel(state.active));

        $("#iptv-load-more")?.addEventListener("click", () => {
            state.visibleLimit += 240;
            render();
        });
        $("#iptv-open-import")?.addEventListener("click", openImport);
        $("#iptv-import-close")?.addEventListener("click", closeImport);
        $("#iptv-import-cancel")?.addEventListener("click", closeImport);
        $("#iptv-import-go")?.addEventListener("click", doImport);
        $("#iptv-import-source")?.addEventListener("change", (event) => {
            $("#iptv-import-url").disabled = !!event.target.value;
            $("#iptv-import-name").disabled = !!event.target.value;
        });

        $("#iptv-player").addEventListener("error", () => {
            if (state.active && !state.hls && $("#iptv-player").error) {
                showPlayerError(LANG === "en" ? "Cannot play this channel. Try another source." : "No se puede reproducir este canal. Prueba otra fuente.");
            }
        });
        $("#iptv-player").addEventListener("playing", () => { $("#iptv-player-error").hidden = true; });
        window.addEventListener("pagehide", stopPlayback);
    }

    /* ── Utils ──────────────────────────────────────────────────────────── */
    function escapeHTML(s) {
        return String(s).replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }
    function escapeAttr(s) { return escapeHTML(s).replace(/`/g, "&#96;"); }

    function toast(msg, error) {
        if (window.showToast) { window.showToast("", msg, error ? "error" : "ok"); return; }
        let host = $("#toast-host");
        if (!host) {
            host = document.createElement("div");
            host.id = "toast-host";
            host.className = "toast-container";
            document.body.appendChild(host);
        }
        const t = document.createElement("div");
        t.className = "toast " + (error ? "toast--error" : "toast--success");
        t.textContent = msg;
        host.appendChild(t);
        requestAnimationFrame(() => t.classList.add("toast--visible"));
        setTimeout(() => { t.classList.remove("toast--visible"); setTimeout(() => t.remove(), 400); }, 3000);
    }

    async function init() {
        wire();
        await fullReload();
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
