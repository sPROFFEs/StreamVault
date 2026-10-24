/* media_detail.js — Full-page detail view for movies / TV / anime */

const LANG      = window.APP_LANG === "es" ? "es" : "en";
const TMDB_LANG = LANG === "es" ? "es-ES" : "en-US";
const TMDB_ID   = window.MEDIA_TMDB_ID;
const MTYPE     = (window.MEDIA_TYPE === "series" ? "tv" : window.MEDIA_TYPE) || "movie";

const T = {
    en: {
        loading: "Loading…",
        play: "Play", save: "Save", removeFav: "Saved ✓",
        download: "Download", fullscreen: "Fullscreen",
        noSynopsis: "No synopsis available.",
        movie: "Movie", series: "Series", unrated: "unrated",
        seasonLabel: (n) => `Season ${n}`,
        episodeLabel: (n, t) => `E${n} · ${t}`,
        untitled: "Untitled",
        searchingSources: "Looking for sources…",
        sourcesFound: (n) => `${n} sources found.`,
        noSources: "No sources found. Check your TMDB key or try later.",
        resolverError: "Could not load sources (timeout or error).",
        torrentConnecting: "Connecting to peers…",
        torrentBuffering: (p, s, n) => `${p}% · ${s} · ${n} peers`,
        torrentReady: "Stream ready",
        noBrowserTorrent: "WebTorrent not supported here — use Download instead.",
        sourceLoadError: "Error loading this source.",
        sourceFallback: (name) => `Source failed. Trying ${name}…`,
        downloadQueued: "Queued. You'll be notified when ready.",
        downloadFailed: "Download could not start.",
        downloadConnErr: "Connection error starting download.",
        chooseDownload: "Choose a download source",
        extractingServer: "Extract from active streaming server",
        findingTorrents: "Finding Torrentio downloads…",
        noTorrents: "No Torrentio downloads found. You can still try the active server.",
        seeders: (n) => `${n || 0} seeders`,
        weakSwarm: "Weak swarm — this may be very slow",
        playFirst: "Play a source first to download via yt-dlp.",
        continueLabel: (t) => `Continue · ${t}`,
        notifyTitle: "Download complete",
        notifyBody: (t) => `"${t}" is ready for offline playback.`,
        saveErr: "Could not update favorites.",
        audioTrack: (i) => `Track ${i + 1}`,
        checkingSubs: "Checking subtitles…",
        checking: "checking", pending: "pending",
        tracks: (n) => `${n} tracks`,
        noLang: "no language", notIndexed: "not indexed", manual: "manual",
        subErr: "Subtitle query failed.",
        back: "← Catalog",
        subOverlayActive: "Subtitles overlay active for embed player.",
        subOffset: (ms) => `Offset: ${(ms / 1000).toFixed(1)}s`,
        subSyncHint: "Use ← / → to adjust subtitle timing.",
        subSyncHintVideo: "Use ← / → to nudge subtitle sync.",
        subAutoOn: (lang) => `Auto-enabled ${lang} subtitles.`,
    },
    es: {
        loading: "Cargando…",
        play: "Reproducir", save: "Guardar", removeFav: "Guardado ✓",
        download: "Descargar", fullscreen: "Pantalla completa",
        noSynopsis: "Sin sinopsis disponible.",
        movie: "Pelicula", series: "Serie", unrated: "sin rating",
        seasonLabel: (n) => `Temporada ${n}`,
        episodeLabel: (n, t) => `E${n} · ${t}`,
        untitled: "Sin titulo",
        searchingSources: "Buscando fuentes…",
        sourcesFound: (n) => `${n} fuentes encontradas.`,
        noSources: "Sin fuentes. Comprueba tu clave TMDB o inténtalo más tarde.",
        resolverError: "No se cargaron fuentes (timeout o error).",
        torrentConnecting: "Conectando con peers…",
        torrentBuffering: (p, s, n) => `${p}% · ${s} · ${n} peers`,
        torrentReady: "Stream listo",
        noBrowserTorrent: "WebTorrent no soportado — usa Descargar.",
        sourceLoadError: "Error cargando esta fuente.",
        sourceFallback: (name) => `La fuente falló. Probando ${name}…`,
        downloadQueued: "En cola. Recibirás notificación al terminar.",
        downloadFailed: "No se pudo iniciar la descarga.",
        downloadConnErr: "Error de conexión al iniciar descarga.",
        chooseDownload: "Elige una fuente de descarga",
        extractingServer: "Extraer del servidor de streaming activo",
        findingTorrents: "Buscando descargas en Torrentio…",
        noTorrents: "No hay descargas de Torrentio. Aún puedes probar el servidor activo.",
        seeders: (n) => `${n || 0} seeders`,
        weakSwarm: "Swarm débil — puede ser muy lento",
        playFirst: "Reproduce una fuente primero para descargar via yt-dlp.",
        continueLabel: (t) => `Continuar · ${t}`,
        notifyTitle: "Descarga completa",
        notifyBody: (t) => `"${t}" disponible sin conexión.`,
        saveErr: "No se pudo actualizar favoritos.",
        audioTrack: (i) => `Pista ${i + 1}`,
        checkingSubs: "Consultando subtitulos…",
        checking: "revisando", pending: "pendiente",
        tracks: (n) => `${n} pistas`,
        noLang: "sin idioma", notIndexed: "sin indexar", manual: "manual",
        subErr: "Error consultando subtitulos.",
        back: "← Catalogo",
        subOverlayActive: "Capa de subtitulos activa para reproductor embed.",
        subOffset: (ms) => `Desfase: ${(ms / 1000).toFixed(1)}s`,
        subSyncHint: "Usa ← / → para ajustar el tiempo de los subtitulos.",
        subSyncHintVideo: "Usa ← / → para ajustar la sincronía.",
        subAutoOn: (lang) => `Subtitulos en ${lang} activados automaticamente.`,
    },
}[LANG];

/* ------------------------------------------------------------------ */
/* State                                                               */
/* ------------------------------------------------------------------ */
const state = {
    item: null,
    streams: [],
    streamsLoading: false,
    streamController: null,
    currentProgress: null,
    isFavorite: false,
    hls: null,
    wtClient: null,
    progressTimer: null,
    torrentPeerTimer: null,
    torrentStatsTimer: null,
    iframeTimer: null,
    sourceAttempt: 0,
    failedSources: new Set(),
    autoSourceFailover: true,
    downloadSubmitting: false,
    activeLocalSource: null,
    remoteSubtitles: [],
    localSubtitles: [],
    activeSubtitleKey: null,
    subtitleBlobUrl: null,
    subtitleController: null,
    localPlaybackOffset: 0,
    embedSubtitles: null,
    embedOverlayRaf: null,
    embedSubtitleOffset: 0,
    subtitleLangPref: null,
    subtitleAutoActivated: false,
    videoSubtitles: null,
    videoSubtitleOffset: 0,
    _videoSubTimeUpdate: null,
    _videoSubKeyHandler: null,
};

const el = {
    playerContainer:   document.getElementById("player-container"),
    torrentStatus:     document.getElementById("torrent-status"),
    torrentBar:        document.getElementById("torrent-bar"),
    torrentInfo:       document.getElementById("torrent-info"),
    playBtn:           document.getElementById("play-button"),
    favoriteBtn:       document.getElementById("favorite-button"),
    downloadBtn:       document.getElementById("download-button"),
    fullscreenBtn:     document.getElementById("fullscreen-button"),
    detailStatus:      document.getElementById("detail-status"),
    detailTitle:       document.getElementById("detail-title"),
    detailMeta:        document.getElementById("detail-meta"),
    detailDescription: document.getElementById("detail-description"),
    detailPoster:      document.getElementById("detail-poster"),
    seriesControls:    document.getElementById("series-controls"),
    seasonSelect:      document.getElementById("season-select"),
    episodeSelect:     document.getElementById("episode-select"),
    sourceSelect:      document.getElementById("source-select"),
    audioWrapper:      document.getElementById("audio-wrapper"),
    audioSelect:       document.getElementById("audio-select"),
    subtitleBadge:     document.getElementById("subtitle-badge"),
    subtitleSummary:   document.getElementById("subtitle-summary"),
    subtitleList:      document.getElementById("subtitle-list"),
    subtitleLangSelect: document.getElementById("subtitle-lang-select"),
    toastContainer:    document.getElementById("toast-container"),
    downloadPicker:    document.getElementById("download-picker"),
    downloadOptions:   document.getElementById("download-options"),
    downloadPickerClose: document.getElementById("download-picker-close"),
};

/* ------------------------------------------------------------------ */
/* Boot                                                                */
/* ------------------------------------------------------------------ */
async function boot() {
    if (!TMDB_ID) { el.detailTitle.textContent = "404 – no TMDB ID"; return; }

    connectNotificationSse();

    el.playBtn.addEventListener("click", playCurrentItem);
    el.favoriteBtn.addEventListener("click", toggleFavorite);
    el.downloadBtn.addEventListener("click", openDownloadPicker);
    document.getElementById("device-download-button")?.addEventListener("click", saveCurrentToDevice);
    el.downloadPickerClose?.addEventListener("click", closeDownloadPicker);
    el.downloadPicker?.addEventListener("click", (event) => {
        if (event.target === el.downloadPicker) closeDownloadPicker();
    });
    el.fullscreenBtn.addEventListener("click", requestFullscreen);

    // Cinematic placeholder play button
    const placeholderPlay = document.querySelector(".player-placeholder__play");
    if (placeholderPlay) placeholderPlay.addEventListener("click", playCurrentItem);

    initSubtitleSize();

    // Sync pseudo-fullscreen state with native fullscreen changes (Esc key,
    // F11, etc.).  If native fullscreen exits, exit pseudo-fullscreen too.
    document.addEventListener("fullscreenchange", _onFsChange);
    document.addEventListener("webkitfullscreenchange", _onFsChange);

    el.sourceSelect.addEventListener("change", onSourceChange);
    el.subtitleLangSelect?.addEventListener("change", async () => {
        state.subtitleLangPref = el.subtitleLangSelect.value;
        state.subtitleAutoActivated = false;
        try {
            await window.SVAppearance.savePrefs({ subtitle_lang: el.subtitleLangSelect.value });
        } catch (_) {}
        await loadSubtitles();
        maybeAutoActivateSubtitle();
    });
    window.addEventListener("message", onPlayerMessage);
    el.audioSelect?.addEventListener("change", () => {
        if (state.hls) state.hls.audioTrack = Number(el.audioSelect.value || 0);
        if (state.activeLocalSource) {
            const source = state.activeLocalSource;
            const video = el.playerContainer.querySelector("video");
            const absoluteTime = state.localPlaybackOffset + Number(video?.currentTime || 0);
            state.currentProgress = { ...state.currentProgress, progress_seconds: absoluteTime };
            destroyPlayer();
            loadLocalSource(source, Number(el.audioSelect.value));
        }
    });

    if (MTYPE === "tv") {
        el.seriesControls.style.display = "grid";
        el.seasonSelect.addEventListener("change", async () => {
            await loadEpisodes();
            await Promise.all([loadProgress(), loadSubtitles()]);
            autoLoadStreams();
        });
        el.episodeSelect.addEventListener("change", async () => {
            await Promise.all([loadProgress(), loadSubtitles()]);
            autoLoadStreams();
        });
    }

    const back = document.getElementById("detail-back");
    try {
        const saved = JSON.parse(sessionStorage.getItem("sv_catalog_return"));
        if (saved && saved.detail === location.pathname + location.search && /^\/browse(?:\?|$)/.test(saved.url)) back.href = saved.url;
        else if (document.referrer) {
            const previous = new URL(document.referrer);
            if (previous.origin === location.origin && ["/browse", "/portal"].includes(previous.pathname)) back.href = previous.pathname + previous.search;
        }
    } catch (_) {}
    document.getElementById("retry-detail").addEventListener("click", loadDetailMetadata);
    await loadDetailMetadata();
}

async function loadDetailMetadata() {
    const retrying = document.activeElement?.id === "retry-detail";
    document.getElementById("detail-error").hidden = true;
    document.getElementById("retry-detail").disabled = true;
    el.favoriteBtn.disabled = true;
    el.detailTitle.textContent = T.loading;
    const loaded = await fetchItem();
    document.getElementById("retry-detail").disabled = false;
    if (!loaded) {
        document.getElementById("detail-error").hidden = false;
        return false;
    }
    el.favoriteBtn.disabled = false;
    if (retrying) el.favoriteBtn.focus();
    if (MTYPE === "tv") await loadSeasons();

    // Parallel: progress + subtitles + favorites check
    await Promise.all([loadProgress(), loadSubtitles(), checkFavorite()]);

    try {
        const prefs = await window.SVAppearance.loadPrefs();
        if (prefs) {
            state.autoSourceFailover = prefs.auto_source_failover !== false;
            state.subtitleLangPref = prefs.subtitle_lang || null;
            if (state.subtitleLangPref && el.subtitleLangSelect) {
                el.subtitleLangSelect.value = state.subtitleLangPref;
            }
        }
    } catch (_) {}

    // Auto-load stream sources
    autoLoadStreams();
    return true;
}

/* ------------------------------------------------------------------ */
/* TMDB metadata                                                       */
/* ------------------------------------------------------------------ */
async function fetchItem() {
    try {
        const ep = MTYPE === "tv"
            ? `/api/tmdb/tv/${TMDB_ID}?lang=${encodeURIComponent(TMDB_LANG)}`
            : `/api/tmdb/movie/${TMDB_ID}?lang=${encodeURIComponent(TMDB_LANG)}`;
        const resp = await fetch(ep);
        if (!resp.ok) throw new Error(`Metadata request failed: ${resp.status}`);
        const data = await resp.json();
        state.item = data;

        const title  = data.title || data.name || T.untitled;
        const year   = (data.release_date || data.first_air_date || "").split("-")[0];
        const score = Number(data.vote_average || 0);
        const rating = Number.isFinite(score) && score > 0 ? `${score.toFixed(1)}/10` : T.unrated;
        const runtime = Number(data.runtime || data.episode_run_time?.[0] || 0);
        const tLabel = MTYPE === "tv" ? T.series : T.movie;

        document.title = `${title} · StreamVault`;
        el.detailTitle.textContent = title;
        el.detailMeta.textContent  = [tLabel, year, rating, runtime > 0 ? `${runtime} min` : ""].filter(Boolean).join(" · ");
        el.detailDescription.textContent = data.overview || T.noSynopsis;
        const genres = document.getElementById("detail-genres");
        if (genres) genres.textContent = (data.genres || []).map(genre => genre.name).filter(Boolean).join(" · ");

        const image = document.createElement("img");
        image.width = 400;
        image.height = 600;
        image.decoding = "async";
        const placeholder = document.createElement("span");
        placeholder.textContent = LANG === "es" ? "Sin carátula" : "No artwork";
        el.detailPoster.replaceChildren(image, placeholder);
        window.SVArtwork.setImage(image, data.poster_path || data.poster_url, { alt: title, placeholder });

        // Set the cinematic backdrop on the player placeholder.
        const bgImg = data.backdrop_path || data.poster_path;
        if (bgImg) {
            const ph = document.getElementById("player-placeholder");
            if (ph) {
                window.SVArtwork.setBackdrop(ph, bgImg);
            }
            const pageBg = document.getElementById("detail-page-bg");
            if (pageBg) {
                window.SVArtwork.setBackdrop(pageBg, data.backdrop_path);
            }
        }
        return true;
    } catch (e) {
        el.detailTitle.textContent = LANG === "es" ? "No se pudo cargar" : "Could not load details";
        return false;
    }
}

/* ------------------------------------------------------------------ */
/* Seasons / episodes                                                  */
/* ------------------------------------------------------------------ */
async function loadSeasons() {
    el.seasonSelect.innerHTML = `<option>${T.loading}</option>`;
    try {
        const data = state.item;
        const seasons = (data.seasons || []).filter(s => s.season_number > 0);
        el.seasonSelect.innerHTML = seasons.map(s =>
            `<option value="${s.season_number}">${T.seasonLabel(s.season_number)}</option>`
        ).join("");

        const params = new URLSearchParams(window.location.search);
        const qs = params.get("s");
        if (qs) el.seasonSelect.value = qs;

        await loadEpisodes(params.get("e"));
    } catch {
        el.seasonSelect.innerHTML = `<option>Error</option>`;
    }
}

async function loadEpisodes(defaultEp) {
    const season = el.seasonSelect.value;
    if (!season) return;
    el.episodeSelect.innerHTML = `<option>${T.loading}</option>`;
    try {
        const resp = await fetch(`/api/tmdb/tv/${TMDB_ID}/season/${season}?lang=${encodeURIComponent(TMDB_LANG)}`);
        const data = await resp.json();
        el.episodeSelect.innerHTML = (data.episodes || []).map(ep =>
            `<option value="${ep.episode_number}">${T.episodeLabel(ep.episode_number, escH(ep.name || T.untitled))}</option>`
        ).join("");
        if (defaultEp) el.episodeSelect.value = defaultEp;
    } catch {
        el.episodeSelect.innerHTML = `<option>Error</option>`;
    }
}

/* ------------------------------------------------------------------ */
/* Stream auto-loading                                                 */
/* ------------------------------------------------------------------ */
async function autoLoadStreams() {
    if (state.streamController) state.streamController.abort();
    const ctrl = new AbortController();
    state.streamController = ctrl;

    state.streams = [];
    updateDeviceDownloadButton();
    state.streamsLoading = true;
    el.sourceSelect.innerHTML = `<option>${T.searchingSources}</option>`;
    el.detailStatus.textContent = T.searchingSources;

    const params = new URLSearchParams({ type: MTYPE });
    const title = state.item?.title || state.item?.name;
    if (title) params.set("title", title.slice(0, 255));
    const originalTitle = state.item?.original_title || state.item?.original_name;
    if (originalTitle) params.set("original_title", originalTitle.slice(0, 255));
    const year = Number(String(state.item?.release_date || "").slice(0, 4));
    if (MTYPE === "movie" && year >= 1800 && year <= 2200) params.set("year", String(year));
    if (MTYPE === "tv") {
        params.set("s", el.seasonSelect.value || "1");
        params.set("e", el.episodeSelect.value || "1");
    }

    const tid = setTimeout(() => ctrl.abort(), 22000);
    
    // Completed local and peer files are independent of external providers.
    const [localSource, federatedSources] = await Promise.all([
        findLocalSource(), findFederatedSources(params, ctrl.signal),
    ]);
    const immediateSources = [...(localSource ? [localSource] : []), ...federatedSources];
    if (ctrl.signal.aborted) return;
    
    if (localSource) {
        state.localSubtitles = localSource.subtitles || [];
        renderAvailableSubtitles();
        repairLocalProgressDuration(localSource);
    } else {
        state.localSubtitles = [];
    }
    if (immediateSources.length) {
        state.streams = immediateSources;
        el.sourceSelect.innerHTML = immediateSources.map((source, index) => `<option value="${index}">${escH(buildSourceLabel(source))}</option>`).join("");
        updateDeviceDownloadButton();
        el.detailStatus.textContent = T.sourcesFound(immediateSources.length);
        if (state._playPending) {
            state._playPending = false;
            playCurrentItem();
        }
    }

    // 2. Fetch external streams in background
    try {
        const resp = await fetch(`/api/stream/resolve/${TMDB_ID}?${params}`, { signal: ctrl.signal });
        clearTimeout(tid);
        if (ctrl.signal.aborted) return;
        const payload = await resp.json();
        const urls = new Set(immediateSources.map(source => source.url));
        const remoteStreams = Array.isArray(payload) ? payload.filter(source => !urls.has(source.url)) : [];
        const selectedUrl = state.streams[Number(el.sourceSelect.value || 0)]?.url;
        state.streams = [...immediateSources, ...remoteStreams];
        state.failedSources.clear();
        state.streamsLoading = false;
        
        if (!state.streams.length) {
            el.detailStatus.textContent = T.noSources;
            el.sourceSelect.innerHTML = `<option>${T.noSources}</option>`;
            return;
        }

        el.detailStatus.textContent = T.sourcesFound(state.streams.length);
        el.sourceSelect.innerHTML = state.streams.map((s, i) => `<option value="${i}">${escH(buildSourceLabel(s))}</option>`).join("");
        const selectedIndex = state.streams.findIndex(source => source.url === selectedUrl);
        if (selectedIndex >= 0) el.sourceSelect.value = String(selectedIndex);
        updateDeviceDownloadButton();
        maybeAutoActivateSubtitle();
        
        // If user clicked play while waiting, play the first available stream now!
        if (state._playPending) {
            state._playPending = false;
            playCurrentItem();
        }
    } catch (err) {
        clearTimeout(tid);
        if (immediateSources.length) {
            state.streamsLoading = false;
            el.detailStatus.textContent = T.sourcesFound(1);
            return;
        }
        if (!ctrl.signal.aborted) {
            state.streamsLoading = false;
            el.detailStatus.textContent = T.resolverError;
            el.sourceSelect.innerHTML = `<option>${T.resolverError}</option>`;
        }
    }
}

async function findFederatedSources(params, signal) {
    try {
        const response = await fetch(`/api/federation/sources/${TMDB_ID}?${params}`, {signal});
        if (!response.ok) return [];
        const sources = await response.json();
        return Array.isArray(sources) ? sources : [];
    } catch (_) { return []; }
}

async function findLocalSource() {
    const title = state.item?.title || state.item?.name;
    if (!title) return null;
    const params = new URLSearchParams({ title, media_type: MTYPE });
    if (MTYPE === "tv") {
        params.set("season", el.seasonSelect.value || "1");
        params.set("episode", el.episodeSelect.value || "1");
    } else {
        const releaseYear = state.item?.release_date
            ? new Date(state.item.release_date).getFullYear()
            : (state.item?.year || null);
        if (releaseYear && !isNaN(releaseYear)) {
            params.set("year", releaseYear);
        }
    }
    try {
        const response = await fetch(`/api/downloads/files/match?${params}`);
        const data = response.ok ? await response.json() : null;
        if (!data?.match) return null;
        return {
            ...data.match,
            type: "local",
            source: LANG === "es" ? "Este servidor" : "This server",
            quality: LANG === "es" ? "Descargado" : "Downloaded",
        };
    } catch (_) {
        return null;
    }
}

function buildSourceLabel(s) {
    const icon = s.type === "local" ? "●" : s.type === "federation" ? "⇄" : s.type === "torrent" ? "🧲" : s.type === "external" ? "🔗" : "🌐";
    const parts = [icon, s.source || "resolver", s.quality || "auto"];
    if (s.type === "torrent" && s.seeders)    parts.push(`👤 ${s.seeders}`);
    if (s.type === "torrent" && s.size_bytes) parts.push(fmtBytes(s.size_bytes));
    if (s.type === "external") parts.push("(new tab)");
    return parts.join(" · ");
}

/* ------------------------------------------------------------------ */
/* Playback                                                            */
/* ------------------------------------------------------------------ */
async function playCurrentItem() {
    if (!state.streams.length) {
        if (state.streamsLoading) {
            state._playPending = true;
            el.detailStatus.textContent = T.searchingSources + " (Iniciando...)";
            return;
        }
        await autoLoadStreams();
        if (!state.streams.length) return;
    }
    destroyPlayer();
    const src = state.streams[Number(el.sourceSelect.value || 0)];
    if (src) loadSource(src);
}

function onSourceChange() {
    updateDeviceDownloadButton();
    const src = state.streams[Number(el.sourceSelect.value || 0)];
    if (src) {
        state.failedSources.clear();
        saveSourcePreference(src);
        destroyPlayer();
        loadSource(src);
    }
}

function updateDeviceDownloadButton() {
    const button = document.getElementById("device-download-button");
    if (button) button.disabled = !["local", "federation"].includes(state.streams[Number(el.sourceSelect.value || 0)]?.type);
}

function saveCurrentToDevice() {
    const selected = state.streams[Number(el.sourceSelect.value || 0)];
    if (!selected || !["local", "federation"].includes(selected.type)) return;
    const active = state.activeLocalSource;
    const isActive = active && (active.playback_url || active.relative_path) === (selected.playback_url || selected.relative_path);
    const source = isActive ? active : selected;
    window.SVOfflineUI.download({...source, title: state.item?.title || state.item?.name || "Video",
        peer_name: source.peer_name || source.source, media_type: MTYPE,
        tmdb_id: TMDB_ID, overview: state.item?.overview, year: state.item?.release_date?.slice(0, 4) || state.item?.first_air_date?.slice(0, 4),
        poster_path: state.item?.poster_path || state.item?.poster_url, backdrop_path: state.item?.backdrop_path,
        genres: (state.item?.genres || []).map(genre => genre.name),
        ...(MTYPE === "tv" ? {season_number: Number(el.seasonSelect.value), episode_number: Number(el.episodeSelect.value)} : {}),
    }, {audio: isActive && el.audioSelect.value ? Number(el.audioSelect.value) : undefined,
        languages: [state.subtitleLangPref || LANG, "en"],
        subtitles: state.remoteSubtitles.filter(track => track.play_url && track.play_url === state.activeSubtitleKey)
            .map(track => ({url: track.play_url, language: track.language, label: `${track.language || T.noLang} · ${track.provider || "Subtitles"}`})),
    });
}

async function saveSourcePreference(source) {
    if (!source.provider_id) return;
    try {
        await window.SVAppearance.savePrefs({ default_source: source.provider_id });
    } catch (_) {}
}

const playbackLibraries = new Map();

function loadPlaybackLibrary(name, url) {
    if (window[name]) return Promise.resolve(window[name]);
    if (!url) return Promise.reject(new Error("Decoder unavailable"));
    if (playbackLibraries.has(name)) return playbackLibraries.get(name);
    const request = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = url;
        script.dataset.playbackLibrary = name;
        const finish = (error) => {
            clearTimeout(timeout);
            script.onload = script.onerror = null;
            if (error) { script.remove(); reject(error); }
            else resolve(window[name]);
        };
        const timeout = setTimeout(() => finish(new Error("Decoder load timed out")), 15000);
        script.onload = () => finish(window[name] ? null : new Error("Decoder unavailable"));
        script.onerror = () => finish(new Error("Decoder load failed"));
        document.head.appendChild(script);
    }).catch(error => { playbackLibraries.delete(name); throw error; });
    playbackLibraries.set(name, request);
    return request;
}

async function loadSource(source) {
    destroyPlayer();
    if (el.audioWrapper) el.audioWrapper.style.display = "none";

    const attempt = state.sourceAttempt;
    const hlsSource = source.type !== "local" && source.type !== "embed" && source.type !== "external" && (source.url || "").includes(".m3u8");
    const nativeSupport = hlsSource && document.createElement("video").canPlayType("application/vnd.apple.mpegurl");
    const nativeHls = nativeSupport === "probably";
    const library = source.type === "torrent" ? "WebTorrent" : hlsSource && !nativeHls ? "Hls" : null;
    if (library && !window[library]) {
        el.playerContainer.innerHTML = `<div class="player-placeholder" role="status">${T.loading}</div>`;
        try {
            await loadPlaybackLibrary(library, window.PLAYBACK_LIBRARY_URLS?.[library]);
        } catch (_) {
            if (attempt !== state.sourceAttempt) return;
            // Preserve native playback in wrappers when a CDN is unavailable.
            if (!nativeSupport) {
                el.playerContainer.innerHTML = `<div class="player-placeholder" role="alert">${T.sourceLoadError}</div>`;
                tryNextSource("decoder-error");
                return;
            }
        }
        if (attempt !== state.sourceAttempt) return;
    }

    if (source.type === "torrent") { loadTorrentSource(source); return; }
    if (source.type === "local") { loadLocalSource(source); return; }
    if (source.type === "federation") { loadFederatedSource(source); return; }

    const url = source.url || "";

    if (source.type === "external") {
        // X-Frame-Options blocks iframe — open in new tab
        el.playerContainer.innerHTML =
            `<div class="player-placeholder" style="cursor:pointer"
                  onclick="window.open('${url}','_blank')">
                <div style="text-align:center">
                    <div style="font-size:2.5rem;margin-bottom:.5rem">🔗</div>
                    <div>${LANG === "es" ? "Clic para abrir en nueva pestaña" : "Click to open in new tab"}</div>
                    <div class="muted" style="font-size:.75rem;margin-top:.3rem">${escH(source.source)}</div>
                </div>
            </div>`;
        return;
    }

    if (source.type === "embed") {
        el.playerContainer.tabIndex = 0;
        el.playerContainer.innerHTML =
            `<div class="player-stage">
                <iframe src="${url}" allow="autoplay; fullscreen *; picture-in-picture" allowfullscreen></iframe>
                <div id="subtitle-overlay" class="subtitle-overlay" aria-live="polite"></div>
            </div>`;
        const iframe = el.playerContainer.querySelector("iframe");
        const attempt = ++state.sourceAttempt;
        state.iframeTimer = setTimeout(() => {
            if (attempt === state.sourceAttempt) tryNextSource("timeout");
        }, 15000);
        iframe.addEventListener("load", () => {
            if (attempt === state.sourceAttempt && state.iframeTimer) {
                clearTimeout(state.iframeTimer);
                state.iframeTimer = null;
            }
            initEmbedSubtitleOverlay();
        }, { once: true });
        iframe.addEventListener("error", () => tryNextSource("iframe-error"), { once: true });
        return;
    }

    el.playerContainer.innerHTML = '<video id="media-video" playsinline webkit-playsinline></video>';
    const video = document.getElementById("media-video");
    interceptVideoFullscreen(video);
    attachProgressTracking(video);
    video.addEventListener("error", () => tryNextSource("video-error"));

    if (window.SVInitCustomPlayer) {
        let displayTitle = state.item?.title || state.item?.name || "Video";
        if (MTYPE === "tv") {
            const sn = el.seasonSelect.value;
            const en = el.episodeSelect.value;
            if (sn && en) displayTitle += ` - S${String(sn).padStart(2, '0')}E${String(en).padStart(2, '0')}`;
        }
        window.SVInitCustomPlayer(video, el.playerContainer, {
            title: displayTitle,
            duration: Number(state.item?.runtime ? state.item.runtime * 60 : 0),
        });
    }

    if (hlsSource && !nativeHls && window.Hls?.isSupported()) {
        const hls = state.hls = new window.Hls();
        let networkRetries = 0, mediaRetries = 0;
        hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
            if (state.hls !== hls) return;
            video.play().catch(error => video._svSetStatus?.(error.name === "NotAllowedError" ? "blocked" : "error"));
            updateAudioTracks();
        });
        hls.on(window.Hls.Events.AUDIO_TRACKS_UPDATED, () => { if (state.hls === hls) updateAudioTracks(); });
        hls.on(window.Hls.Events.ERROR, (_, data) => {
            if (state.hls !== hls || !data.fatal) return;
            if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR && networkRetries++ < 1) {
                video._svSetStatus?.("buffering");
                hls.startLoad();
            } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && mediaRetries++ < 1) {
                video._svSetStatus?.("buffering");
                hls.recoverMediaError();
            } else {
                hls.destroy();
                state.hls = null;
                video.pause();
                video._svSetStatus?.("error");
                tryNextSource("hls-error");
            }
        });
        hls.loadSource(url);
        hls.attachMedia(video);
    } else {
        video.src = url;
        video.play().catch(error => video._svSetStatus?.(error.name === "NotAllowedError" ? "blocked" : "error"));
    }
}

async function loadFederatedSource(source) {
    const attempt = ++state.sourceAttempt;
    el.playerContainer.innerHTML = `<div class="player-placeholder" role="status">${T.loading}</div>`;
    try {
        const response = await fetch(source.metadata_url);
        const metadata = await response.json();
        if (attempt !== state.sourceAttempt) return;
        if (!response.ok) throw new Error(metadata.detail || T.sourceLoadError);
        state.localSubtitles = metadata.subtitles || [];
        renderAvailableSubtitles();
        await loadLocalSource({...source, ...metadata});
    } catch (error) {
        if (attempt !== state.sourceAttempt) return;
        showToast(T.sourceLoadError, error.message, "error");
        tryNextSource("federation-error");
    }
}

async function loadLocalSource(source, selectedStreamIndex) {
    state.activeLocalSource = source;
    el.playerContainer.innerHTML = '<video id="media-video" playsinline webkit-playsinline></video>';
    const video = document.getElementById("media-video");
    interceptVideoFullscreen(video);

    const tracks = source.audio_tracks || [];
    const chosen = Number.isFinite(selectedStreamIndex)
        ? selectedStreamIndex
        : tracks[0]?.stream_index;
    const resumeAt = savedResumePosition(source.duration_seconds);

    const dur = Number(source.duration_seconds || (state.item?.runtime ? state.item.runtime * 60 : 0));
    const onSeek = (targetTime) => {
        const localVideo = el.playerContainer.querySelector("video");
        if (!localVideo) return;
        return window.SVLocalPlayback.open(localVideo, source, {
            audio: chosen,
            start: targetTime,
            onOffset: (offset) => { state.localPlaybackOffset = offset; },
            onError: (error) => showToast(T.sourceLoadError, error.message, "error"),
        });
    };

    if (window.SVInitCustomPlayer) {
        let displayTitle = state.item?.title || state.item?.name || "Video";
        if (MTYPE === "tv") {
            const sn = el.seasonSelect.value;
            const en = el.episodeSelect.value;
            if (sn && en) displayTitle += ` - S${String(sn).padStart(2, '0')}E${String(en).padStart(2, '0')}`;
        }
        window.SVInitCustomPlayer(video, el.playerContainer, {
            title: displayTitle,
            duration: dur,
            onSeek: onSeek,
        });
    }

    (source.subtitles || []).forEach((subtitle, index) => {
        const track = document.createElement("track");
        track.kind = "subtitles";
        track.label = subtitle.label || subtitle.language || `Subtitles ${index + 1}`;
        track.srclang = subtitle.language || "und";
        track.src = subtitle.url;
        track.dataset.playUrl = subtitle.url;
        video.appendChild(track);
    });
    video.textTracks?.addEventListener?.("change", syncSubtitleSelection);
    if (tracks.length > 1) {
        el.audioWrapper.style.display = "grid";
        el.audioSelect.innerHTML = tracks.map((track, index) => {
            const label = track.title || track.language?.toUpperCase() || T.audioTrack(index);
            return `<option value="${track.stream_index}"${track.stream_index === chosen ? " selected" : ""}>${escH(label)} · ${escH(track.codec)}</option>`;
        }).join("");
    }
    attachProgressTracking(video, source.duration_seconds, () => Number(video.dataset.playbackOffset || 0));
    try {
        await window.SVLocalPlayback.open(video, source, {
            audio: chosen, start: resumeAt,
            onOffset: (offset) => { state.localPlaybackOffset = offset; },
            onError: (error) => {
                showToast(T.sourceLoadError, error.message, "error");
                tryNextSource("local-error");
            },
        });
    } catch (error) {
        showToast(T.sourceLoadError, error.message, "error");
        tryNextSource("local-exception");
    }
}

function onPlayerMessage(event) {
    const index = Number(el.sourceSelect.value || 0);
    const source = state.streams[index];
    if (!source || source.type !== "embed") return;
    const acceptedOrigin = source.failure_origin || source.origin;
    if (!acceptedOrigin || event.origin !== acceptedOrigin) return;

    let payload = event.data;
    if (typeof payload === "string") {
        try { payload = JSON.parse(payload); } catch (_) { return; }
    }
    if (!payload || typeof payload !== "object") return;

    const eventType = payload.type || payload.event || payload.eventType;
    const failed = source.failure_event
        ? eventType === source.failure_event
        : ["PLAYER_ERROR", "MEDIA_ERROR", "PLAYBACK_ERROR", "error"].includes(eventType);
    if (failed) { tryNextSource(eventType); return; }

    if (eventType !== "MEDIA_DATA") return;
    const media = currentMediaProgress(payload);
    if (!media) return;
    const progress = Number(media.watched ?? media.currentTime ?? media.current_time);
    const duration = Number(media.duration ?? media.totalDuration ?? 0);
    if (Number.isFinite(progress) && progress >= 0) {
        if (state.progressTimer) return;
        state.progressTimer = setTimeout(() => {
            state.progressTimer = null;
            persistProgress(progress, Number.isFinite(duration) ? duration : 0,
                duration > 0 && progress >= duration * 0.9);
        }, 4000);
    }
}

function currentMediaProgress(payload) {
    let data = payload.data ?? payload.media ?? payload;
    if (typeof data === "string") {
        try { data = JSON.parse(data); } catch (_) { return null; }
    }
    if (!data || typeof data !== "object") return null;

    let item = data;
    if (!data.progress && data.watched == null && data.currentTime == null) {
        const entries = Array.isArray(data) ? data.map((value, i) => [String(i), value])
            : Object.entries(data);
        const match = entries.find(([key, value]) =>
            String(key) === String(TMDB_ID)
            || String(key).endsWith(`-${TMDB_ID}`)
            || String(value?.id) === String(TMDB_ID));
        if (!match) return null;
        item = match[1];
    }

    if (MTYPE === "tv" && item?.show_progress) {
        const episodeProgress = Object.values(item.show_progress)
            .filter(Boolean)
            .sort((a, b) => Number(b.last_updated || 0) - Number(a.last_updated || 0))[0];
        if (episodeProgress?.progress) return episodeProgress.progress;
    }
    return item?.progress || item;
}

function tryNextSource() {
    if (!state.autoSourceFailover || state.streams.length < 2) return;
    const current = Number(el.sourceSelect.value || 0);
    state.failedSources.add(current);
    for (let offset = 1; offset < state.streams.length; offset += 1) {
        const next = (current + offset) % state.streams.length;
        if (state.failedSources.has(next)) continue;
        el.sourceSelect.value = String(next);
        const source = state.streams[next];
        el.detailStatus.textContent = T.sourceFallback(source.source || "source");
        destroyPlayer();
        loadSource(source);
        return;
    }
    el.detailStatus.textContent = T.sourceLoadError;
}

/* ------------------------------------------------------------------ */
/* WebTorrent streaming                                                */
/* ------------------------------------------------------------------ */
function loadTorrentSource(source) {
    if (typeof window.WebTorrent === "undefined") {
        el.playerContainer.innerHTML =
            `<div class="player-placeholder">${T.noBrowserTorrent}</div>`;
        return;
    }

    el.playerContainer.innerHTML = '<video id="media-video" playsinline webkit-playsinline></video>';
    const video = document.getElementById("media-video");
    interceptVideoFullscreen(video);

    if (window.SVInitCustomPlayer) {
        let displayTitle = state.item?.title || state.item?.name || "Video";
        if (MTYPE === "tv") {
            const sn = el.seasonSelect.value;
            const en = el.episodeSelect.value;
            if (sn && en) displayTitle += ` - S${String(sn).padStart(2, '0')}E${String(en).padStart(2, '0')}`;
        }
        window.SVInitCustomPlayer(video, el.playerContainer, {
            title: displayTitle,
            duration: Number(state.item?.runtime ? state.item.runtime * 60 : 0),
        });
    }

    el.torrentStatus.hidden = false;
    el.torrentBar.style.width = "0%";
    el.torrentInfo.textContent = T.torrentConnecting;

    if (!state.wtClient) state.wtClient = new window.WebTorrent();

    // 45s timeout if no peers
    const attempt = state.sourceAttempt;
    const peerTid = state.torrentPeerTimer = setTimeout(() => {
        if (el.torrentInfo.textContent === T.torrentConnecting)
            el.torrentInfo.textContent = T.noBrowserTorrent;
    }, 45000);

    state.wtClient.on("error", (err) => {
        clearTimeout(peerTid);
        console.error("WebTorrent:", err);
        el.torrentInfo.textContent = T.sourceLoadError;
    });

    const wtOpts = {
        announce: [
            "wss://tracker.openwebtorrent.com",
            "wss://tracker.btorrent.xyz",
            "wss://tracker.webtorrent.dev",
            "wss://tracker.files.fm:7073/announce",
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://open.tracker.cl:1337/announce"
        ]
    };

    state.wtClient.add(source.url, wtOpts, (torrent) => {
        clearTimeout(peerTid);
        if (attempt !== state.sourceAttempt) return;

        torrent.on("error", (err) => {
            console.error("Torrent stream error:", err);
            el.torrentInfo.textContent = T.sourceLoadError;
        });
        torrent.on("warning", (warn) => {
            console.warn("Torrent stream warning:", warn);
        });

        const videoExts = /\.(mp4|mkv|avi|mov|ts|webm|m4v)$/i;
        let file = (source.file_idx != null && torrent.files[source.file_idx])
            ? torrent.files[source.file_idx]
            : torrent.files.filter(f => videoExts.test(f.name)).sort((a, b) => b.length - a.length)[0];

        if (!file) { el.torrentInfo.textContent = T.sourceLoadError; return; }

        file.renderTo(video, { autoplay: true }, (err) => {
            if (err) {
                el.torrentInfo.textContent = T.sourceLoadError;
                tryNextSource("torrent-error");
            }
        });

        const interval = state.torrentStatsTimer = setInterval(() => {
            if (!state.wtClient || torrent.destroyed) { clearInterval(interval); return; }
            const pct   = Math.round(torrent.progress * 100);
            const speed = fmtBytes(torrent.downloadSpeed) + "/s";
            el.torrentBar.style.width   = `${pct}%`;
            el.torrentInfo.textContent  = T.torrentBuffering(pct, speed, torrent.numPeers);
            if (pct >= 100) { el.torrentInfo.textContent = T.torrentReady; clearInterval(interval); }
        }, 2000);

        attachProgressTracking(video);
    });
}

/* ------------------------------------------------------------------ */
/* Audio tracks (HLS)                                                  */
/* ------------------------------------------------------------------ */
function updateAudioTracks() {
    if (!state.hls?.audioTracks || state.hls.audioTracks.length <= 1) {
        if (el.audioWrapper) el.audioWrapper.style.display = "none";
        return;
    }
    el.audioWrapper.style.display = "grid";
    el.audioSelect.innerHTML = state.hls.audioTracks
        .map((t, i) => `<option value="${i}">${escH(t.name || t.lang || T.audioTrack(i))}</option>`)
        .join("");
}

/* ------------------------------------------------------------------ */
/* Fullscreen — CSS pseudo-fullscreen                                  */
/* ------------------------------------------------------------------ */

/**
 * We use a CSS-based pseudo-fullscreen (position:fixed; inset:0) instead of
 * the native Fullscreen API.  The native API has a fatal flaw for us: when
 * the user clicks the fullscreen button on the native <video controls>,
 * Chrome fullscreens the bare <video> element via internal C++ code that
 * bypasses any JS override — and our subtitle overlay (a sibling <div>) is
 * excluded from the fullscreen view entirely.
 *
 * With pseudo-fullscreen the DOM is unchanged: the overlay stays a sibling
 * of <video> inside the container, so it is always visible.
 */

/**
 * Hide the native fullscreen button on the video controls so users use
 * our app button instead.  Also install a fallback that redirects any
 * native fullscreen call to our pseudo-fullscreen.
 */
function interceptVideoFullscreen(video) {
    if (!video || video.dataset.fsIntercepted) return;
    video.dataset.fsIntercepted = "1";

    // Hide the native fullscreen button on the controls bar.
    video.controlsList = "nofullscreen";

    // Redirect any native fullscreen call to our pseudo-fullscreen.
    const redirect = () => togglePseudoFullscreen();
    video.requestFullscreen = redirect;
    video.webkitRequestFullscreen = redirect;
    video.mozRequestFullScreen = redirect;
    video.msRequestFullscreen = redirect;

    // Restore saved volume and persist adjustments
    try {
        const savedVol = localStorage.getItem("sv_video_volume");
        if (savedVol !== null) {
            const val = parseFloat(savedVol);
            if (!isNaN(val) && val >= 0 && val <= 1) video.volume = val;
        }
        const savedMuted = localStorage.getItem("sv_video_muted");
        if (savedMuted !== null) {
            video.muted = savedMuted === "true";
        }
        video.addEventListener("volumechange", () => {
            try {
                localStorage.setItem("sv_video_volume", String(video.volume));
                localStorage.setItem("sv_video_muted", String(video.muted));
            } catch (_) {}
        });
    } catch (_) {}
}

/**
 * Move the subtitle overlay to the end of the container so it renders
 * above the video in the stacking order.
 */
function ensureOverlayOnTop() {
    const container = el.playerContainer;
    const overlay = container.querySelector("#video-subtitle-overlay");
    if (overlay) container.appendChild(overlay);
}

let _pseudoFsActive = false;
let _fsReturnFocus = null;

function togglePseudoFullscreen() {
    if (_pseudoFsActive || document.fullscreenElement || document.webkitFullscreenElement) exitPseudoFullscreen();
    else enterPseudoFullscreen();
}

function enterPseudoFullscreen() {
    const container = el.playerContainer;
    const player = container.querySelector("video, iframe");
    if (!player) return;

    _fsReturnFocus = document.activeElement;
    _pseudoFsActive = true;
    container.classList.add("player-box--pseudo-fs");
    ensureOverlayOnTop();

    // Add an exit button if not already present.
    if (!container.querySelector("#pseudo-fs-exit")) {
        const btn = document.createElement("button");
        btn.id = "pseudo-fs-exit";
        btn.type = "button";
        btn.setAttribute("aria-label", "Exit fullscreen");
        btn.innerHTML = "&#10005;";
        btn.addEventListener("click", exitPseudoFullscreen);
        container.appendChild(btn);
    }

    // Try to also enter native fullscreen on the container for true
    // fullscreen (hides browser chrome).  If it fails, pseudo-fullscreen
    // alone still works.
    const fn = container.requestFullscreen || container.webkitRequestFullscreen
            || container.mozRequestFullScreen || container.msRequestFullscreen;
    try {
        if (fn) Promise.resolve(fn.call(container)).catch(() => {});
        else if (player.tagName === "VIDEO" && player.webkitEnterFullscreen) player.webkitEnterFullscreen();
    } catch (_) { /* CSS fullscreen remains available when native APIs reject. */ }
}

function exitPseudoFullscreen() {
    const container = el.playerContainer;
    _pseudoFsActive = false;
    container.classList.remove("player-box--pseudo-fs");
    const exitButton = container.querySelector("#pseudo-fs-exit");
    const restore = exitButton && document.activeElement === exitButton;
    exitButton?.remove();
    if (restore) {
        const target = _fsReturnFocus?.isConnected ? _fsReturnFocus : container.querySelector("#sv-play");
        target?.focus({preventScroll: true});
    }
    _fsReturnFocus = null;

    // Exit native fullscreen if active.
    const exitFn = document.exitFullscreen || document.webkitExitFullscreen
            || document.mozCancelFullScreen || document.msExitFullscreen;
    if (exitFn && (document.fullscreenElement || document.webkitFullscreenElement)) {
        Promise.resolve(exitFn.call(document)).catch(() => {});
    }
}

async function requestFullscreen() {
    enterPseudoFullscreen();
}

// Listen for native fullscreen changes.  Two cases to handle:
//  1. The user clicked the native fullscreen button on <video controls>.
//     Chrome bypasses our JS overrides and fullscreens the bare <video>,
//     which hides our subtitle overlay (a sibling <div>).  We detect this
//     (fullscreenElement is the <video>) and switch the native <track> to
//     "showing" so the browser renders subtitles natively inside its own
//     fullscreen view.  When fullscreen exits, switch back to "hidden".
//  2. The user pressed Esc / F11 to exit.  Sync pseudo-fullscreen state.
function _onFsChange() {
    const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
    const video = el.playerContainer.querySelector("video");

    // Case 1: native fullscreen landed on the bare <video>.
    if (fsEl && fsEl === video) {
        // Show native subtitles inside the browser's own fullscreen.
        const track = video.querySelector("track[data-sub-track]");
        if (track?.track) track.track.mode = "showing";
        // Hide our overlay — it's invisible inside native video fullscreen
        // anyway, and this avoids any z-index confusion on exit.
        const overlay = el.playerContainer.querySelector("#video-subtitle-overlay");
        if (overlay) overlay.style.display = "none";
        return;
    }

    // If we just exited native video fullscreen, restore overlay mode.
    if (!fsEl && video) {
        const track = video.querySelector("track[data-sub-track]");
        if (track?.track) track.track.mode = "hidden";
        if (state.videoSubtitles) {
            const overlay = el.playerContainer.querySelector("#video-subtitle-overlay");
            if (overlay) overlay.style.display = "block";
        }
    }

    // Case 2: native fullscreen exited (Esc/F11).  Sync pseudo-fullscreen.
    if (!fsEl && _pseudoFsActive) {
        exitPseudoFullscreen();
    }
    if (fsEl && fsEl === el.playerContainer) {
        ensureOverlayOnTop();
    }
}

/* ------------------------------------------------------------------ */
/* Subtitle size control                                              */
/* ------------------------------------------------------------------ */

let _subSize = 100;  // percentage, 50–200, persisted in localStorage

function initSubtitleSize() {
    const saved = localStorage.getItem("subtitleSize");
    if (saved) _subSize = Math.min(200, Math.max(50, parseInt(saved, 10) || 100));
    applySubtitleSize(_subSize);

    const slider = document.getElementById("subtitle-size-slider");
    if (!slider || slider.dataset.bound) return;
    slider.dataset.bound = "1";
    slider.value = _subSize;
    slider.addEventListener("input", () => {
        _subSize = parseInt(slider.value, 10);
        localStorage.setItem("subtitleSize", String(_subSize));
        applySubtitleSize(_subSize);
        const valEl = document.getElementById("subtitle-size-value");
        if (valEl) valEl.textContent = _subSize + "%";
    });
}

function applySubtitleSize(pct) {
    document.documentElement.style.setProperty("--sub-scale", String(pct / 100));
}

/* ------------------------------------------------------------------ */
/* Download                                                            */
/* ------------------------------------------------------------------ */
async function openDownloadPicker() {
    if (!state.streams.length && !state.streamsLoading) await autoLoadStreams();
    if (!state.streams.length) { el.detailStatus.textContent = T.noSources; return; }

    const src = state.streams[Number(el.sourceSelect.value || 0)];
    if (!src) return;

    el.downloadPicker.style.display = "flex";
    el.downloadOptions.innerHTML = `
        <button class="download-option" type="button" data-active-server>
            <span><strong>${escH(T.extractingServer)}</strong><small>${escH(src.source || "Server")} · ${escH(src.quality || "Multi")}</small></span>
            <span class="badge badge--neutral">${LANG === "es" ? "Directo" : "Direct"}</span>
        </button>
        <div class="download-option-status">${escH(T.findingTorrents)}</div>`;
    el.downloadOptions.querySelector("[data-active-server]")
        .addEventListener("click", () => queueDownload(src));

    const params = new URLSearchParams({ type: MTYPE });
    if (MTYPE === "tv") {
        params.set("s", el.seasonSelect.value || "1");
        params.set("e", el.episodeSelect.value || "1");
    }
    try {
        const response = await fetch(`/api/stream/download-options/${TMDB_ID}?${params}`);
        const torrents = response.ok ? await response.json() : [];
        renderTorrentOptions(torrents);
    } catch (_) {
        renderTorrentOptions([]);
    }
}

function closeDownloadPicker() {
    if (el.downloadPicker) el.downloadPicker.style.display = "none";
}

function renderTorrentOptions(torrents) {
    el.downloadOptions.querySelector(".download-option-status")?.remove();
    if (!torrents.length) {
        el.downloadOptions.insertAdjacentHTML("beforeend",
            `<div class="download-option-status">${escH(T.noTorrents)}</div>`);
        return;
    }
    torrents.forEach((source, index) => {
        const weak = source.health === "weak" || source.health === "unknown";
        const button = document.createElement("button");
        button.className = `download-option${weak ? " download-option--weak" : ""}`;
        button.type = "button";
        button.innerHTML = `
            <span><strong>${escH(source.title || source.source)}</strong>
            <small>${escH(source.quality || "HD")} · ${escH(fmtBytes(source.size_bytes))}${weak ? ` · ${escH(T.weakSwarm)}` : ""}</small></span>
            <span class="badge ${source.health === "healthy" ? "badge--ok" : "badge--neutral"}">${escH(T.seeders(source.seeders))}</span>`;
        button.addEventListener("click", () => queueDownload(source));
        el.downloadOptions.appendChild(button);
    });
}

async function queueDownload(src) {
    if (state.downloadSubmitting) return;
    state.downloadSubmitting = true;
    const buttons = [...el.downloadOptions.querySelectorAll("button")];
    buttons.forEach((button) => { button.disabled = true; });
    el.detailStatus.textContent = LANG === "es" ? "Preparando descarga…" : "Preparing download…";
    const title = buildTitle();

    try {
        let endpoint, body;

        if (src.type === "torrent") {
            endpoint = "/api/downloads/start-torrent";
            body = {
                title,
                magnet:     src.url,
                info_hash:  src.info_hash || "",
                file_idx:   src.file_idx  ?? null,
                size_bytes: src.size_bytes ?? null,
            };
        } else if (src.type === "embed" || src.download_mode === "sniff") {
            endpoint = "/api/downloads/start-from-embed";
            body = {
                embed_url: src.url,
                title,
                quality: "best",
                referer: src.origin || null,
            };
        } else {
            const video  = document.getElementById("media-video");
            const url    = video?.currentSrc || src.url;
            if (!url) { el.detailStatus.textContent = T.playFirst; return; }
            endpoint = "/api/downloads/start";
            body     = { url, title, quality: "best", type: MTYPE };
        }

        const resp = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await resp.json();

        if (data.success) {
            closeDownloadPicker();
            el.detailStatus.textContent = T.downloadQueued;
            showToast(T.downloadQueued, title, "ok");
        } else {
            const msg = data.detail || T.downloadFailed;
            el.detailStatus.textContent = msg;
            showToast(T.downloadFailed, msg, "error");
        }
    } catch {
        el.detailStatus.textContent = T.downloadConnErr;
    } finally {
        state.downloadSubmitting = false;
        buttons.forEach((button) => { button.disabled = false; });
    }
}

/* ------------------------------------------------------------------ */
/* Favorites                                                           */
/* ------------------------------------------------------------------ */
async function checkFavorite() {
    try {
        const resp = await fetch("/api/library/favorites");
        const data = await resp.json();
        state.isFavorite = (data.items || []).some(
            i => String(i.tmdb_id) === String(TMDB_ID) && i.media_type === MTYPE
        );
        syncFavBtn();
    } catch (_) {}
}

async function toggleFavorite() {
    if (!state.item) return;
    try {
        if (state.isFavorite) {
            const response = await fetch(`/api/library/favorites/${MTYPE}/${TMDB_ID}`, { method: "DELETE" });
            if (!response.ok) throw new Error("Favorite update failed");
            state.isFavorite = false;
        } else {
            const response = await fetch("/api/library/favorites", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    tmdb_id:      TMDB_ID,
                    media_type:   MTYPE,
                    title:        state.item.title || state.item.name || T.untitled,
                    poster_path:  state.item.poster_path   || null,
                    backdrop_path:state.item.backdrop_path || null,
                    release_date: state.item.release_date  || state.item.first_air_date || null,
                    rating:       state.item.vote_average  || null,
                }),
            });
            if (!response.ok) throw new Error("Favorite update failed");
            state.isFavorite = true;
        }
        syncFavBtn();
        // Keep the saved list snapshot consistent when returning to its card.
        try {
            const saved = JSON.parse(sessionStorage.getItem("sv_catalog_return"));
            if (saved && new URL(saved.url, location.origin).searchParams.get("view") === "favorites" && !state.isFavorite) {
                saved.items = saved.items.filter(item => String(item.id || item.tmdb_id) !== String(TMDB_ID));
                sessionStorage.setItem("sv_catalog_return", JSON.stringify(saved));
            }
        } catch (_) {}
    } catch {
        el.detailStatus.textContent = T.saveErr;
    }
}

function syncFavBtn() {
    setBtnLabel(el.favoriteBtn, state.isFavorite ? T.removeFav : T.save);
    el.favoriteBtn.classList.toggle("button--primary", state.isFavorite);
    el.favoriteBtn.setAttribute("aria-pressed", String(state.isFavorite));
}

/* ------------------------------------------------------------------ */
/* Progress tracking                                                   */
/* ------------------------------------------------------------------ */
async function loadProgress() {
    try {
        const params = new URLSearchParams();
        if (MTYPE === "tv") {
            if (el.seasonSelect.value)  params.set("season_number",  el.seasonSelect.value);
            if (el.episodeSelect.value) params.set("episode_number", el.episodeSelect.value);
        }
        const resp = await fetch(`/api/library/history/progress/${MTYPE}/${TMDB_ID}?${params}`);
        const data = await resp.json();
        state.currentProgress = data.item;
        const resumeAt = savedResumePosition(state.currentProgress?.duration_seconds);
        setBtnLabel(el.playBtn, resumeAt
            ? T.continueLabel(fmtClock(resumeAt))
            : T.play);
    } catch {
        setBtnLabel(el.playBtn, T.play);
    }
}

function savedResumePosition(knownDuration = 0) {
    if (state.currentProgress?.completed) return 0;
    const progress = Number(state.currentProgress?.progress_seconds || 0);
    const duration = Number(knownDuration || state.currentProgress?.duration_seconds || 0);
    return progress > 5 && (!duration || progress < duration - 5) ? progress : 0;
}

function attachProgressTracking(video, knownDuration = 0, resumeOffset = 0) {
    const init = typeof resumeOffset === "function" || resumeOffset > 0 ? 0 : savedResumePosition(knownDuration);
    video.addEventListener("loadedmetadata", () => {
        if (init > 5 && video.duration && init < video.duration - 5)
            video.currentTime = init;
    }, { once: true });

    video.addEventListener("timeupdate", () => {
        if (state.progressTimer) return;
        state.progressTimer = setTimeout(() => {
            state.progressTimer = null;
            const duration = Number(knownDuration || video.duration || 0);
            const offset = typeof resumeOffset === "function" ? resumeOffset() : resumeOffset;
            const absoluteTime = offset + video.currentTime;
            const done = duration ? absoluteTime >= duration * 0.9 : false;
            persistProgress(absoluteTime, duration, done);
        }, 4000);
    });

    video.addEventListener("ended", () =>
        persistProgress(Number(knownDuration || video.duration || 0), Number(knownDuration || video.duration || 0), true));
}

function repairLocalProgressDuration(source) {
    const duration = Number(source?.duration_seconds || 0);
    const progress = Number(state.currentProgress?.progress_seconds || 0);
    if (!duration || !progress) return;
    const existing = Number(state.currentProgress?.duration_seconds || 0);
    if (Math.abs(existing - duration) < 1) return;
    persistProgress(progress, duration, progress >= duration * 0.9);
}

async function persistProgress(progressSeconds, durationSeconds, completed) {
    if (!state.item) return;
    try {
        await fetch("/api/library/history", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                tmdb_id:       TMDB_ID,
                media_type:    MTYPE,
                title:         state.item.title || state.item.name || T.untitled,
                poster_path:   state.item.poster_path   || null,
                backdrop_path: state.item.backdrop_path || null,
                release_date:  state.item.release_date  || state.item.first_air_date || null,
                rating:        state.item.vote_average  || null,
                season_number:  MTYPE === "tv" ? Number(el.seasonSelect.value  || 1) : null,
                episode_number: MTYPE === "tv" ? Number(el.episodeSelect.value || 1) : null,
                progress_seconds:  progressSeconds,
                duration_seconds:  durationSeconds,
                completed,
            }),
        });
        state.currentProgress = {
            ...state.currentProgress,
            progress_seconds: progressSeconds,
            duration_seconds: durationSeconds,
            completed,
        };
        setBtnLabel(el.playBtn, completed ? T.play : T.continueLabel(fmtClock(progressSeconds)));
    } catch (e) { console.error(e); }
}

/* ------------------------------------------------------------------ */
/* Subtitles                                                           */
/* ------------------------------------------------------------------ */
async function loadSubtitles() {
    el.subtitleSummary.textContent = T.checkingSubs;
    el.subtitleList.innerHTML = "";
    badge(el.subtitleBadge, "neutral", T.checking);

    const subLang = state.subtitleLangPref || LANG;
    const params = new URLSearchParams({ tmdb_id: TMDB_ID, media_type: MTYPE, language: subLang });
    if (MTYPE === "tv") {
        if (el.seasonSelect.value)  params.set("season",  el.seasonSelect.value);
        if (el.episodeSelect.value) params.set("episode", el.episodeSelect.value);
    }

    try {
        const resp = await fetch(`/api/subtitles/search?${params}`);
        const data = await resp.json();
        state.remoteSubtitles = data.items || [];
        renderAvailableSubtitles(data);
    } catch {
        state.remoteSubtitles = [];
        renderAvailableSubtitles({ message: T.subErr, configured: false, error: true });
    }
}

function renderAvailableSubtitles(searchResult = null) {
    const local = (state.localSubtitles || []).map((item) => ({
        ...item,
        provider: LANG === "es" ? "Este servidor" : "This server",
        server: item.label || (LANG === "es" ? "Incluido con el archivo" : "Included with file"),
        type: "local",
        play_url: item.url,
    }));
    const items = [...local, ...(state.remoteSubtitles || [])];
    if (!items.length) {
        el.subtitleList.innerHTML = "";
        el.subtitleSummary.textContent = searchResult?.message || T.checkingSubs;
        badge(el.subtitleBadge, searchResult?.error ? "warn" : "neutral",
            searchResult?.error ? "error" : searchResult?.configured ? T.notIndexed : T.manual);
        return;
    }
    const localMessage = local.length
        ? (LANG === "es" ? "Subtítulos incluidos disponibles en el reproductor." : "Included subtitles are available in the player.")
        : (searchResult?.message || "");
    el.subtitleSummary.textContent = localMessage;
    badge(el.subtitleBadge, "ok", T.tracks(items.length));
    el.subtitleList.innerHTML = items.map((item, index) => `
        <button class="source-item source-item--selectable" type="button" data-subtitle-index="${index}" ${item.play_url ? "" : "disabled"}>
            <span>
                <strong>${escH(item.language || T.noLang)}</strong>
                <span class="muted">${escH(item.provider)} · ${escH(item.server)}</span>
            </span>
            <span class="badge badge--neutral">${escH(item.type)}</span>
        </button>`).join("");
    el.subtitleList.querySelectorAll("[data-subtitle-index]").forEach((button) => {
        button.addEventListener("click", () => void activateSubtitle(items[Number(button.dataset.subtitleIndex)]));
    });
    syncSubtitleSelection();
}

function syncSubtitleSelection() {
    el.subtitleList?.querySelectorAll("[data-subtitle-index]").forEach((button) => {
        const item = [...(state.localSubtitles || []), ...(state.remoteSubtitles || [])][Number(button.dataset.subtitleIndex)];
        const key = item?.url || item?.play_url;
        button.classList.toggle("source-item--active", Boolean(key && key === state.activeSubtitleKey));
        button.setAttribute("aria-pressed", key && key === state.activeSubtitleKey ? "true" : "false");
    });
}

async function maybeAutoActivateSubtitle() {
    if (state.subtitleAutoActivated || !state.remoteSubtitles.length) return;
    const prefLang = state.subtitleLangPref;
    if (!prefLang) return;
    const langCode = prefLang.slice(0, 2).toLowerCase();
    const match = state.remoteSubtitles.find(
        (s) => (s.language || "").slice(0, 2).toLowerCase() === langCode,
    );
    if (match) {
        state.subtitleAutoActivated = true;
        await activateSubtitle(match);
        showToast("Subtitles", T.subAutoOn(match.language || langCode), "ok");
    }
}

async function activateSubtitle(item) {
    if (!item?.play_url) {
        showToast("Subtitles", LANG === "es" ? "Selecciona primero una fuente de vídeo local." : "Select a local or direct video source first.", "warn");
        return;
    }
    const video = el.playerContainer.querySelector("video");
    if (video) {
        await activateVideoSubtitle(item, video);
    } else {
        await activateEmbedSubtitle(item);
    }
}

async function activateVideoSubtitle(item, video) {
    state.subtitleController?.abort();
    const controller = state.subtitleController = new AbortController();
    const attempt = state.sourceAttempt;
    try {
        const response = await fetch(item.play_url, { credentials: "same-origin", signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const content = await response.text();
        if (controller.signal.aborted || attempt !== state.sourceAttempt || !video.isConnected) return;
        const cues = parseVttCues(content);
        if (!cues.length) throw new Error("No cues found in subtitle file");

        // Stop any existing overlay / native tracks
        stopVideoSubtitleOverlay();
        [...video.textTracks].forEach((t) => { t.mode = "hidden"; });

        // Ensure the overlay div exists inside the player container
        let overlay = el.playerContainer.querySelector("#video-subtitle-overlay");
        if (!overlay) {
            overlay = document.createElement("div");
            overlay.id = "video-subtitle-overlay";
            overlay.className = "subtitle-overlay";
            overlay.setAttribute("aria-live", "polite");
            el.playerContainer.appendChild(overlay);
        }
        overlay.innerHTML = "";
        overlay.style.display = "block";
        // Move overlay to the end of the container so it renders above the
        // video in the fullscreen stack.
        el.playerContainer.appendChild(overlay);

        // Also inject a native <track> element so that subtitles render
        // inside Chrome's native video fullscreen (where our overlay div
        // is excluded because it's a sibling of <video>).  The track is
        // kept in "hidden" mode normally — our overlay handles rendering.
        // When native video fullscreen is detected (_onFsChange), we
        // switch the track to "showing" so the browser renders it.
        const oldTrack = video.querySelector("track[data-sub-track]");
        if (oldTrack) oldTrack.remove();
        if (state._videoSubBlobUrl) URL.revokeObjectURL(state._videoSubBlobUrl);
        const blob = new Blob([content], { type: "text/vtt" });
        state._videoSubBlobUrl = URL.createObjectURL(blob);
        const trackEl = document.createElement("track");
        trackEl.setAttribute("data-sub-track", "");
        trackEl.kind = "subtitles";
        trackEl.label = item.language || "Subtitles";
        trackEl.srclang = (item.language || "en").slice(0, 2).toLowerCase();
        trackEl.src = state._videoSubBlobUrl;
        trackEl.default = true;
        video.appendChild(trackEl);
        // Wait for the track to load, then set it to hidden.
        trackEl.addEventListener("load", () => {
            if (trackEl.track) trackEl.track.mode = "hidden";
        }, { once: true });

        state.videoSubtitles = { item, cues, video };
        state.videoSubtitleOffset = 0;

        // The remux endpoint now produces a full seekable MP4 and the browser
        // seeks natively, so video.currentTime already reflects the real movie
        // position.  localPlaybackOffset is kept for legacy/safety but is now
        // always 0.  The manual nudge (videoSubtitleOffset) is added on top.
        function onTimeUpdate() {
            const base = video.currentTime + Number(state.localPlaybackOffset || 0);
            const adjusted = base + state.videoSubtitleOffset;
            const cue = findActiveCue(state.videoSubtitles.cues, adjusted);
            if (cue) {
                if (overlay.dataset.cueId !== String(cue.id)) {
                    overlay.innerHTML = `<div>${escH(cue.text)}</div>`;
                    overlay.dataset.cueId = String(cue.id);
                }
            } else if (overlay.dataset.cueId) {
                overlay.innerHTML = "";
                delete overlay.dataset.cueId;
            }
        }
        state._videoSubTimeUpdate = onTimeUpdate;
        video.addEventListener("timeupdate", onTimeUpdate);
        onTimeUpdate(); // render immediately

        // Shift + Arrow-key nudge for manual sync adjustment (±250 ms per press).
        // Plain arrows are left to the native <video controls> for seeking.
        if (!state._videoSubKeyHandler) {
            state._videoSubKeyHandler = (event) => {
                const active = document.activeElement;
                if (!state.videoSubtitles || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey ||
                    !el.playerContainer.contains(active) || active.isContentEditable || active.matches("input, textarea, select")) return;
                if (!event.shiftKey) return;
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                event.preventDefault();
                const delta = event.key === "ArrowLeft" ? -0.25 : 0.25;
                state.videoSubtitleOffset += delta;
                showToast("Subtitles", T.subOffset(state.videoSubtitleOffset * 1000), "ok");
            };
            document.addEventListener("keydown", state._videoSubKeyHandler);
        }

        const hint = ` · ${T.subSyncHintVideo}`;
        showToast("Subtitles", `${item.language || T.noLang} · ${LANG === "es" ? "activados" : "enabled"}${hint}`, "ok");

        state.activeSubtitleKey = item.play_url;
        syncSubtitleSelection();
    } catch (error) {
        if (controller.signal.aborted || attempt !== state.sourceAttempt || !video.isConnected) return;
        console.error("subtitle activation:", error);
        showToast("Subtitles", LANG === "es" ? "No se pudo cargar esta pista de subtítulos." : "Could not load this subtitle track.", "warn");
    }
}

function stopVideoSubtitleOverlay() {
    state.subtitleController?.abort();
    state.subtitleController = null;
    if (state._videoSubKeyHandler) document.removeEventListener("keydown", state._videoSubKeyHandler);
    state._videoSubKeyHandler = null;
    if (state._videoSubTimeUpdate && state.videoSubtitles?.video) {
        state.videoSubtitles.video.removeEventListener("timeupdate", state._videoSubTimeUpdate);
    }
    state._videoSubTimeUpdate = null;
    // Clean up native track element and blob URL.
    if (state.videoSubtitles?.video) {
        const oldTrack = state.videoSubtitles.video.querySelector("track[data-sub-track]");
        if (oldTrack) oldTrack.remove();
    }
    if (state._videoSubBlobUrl) {
        URL.revokeObjectURL(state._videoSubBlobUrl);
        state._videoSubBlobUrl = null;
    }
    state.videoSubtitles = null;
    state.videoSubtitleOffset = 0;
    const overlay = el.playerContainer.querySelector("#video-subtitle-overlay");
    if (overlay) {
        overlay.innerHTML = "";
        overlay.style.display = "none";
        delete overlay.dataset.cueId;
    }
}

/* ------------------------------------------------------------------ */
/* Embed (iframe) subtitle overlay                                     */
/* ------------------------------------------------------------------ */

function initEmbedSubtitleOverlay() {
    const overlay = document.getElementById("subtitle-overlay");
    if (!overlay) return;
    overlay.innerHTML = "";
    overlay.style.display = "none";
    state.embedSubtitleOffset = 0;
    if (!state._embedSubKeyHandler) {
        state._embedSubKeyHandler = (event) => {
            const active = document.activeElement;
            if (!state.embedSubtitles || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey ||
                !el.playerContainer.contains(active) || active.isContentEditable || active.matches("input, textarea, select")) return;
            if (event.key === "ArrowLeft") {
                state.embedSubtitleOffset -= 500;
                showToast("Subtitles", T.subOffset(state.embedSubtitleOffset), "ok");
            } else if (event.key === "ArrowRight") {
                state.embedSubtitleOffset += 500;
                showToast("Subtitles", T.subOffset(state.embedSubtitleOffset), "ok");
            }
        };
        document.addEventListener("keydown", state._embedSubKeyHandler);
    }
}

async function activateEmbedSubtitle(item) {
    const overlay = document.getElementById("subtitle-overlay");
    if (!overlay) {
        showToast("Subtitles", LANG === "es" ? "Selecciona primero una fuente de vídeo." : "Select a video source first.", "warn");
        return;
    }
    showToast("Subtitles", LANG === "es" ? "Cargando subtitulos…" : "Loading subtitles…", "ok");
    state.subtitleController?.abort();
    const controller = state.subtitleController = new AbortController();
    const attempt = state.sourceAttempt;
    try {
        const response = await fetch(item.play_url, { credentials: "same-origin", signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const content = await response.text();
        if (controller.signal.aborted || attempt !== state.sourceAttempt || !overlay.isConnected) return;
        const cues = parseVttCues(content);
        if (!cues.length) throw new Error("No cues found in subtitle file");

        stopEmbedSubtitleOverlay();
        initEmbedSubtitleOverlay();
        state.embedSubtitles = { item, cues, startTime: performance.now() };
        state.embedSubtitleOffset = 0;
        overlay.style.display = "block";
        runEmbedOverlayLoop();
        state.activeSubtitleKey = item.play_url;
        syncSubtitleSelection();
        showToast("Subtitles", `${item.language || T.noLang} · ${LANG === "es" ? "activados" : "enabled"} · ${T.subSyncHint}`, "ok");
    } catch (error) {
        if (controller.signal.aborted || attempt !== state.sourceAttempt || !overlay.isConnected) return;
        console.error("embed subtitle activation:", error);
        showToast("Subtitles", LANG === "es" ? "No se pudo cargar esta pista de subtítulos." : "Could not load this subtitle track.", "warn");
    }
}

function runEmbedOverlayLoop() {
    const overlay = document.getElementById("subtitle-overlay");
    if (!overlay || !state.embedSubtitles) return;

    function tick() {
        if (!state.embedSubtitles || !document.getElementById("subtitle-overlay")) return;
        const elapsed = (performance.now() - state.embedSubtitles.startTime) / 1000;
        const adjusted = elapsed + state.embedSubtitleOffset / 1000;
        const cue = findActiveCue(state.embedSubtitles.cues, adjusted);
        if (cue) {
            if (overlay.dataset.cueId !== String(cue.id)) {
                overlay.innerHTML = `<div>${escH(cue.text)}</div>`;
                overlay.dataset.cueId = String(cue.id);
            }
        } else if (overlay.dataset.cueId) {
            overlay.innerHTML = "";
            delete overlay.dataset.cueId;
        }
        state.embedOverlayRaf = requestAnimationFrame(tick);
    }
    state.embedOverlayRaf = requestAnimationFrame(tick);
}

function stopEmbedSubtitleOverlay() {
    state.subtitleController?.abort();
    state.subtitleController = null;
    if (state._embedSubKeyHandler) document.removeEventListener("keydown", state._embedSubKeyHandler);
    state._embedSubKeyHandler = null;
    if (state.embedOverlayRaf) {
        cancelAnimationFrame(state.embedOverlayRaf);
        state.embedOverlayRaf = null;
    }
    state.embedSubtitles = null;
    state.embedSubtitleOffset = 0;
    const overlay = document.getElementById("subtitle-overlay");
    if (overlay) {
        overlay.innerHTML = "";
        overlay.style.display = "none";
        delete overlay.dataset.cueId;
    }
}

/**
 * Lightweight WebVTT parser — extracts cue id, start, end, and text.
 * Handles multi-line cue text and standard timestamp formats.
 */
function parseVttCues(vttText) {
    const cues = [];
    const blocks = vttText.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n\n");
    for (const block of blocks) {
        const lines = block.split("\n").filter((line) => line.trim() !== "");
        if (lines.length < 1) continue;
        let cueId = cues.length;
        let timeLineIndex = 0;
        if (!lines[0].includes("-->")) {
            cueId = lines[0];
            timeLineIndex = 1;
        }
        const timeLine = lines[timeLineIndex];
        if (!timeLine || !timeLine.includes("-->")) continue;
        const match = timeLine.match(/(\d{2}:\d{2}:\d{2}[.,]\d{3}|\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3}|\d{2}:\d{2}[.,]\d{3})/);
        if (!match) continue;
        const start = parseVttTimestamp(match[1]);
        const end = parseVttTimestamp(match[2]);
        const text = lines.slice(timeLineIndex + 1).join("\n");
        cues.push({ id: cueId, start, end, text });
    }
    return cues;
}

function parseVttTimestamp(ts) {
    const parts = ts.replace(",", ".").split(":");
    if (parts.length === 3) {
        return Number(parts[0]) * 3600 + Number(parts[1]) * 60 + Number(parts[2]);
    }
    return Number(parts[0]) * 60 + Number(parts[1]);
}

function findActiveCue(cues, seconds) {
    for (const cue of cues) {
        if (seconds >= cue.start && seconds <= cue.end) return cue;
    }
    return null;
}

/* ------------------------------------------------------------------ */
/* Notification SSE                                                    */
/* ------------------------------------------------------------------ */
function connectNotificationSse() {
    if (window.location.hostname.endsWith(".trycloudflare.com")) return;
    // WebView need not implement Notifications. Optional background services
    // must never prevent the detail page from booting.
    let es;
    try {
        if (typeof window.EventSource !== "function") return;
        es = new window.EventSource("/api/downloads/notifications");
    } catch (_) { return; }
    const notifications = window.Notification;
    window.addEventListener("pagehide", () => es.close(), {once: true});
    es.onmessage = (evt) => {
        try {
            const ev = JSON.parse(evt.data);
            if (ev.type === "download_complete") {
                showToast(T.notifyTitle, T.notifyBody(ev.title || ""), "ok");
                if (notifications?.permission === "granted")
                    new notifications(T.notifyTitle, { body: T.notifyBody(ev.title || "") });
            }
        } catch (_) {}
    };
    // EventSource reconnects itself; creating another one here leaks connections.
    try {
        if (notifications?.permission === "default")
            Promise.resolve(notifications.requestPermission()).catch(() => {});
    } catch (_) {}
}

/* ------------------------------------------------------------------ */
/* Cleanup                                                             */
/* ------------------------------------------------------------------ */
function destroyPlayer() {
    const localVideo = el.playerContainer.querySelector("video");
    if (localVideo) {
        localVideo._svCleanup?.();
        window.SVLocalPlayback.close(localVideo);
    }
    exitPseudoFullscreen();
    state.sourceAttempt += 1;
    clearTimeout(state.torrentPeerTimer);
    clearInterval(state.torrentStatsTimer);
    state.torrentPeerTimer = state.torrentStatsTimer = null;
    if (state.iframeTimer) { clearTimeout(state.iframeTimer); state.iframeTimer = null; }
    if (state.hls)      { state.hls.destroy();               state.hls      = null; }
    if (state.wtClient) { try { state.wtClient.destroy(); } catch (_) {} state.wtClient = null; }
    el.torrentStatus.hidden = true;
    if (state.progressTimer) { clearTimeout(state.progressTimer); state.progressTimer = null; }
    state.activeLocalSource = null;
    state.localPlaybackOffset = 0;
    state.activeSubtitleKey = null;
    stopVideoSubtitleOverlay();
    if (state.subtitleBlobUrl) { URL.revokeObjectURL(state.subtitleBlobUrl); state.subtitleBlobUrl = null; }
    stopEmbedSubtitleOverlay();
    if (el.audioWrapper) el.audioWrapper.style.display = "none";
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

/** Update only the text label inside a button, preserving the SVG icon. */
function setBtnLabel(btn, text) {
    if (!btn) return;
    let span = btn.querySelector(".button-text");
    if (!span) {
        span = document.createElement("span");
        span.className = "button-text";
        btn.appendChild(span);
    }
    span.textContent = text;
}

function buildTitle() {
    const title = state.item?.title || state.item?.name || "download";
    if (MTYPE !== "tv") return title;
    const s = String(el.seasonSelect.value  || "1").padStart(2, "0");
    const e = String(el.episodeSelect.value || "1").padStart(2, "0");
    return `${title} S${s}E${e}`;
}

function fmtBytes(bytes) {
    if (!bytes) return "0 B";
    const u = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / 1024 ** i).toFixed(1)} ${u[i]}`;
}

function fmtClock(sec) {
    const t = Math.max(0, Math.floor(Number(sec || 0)));
    return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

function badge(el, mode, text) { el.className = `badge badge--${mode}`; el.textContent = text; }

function escH(v) {
    return String(v)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

window.SVToggleFs = togglePseudoFullscreen;
window.SVDestroyPlayer = () => {
    const trigger = el.playerContainer.querySelector("video")?._svReturnFocus;
    destroyPlayer();
    el.playerContainer.innerHTML = `<div class="player-placeholder"><button type="button" class="button button--primary">${T.play}</button></div>`;
    el.playerContainer.querySelector("button").addEventListener("click", playCurrentItem);
    const target = trigger?.isConnected ? trigger : el.playBtn;
    target?.focus({preventScroll: true});
};
window.SVStopSubtitles = () => {
    stopVideoSubtitleOverlay();
    stopEmbedSubtitleOverlay();
    state.activeSubtitleKey = null;
    syncSubtitleSelection();
};

window.addEventListener("pagehide", destroyPlayer);
document.addEventListener("DOMContentLoaded", boot);
