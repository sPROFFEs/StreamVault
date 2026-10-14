/* StreamVault portal — full-bleed feature hero and horizontal content rails. */
(function () {
    "use strict";

    const $ = (s, r) => (r || document).querySelector(s);
    const spanish = window.APP_LANG === "es";
    const text = (en, es) => spanish ? es : en;
    const visibleRow = key => prefs?.home_visibility?.[key] !== false;
    let trendingFailed = false;

    async function fetchJSON(url) {
        try {
            const r = await fetch(url, { credentials: "same-origin" });
            if (!r.ok) return null;
            return await r.json();
        } catch (e) { return null; }
    }

    let prefs = null;
    let trending = { movies: [], tv: [] };

    function attachRailControls(row) {
        if (!row || row.dataset.hasRailControls) return;
        row.dataset.hasRailControls = "true";
        const wrapper = document.createElement("div");
        wrapper.className = "rail-slider-wrap";
        row.parentNode.insertBefore(wrapper, row);
        wrapper.appendChild(row);

        const prev = document.createElement("button");
        prev.className = "slider-handle slider-handle--prev";
        prev.type = "button";
        prev.setAttribute("aria-label", text("Scroll left", "Desplazar a la izquierda"));
        prev.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M15 18l-6-6 6-6"/></svg>`;
        prev.addEventListener("click", (e) => {
            e.preventDefault();
            row.scrollBy({ left: -Math.max(280, row.clientWidth * 0.75), behavior: "smooth" });
        });

        const next = document.createElement("button");
        next.className = "slider-handle slider-handle--next";
        next.type = "button";
        next.setAttribute("aria-label", text("Scroll right", "Desplazar a la derecha"));
        next.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 18l6-6-6-6"/></svg>`;
        next.addEventListener("click", (e) => {
            e.preventDefault();
            row.scrollBy({ left: Math.max(280, row.clientWidth * 0.75), behavior: "smooth" });
        });

        wrapper.appendChild(prev);
        wrapper.appendChild(next);
    }

    function renderHero(item) {
        const hero = $("#hero");
        if (!hero || !item) return;
        const title = item.title || item.name || "";
        const mt = item.media_type || (item.first_air_date ? "tv" : "movie");
        const date = item.release_date || item.first_air_date || "";
        const year = date ? date.slice(0, 4) : "";
        const score = Number(item.vote_average || 0);
        const rating = Number.isFinite(score) && score > 0 ? score.toFixed(1) : "";

        hero.innerHTML = `
            <div class="hero-bg" aria-hidden="true"></div>
            <div class="hero-gradient"></div>
            <div class="hero-content">
                <div class="hero-type"><svg width="12" height="16" viewBox="0 0 14 18" fill="none" style="vertical-align:-1px;margin-right:6px;"><path d="M0 0h3.5l7 12V0H14v18h-3.5L3.5 6v12H0V0z" fill="var(--red)"/></svg><span>STREAMVAULT ${mt === "tv" ? text("SERIES", "SERIE") : text("FILM", "PELÍCULA")}</span></div>
                <h1 class="hero-title"></h1>
                <div class="hero-meta">
                    ${year ? `<span>${year}</span>` : ""}
                    ${rating ? `<span class="hero-rating"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l3 7h7l-5.5 4 2 7L12 16l-6.5 4 2-7L2 9h7z"/></svg>${rating}</span>` : ""}
                </div>
                <p class="hero-overview"></p>
                <div class="hero-actions">
                    <a class="btn btn-primary" href="/media/${item.id}?type=${mt}">
                        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                        ${text("View details", "Ver detalles")}
                    </a>
                    <a class="btn btn-secondary" href="/media/${item.id}?type=${mt}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:18px;height:18px;"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>${text("More info", "Más información")}</a>
                </div>
            </div>
        `;
        window.SVArtwork.setBackdrop(hero.querySelector(".hero-bg"), item.backdrop_path);
        hero.querySelector(".hero-title").textContent = title;
        hero.querySelector(".hero-overview").textContent = item.overview || "";
    }

    async function loadHistory() {
        const hist = await fetchJSON("/api/library/history");
        if (!hist || !hist.items) return [];
        return hist.items.map((h) => ({
            ...h,
            id: h.tmdb_id,
            progress_pct: h.duration_seconds
                ? Math.max(0, Math.min(100, Math.round((h.progress_seconds / h.duration_seconds) * 100)))
                : 0,
            watched: !!h.completed,
        }));
    }

    async function loadTrending() {
        const [m, t] = await Promise.all([
            fetchJSON("/api/tmdb/trending/movie"),
            fetchJSON("/api/tmdb/trending/tv"),
        ]);
        trendingFailed = m === null && t === null;
        trending.movies = (Array.isArray(m) ? m : (m && (m.results || m.items))) || [];
        trending.tv = (Array.isArray(t) ? t : (t && (t.results || t.items))) || [];
        trending.movies.forEach((x) => (x.media_type = "movie"));
        trending.tv.forEach((x) => (x.media_type = "tv"));
    }

    async function fetchRecommendations(item) {
        if (!item) return [];
        const mt = item.media_type === "tv" ? "tv" : "movie";
        const id = item.tmdb_id || item.id;
        let data = await fetchJSON(`/api/tmdb/${mt}/${id}/recommendations`);
        let results = (Array.isArray(data) ? data : (data && (data.results || data.items))) || [];
        if (!results.length) {
            data = await fetchJSON(`/api/tmdb/${mt}/${id}/similar`);
            results = (Array.isArray(data) ? data : (data && (data.results || data.items))) || [];
        }
        return results.map((entry) => ({ ...entry, media_type: mt }));
    }

    const genreRows = [
        { id: "action", title: text("Action", "Acción"), mediaType: "movie", genre: 28 },
        { id: "comedy", title: text("Comedy", "Comedia"), mediaType: "movie", genre: 35 },
        { id: "crime", title: text("Crime & Mystery", "Crimen y misterio"), mediaType: "tv", genre: 80 },
        { id: "scifi", title: text("Science Fiction", "Ciencia ficción"), mediaType: "movie", genre: 878 },
    ];

    async function loadGenreRows() {
        const host = $("#genre-rows");
        if (!host) return;
        const results = await Promise.all(genreRows.map(async (row) => {
            const data = await fetchJSON(`/api/tmdb/discover/${row.mediaType}?with_genres=${row.genre}&sort_by=popularity.desc`);
            const items = (Array.isArray(data) ? data : (data && (data.results || data.items))) || [];
            return { ...row, items: items.map((item) => ({ ...item, media_type: row.mediaType })) };
        }));
        host.innerHTML = "";
        results.filter((row) => row.items.length).forEach((row) => {
            const section = document.createElement("section");
            section.className = "section";
            section.innerHTML = `<h2 class="section-title">${row.title}</h2><div class="scroll-row" id="genre-${row.id}-row"></div>`;
            const scrollRow = section.querySelector(".scroll-row");
            window.SVMediaCard.renderInto(scrollRow, row.items.slice(0, 18));
            attachRailControls(scrollRow);
            host.appendChild(section);
        });
    }

    function rowsTemplate() {
        return `
            <section class="hero" id="hero"></section>
            <section class="section" id="row-continue" hidden>
                <h2 class="section-title">${text("Continue Watching", "Seguir viendo")}</h2>
                <div class="scroll-row" id="continue-row"></div>
            </section>
            <section class="section" id="row-similar" hidden>
                <h2 class="section-title">${text("Because You Watched", "Porque viste")} <span id="similar-tag" style="font-size:14px;color:var(--text3);letter-spacing:0"></span></h2>
                <div class="scroll-row" id="similar-row"></div>
            </section>
            <section class="section" id="row-trending-movies">
                <h2 class="section-title">${text("Trending Movies", "Películas en tendencia")}</h2>
                <div class="scroll-row" id="trending-movies-row"></div>
            </section>
            <section class="section" id="row-trending-tv">
                <h2 class="section-title">${text("Trending Series", "Series en tendencia")}</h2>
                <div class="scroll-row" id="trending-tv-row"></div>
            </section>
            <section class="section" id="row-top-rated">
                <h2 class="section-title">${text("Top Rated", "Mejor valoradas")}</h2>
                <div class="scroll-row" id="top-rated-row"></div>
            </section>
            <div id="genre-rows"></div>
        `;
    }

    function applyVisibility() {
        if (!prefs) return;
        const vis = prefs.home_visibility || {};
        const r = (id, key) => { const el = $(id); if (el && vis[key] === false) el.hidden = true; };
        r("#row-continue", "continue");
        r("#row-similar", "similar");
        r("#row-trending-movies", "trending_movies");
        r("#row-trending-tv", "trending_tv");
        r("#row-top-rated", "top_rated");
    }

    async function buildHome() {
        const host = $("#home-host");
        if (!host) return;
        host.innerHTML = rowsTemplate();

        const M = trending.movies, T = trending.tv;
        const allTrending = [];
        for (let i = 0; i < Math.max(M.length, T.length); i++) {
            if (M[i]) allTrending.push(M[i]);
            if (T[i]) allTrending.push(T[i]);
        }
        const featured = allTrending[0];
        if (featured) renderHero(featured);
        else {
            $("#hero").innerHTML = `<div id="home-feedback" class="home-feedback" role="${trendingFailed ? "alert" : "status"}">
                <h1>${text("Discover", "Descubrir")}</h1>
                <p>${trendingFailed ? text("Could not load recommendations.", "No se pudieron cargar las recomendaciones.") : text("No recommendations available yet.", "Todavía no hay recomendaciones.")}</p>
                <div><button id="retry-home" class="button" type="button">${text("Retry", "Reintentar")}</button>
                <a class="button" href="/browse">${text("Browse catalog", "Explorar catálogo")}</a></div></div>`;
            $("#retry-home").addEventListener("click", init);
        }
        // Show the primary rails before optional personal recommendations finish.
        window.SVMediaCard.renderInto($("#trending-movies-row"), M.slice(0, 18));
        attachRailControls($("#trending-movies-row"));
        window.SVMediaCard.renderInto($("#trending-tv-row"), T.slice(0, 18));
        attachRailControls($("#trending-tv-row"));
        $("#row-trending-movies").hidden = !M.length;
        $("#row-trending-tv").hidden = !T.length;
        applyVisibility();

        const history = visibleRow("continue") || visibleRow("similar") ? await loadHistory() : [];
        const inProgress = history.filter((h) => h.progress_pct > 1 && h.progress_pct < 95).slice(0, 12);
        if (inProgress.length && visibleRow("continue")) {
            $("#row-continue").hidden = false;
            window.SVMediaCard.renderInto($("#continue-row"), inProgress);
            attachRailControls($("#continue-row"));
        }

        const seed = history[Math.floor(Math.random() * Math.min(history.length, 5))];
        if (seed && visibleRow("similar")) {
            const sim = await fetchRecommendations(seed);
            if (sim.length) {
                $("#row-similar").hidden = false;
                $("#similar-tag").textContent = seed.title || seed.name || "";
                window.SVMediaCard.renderInto($("#similar-row"),
                    sim.slice(0, 18).map((x) => ({ ...x, media_type: seed.media_type || "movie" })));
                attachRailControls($("#similar-row"));
            }
        }

        const [tm, tt] = visibleRow("top_rated") ? await Promise.all([
            fetchJSON("/api/tmdb/discover/movie?sort_by=vote_average.desc&vote_count.gte=500"),
            fetchJSON("/api/tmdb/discover/tv?sort_by=vote_average.desc&vote_count.gte=500"),
        ]) : [null, null];
        const topM = ((Array.isArray(tm) ? tm : (tm && (tm.results || tm.items))) || []).slice(0, 8).map(x => ({ ...x, media_type: "movie" }));
        const topT = ((Array.isArray(tt) ? tt : (tt && (tt.results || tt.items))) || []).slice(0, 8).map(x => ({ ...x, media_type: "tv" }));
        const top = [];
        for (let i = 0; i < Math.max(topM.length, topT.length); i++) {
            if (topM[i]) top.push(topM[i]);
            if (topT[i]) top.push(topT[i]);
        }
        window.SVMediaCard.renderInto($("#top-rated-row"), top);
        attachRailControls($("#top-rated-row"));
        $("#row-top-rated").hidden = !top.length;

        await loadGenreRows();

        applyVisibility();
        restoreFocus();
    }

    function restoreFocus() {
        try {
            const saved = JSON.parse(sessionStorage.getItem("sv_portal_return"));
            if (!saved || Date.now() - saved.time > 300000) return;
            requestAnimationFrame(() => {
                const row = document.getElementById(saved.row) || $("#home-host");
                const link = [...row.querySelectorAll("a")].find(a => a.getAttribute("href") === saved.href);
                link?.focus({ preventScroll: true }); window.scrollTo(0, saved.scroll);
            });
            sessionStorage.removeItem("sv_portal_return");
        } catch (_) {}
    }

    document.addEventListener("click", event => {
        const link = event.target.closest("#home-host a[href^='/media/']");
        if (!link) return;
        try {
            const row = link.closest(".scroll-row") || $("#hero");
            sessionStorage.setItem("sv_portal_return", JSON.stringify({ row: row.id, href: link.getAttribute("href"), scroll: scrollY, time: Date.now() }));
        } catch (_) {}
    });

    window.addEventListener("sv:prefs-loaded", (e) => {
        prefs = e.detail;
        applyVisibility();
    });

    async function init() {
        prefs = window.SVPrefs || null;
        await loadTrending();
        await buildHome();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
