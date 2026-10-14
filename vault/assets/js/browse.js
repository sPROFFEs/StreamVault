const LANG = window.APP_LANG === "es" ? "es" : "en";
const TMDB_LANG = LANG === "es" ? "es-ES" : "en-US";
const TEXT = {
    en: {
        favorites: "Favorites",
        downloads: "Downloaded",
        catalog: "Catalog",
        savedLibrary: "Saved personal library for this user.",
        downloadedLibrary: "Movies and episodes stored on this server.",
        resultsFor: (query) => `Results for "${query}".`,
        trendingCaption: "Discover your next movie or series.",
        untitled: "Untitled",
        noDate: "No date",
        unrated: "unrated",
    },
    es: {
        favorites: "Favoritos",
        downloads: "Descargado",
        catalog: "Catálogo",
        savedLibrary: "Biblioteca personal guardada por usuario.",
        downloadedLibrary: "Películas y episodios guardados en este servidor.",
        resultsFor: (query) => `Resultados para "${query}".`,
        trendingCaption: "Descubre tu próxima película o serie.",
        untitled: "Sin titulo",
        noDate: "Sin fecha",
        unrated: "sin rating",
    },
}[LANG];

const TMDB_CACHE = {
    has(key) { try { return sessionStorage.getItem(`sv_tmdb_${key}`) !== null; } catch (_) { return false; } },
    get(key) { try { return JSON.parse(sessionStorage.getItem(`sv_tmdb_${key}`)); } catch(_) { return null; } },
    set(key, val) { try { sessionStorage.setItem(`sv_tmdb_${key}`, JSON.stringify(val)); } catch(_) {} }
};

const state = {
    mediaType: "movie", // "movie" | "tv"
    view: "discover",   // "discover" | "favorites" | "downloads"
    query: "",
    genre: "",
    sortBy: "popularity.desc",
    genreList: [],
    items: new Map(),
    favorites: new Map(),
    searchTimer: null,
    page: 1,
    loadingMore: false,
    generation: 0,
    controller: null,
    favoritesLoaded: false,
    downloads: null,
};

const elements = {
    typeFilter: document.getElementById("type-filter"),
    genreFilter: document.getElementById("genre-filter"),
    sortFilter: document.getElementById("sort-filter"),
    viewFilter: document.getElementById("view-filter"),
    search: document.getElementById("browse-search"),
    refreshButton: document.getElementById("refresh-button"),
    resultsTitle: document.getElementById("results-title"),
    resultsCaption: document.getElementById("results-caption"),
    resultsCount: document.getElementById("results-count"),
    grid: document.getElementById("browse-grid"),
    empty: document.getElementById("browse-empty"),
    loadMoreButton: document.getElementById("load-more-button"),
};

async function boot() {
    const params = new URLSearchParams(window.location.search);
    state.mediaType = params.get("type") || "movie";
    state.view = params.get("view") || "discover";
    state.query = params.get("q") || "";
    state.genre = params.get("genre") || "";
    state.sortBy = params.get("sort") || "popularity.desc";

    if (elements.typeFilter) elements.typeFilter.value = state.mediaType;
    if (elements.viewFilter) elements.viewFilter.value = state.view;
    if (elements.sortFilter) elements.sortFilter.value = state.sortBy;
    if (elements.search) elements.search.value = state.query;

    elements.typeFilter?.addEventListener("change", onTypeChange);
    elements.genreFilter?.addEventListener("change", onGenreChange);
    elements.sortFilter?.addEventListener("change", onSortChange);
    elements.viewFilter?.addEventListener("change", onViewChange);
    elements.search?.addEventListener("input", onSearchInput);
    elements.loadMoreButton?.addEventListener("click", loadMore);

    elements.empty.querySelector("button")?.addEventListener("click", resetFilters);
    document.getElementById("retry-catalog")?.addEventListener("click", () => loadCatalog(true));
    elements.refreshButton.onclick = () => loadCatalog(true);
    elements.genreFilter.disabled = state.view !== "discover";
    // Genres and results do not depend on one another.
    loadGenres();
    if (!restoreCatalog()) loadCatalog();
}

async function loadGenres() {
    try {
        const mediaType = state.mediaType;
        const res = await fetch(`/api/tmdb/genres/${mediaType}?lang=${LANG}`);
        if (!res.ok) return;
        const genres = await res.json();
        if (mediaType !== state.mediaType) return;
        state.genreList = Array.isArray(genres) ? genres : [];
        if (elements.genreFilter) {
            const allOption = `<option value="">${LANG === "es" ? "Todos los géneros" : "All genres"}</option>`;
            elements.genreFilter.innerHTML = allOption + state.genreList.map(g => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join("");
            if (state.genre) {
                if (!state.genreList.some(g => String(g.id) === String(state.genre))) {
                    const option = new Option(LANG === "es" ? "Género seleccionado" : "Selected genre", state.genre);
                    elements.genreFilter.add(option);
                }
                elements.genreFilter.value = state.genre;
            } else {
                state.genre = "";
                elements.genreFilter.value = "";
            }
        }
    } catch (err) {
        console.error("Failed to load genres", err);
    }
}

async function requestJSON(url, signal) {
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`Request failed: ${response.status}`);
    return response.json();
}

async function loadCatalog(force = false) {
    clearTimeout(state.searchTimer);
    const generation = ++state.generation;
    state.controller?.abort();
    state.controller = new AbortController();
    const signal = state.controller.signal;
    state.loadingMore = false;
    state.page = 1;
    if (force) { state.favoritesLoaded = false; state.downloads = null; }
    const filters = { ...state };
    const retrying = document.activeElement?.id === "retry-catalog";
    showLoading();
    try {
        let items;
        if (filters.view === "favorites") {
            await loadFavorites(signal);
            items = getFavoriteItems();
        } else if (filters.view === "downloads") {
            items = await loadDownloadedItems(filters, signal);
        } else {
            items = await loadDiscoverItems(filters, signal);
        }
        if (generation !== state.generation) return;
        renderCatalog(filters.view === "downloads" ? filterItems(items, filters) : items);
        if (retrying) (elements.grid.querySelector("a") || elements.search).focus();
    } catch (error) {
        if (generation !== state.generation || error.name === "AbortError") return;
        elements.grid.replaceChildren();
        elements.resultsCount.textContent = "";
        document.getElementById("browse-error").hidden = false;
    } finally {
        if (generation === state.generation) elements.grid.setAttribute("aria-busy", "false");
    }
}

function showLoading() {
    elements.grid.setAttribute("aria-busy", "true");
    elements.resultsCount.textContent = LANG === "es" ? "Cargando…" : "Loading…";
    elements.grid.innerHTML = Array.from({ length: 8 }, () =>
        `<div class="catalog-card catalog-card--skeleton" aria-hidden="true"><div class="catalog-card__skeleton-img"></div><div class="catalog-card__skeleton-line"></div></div>`
    ).join("");
    elements.empty.hidden = true;
    document.getElementById("browse-error").hidden = true;
    elements.loadMoreButton.hidden = true;
    elements.loadMoreButton.disabled = false;
    elements.loadMoreButton.textContent = LANG === "es" ? "Cargar más" : "Load more";
}

async function loadDownloadedItems(filters, signal) {
    const data = state.downloads || await requestJSON("/api/downloads/library", signal);
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    state.downloads = data;
    const group = filters.mediaType === "tv" ? (data.series || []) : (data.movies || []);
    const filtered = filterItems(group, filters);
    const deduped = filters.mediaType === "tv" ? deduplicateSeries(filtered) : filtered;
    return batchEnrich(deduped, 4, filters.mediaType, signal);
}

/** Collapse multiple episodes of the same show into a single catalog entry.
 *  Keeps episode_count and representative episode info for display.
 *  Normalizes titles so "Show.Name" and "Show Name" are treated as the same. */
function deduplicateSeries(episodes) {
    const byTitle = new Map();
    const normalize = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
    for (const ep of episodes) {
        const rawTitle = (ep.title || ep.name || "").trim();
        if (!rawTitle) continue;
        const key = normalize(rawTitle);
        if (!byTitle.has(key)) {
            byTitle.set(key, { ...ep, title: rawTitle, episode_count: 1 });
        } else {
            byTitle.get(key).episode_count += 1;
        }
    }
    return [...byTitle.values()];
}

async function batchEnrich(items, concurrency, mediaType, signal) {
    const results = [];
    for (let i = 0; i < items.length; i += concurrency) {
        if (signal.aborted) throw new DOMException("Aborted", "AbortError");
        const batch = items.slice(i, i + concurrency);
        results.push(...await Promise.all(batch.map(item => enrichDownloadedItem(item, mediaType, signal))));
    }
    return results;
}

async function enrichDownloadedItem(item, mediaType, signal) {
    try {
        const itemYear = item.year || null;
        const cacheKey = `${TMDB_LANG}:${mediaType}:${item.title}:${itemYear || "none"}`;
        if (TMDB_CACHE.has(cacheKey)) {
            const metadata = TMDB_CACHE.get(cacheKey);
            return metadata ? { ...metadata, ...item, id: metadata.id, poster_path: metadata.poster_path } : item;
        }
        let endpoint = `/api/tmdb/search/${mediaType}?q=${encodeURIComponent(item.title)}&lang=${encodeURIComponent(TMDB_LANG)}`;
        if (itemYear) {
            endpoint += `&year=${itemYear}`;
        }
        const results = normalizeResults(await requestJSON(endpoint, signal));
        const normalized = (value) => String(value || "").toLocaleLowerCase().replace(/[^a-z0-9]+/g, "");
        
        let metadata = null;
        if (itemYear) {
            metadata = results.find((candidate) => {
                const candDate = candidate.release_date || candidate.first_air_date || "";
                const candYear = candDate ? parseInt(candDate.slice(0, 4), 10) : null;
                return normalized(candidate.title || candidate.name) === normalized(item.title) && candYear === itemYear;
            });
        }
        if (!metadata) {
            metadata = results.find((candidate) =>
                normalized(candidate.title || candidate.name) === normalized(item.title)
            ) || results[0];
        }

        TMDB_CACHE.set(cacheKey, metadata || null);
        return metadata ? { ...metadata, ...item, id: metadata.id, poster_path: metadata.poster_path } : item;
    } catch (_) {
        return item;
    }
}

function filterItems(items, filters) {
    const query = filters.query.toLocaleLowerCase();
    const result = items.filter(item => (!query || String(item.title || item.name || "").toLocaleLowerCase().includes(query)) &&
        (!filters.genre || filters.view !== "discover" || item.genre_ids?.includes(Number(filters.genre))));
    if (filters.sortBy === "vote_average.desc") result.sort((a, b) => (b.vote_average || b.rating || 0) - (a.vote_average || a.rating || 0));
    if (filters.sortBy === "primary_release_date.desc") result.sort((a, b) => (b.release_date || b.first_air_date || "").localeCompare(a.release_date || a.first_air_date || ""));
    return result;
}

async function loadDiscoverItems(filters = state, signal) {
    const langParam = `lang=${encodeURIComponent(TMDB_LANG)}`;
    if (filters.query) {
        const items = normalizeResults(await requestJSON(`/api/tmdb/search/${filters.mediaType}?q=${encodeURIComponent(filters.query)}&${langParam}`, signal));
        // The service already matches localized/alternative titles.
        return filterItems(items, { ...filters, query: "" });
    }
    let sortField = filters.sortBy || "popularity.desc";
    if (sortField === "primary_release_date.desc" && filters.mediaType === "tv") sortField = "first_air_date.desc";
    let endpoint = `/api/tmdb/discover/${filters.mediaType}?sort_by=${encodeURIComponent(sortField)}&page=${state.page}&${langParam}`;
    if (filters.genre) endpoint += `&with_genres=${encodeURIComponent(filters.genre)}`;
    if (filters.sortBy === "vote_average.desc") endpoint += "&vote_count.gte=100";
    return normalizeResults(await requestJSON(endpoint, signal));
}

function normalizeResults(payload) {
    if (Array.isArray(payload)) return payload;
    return Array.isArray(payload?.results) ? payload.results : [];
}

async function loadMore() {
    if (state.loadingMore || state.view !== "discover" || state.query) return;
    state.loadingMore = true;
    const generation = state.generation;
    state.page += 1;
    elements.grid.setAttribute("aria-busy", "true");
    elements.loadMoreButton.disabled = true;
    try {
        const items = await loadDiscoverItems({ ...state }, state.controller.signal);
        if (generation !== state.generation) return;
        appendCatalog(items);
        elements.loadMoreButton.hidden = items.length < 20;
        document.getElementById("browse-error").hidden = true;
    } catch (error) {
        if (generation !== state.generation || error.name === "AbortError") return;
        state.page -= 1;
        document.getElementById("browse-error").hidden = false;
    } finally {
        if (generation === state.generation) {
            state.loadingMore = false;
            elements.grid.setAttribute("aria-busy", "false");
            elements.loadMoreButton.disabled = false;
        }
    }
}

async function loadFavorites(signal) {
    if (state.favoritesLoaded) return;
    const data = await requestJSON("/api/library/favorites", signal);
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    state.favorites = new Map((data.items || []).map(item => [`${item.media_type}:${item.tmdb_id}`, item]));
    state.favoritesLoaded = true;
}

function getFavoriteItems() {
    return filterItems([...state.favorites.values()], state)
        .filter((item) => item.media_type === state.mediaType)
        .map((item) => ({
            id: item.tmdb_id,
            media_type: item.media_type,
            title: item.title,
            name: item.title,
            poster_path: item.poster_path,
            release_date: item.release_date,
            vote_average: item.rating,
        }));
}

async function onTypeChange() {
    state.mediaType = elements.typeFilter.value;
    state.genre = "";
    loadGenres();
    updateQueryString();
    loadCatalog();
}

function onGenreChange() {
    state.genre = elements.genreFilter.value;
    updateQueryString();
    loadCatalog();
}

function onSortChange() {
    state.sortBy = elements.sortFilter.value;
    updateQueryString();
    loadCatalog();
}

function onViewChange() {
    state.view = elements.viewFilter.value;
    elements.genreFilter.disabled = state.view !== "discover";
    updateQueryString();
    loadCatalog();
}

function onSearchInput() {
    const query = elements.search.value.trim();
    if (query === state.query) return;
    state.query = query;
    ++state.generation;
    state.controller?.abort();
    elements.resultsCount.textContent = LANG === "es" ? "Buscando…" : "Searching…";
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => {
        updateQueryString();
        loadCatalog();
    }, 250);
}

function updateQueryString() {
    const params = new URLSearchParams();
    params.set("type", state.mediaType);
    params.set("view", state.view);
    if (state.genre) params.set("genre", state.genre);
    if (state.sortBy && state.sortBy !== "popularity.desc") params.set("sort", state.sortBy);
    if (state.query) params.set("q", state.query);
    window.history.replaceState({ ...history.state }, "", `${window.location.pathname}?${params.toString()}`);
}

function renderCatalog(items) {
    state.items.clear();
    items.forEach((item) => state.items.set(String(item.id || item.relative_path), item));

    elements.resultsTitle.textContent = state.view === "favorites"
        ? TEXT.favorites : state.view === "downloads" ? TEXT.downloads : TEXT.catalog;
    elements.resultsCaption.textContent = state.view === "favorites"
        ? TEXT.savedLibrary
        : state.view === "downloads" ? TEXT.downloadedLibrary
            : (state.query ? TEXT.resultsFor(state.query) : TEXT.trendingCaption);
    elements.resultsCount.textContent = `${items.length} ${LANG === "es" ? "resultados" : "results"}`;

    if (!items.length) {
        elements.grid.innerHTML = "";
        elements.empty.hidden = false;
        elements.loadMoreButton.hidden = true;
        return;
    }

    elements.empty.hidden = true;
    elements.grid.replaceChildren(...items.map(renderCard));
    elements.loadMoreButton.hidden = state.view !== "discover" || !!state.query || items.length < 20;
}

function appendCatalog(items) {
    const unique = items.filter((item) => !state.items.has(String(item.id)));
    unique.forEach((item) => state.items.set(String(item.id), item));
    elements.grid.append(...unique.map(renderCard));
    elements.resultsCount.textContent = `${state.items.size} ${LANG === "es" ? "resultados" : "results"}`;
}

function renderCard(item) {
    const card = window.SVMediaCard.render({ ...item, media_type: item.media_type || state.mediaType }, { contextMenu: false });
    card.addEventListener("click", () => rememberCatalog(card));
    return card;
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

document.addEventListener("DOMContentLoaded", boot);


function resetFilters() {
    state.query = ""; state.genre = ""; state.sortBy = "popularity.desc";
    elements.search.value = ""; elements.genreFilter.value = ""; elements.sortFilter.value = state.sortBy;
    updateQueryString(); loadCatalog(); elements.search.focus();
}

function rememberCatalog(card) {
    try {
        sessionStorage.setItem("sv_catalog_return", JSON.stringify({
            url: location.pathname + location.search, detail: card.getAttribute("href"),
            focus: card.getAttribute("href"), scroll: scrollY, page: state.page,
            items: [...state.items.values()], time: Date.now(),
        }));
    } catch (_) {}
}

function restoreCatalog() {
    try {
        const saved = JSON.parse(sessionStorage.getItem("sv_catalog_return"));
        if (!saved || saved.url !== location.pathname + location.search || Date.now() - saved.time > 300000) return false;
        renderCatalog(saved.items); state.page = saved.page;
        state.controller = new AbortController();
        elements.grid.setAttribute("aria-busy", "false");
        requestAnimationFrame(() => {
            const card = [...elements.grid.querySelectorAll("a")].find(a => a.getAttribute("href") === saved.focus);
            (card || elements.grid.querySelector("a") || elements.search)?.focus({ preventScroll: true }); window.scrollTo(0, saved.scroll);
        });
        sessionStorage.removeItem("sv_catalog_return");
        return true;
    } catch (_) { return false; }
}
