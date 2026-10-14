/* StreamVault search modal — Spotlight-style Ctrl+F overlay with debounced
   TMDB multi-search and recent-search history. */
(function () {
    "use strict";

    const HIST_KEY = "sv_search_history";
    const HIST_MAX = 12;
    const DEBOUNCE_MS = 380;

    let modal = null;
    let input = null;
    let resultsEl = null;
    let timer = null;
    let focusedIndex = -1;
    let lastResults = [];

    function loadHistory() {
        try {
            return JSON.parse(localStorage.getItem(HIST_KEY) || "[]");
        } catch (e) {
            return [];
        }
    }
    function saveHistory(arr) {
        localStorage.setItem(HIST_KEY, JSON.stringify(arr.slice(0, HIST_MAX)));
    }
    function pushHistory(term) {
        if (!term) return;
        const t = term.trim();
        if (!t) return;
        const cur = loadHistory().filter((x) => x.toLowerCase() !== t.toLowerCase());
        cur.unshift(t);
        saveHistory(cur);
    }

    function build() {
        modal = document.createElement("div");
        modal.className = "modal-overlay";
        modal.id = "sv-search-modal";
        modal.innerHTML = `
            <div class="search-box" role="dialog" aria-modal="true" aria-label="${document.documentElement.lang === 'es' ? 'Buscar' : 'Search'}">
                <div class="search-input-wrap">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
                    <input class="search-input" type="text" placeholder="Search movies and series…" aria-label="${document.documentElement.lang === 'es' ? 'Buscar películas y series' : 'Search movies and series'}" autocomplete="off">
                    <button class="icon-btn" type="button" data-close-search aria-label="${document.documentElement.lang === 'es' ? 'Cerrar búsqueda' : 'Close search'}">×</button>
                </div>
                <div class="search-results" id="sv-search-results"></div>
            </div>
        `;
        modal.addEventListener("click", (e) => {
            if (e.target === modal) close();
        });
        modal.querySelector("[data-close-search]").addEventListener("click", close);
        document.body.appendChild(modal);
        input = modal.querySelector(".search-input");
        resultsEl = modal.querySelector("#sv-search-results");
        input.addEventListener("input", onInput);
        input.addEventListener("keydown", onKey);
    }

    function open() {
        if (!modal) build();
        if (window.SVShell.isOpen(modal)) return;
        modal.classList.remove("hidden");
        input.value = "";
        focusedIndex = -1;
        renderHistory();
        window.SVShell.open(modal, {initialFocus: input, trigger: document.activeElement});
    }
    function close() {
        if (!modal) return;
        clearTimeout(timer);
        window.SVShell.close(modal);
    }

    function onInput() {
        clearTimeout(timer);
        const q = input.value.trim();
        if (!q) {
            renderHistory();
            return;
        }
        timer = setTimeout(() => doSearch(q), DEBOUNCE_MS);
    }

    function onKey(e) {
        if (e.key === "ArrowDown") {
            e.preventDefault();
            focusedIndex = Math.min(focusedIndex + 1, lastResults.length - 1);
            highlight();
            const row = resultsEl.querySelectorAll(".search-result")[focusedIndex];
            if (row) row.focus();
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            focusedIndex = Math.max(focusedIndex - 1, -1);
            highlight();
        } else if (e.key === "Enter") {
            e.preventDefault();
            if (focusedIndex >= 0 && lastResults[focusedIndex]) {
                openResult(lastResults[focusedIndex]);
            } else if (lastResults[0]) {
                openResult(lastResults[0]);
            }
        }
    }

    function highlight() {
        Array.from(resultsEl.querySelectorAll(".search-result")).forEach((el, i) => {
            el.classList.toggle("focused", i === focusedIndex);
            if (i === focusedIndex) el.scrollIntoView({ block: "nearest" });
        });
    }

    async function doSearch(q) {
        resultsEl.innerHTML = `<div class="search-hint">Searching…</div>`;
        try {
            const [movies, series] = await Promise.all([
                fetch(`/api/tmdb/search/movie?q=${encodeURIComponent(q)}`, { credentials: "same-origin" })
                    .then((r) => (r.ok ? r.json() : { results: [] }))
                    .catch(() => ({ results: [] })),
                fetch(`/api/tmdb/search/tv?q=${encodeURIComponent(q)}`, { credentials: "same-origin" })
                    .then((r) => (r.ok ? r.json() : { results: [] }))
                    .catch(() => ({ results: [] })),
            ]);
            const m = normalizeResults(movies).map((x) => ({ ...x, media_type: "movie" }));
            const t = normalizeResults(series).map((x) => ({ ...x, media_type: "tv" }));
            const merged = interleave(m, t).slice(0, 24);
            lastResults = merged;
            renderResults(merged);
        } catch (e) {
            resultsEl.innerHTML = `<div class="search-empty">Search failed.</div>`;
        }
    }

    function normalizeResults(payload) {
        if (Array.isArray(payload)) return payload;
        return Array.isArray(payload?.results) ? payload.results : [];
    }

    function interleave(a, b) {
        const out = [];
        const n = Math.max(a.length, b.length);
        for (let i = 0; i < n; i++) {
            if (a[i]) out.push(a[i]);
            if (b[i]) out.push(b[i]);
        }
        return out;
    }

    function imgUrl(path, size) {
        return path ? `https://image.tmdb.org/t/p/${size || "w92"}${path}` : "";
    }

    function renderResults(items) {
        if (!items.length) {
            resultsEl.innerHTML = `<div class="search-empty">No results.</div>`;
            return;
        }
        resultsEl.innerHTML = "";
        items.forEach((it, i) => {
            const row = document.createElement("a");
            row.className = "search-result";
            row.href = `/media/${it.id}?type=${it.media_type === "tv" ? "tv" : "movie"}`;
            const isTV = it.media_type === "tv";
            const title = it.title || it.name || "?";
            const date = it.release_date || it.first_air_date || "";
            const year = date ? date.slice(0, 4) : "";
            const rating = it.vote_average ? it.vote_average.toFixed(1) : "";
            row.innerHTML = `
                ${it.poster_path ? `<img src="${imgUrl(it.poster_path)}" alt="">` : `<div style="width:40px;height:58px;border-radius:5px;background:var(--surface3)"></div>`}
                <div class="search-result-info">
                    <div class="search-result-title">${escapeHTML(title)}</div>
                    <div class="search-result-meta">${year}${rating ? " · ★ " + rating : ""}</div>
                </div>
                <span class="search-result-type ${isTV ? "type-tv" : "type-movie"}">${isTV ? "Series" : "Movie"}</span>
            `;
            row.addEventListener("click", () => pushHistory(input.value));
            row.addEventListener("focus", () => { focusedIndex = i; highlight(); });
            row.addEventListener("keydown", event => {
                if (event.key === "ArrowUp" && i === 0) {
                    event.preventDefault();
                    focusedIndex = -1;
                    highlight();
                    input.focus();
                }
            });
            resultsEl.appendChild(row);
        });
    }

    function renderHistory() {
        focusedIndex = -1;
        lastResults = [];
        const hist = loadHistory();
        if (!hist.length) {
            resultsEl.innerHTML = `<div class="search-hint">Type to search movies and series.</div>`;
            return;
        }
        resultsEl.innerHTML = `
            <div class="search-history">
                <div class="search-history-header">
                    <span class="search-history-label">Recent</span>
                    <button class="search-history-clear" id="sv-hist-clear">Clear</button>
                </div>
            </div>
        `;
        const wrap = resultsEl.querySelector(".search-history");
        hist.forEach((term) => {
            const it = document.createElement("button");
            it.type = "button";
            it.className = "search-history-item";
            it.innerHTML = `
                <span class="search-history-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg></span>
                <span class="search-history-term"></span>
            `;
            it.querySelector(".search-history-term").textContent = term;
            it.addEventListener("click", () => {
                input.value = term;
                input.focus();
                doSearch(term);
            });
            wrap.appendChild(it);
        });
        const clear = resultsEl.querySelector("#sv-hist-clear");
        if (clear) clear.addEventListener("click", () => { saveHistory([]); renderHistory(); });
    }

    function openResult(it) {
        const id = it.id;
        const mt = it.media_type === "tv" ? "tv" : "movie";
        pushHistory(input.value);
        window.SVShell.navigate(`/media/${id}?type=${mt}`);
    }

    function escapeHTML(s) {
        return String(s).replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }

    window.SVSearchModal = { open, close, normalizeResults };
})();
