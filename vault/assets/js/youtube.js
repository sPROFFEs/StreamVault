/* StreamVault YouTube — Streambert-style YouTube UI with real watch
   history, region-aware Trending, quality picker, native ↔ iframe player
   toggle, channel sidebar with avatars, and download with the same
   quality the user picked. */
(function () {
    "use strict";

    const $ = (s, r) => (r || document).querySelector(s);
    const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    const LANG = window.APP_LANG === "es" ? "es" : "en";

    const STATE_KEY = "sv_yt_state";
    let playerRequest = 0;
    let browseRequest = 0;
    let retryBrowse = loadTab;

    const state = {
        tab: "trending",
        region: "ES",
        query: "",
        videos: [],
        videoIndex: new Map(),     // video_id -> video object
        favorites: new Set(),
        subscriptions: new Map(),  // channel_id -> {name, url}
        history: new Map(),        // video_id -> {watch_progress, duration_seconds, watched_at}
        current: null,
        playerMode: "iframe",      // "iframe" | "native"
        currentQuality: "720",
    };

    /* ── Networking ─────────────────────────────────────────────────────── */
    async function fetchJSON(url, init) {
        const r = await fetch(url, { credentials: "same-origin", ...(init || {}) });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    }
    function fetchSafe(url, init) {
        return fetchJSON(url, init).catch(() => null);
    }

    async function refreshLocalState() {
        const [favs, subs, hist] = await Promise.all([
            fetchSafe("/api/youtube/favorites"),
            fetchSafe("/api/youtube/subscriptions/local"),
            fetchSafe("/api/youtube/history"),
        ]);
        state.favorites = new Set(((favs && favs.items) || []).map((f) => f.video_id));
        state.subscriptions = new Map(
            ((subs && subs.items) || []).map((s) => [s.channel_id, s])
        );
        state.history = new Map(
            ((hist && hist.items) || []).map((h) => [h.video_id, h])
        );
        renderSubsSidebar();
    }

    function beginBrowse(retry) {
        retryBrowse = retry;
        $("#yt-loading").hidden = false;
        $("#yt-empty").hidden = true;
        $("#yt-error").hidden = true;
        $("#yt-grid").innerHTML = "";
        return ++browseRequest;
    }

    async function loadTab() {
        const request = beginBrowse(loadTab);
        try {
            switch (state.tab) {
                case "trending": {
                    const data = await fetchJSON(`/api/youtube/trending?region=${encodeURIComponent(state.region)}`);
                    if (request !== browseRequest) return;
                    setVideos(data || []);
                    break;
                }
                case "subscriptions": {
                    const data = await fetchJSON("/api/youtube/subscriptions/local/feed");
                    if (request !== browseRequest) return;
                    setVideos((data && data.items) || []);
                    if (!state.videos.length) showEmpty(LANG === "en" ? "Follow some channels first." : "Sigue algunos canales primero.");
                    break;
                }
                case "favorites": {
                    const data = await fetchJSON("/api/youtube/favorites");
                    if (request !== browseRequest) return;
                    const items = ((data && data.items) || []).map((f) => ({
                        video_id: f.video_id,
                        title: f.title,
                        author: f.author,
                        author_id: f.author_id,
                        thumbnail: f.thumbnail,
                        duration_text: f.duration_text,
                    }));
                    setVideos(items);
                    if (!items.length) showEmpty(LANG === "en" ? "No saved videos yet." : "Aún no has guardado nada.");
                    break;
                }
                case "history": {
                    const data = await fetchJSON("/api/youtube/history");
                    if (request !== browseRequest) return;
                    const items = ((data && data.items) || []).map((h) => ({
                        video_id: h.video_id,
                        title: h.title,
                        author: h.author,
                        author_id: h.author_id,
                        thumbnail: h.thumbnail,
                        duration_text: h.duration_text,
                        watch_progress: h.watch_progress,
                        duration_seconds: h.duration_seconds,
                    }));
                    setVideos(items);
                    if (!items.length) showEmpty(LANG === "en" ? "Nothing watched yet." : "Aún no has visto nada.");
                    break;
                }
                case "music": {
                    const data = await fetchJSON(`/api/youtube/search?q=${encodeURIComponent("music " + state.region + " latest")}&page=1`);
                    if (request !== browseRequest) return;
                    setVideos(data || []);
                    break;
                }
            }
            if (!state.videos.length && $("#yt-empty").hidden) showEmpty(LANG === "en" ? "No videos here yet." : "Aún no hay videos aquí.");
        } catch (e) {
            if (request === browseRequest) $("#yt-error").hidden = false;
        } finally {
            if (request === browseRequest) $("#yt-loading").hidden = true;
        }
    }

    function setVideos(arr) {
        state.videos = arr || [];
        state.videoIndex = new Map(state.videos.map((v) => [v.video_id, v]));
        renderGrid();
    }

    function showEmpty(msg) {
        $("#yt-empty").hidden = false;
        $("#yt-empty-msg").textContent = msg;
    }

    async function loadChannelVideos(channelId) {
        const request = beginBrowse(() => loadChannelVideos(channelId));
        clearTimeout(searchTimer);
        state.query = "";
        $("#yt-search").value = "";
        $$("#yt-tabs [data-tab]").forEach(tab => tab.classList.remove("active"));
        try {
            const data = await fetchJSON(`/api/youtube/channel/${encodeURIComponent(channelId)}/videos`);
            if (request !== browseRequest) return;
            setVideos(data || []);
            if (!state.videos.length) showEmpty(LANG === "en" ? "No videos here yet." : "Aún no hay videos aquí.");
        } catch (error) {
            if (request === browseRequest) $("#yt-error").hidden = false;
        } finally {
            if (request === browseRequest) $("#yt-loading").hidden = true;
        }
    }

    /* ── Search ─────────────────────────────────────────────────────────── */
    let searchTimer = null;
    function attachSearch() {
        const input = $("#yt-search");
        input.addEventListener("input", () => {
            ++browseRequest;
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => doSearch(input.value.trim()), 320);
        });
    }
    async function doSearch(q) {
        state.query = q;
        if (!q) return loadTab();
        $$("#yt-tabs [data-tab]").forEach((a) => a.classList.remove("active"));
        const request = beginBrowse(() => doSearch(q));
        try {
            const data = await fetchJSON(`/api/youtube/search?q=${encodeURIComponent(q)}&page=1`);
            if (request !== browseRequest) return;
            setVideos(data || []);
            if (!state.videos.length) showEmpty(LANG === "en" ? "No results." : "Sin resultados.");
        } catch (error) {
            if (request === browseRequest) $("#yt-error").hidden = false;
        } finally {
            if (request === browseRequest) $("#yt-loading").hidden = true;
        }
    }

    /* ── Rendering ──────────────────────────────────────────────────────── */
    function renderGrid() {
        const grid = $("#yt-grid");
        grid.innerHTML = "";
        const frag = document.createDocumentFragment();
        state.videos.forEach((v) => frag.appendChild(renderCard(v)));
        grid.appendChild(frag);
    }

    function renderCard(v) {
        const card = document.createElement("a");
        card.className = "yt-card";
        card.href = "#";
        card.dataset.id = v.video_id;
        const hist = state.history.get(v.video_id);
        const pct = hist && hist.duration_seconds
            ? Math.min(100, (hist.watch_progress / hist.duration_seconds) * 100)
            : 0;
        card.innerHTML = `
            <div class="yt-card__thumb">
                ${v.thumbnail ? `<img src="${escapeAttr(v.thumbnail)}" alt="" loading="lazy">` : ""}
                ${v.duration_text ? `<span class="yt-card__duration">${escapeHTML(v.duration_text)}</span>` : ""}
                ${pct > 0 ? `<div class="yt-card__progress"><div class="yt-card__progress-fill" style="width:${pct}%"></div></div>` : ""}
            </div>
            <div class="yt-card__title">${escapeHTML(v.title || "")}</div>
            <div class="yt-card__meta">
                <span class="yt-card__meta-author">${escapeHTML(v.author || "")}</span>
                ${v.views_text ? `<span>${escapeHTML(v.views_text)}</span>` : ""}
                ${v.published ? `<span>${escapeHTML(formatPublished(v.published))}</span>` : ""}
            </div>
        `;
        card.addEventListener("click", (e) => {
            e.preventDefault();
            playVideo(v.video_id);
        });
        card.addEventListener("contextmenu", (e) => {
            e.preventDefault();
            showContextMenu(e.clientX, e.clientY, [
                state.favorites.has(v.video_id)
                    ? { label: LANG === "en" ? "Remove from saved" : "Quitar de guardados", danger: true, onClick: () => toggleFavorite(v) }
                    : { label: LANG === "en" ? "Save video" : "Guardar video", onClick: () => toggleFavorite(v) },
                state.tab === "history"
                    ? { label: LANG === "en" ? "Remove from history" : "Quitar del historial", danger: true,
                        onClick: async () => { await fetch(`/api/youtube/history/${v.video_id}`, { method: "DELETE", credentials: "same-origin" }); await refreshLocalState(); loadTab(); } }
                    : null,
            ].filter(Boolean));
        });
        return card;
    }

    function renderSubsSidebar() {
        const host = $("#yt-subs");
        host.innerHTML = "";
        if (!state.subscriptions.size) {
            host.innerHTML = `<div class="text-muted text-sm" style="padding:8px 10px">${LANG === "en" ? "Follow channels from the modal." : "Sigue canales desde el modal."}</div>`;
            return;
        }
        state.subscriptions.forEach((s) => {
            const row = document.createElement("a");
            row.className = "yt-sub-row";
            row.href = "#";
            row.innerHTML = `
                <span class="yt-sub-row__avatar">${escapeHTML((s.channel_name || "?").slice(0, 1).toUpperCase())}</span>
                <span class="yt-sub-row__name">${escapeHTML(s.channel_name || s.channel_id)}</span>
            `;
            row.addEventListener("click", async (e) => {
                e.preventDefault();
                await loadChannelVideos(s.channel_id);
            });
            row.addEventListener("contextmenu", (e) => {
                e.preventDefault();
                showContextMenu(e.clientX, e.clientY, [{
                    label: LANG === "en" ? "Unfollow" : "Dejar de seguir",
                    danger: true,
                    onClick: async () => {
                        await fetch(`/api/youtube/subscriptions/local/${encodeURIComponent(s.channel_id)}`, {
                            method: "DELETE", credentials: "same-origin",
                        });
                        await refreshLocalState();
                    },
                }]);
            });
            host.appendChild(row);
        });
    }

    /* ── Player ─────────────────────────────────────────────────────────── */
    async function playVideo(videoId) {
        const v = state.videoIndex.get(videoId) || { video_id: videoId, title: "?", author: "" };
        state.current = v;
        const modal = $("#yt-modal");
        modal.style.display = "flex";
        $("#yt-video-title").textContent = v.title || "?";
        $("#yt-video-author").textContent = v.author || "";

        // Default to the iframe; the user can opt-in to native via the toggle
        switchToIframe(videoId);
        const request = playerRequest;
        window.SVShell.open(modal, {initialFocus: $("#yt-modal-close"), onClose: releasePlayer});

        // Update favorite/sub buttons
        const isFav = state.favorites.has(videoId);
        const favBtn = $("#yt-fav"); const favLabel = $("#yt-fav-label");
        favBtn.disabled = false;
        favBtn.classList.toggle("btn-primary", isFav);
        favLabel.textContent = isFav ? (LANG === "en" ? "Saved" : "Guardado") : (LANG === "en" ? "Save" : "Guardar");

        const channelId = v.author_id || "";
        const isSub = channelId && state.subscriptions.has(channelId);
        const subBtn = $("#yt-sub"); const subLabel = $("#yt-sub-label");
        subBtn.disabled = !channelId;
        subBtn.classList.toggle("btn-primary", isSub);
        subLabel.textContent = isSub
            ? (LANG === "en" ? "Following" : "Siguiendo")
            : (LANG === "en" ? "Follow channel" : "Seguir canal");

        $("#yt-download").disabled = false;

        // Load qualities (used both for native player + download choice)
        $("#yt-quality").style.display = "none";
        try {
            const q = await fetchSafe(`/api/youtube/video/${videoId}/qualities`);
            if (request !== playerRequest) return;
            if (q && q.length) {
                const sel = $("#yt-quality");
                sel.innerHTML = "";
                q.forEach((h) => {
                    const o = document.createElement("option");
                    o.value = String(h); o.textContent = `${h}p`;
                    sel.appendChild(o);
                });
                sel.value = state.currentQuality;
                sel.style.display = "";
            }
        } catch (e) {}

        // Push a history row immediately when playback starts
        if (state.subscriptions !== null) {
            await fetch("/api/youtube/history", {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    video_id: videoId,
                    title: v.title || "?",
                    author: v.author || null,
                    author_id: v.author_id || null,
                    thumbnail: v.thumbnail || null,
                    duration_text: v.duration_text || null,
                    watch_progress: 0,
                    duration_seconds: 0,
                }),
            }).catch(() => null);
            // refresh in-memory history so the progress bar shows after reload
            await refreshLocalState();
        }
    }

    function switchToIframe(videoId) {
        ++playerRequest;
        clearInterval(window.__ytProgress);
        state.playerMode = "iframe";
        $("#yt-player-iframe-wrap").style.display = "";
        $("#yt-player-native").style.display = "none";
        $("#yt-mode-label").textContent = LANG === "en" ? "Native" : "Nativo";
        $("#yt-player").src = `https://www.youtube-nocookie.com/embed/${videoId}?autoplay=1&modestbranding=1`;
        // Stop native if it was playing
        const nv = $("#yt-player-native");
        try { nv.pause(); nv.src = ""; } catch (e) {}
    }

    async function switchToNative(videoId) {
        const request = ++playerRequest;
        const q = $("#yt-quality").value || state.currentQuality;
        const data = await fetchSafe(`/api/youtube/video/${videoId}/stream?quality=${encodeURIComponent(q)}`);
        if (request !== playerRequest || !state.current || state.current.video_id !== videoId) return;
        if (!data || !data.url) {
            toast(LANG === "en" ? "Native stream unavailable; staying on iframe." : "Stream nativo no disponible.", true);
            return;
        }
        state.playerMode = "native";
        $("#yt-player").src = "";
        $("#yt-player-iframe-wrap").style.display = "none";
        const nv = $("#yt-player-native");
        nv.style.display = "";
        nv.src = data.url;
        nv.play().catch(() => null);
        $("#yt-mode-label").textContent = LANG === "en" ? "Iframe" : "Iframe";

        // Save progress every 5s
        clearInterval(window.__ytProgress);
        window.__ytProgress = setInterval(() => {
            if (!nv.duration) return;
            fetch("/api/youtube/history", {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    video_id: videoId,
                    title: state.current.title || "?",
                    author: state.current.author || null,
                    author_id: state.current.author_id || null,
                    thumbnail: state.current.thumbnail || null,
                    duration_text: state.current.duration_text || null,
                    watch_progress: nv.currentTime,
                    duration_seconds: nv.duration,
                    completed: (nv.currentTime / nv.duration) >= 0.95,
                }),
            }).catch(() => null);
        }, 5000);
    }

    function closePlayer() {
        window.SVShell.close($("#yt-modal"));
    }

    function releasePlayer() {
        ++playerRequest;
        state.current = null;
        $("#yt-modal").style.display = "none";
        $("#yt-player").removeAttribute("src");
        const nv = $("#yt-player-native");
        try { nv.pause(); nv.removeAttribute("src"); nv.load(); } catch (e) {}
        clearInterval(window.__ytProgress);
    }

    /* ── Actions ────────────────────────────────────────────────────────── */
    async function toggleFavorite(v) {
        const vid = v.video_id;
        if (state.favorites.has(vid)) {
            await fetch(`/api/youtube/favorites/${vid}`, { method: "DELETE", credentials: "same-origin" });
            state.favorites.delete(vid);
        } else {
            await fetch("/api/youtube/favorites", {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    video_id: vid,
                    title: v.title || "?",
                    author: v.author || null,
                    author_id: v.author_id || null,
                    thumbnail: v.thumbnail || null,
                    duration_text: v.duration_text || null,
                }),
            });
            state.favorites.add(vid);
        }
        if (state.current && state.current.video_id === vid) {
            const isFav = state.favorites.has(vid);
            $("#yt-fav").classList.toggle("btn-primary", isFav);
            $("#yt-fav-label").textContent = isFav
                ? (LANG === "en" ? "Saved" : "Guardado")
                : (LANG === "en" ? "Save" : "Guardar");
        }
        if (state.tab === "favorites") loadTab();
    }

    async function toggleSubscription() {
        const v = state.current;
        if (!v || !v.author_id) return;
        const cid = v.author_id;
        if (state.subscriptions.has(cid)) {
            await fetch(`/api/youtube/subscriptions/local/${encodeURIComponent(cid)}`, {
                method: "DELETE", credentials: "same-origin",
            });
            state.subscriptions.delete(cid);
        } else {
            await fetch("/api/youtube/subscriptions/local", {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    channel_id: cid,
                    channel_name: v.author || cid,
                    channel_url: v.channel_url || null,
                }),
            });
            state.subscriptions.set(cid, { channel_id: cid, channel_name: v.author || cid, channel_url: v.channel_url });
        }
        const isSub = state.subscriptions.has(cid);
        $("#yt-sub").classList.toggle("btn-primary", isSub);
        $("#yt-sub-label").textContent = isSub
            ? (LANG === "en" ? "Following" : "Siguiendo")
            : (LANG === "en" ? "Follow channel" : "Seguir canal");
        renderSubsSidebar();
    }

    async function downloadCurrent() {
        const v = state.current;
        if (!v) return;
        const q = $("#yt-quality").value || "best";
        try {
            await fetch("/api/downloads/start", {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    url: `https://www.youtube.com/watch?v=${v.video_id}`,
                    title: v.title || v.video_id,
                    quality: q === "best" ? "best" : `${q}p`,
                }),
            });
            toast(LANG === "en" ? "Download queued" : "Descarga en cola");
        } catch (e) {
            toast(LANG === "en" ? "Download failed" : "Error al descargar", true);
        }
    }

    /* ── Misc UI ────────────────────────────────────────────────────────── */
    function showContextMenu(x, y, items) {
        $$(".context-menu").forEach((m) => m.remove());
        const menu = document.createElement("div");
        menu.className = "context-menu";
        menu.style.left = `${x}px`; menu.style.top = `${y}px`;
        items.forEach((it) => {
            const row = document.createElement("div");
            row.className = "context-menu__item" + (it.danger ? " context-menu__item--danger" : "");
            row.textContent = it.label;
            row.addEventListener("click", () => { menu.remove(); it.onClick && it.onClick(); });
            menu.appendChild(row);
        });
        document.body.appendChild(menu);
        setTimeout(() => document.addEventListener("click", (ev) => {
            if (!menu.contains(ev.target)) menu.remove();
        }, { once: true }), 0);
    }

    function toast(msg, error) {
        if (window.showToast) { window.showToast("", msg, error ? "error" : "ok"); return; }
        let host = $("#toast-host");
        if (!host) {
            host = document.createElement("div");
            host.id = "toast-host"; host.className = "toast-container";
            document.body.appendChild(host);
        }
        const t = document.createElement("div");
        t.className = "toast " + (error ? "toast--error" : "toast--success");
        t.textContent = msg;
        host.appendChild(t);
        requestAnimationFrame(() => t.classList.add("toast--visible"));
        setTimeout(() => { t.classList.remove("toast--visible"); setTimeout(() => t.remove(), 400); }, 3000);
    }

    function escapeHTML(s) {
        return String(s).replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }
    function escapeAttr(s) { return escapeHTML(s).replace(/`/g, "&#96;"); }

    function formatPublished(s) {
        // yt-dlp gives YYYYMMDD; render as "YYYY-MM-DD" if so
        if (/^\d{8}$/.test(s)) return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
        return s;
    }

    function wire() {
        $$("#yt-tabs [data-tab]").forEach((a) =>
            a.addEventListener("click", (e) => {
                e.preventDefault();
                $$("#yt-tabs [data-tab]").forEach((x) => x.classList.remove("active"));
                a.classList.add("active");
                state.query = "";
                $("#yt-search").value = "";
                clearTimeout(searchTimer);
                state.tab = a.dataset.tab;
                loadTab();
            }));
        $("#yt-region").addEventListener("change", (e) => {
            state.region = e.target.value;
            if (state.tab === "trending" || state.tab === "music") loadTab();
        });
        attachSearch();
        $("#yt-retry").addEventListener("click", () => retryBrowse());

        $("#yt-modal-close").addEventListener("click", closePlayer);
        $("#yt-modal").addEventListener("click", (e) => { if (e.target.id === "yt-modal") closePlayer(); });
        $("#yt-fav").addEventListener("click", () => state.current && toggleFavorite(state.current));
        $("#yt-sub").addEventListener("click", toggleSubscription);
        $("#yt-download").addEventListener("click", downloadCurrent);
        $("#yt-mode-toggle").addEventListener("click", () => {
            if (!state.current) return;
            if (state.playerMode === "iframe") switchToNative(state.current.video_id);
            else switchToIframe(state.current.video_id);
        });
        $("#yt-quality").addEventListener("change", () => {
            state.currentQuality = $("#yt-quality").value;
            if (state.playerMode === "native" && state.current) switchToNative(state.current.video_id);
        });

        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && $("#yt-modal").style.display === "flex") closePlayer();
        });
        window.addEventListener("pagehide", releasePlayer);
    }

    async function init() {
        wire();
        await refreshLocalState();
        await loadTab();
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
