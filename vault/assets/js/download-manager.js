const LANG = window.APP_LANG === "es" ? "es" : "en";
const T = {
    en: {
        noActive: "No active downloads.",
        noFiles: "No downloaded files.",
        cancel: "Cancel",
        delete: "Delete",
        play: "Play",
        confirmDelete: (name) => `Delete "${name}" from disk?`,
        statusPending: "Pending",
        statusConnecting: "Connecting...",
        statusDownloading: "Downloading",
        statusCompleted: "Completed",
        statusError: "Error",
        statusCancelled: "Cancelled",
        storage: (used, quota) => `${used} used of ${quota}`,
        speed: (s) => `${s} · `,
        eta: (s) => `ETA ${s}`,
        seeders: (n) => `${n} seeders`,
        size: (s) => s,
        loadError: "Could not load downloads. Retrying…",
        results: (n) => `${n} downloaded ${n === 1 ? "file" : "files"}`,
        searching: "Searching library…",
        noResults: "No results",
        noResultsHint: "Try another title or clear the filters.",
        emptyLibrary: "Library is empty",
        emptyLibraryHint: "Completed downloads will be grouped here as movies and series.",
        localOrigin: "This server",
        nodeOrigin: "Node",
    },
    es: {
        noActive: "No hay descargas en curso.",
        noFiles: "No hay archivos descargados.",
        cancel: "Cancelar",
        delete: "Eliminar",
        play: "Reproducir",
        confirmDelete: (name) => `¿Eliminar "${name}" del disco?`,
        statusPending: "Pendiente",
        statusConnecting: "Conectando...",
        statusDownloading: "Descargando",
        statusCompleted: "Completado",
        statusError: "Error",
        statusCancelled: "Cancelado",
        storage: (used, quota) => `${used} usados de ${quota}`,
        speed: (s) => `${s} · `,
        eta: (s) => `ETA ${s}`,
        seeders: (n) => `${n} seeders`,
        size: (s) => s,
        loadError: "No se pudieron cargar las descargas. Reintentando…",
        results: (n) => `${n} ${n === 1 ? "archivo descargado" : "archivos descargados"}`,
        searching: "Buscando en la biblioteca…",
        noResults: "Sin resultados",
        noResultsHint: "Prueba otro título o limpia los filtros.",
        emptyLibrary: "La biblioteca está vacía",
        emptyLibraryHint: "Las descargas terminadas aparecerán aquí agrupadas como películas y series.",
        localOrigin: "Este servidor",
        nodeOrigin: "Nodo",
    },
}[LANG];

// ------------------------------------------------------------------ //
// State                                                               //
// ------------------------------------------------------------------ //
const state = {
    torrentJobs: {},
    ytJobs: {},
    files: [],
    refreshTimer: null,
    libraryPage: 1,
    query: "",
    mediaType: "all",
    sort: "recent",
    origin: "all",
    filterRevision: 0,
    searchTimer: null,
    openShows: new Set(),
    jobsLoading: false,
    filesLoading: false,
    filesReloadPending: false,
    filesNeedReload: true,
};

const el = {
    activeList: document.getElementById("active-list"),
    activeEmpty: document.getElementById("active-empty"),
    activeCount: document.getElementById("active-count"),
    libraryList: document.getElementById("library-list"),
    libraryEmpty: document.getElementById("library-empty"),
    libraryCount: document.getElementById("library-count"),
    libraryPrevious: document.getElementById("library-previous"),
    libraryNext: document.getElementById("library-next"),
    libraryPage: document.getElementById("library-page"),
    librarySearch: document.getElementById("library-search"),
    libraryType: document.getElementById("library-type"),
    librarySort: document.getElementById("library-sort"),
    libraryOrigin: document.getElementById("library-origin"),
    libraryReset: document.getElementById("library-reset"),
    librarySummary: document.getElementById("library-summary"),
    libraryEmptyTitle: document.getElementById("library-empty-title"),
    libraryEmptyDescription: document.getElementById("library-empty-description"),
    storageLabel: document.getElementById("storage-label"),
    storageBar: document.getElementById("storage-bar"),
    refreshBtn: document.getElementById("refresh-btn"),
    confirmOverlay: document.getElementById("confirm-overlay"),
    confirmMessage: document.getElementById("confirm-message"),
    confirmOk: document.getElementById("confirm-ok"),
    confirmCancel: document.getElementById("confirm-cancel"),
    playerOverlay: document.getElementById("local-player-overlay"),
    player: document.getElementById("local-player"),
    playerTitle: document.getElementById("local-player-title"),
    playerClose: document.getElementById("local-player-close"),
    playerAudioWrapper: document.getElementById("local-player-audio-wrapper"),
    playerAudio: document.getElementById("local-player-audio"),
    previewOverlay: document.getElementById("preview-overlay"),
    previewTitle: document.getElementById("preview-title"),
    previewBody: document.getElementById("preview-body"),
    previewClose: document.getElementById("preview-close"),
};

// ------------------------------------------------------------------ //
// Boot                                                                //
// ------------------------------------------------------------------ //
async function boot() {
    const params = new URLSearchParams(window.location.search);
    state.query = params.get("q") || "";
    state.mediaType = ["movie", "series"].includes(params.get("type")) ? params.get("type") : "all";
    state.sort = ["title", "size"].includes(params.get("sort")) ? params.get("sort") : "recent";
    state.origin = /^(local|federated|peer:[1-9][0-9]*)$/.test(params.get("origin")) ? params.get("origin") : "all";
    state.libraryPage = Math.max(1, parseInt(params.get("page"), 10) || 1);
    if (el.librarySearch) el.librarySearch.value = state.query;
    if (el.libraryType) el.libraryType.value = state.mediaType;
    if (el.librarySort) el.librarySort.value = state.sort;
    if (el.libraryOrigin) {
        if (state.origin.startsWith("peer:")) el.libraryOrigin.add(new Option(`${T.nodeOrigin} ${state.origin.slice(5)}`, state.origin));
        el.libraryOrigin.value = state.origin;
    }
    updateLibraryUrl();
    el.librarySearch?.addEventListener("input", () => changeLibraryFilters(true));
    el.libraryType?.addEventListener("change", () => changeLibraryFilters());
    el.librarySort?.addEventListener("change", () => changeLibraryFilters());
    el.libraryOrigin?.addEventListener("change", () => changeLibraryFilters());
    el.libraryReset?.addEventListener("click", () => {
        el.librarySearch.value = "";
        el.libraryType.value = "all";
        el.librarySort.value = "recent";
        if (el.libraryOrigin) el.libraryOrigin.value = "all";
        changeLibraryFilters();
    });
    el.refreshBtn.addEventListener("click", refresh);
    el.libraryPrevious?.addEventListener("click", () => changeLibraryPage(-1));
    el.libraryNext?.addEventListener("click", () => changeLibraryPage(1));
    el.confirmCancel.addEventListener("click", () => {
        el.confirmOverlay.style.display = "none";
    });
    el.playerClose.addEventListener("click", closeLocalPlayer);
    el.playerAudio.addEventListener("change", () => {
        const file = state.files.find((item) => item.relative_path === el.player.dataset.path);
        if (file) openLocalPlayer(file, Number(el.playerAudio.value));
    });
    el.playerOverlay.addEventListener("click", (event) => {
        if (event.target === el.playerOverlay) closeLocalPlayer();
    });
    el.previewClose.addEventListener("click", closePreview);
    el.previewOverlay.addEventListener("click", (event) => {
        if (event.target === el.previewOverlay) closePreview();
    });
    // #4: Listen for global notifications (download complete, library updated).
    try {
        if (window.location.hostname.endsWith(".trycloudflare.com")) throw new Error("Use polling for quick tunnels");
        const notifEs = new EventSource("/api/downloads/notifications");
        notifEs.onmessage = (evt) => {
            try {
                const event = JSON.parse(evt.data);
                if (event.type === "download_complete" || event.type === "library_updated") {
                    loadFiles();
                    loadJobs();
                }
            } catch (_) {}
        };
    } catch (_) {}
    ensurePolling();
    await refresh();
}

function ensurePolling() {
    if (state.refreshTimer) return;
    state.refreshTimer = setInterval(async () => {
        if (!document.hidden) {
            await Promise.all([loadJobs(), state.filesNeedReload ? loadFiles() : Promise.resolve()]);
        }
    }, 5000);
}

async function refresh() {
    await Promise.all([loadJobs(), loadFiles()]);
}

async function fetchDownloadJson(url) {
    // AbortSignal.timeout is unavailable on some supported browsers. Keep the
    // deadline active through reading the response body, including via tunnels.
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
        const response = await fetch(url, {signal: controller.signal, cache: "no-store"});
        if (!response.ok) throw new Error(`Downloads request failed (${response.status})`);
        return await response.json();
    } finally {
        clearTimeout(timeout);
    }
}

// ------------------------------------------------------------------ //
// Load jobs (active + recent)                                        //
// ------------------------------------------------------------------ //
async function loadJobs() {
    if (state.jobsLoading) return;
    state.jobsLoading = true;
    try {
        const data = await fetchDownloadJson("/api/downloads/jobs");
        const previous = {...state.torrentJobs, ...state.ytJobs};
        const completed = [...(data.torrent || []), ...(data.yt_dlp || [])].some(
            job => job.status === "completed" && previous[job.id]?.status !== "completed"
        );
        state.torrentJobs = {};
        state.ytJobs = {};

        for (const job of data.torrent || []) {
            state.torrentJobs[job.id] = job;
        }
        for (const job of data.yt_dlp || []) {
            state.ytJobs[job.id] = job;
        }
        renderActive();
        if (completed) await loadFiles();
    } catch (e) {
        console.error("Error cargando descargas:", e);
    } finally {
        state.jobsLoading = false;
    }
}

// ------------------------------------------------------------------ //
// Render active downloads                                             //
// ------------------------------------------------------------------ //
function renderActive() {
    const activeStatuses = ["pending", "connecting", "downloading"];
    const all = [
        ...Object.values(state.torrentJobs),
        ...Object.values(state.ytJobs),
    ].filter(j => activeStatuses.includes(j.status));

    el.activeCount.textContent = String(all.length);

    if (!all.length) {
        el.activeList.innerHTML = "";
        el.activeEmpty.hidden = false;
        return;
    }
    el.activeEmpty.hidden = true;

    el.activeList.innerHTML = "";
    for (const job of all) {
        el.activeList.insertAdjacentHTML("beforeend", buildJobRow(job));
        el.activeList.querySelector(`[data-job-id="${job.id}"] .cancel-btn`)
            ?.addEventListener("click", () => cancelJob(job.id));
        updateJobRow(job);
    }
}

function buildJobRow(job) {
    return `
    <div class="download-item" data-job-id="${job.id}">
        <div class="download-item__info">
            <span class="download-item__title">${escHtml(job.title || job.id)}</span>
            <span class="download-item__status" data-status>${statusLabel(job.status)}</span>
        </div>
        <div class="download-item__metrics">
            <span><small>${LANG === "es" ? "Velocidad" : "Speed"}</small><strong data-speed>–</strong></span>
            <span><small>ETA</small><strong data-eta>–</strong></span>
            <span><small>${LANG === "es" ? "Tamaño" : "Size"}</small><strong data-size>${formatBytes(job.size_bytes)}</strong></span>
        </div>
        <div class="download-item__progress-wrap">
            <div class="progress-bar-track">
                <div class="progress-bar-fill" data-fill style="width:0%"></div>
            </div>
            <span class="download-item__pct" data-pct>0%</span>
        </div>
        <div class="download-item__actions">
            <button class="button cancel-btn" type="button">${T.cancel}</button>
        </div>
    </div>`;
}

function updateJobRow(job) {
    const row = el.activeList.querySelector(`[data-job-id="${job.id}"]`);
    if (!row) return;

    const pct = Math.min(100, job.progress || 0);
    row.querySelector("[data-fill]").style.width = `${pct}%`;
    row.querySelector("[data-pct]").textContent = `${pct.toFixed(0)}%`;

    row.querySelector("[data-speed]").textContent = job.speed_bytes > 0 ? `${formatBytes(job.speed_bytes)}/s` : "–";
    row.querySelector("[data-eta]").textContent = job.eta_seconds ? formatEta(job.eta_seconds) : "–";
    row.querySelector("[data-size]").textContent = formatBytes(job.size_bytes);
    row.querySelector("[data-status]").textContent = job.seeders
        ? `${statusLabel(job.status)} · ${T.seeders(job.seeders)}` : statusLabel(job.status);

    // Completado → quitar del panel activo (aparecerá en biblioteca)
    if (job.status === "completed") {
        setTimeout(() => {
            row.remove();
            const remaining = el.activeList.querySelectorAll("[data-job-id]").length;
            el.activeCount.textContent = String(remaining);
            if (!remaining) el.activeEmpty.hidden = false;
        }, 2000);
    }
}

async function cancelJob(jobId) {
    try {
        const response = await fetch(`/api/downloads/jobs/${jobId}`, { method: "DELETE" });
        if (!response.ok) throw new Error("Could not cancel download");
        delete state.torrentJobs[jobId];
        el.activeList.querySelector(`[data-job-id="${jobId}"]`)?.remove();
        const remaining = el.activeList.querySelectorAll("[data-job-id]").length;
        el.activeCount.textContent = String(remaining);
        if (!remaining) el.activeEmpty.hidden = false;
    } catch (e) {
        console.error(e);
    }
}

// ------------------------------------------------------------------ //
// Library (files on disk)                                             //
// ------------------------------------------------------------------ //
function changeLibraryFilters(debounce = false) {
    clearTimeout(state.searchTimer);
    state.query = el.librarySearch.value.trim();
    state.mediaType = el.libraryType.value;
    state.sort = el.librarySort.value;
    state.origin = el.libraryOrigin?.value || "all";
    state.libraryPage = 1;
    state.filterRevision++;
    updateLibraryUrl();
    if (debounce) state.searchTimer = setTimeout(loadFiles, 250);
    else void loadFiles();
}

function changeLibraryPage(delta) {
    state.libraryPage = Math.max(1, state.libraryPage + delta);
    state.filterRevision++;
    updateLibraryUrl();
    void loadFiles();
}

function updateLibraryUrl() {
    const params = new URLSearchParams();
    if (state.query) params.set("q", state.query);
    if (state.mediaType !== "all") params.set("type", state.mediaType);
    if (state.sort !== "recent") params.set("sort", state.sort);
    if (state.origin !== "all") params.set("origin", state.origin);
    if (state.libraryPage > 1) params.set("page", String(state.libraryPage));
    const query = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
    if (el.libraryReset) el.libraryReset.disabled = !state.query && state.mediaType === "all" && state.sort === "recent" && state.origin === "all";
}

async function loadFiles() {
    if (state.filesLoading) {
        state.filesReloadPending = true;
        return;
    }
    state.filesLoading = true;
    const revision = state.filterRevision;
    const params = new URLSearchParams(`page=${state.libraryPage}`);
    if (state.query) params.set("q", state.query);
    if (state.mediaType !== "all") params.set("media_type", state.mediaType);
    params.set("sort", state.sort);
    params.set("origin", state.origin);
    el.libraryList.setAttribute("aria-busy", "true");
    if (el.librarySummary) el.librarySummary.textContent = T.searching;
    try {
        const data = await fetchDownloadJson(`/api/downloads/library?${params}`);
        // A slow response for the previous filter must not overwrite a newer search.
        if (revision !== state.filterRevision) return;
        state.libraryPage = data.page;
        updateLibraryUrl();
        if (el.libraryPrevious) el.libraryPrevious.disabled = data.page <= 1;
        if (el.libraryNext) el.libraryNext.disabled = data.page >= data.pages;
        if (el.libraryPage) el.libraryPage.textContent = `${data.page} / ${data.pages}`;
        state.files = data.files || [];
        if (el.libraryOrigin && Array.isArray(data.nodes)) {
            for (const node of data.nodes) {
                const value = `peer:${node.id}`;
                const option = Array.from(el.libraryOrigin.options).find(item => item.value === value);
                if (option) option.textContent = `${T.nodeOrigin} · ${node.name}`;
                else el.libraryOrigin.add(new Option(`${T.nodeOrigin} · ${node.name}`, value));
            }
            el.libraryOrigin.value = state.origin;
        }
        renderLibrary(data);
        state.filesNeedReload = false;
    } catch (e) {
        if (revision !== state.filterRevision) return;
        state.filesNeedReload = true;
        el.storageLabel.textContent = T.loadError;
        if (el.librarySummary) el.librarySummary.textContent = T.loadError;
        console.error("Error cargando archivos:", e);
    } finally {
        state.filesLoading = false;
        el.libraryList.setAttribute("aria-busy", "false");
        if (state.filesReloadPending) {
            state.filesReloadPending = false;
            void loadFiles();
        }
    }
}

function renderLibrary({ files, movies = [], series = [], used_bytes, quota_bytes, total }) {
    // Storage bar
    const pct = quota_bytes > 0 ? Math.min(100, (used_bytes / quota_bytes) * 100) : 0;
    el.storageBar.style.width = `${pct}%`;
    el.storageBar.className = `progress-bar-fill${pct > 85 ? " progress-bar-fill--warn" : ""}`;
    el.storageLabel.textContent = T.storage(formatBytes(used_bytes), formatBytes(quota_bytes));

    el.libraryCount.textContent = String(total ?? files.length);
    if (el.librarySummary) el.librarySummary.textContent = T.results(total ?? files.length);
    const filtered = !!state.query || state.mediaType !== "all" || state.origin !== "all";
    if (el.libraryEmptyTitle) el.libraryEmptyTitle.textContent = filtered ? T.noResults : T.emptyLibrary;
    if (el.libraryEmptyDescription) el.libraryEmptyDescription.textContent = filtered ? T.noResultsHint : T.emptyLibraryHint;

    if (!files.length) {
        el.libraryList.innerHTML = "";
        el.libraryEmpty.hidden = false;
        return;
    }
    el.libraryEmpty.hidden = true;
    el.libraryList.innerHTML = [
        movies.length ? buildLibrarySection(LANG === "es" ? "Películas" : "Movies", movies) : "",
        series.length ? buildSeriesSection("Series", series) : "",
    ].join("");
    // Delegate clicks within the library container
    el.libraryList.onclick = (e) => {
        // Expand/collapse a series show.
        const showToggle = e.target.closest("[data-toggle-show]");
        if (showToggle) {
            const body = showToggle.nextElementSibling;
            if (body) body.hidden = !body.hidden;
            const open = body && !body.hidden;
            showToggle.setAttribute("aria-expanded", String(!!open));
            showToggle.classList.toggle("series-group__header--open", !!open);
            const title = showToggle.closest("[data-show]").dataset.show;
            if (open) state.openShows.add(title);
            else state.openShows.delete(title);
            return;
        }
        const playBtn = e.target.closest("[data-play]");
        if (playBtn) {
            const file = state.files.find((item) => item.relative_path === playBtn.dataset.play);
            if (file) openLocalPlayer(file);
            return;
        }
        const deviceBtn = e.target.closest("[data-device-download]");
        if (deviceBtn) {
            const file = state.files.find(item => item.relative_path === deviceBtn.dataset.deviceDownload);
            if (file) window.SVOfflineUI.download(file);
            return;
        }
        const delBtn = e.target.closest("[data-delete]");
        if (delBtn) {
            confirmDelete(delBtn.dataset.delete, delBtn.dataset.name);
            return;
        }
        const prevBtn = e.target.closest("[data-preview]");
        if (prevBtn) {
            openPreview(prevBtn.dataset.preview, prevBtn.dataset.previewName);
            return;
        }
        const card = e.target.closest("[data-file-card]");
        if (card) {
            // Clicking the card body (not a button) triggers play for videos, preview for others
            const path = card.dataset.fileCard;
            const file = state.files.find((item) => item.relative_path === path);
            if (file) {
                if (file.relative_path.match(/\.(jpg|jpeg|png|webp|gif|bmp|txt|srt|vtt|nfo|json|ass|ssa|sub)$/i)) {
                    openPreview(path, file.name);
                } else {
                    openLocalPlayer(file);
                }
            }
        }
    };
}

function buildLibrarySection(title, files) {
    return `
        <section class="local-library__section">
            <div class="section-header section-header--compact"><strong>${escHtml(title)}</strong><span class="badge badge--neutral">${files.length}</span></div>
            <div class="fm-list">${files.length ? files.map(buildFileRow).join("") : `<div class="download-option-status">${T.noFiles}</div>`}</div>
        </section>`;
}

/** Group series episodes by title → season → episodes and render as
 *  collapsible show rows. Clicking a show expands to reveal its seasons. */
function buildSeriesSection(title, files) {
    if (!files.length) {
        return `
        <section class="local-library__section">
            <div class="section-header section-header--compact"><strong>${escHtml(title)}</strong><span class="badge badge--neutral">0</span></div>
            <div class="download-option-status">${T.noFiles}</div>
        </section>`;
    }

    // Group by normalized title, then by season.
    const shows = new Map();
    for (const file of files) {
        const key = file.title || file.name;
        if (!shows.has(key)) shows.set(key, { title: key, seasons: {} });
        const show = shows.get(key);
        const sn = file.season_number ?? 1;
        if (!show.seasons[sn]) show.seasons[sn] = [];
        show.seasons[sn].push(file);
    }

    // Keep server-side show order; sort seasons and episodes within each show.
    const showList = [...shows.values()];
    for (const show of showList) {
        for (const sn of Object.keys(show.seasons)) {
            show.seasons[sn].sort((a, b) =>
                (a.episode_number || 0) - (b.episode_number || 0));
        }
    }

    const totalEpisodes = files.length;
    const cards = showList.map(show => {
        const open = state.openShows.has(show.title);
        const seasonNums = Object.keys(show.seasons).map(Number).sort((a, b) => a - b);
        const epCount = seasonNums.reduce((sum, sn) => sum + show.seasons[sn].length, 0);

        // Build season rows with episode lists.
        const seasonRows = seasonNums.map(sn => {
            const eps = show.seasons[sn];
            const epRows = eps.map(buildFileRow).join("");
            return `
                <div class="series-group__season">
                    <div class="series-group__season-header">
                        <span>${LANG === "es" ? "Temporada" : "Season"} ${sn}</span>
                        <span class="badge badge--neutral">${eps.length}</span>
                    </div>
                    <div class="fm-list series-group__episodes">${epRows}</div>
                </div>`;
        }).join("");

        return `
        <div class="series-group" data-show="${escHtml(show.title)}">
            <button class="series-group__header${open ? " series-group__header--open" : ""}" type="button" data-toggle-show aria-expanded="${open}">
                <svg class="series-group__chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg>
                <span class="series-group__title">${escHtml(show.title)}</span>
                <span class="badge badge--neutral">${epCount} ${LANG === "es" ? "episodios" : "episodes"}</span>
            </button>
            <div class="series-group__body"${open ? "" : " hidden"}>${seasonRows}</div>
        </div>`;
    }).join("");

    return `
    <section class="local-library__section">
        <div class="section-header section-header--compact">
            <strong>${escHtml(title)}</strong>
            <span class="badge badge--neutral">${totalEpisodes}</span>
        </div>
        <div class="series-groups">${cards}</div>
    </section>`;
}

/** Compact file-manager row — replaces the large card grid for scalability. */
function buildFileRow(file) {
    const name = file.name;
    const size = formatBytes(file.size_bytes);
    const date = new Date(file.modified_at * 1000).toLocaleDateString();
    const episode = file.media_type === "series"
        ? `S${String(file.season_number).padStart(2, "0")}E${String(file.episode_number).padStart(2, "0")}` : "";
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    const remote = file.origin === "federation";
    const isVideo = remote || ["mp4", "mkv", "avi", "mov", "ts", "webm", "m4v"].includes(ext);
    const isImage = !remote && ["jpg", "jpeg", "png", "webp", "gif", "bmp"].includes(ext);
    const isText = !remote && ["txt", "srt", "vtt", "nfo", "json", "ass", "ssa", "sub"].includes(ext);
    const kindClass = isVideo ? "video" : isImage ? "image" : isText ? "subtitle" : "other";
    const kindLabel = isVideo ? "VIDEO" : isImage ? "IMAGE" : isText ? "TEXT" : "FILE";

    // Action buttons: play for videos, preview for images/text, delete for all
    let actions = "";
    if (isVideo) {
        actions += `<button class="fm-row__btn fm-row__btn--play" type="button" data-play="${escHtml(file.relative_path)}" data-name="${escHtml(name)}" title="${T.play}"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></button>`;
        const deviceLabel = LANG === "es" ? "Guardar en dispositivo" : "Save to device";
        actions += `<button class="fm-row__btn" type="button" data-device-download="${escHtml(file.relative_path)}" title="${deviceLabel}" aria-label="${deviceLabel}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0-4-4m4 4 4-4M4 19h16"/></svg></button>`;
    }
    if (isImage || isText) {
        actions += `<button class="fm-row__btn" type="button" data-preview="${escHtml(file.relative_path)}" data-preview-name="${escHtml(name)}" title="Preview"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>`;
    }
    if (window.IS_ADMIN && !remote) {
        actions += `<button class="fm-row__btn fm-row__btn--danger" type="button" data-delete="${escHtml(file.relative_path)}" data-name="${escHtml(name)}" title="${T.delete}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg></button>`;
    }

    return `
    <div class="fm-row" data-file-card="${escHtml(file.relative_path)}">
        <span class="fm-row__kind fm-row__kind--${kindClass}">${kindLabel}</span>
        <div class="fm-row__info">
            <div class="fm-row__name" title="${escHtml(file.title || file.name)}">${escHtml(file.title || file.name)}</div>
            <div class="fm-row__meta">${episode ? `${episode} · ` : ""}${escHtml(size)} · ${remote ? escHtml(file.quality || "") : escHtml(date)}</div>
            <span class="fm-row__origin">${escHtml(remote ? `${T.nodeOrigin} · ${file.peer_name}` : T.localOrigin)}</span>
        </div>
        <div class="fm-row__actions">${actions}</div>
    </div>`;
}

async function openLocalPlayer(file, selectedStreamIndex) {
    const start = el.player.dataset.path === file.relative_path ? window.SVLocalPlayback.currentTime(el.player) : 0;
    const attempt = (state.playerAttempt || 0) + 1;
    state.playerAttempt = attempt;
    window.SVLocalPlayback.close(el.player);
    const params = new URLSearchParams({ title: file.title, media_type: file.media_type, path: file.relative_path });
    if (file.media_type === "series") {
        params.set("season", file.season_number);
        params.set("episode", file.episode_number);
    }
    let source;
    try {
        if (file.origin === "federation") {
            const metadata = await fetchDownloadJson(file.metadata_url);
            source = {...file, ...metadata, relative_path: undefined};
        } else {
            source = (await fetchDownloadJson(`/api/downloads/files/match?${params}`)).match;
        }
    } catch (error) {
        if (attempt === state.playerAttempt) {
            el.playerTitle.textContent = error.message;
            el.playerOverlay.style.display = "flex";
        }
        return;
    }
    if (attempt !== state.playerAttempt || !source) return;
    const audioTracks = source?.audio_tracks || [];
    const chosen = Number.isFinite(selectedStreamIndex) ? selectedStreamIndex : audioTracks[0]?.stream_index;

    el.playerTitle.textContent = file.title || file.name;
    el.player.dataset.path = file.relative_path;
    el.player.innerHTML = "";
    (source?.subtitles || []).forEach((subtitle) => {
        const track = document.createElement("track");
        track.kind = "subtitles";
        track.label = subtitle.label || subtitle.language;
        track.srclang = subtitle.language || "und";
        track.src = subtitle.url;
        el.player.appendChild(track);
    });
    el.playerAudioWrapper.hidden = audioTracks.length <= 1;
    el.playerAudio.innerHTML = audioTracks.map((track) =>
        `<option value="${track.stream_index}"${track.stream_index === chosen ? " selected" : ""}>${escHtml(track.title || track.language.toUpperCase())} · ${escHtml(track.codec)}</option>`
    ).join("");
    el.playerOverlay.style.display = "flex";
    try {
        await window.SVLocalPlayback.open(el.player, source, {
            audio: chosen, start,
            onError: (error) => { el.playerTitle.textContent = error.message; },
        });
    } catch (error) {
        el.playerTitle.textContent = error.message;
    }
}

function closeLocalPlayer() {
    state.playerAttempt = (state.playerAttempt || 0) + 1;
    window.SVLocalPlayback.close(el.player);
    el.player.removeAttribute("data-path");
    el.playerAudioWrapper.hidden = true;
    el.playerOverlay.style.display = "none";
}

/* ── File preview overlay (images, text, subtitles) ── */
async function openPreview(path, name) {
    el.previewTitle.textContent = name;
    el.previewBody.innerHTML = `<div class="spinner" style="margin:2rem auto;"></div>`;
    el.previewOverlay.classList.add("preview-overlay--open");

    const ext = (path.split(".").pop() || "").toLowerCase();
    const previewUrl = `/api/downloads/files/preview?path=${encodeURIComponent(path)}`;

    try {
        if (["jpg", "jpeg", "png", "webp", "gif", "bmp"].includes(ext)) {
            el.previewBody.innerHTML = `<img src="${previewUrl}" alt="${escHtml(name)}">`;
        } else {
            const resp = await fetch(previewUrl);
            if (!resp.ok) throw new Error("Preview failed");
            const text = await resp.text();
            el.previewBody.innerHTML = `<pre>${escHtml(text)}</pre>`;
        }
    } catch (e) {
        el.previewBody.innerHTML = `<p style="color:var(--danger);padding:2rem;">${escHtml(e.message)}</p>`;
    }
}

function closePreview() {
    el.previewOverlay.classList.remove("preview-overlay--open");
    el.previewBody.innerHTML = "";
}

function confirmDelete(path, name) {
    el.confirmMessage.textContent = T.confirmDelete(name);
    el.confirmOverlay.style.display = "flex";

    const cleanup = () => { el.confirmOverlay.style.display = "none"; };
    el.confirmOk.onclick = async () => {
        cleanup();
        try {
            await fetch(`/api/downloads/library?path=${encodeURIComponent(path)}`, { method: "DELETE" });
            await loadFiles();
        } catch (e) {
            console.error(e);
        }
    };
    el.confirmCancel.onclick = cleanup;
}

// ------------------------------------------------------------------ //
// Helpers                                                              //
// ------------------------------------------------------------------ //
function statusLabel(status) {
    const map = {
        pending: T.statusPending,
        connecting: T.statusConnecting,
        downloading: T.statusDownloading,
        completed: T.statusCompleted,
        error: T.statusError,
        cancelled: T.statusCancelled,
    };
    return map[status] || status;
}

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / 1024 ** i).toFixed(1)} ${units[i]}`;
}

function formatEta(seconds) {
    if (!seconds) return "–";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}h${String(m).padStart(2, "0")}m`;
    if (m > 0) return `${m}m${String(s).padStart(2, "0")}s`;
    return `${s}s`;
}

function escHtml(v) {
    return String(v)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

document.addEventListener("DOMContentLoaded", boot);
