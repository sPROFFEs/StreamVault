/* Device-only cinema library. Reuses the online player, never needs a session. */
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
        deleteGroup: n => `Se eliminarán las ${n} copias/episodios de este título en el dispositivo. Los archivos del servidor no se borrarán.`,
        search: "Buscar descargas", type: "Tipo", all: "Todo", movies: "Películas", series: "Series",
        storageSettings: "Almacenamiento y privacidad", back: "Volver a la biblioteca", options: "Opciones de reproducción",
        season: "Temporada", episode: "Episodio", source: "Fuente", subtitles: "Subtítulos", off: "Desactivados",
        subtitleSize: "Tamaño de subtítulos", subtitleOffset: "Desfase de subtítulos (segundos)", speed: "Velocidad",
        available: "Sin conexión solo están disponibles las pistas guardadas en este dispositivo.",
        details: "Detalles", continue: "Continuar", noSynopsis: "Sin sinopsis guardada.", noResults: "No hay descargas con estos filtros.",
        audioSaved: "Audio guardado en el vídeo", episodes: "episodios", savedItems: "títulos", audioDownload: "Audio para guardar",
        refreshMetadata: "Actualizar fichas online", refreshing: "Actualizando fichas…", refreshed: n => `${n} fichas actualizadas. Los vídeos no se han vuelto a descargar.`, needsOnline: "Conecta con el servidor para actualizar las fichas.",
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
        deleteGroup: n => `All ${n} device copies/episodes of this title will be deleted. Server files will not be deleted.`,
        search: "Search downloads", type: "Type", all: "All", movies: "Movies", series: "Series",
        storageSettings: "Storage and privacy", back: "Back to library", options: "Playback options",
        season: "Season", episode: "Episode", source: "Source", subtitles: "Subtitles", off: "Off",
        subtitleSize: "Subtitle size", subtitleOffset: "Subtitle offset (seconds)", speed: "Playback speed",
        available: "Only tracks saved on this device are available offline.",
        details: "Details", continue: "Continue", noSynopsis: "No synopsis saved.", noResults: "No downloads match these filters.",
        audioSaved: "Audio saved in the video", episodes: "episodes", savedItems: "titles", audioDownload: "Audio to save",
        refreshMetadata: "Refresh details online", refreshing: "Refreshing details…", refreshed: n => `${n} details updated. Videos were not downloaded again.`, needsOnline: "Connect to the server to refresh details.",
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
        const audioLabel = node('label', text.audioDownload), audioChoice = document.createElement('select');
        audioLabel.append(audioChoice); audioLabel.hidden = true;
        const cancel = button(text.cancel, () => { controller.abort(); panel.close(); });
        panel.addEventListener("cancel", () => controller.abort());
        const start = button(text.start, async () => {
            start.disabled = true; audioChoice.disabled = true; progress.hidden = false; status.textContent = text.preparing;
            window.addEventListener("beforeunload", warn);
            try {
                await SVOffline.save(file, {...options, audio: audioChoice.options.length ? Number(audioChoice.value) : options.audio,
                    signal: controller.signal, onProgress: ({loaded, total}) => {
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
                start.disabled = false; audioChoice.disabled = false; progress.hidden = true;
            } finally { window.removeEventListener("beforeunload", warn); }
        });
        function warn(event) { event.preventDefault(); event.returnValue = ""; }
        start.classList.add("offline-primary");
        actions.append(cancel, start); panel.append(audioLabel, status, progress, actions);
        start.disabled = true;
        SVOffline.prepare(file, controller.signal).then(prepared => {
            if (controller.signal.aborted) return;
            file = prepared;
            for (const track of prepared.audio_tracks || []) option(audioChoice, track.stream_index,
                [track.language?.toUpperCase(), track.title, track.codec].filter(Boolean).join(' · ') || `Audio ${track.stream_index}`);
            if (options.audio != null) audioChoice.value = options.audio;
            if (!audioChoice.value && audioChoice.options.length) audioChoice.selectedIndex = 0;
            audioLabel.hidden = !audioChoice.options.length;
            start.disabled = false;
        }).catch(error => { if (!controller.signal.aborted) status.textContent = error.message; });
        cancel.focus();
    }

    const $ = id => document.getElementById(id);
    let entries = [], current = null, returnFocus = null, lastSaved = 0;
    let trackUrls = [], coverUrls = [], detailUrls = [], cueTimes = new WeakMap(), trackOffsets = new WeakMap(), mediaEvents;
    const kind = entry => entry.media_type === "series" || entry.media_type === "tv" || entry.episode ? "series" : "movie";
    const seasonOf = entry => entry.season_number || Number(entry.episode?.match(/S(\d+)/i)?.[1] || 1);
    const episodeOf = entry => entry.episode_number || Number(entry.episode?.match(/E(\d+)/i)?.[1] || 1);
    const groupKey = entry => `${kind(entry)}:${entry.tmdb_id || entry.title.toLocaleLowerCase()}:${entry.year || ""}`;
    const sameEpisode = (a, b) => groupKey(a) === groupKey(b) && (kind(a) !== "series" || seasonOf(a) === seasonOf(b) && episodeOf(a) === episodeOf(b));
    const revoke = urls => { urls.forEach(url => URL.revokeObjectURL(url)); urls.length = 0; };
    function picture(target, entry, urls) {
        target.replaceChildren();
        const fallback = node("span", entry.title.slice(0, 1), "offline-art-fallback");
        target.append(fallback);
        if (entry.poster instanceof Blob) {
            const image = document.createElement("img"), url = URL.createObjectURL(entry.poster);
            urls.push(url); image.src = url; image.alt = entry.title; image.decoding = "async";
            image.loading = target.classList.contains('offline-cover') ? 'lazy' : 'eager';
            image.onload = () => { fallback.hidden = true; };
            image.onerror = () => { image.hidden = true; fallback.hidden = false; };
            target.append(image);
        }
    }
    function option(select, value, label) { const item = node("option", label); item.value = value; select.append(item); }
    function persist() {
        if (!current) return Promise.resolve();
        const video = $('offline-video');
        const subtitle = current.subtitles?.[Number($('offline-subtitle-select').value)];
        const loaded = video.hasAttribute('src') && video.readyState > 0;
        const values = {progress_seconds: loaded ? (video.ended ? 0 : video.currentTime) : (current.playback?.progress_seconds || 0),
            subtitle_language: subtitle?.language || "off", subtitle_size: Number($('offline-subtitle-size').value),
            subtitle_offset: Number($('offline-subtitle-offset').value), rate: video.playbackRate};
        current.playback = {...current.playback, ...values};
        return SVOffline.updatePlayback(current.id, values).catch(() => {});
    }
    function exitFullscreen() {
        $('offline-player').classList.remove('player-box--pseudo-fs');
        document.body.classList.remove('offline-fullscreen');
        const exit = document.exitFullscreen || document.webkitExitFullscreen;
        if (exit && (document.fullscreenElement || document.webkitFullscreenElement)) Promise.resolve(exit.call(document)).catch(() => {});
    }
    function stopVideo() {
        persist();
        const video = $('offline-video');
        mediaEvents?.abort();
        video._svCleanup?.(); video.pause(); video.removeAttribute('src'); video.replaceChildren(); video.load();
        video.hidden = true; $('offline-placeholder').hidden = false;
        revoke(trackUrls); cueTimes = new WeakMap(); trackOffsets = new WeakMap();
    }
    function closePlayer() {
        exitFullscreen(); stopVideo(); revoke(detailUrls); current = null;
        $('offline-player-panel').hidden = true;
        $('offline-library').hidden = false;
        returnFocus?.focus({preventScroll: true});
    }
    function applySubtitles() {
        const selected = $('offline-subtitle-select').value;
        const video = $('offline-video'), offset = Number($('offline-subtitle-offset').value) || 0;
        Array.from(video.textTracks).forEach((track, index) => {
            // Hidden loads the small saved VTT, but paints only the chosen track.
            track.mode = String(index) === selected ? 'showing' : 'hidden';
            const applied = trackOffsets.get(track), count = track.cues?.length || 0;
            // Language/size changes must not rewrite thousands of cue timings.
            if ((applied?.offset || 0) !== offset || offset !== 0 && applied?.count !== count) {
                Array.from(track.cues || []).forEach(cue => {
                    if (!cueTimes.has(cue)) cueTimes.set(cue, [cue.startTime, cue.endTime]);
                    const [start, end] = cueTimes.get(cue);
                    cue.startTime = Math.max(0, start + offset);
                    cue.endTime = Math.max(cue.startTime + .01, end + offset);
                });
            }
            trackOffsets.set(track, {offset, count});
        });
        $('subtitle-list').querySelectorAll('button').forEach((button, index) => button.classList.toggle('source-item--active', String(index) === selected));
        const size = Number($('offline-subtitle-size').value) || 100;
        $('offline-player').style.setProperty('--sub-scale', size / 100);
        $('offline-subtitle-size-value').textContent = `${size}%`;
        persist();
    }
    function playEntry(startAt) {
        if (!current) return;
        if ($('offline-video').hasAttribute('src')) { $('offline-video').play().catch(() => {}); return; }
        const video = $('offline-video');
        mediaEvents = new AbortController();
        video.hidden = false; $('offline-placeholder').hidden = true;
        for (const subtitle of current.subtitles || []) {
            const track = document.createElement('track');
            track.kind = 'subtitles'; track.label = subtitle.label; track.srclang = subtitle.language;
            track.src = URL.createObjectURL(new Blob([subtitle.text], {type: 'text/vtt'}));
            trackUrls.push(track.src); track.addEventListener('load', applySubtitles); video.append(track);
            track.track.mode = 'hidden';
        }
        video.src = SVOffline.mediaUrl(current.id);
        const resume = Number(startAt ?? current.playback?.progress_seconds ?? 0);
        video.playbackRate = Number($('offline-speed').value);
        video.addEventListener('loadedmetadata', () => {
            if (resume > 0 && Number.isFinite(video.duration)) video.currentTime = Math.min(resume, Math.max(0, video.duration - .5));
            applySubtitles();
        }, {once: true, signal: mediaEvents.signal});
        window.SVInitCustomPlayer(video, $('offline-player'), {title: `${current.title}${current.episode ? ` · ${current.episode}` : ''}`, duration: current.duration_seconds});
        applySubtitles(); lastSaved = Date.now();
        video.play().catch(error => { $('offline-status').textContent = error.name === 'NotAllowedError' ? text.play : text.unsupported; });
        $('offline-player').scrollIntoView({block: 'center', behavior: 'smooth'});
    }
    function showDetail(entry, autoplay = false, startAt) {
        stopVideo(); revoke(detailUrls); current = entry;
        $('offline-library').hidden = true; $('offline-player-panel').hidden = false;
        $('offline-status').textContent = '';
        $('offline-player-title').textContent = entry.title;
        $('offline-player-meta').textContent = [kind(entry) === 'series' ? text.series : text.movies, entry.year,
            entry.episode, entry.duration_seconds > 0 ? `${Math.round(entry.duration_seconds / 60)} min` : '', bytes(entry.size_bytes)].filter(Boolean).join(' · ');
        $('offline-player-description').textContent = entry.overview || text.noSynopsis;
        picture($('offline-poster'), entry, detailUrls);
        $('offline-backdrop').style.backgroundImage = 'none';
        $('offline-placeholder').style.backgroundImage = 'none';
        if (entry.backdrop instanceof Blob) {
            const url = URL.createObjectURL(entry.backdrop); detailUrls.push(url);
            $('offline-backdrop').style.backgroundImage = `url(${JSON.stringify(url)})`;
            $('offline-placeholder').style.backgroundImage = `url(${JSON.stringify(url)})`;
        }
        const episodes = entries.filter(item => groupKey(item) === groupKey(entry) && item.status === 'complete');
        $('offline-series-controls').hidden = kind(entry) !== 'series';
        $('offline-season-select').replaceChildren(); $('offline-episode-select').replaceChildren();
        [...new Set(episodes.map(seasonOf))].sort((a, b) => a - b).forEach(season => option($('offline-season-select'), season, `${text.season} ${season}`));
        $('offline-season-select').value = seasonOf(entry);
        [...new Map(episodes.filter(item => seasonOf(item) === seasonOf(entry)).map(item => [episodeOf(item), item])).values()]
            .sort((a, b) => episodeOf(a) - episodeOf(b)).forEach(item => option($('offline-episode-select'), episodeOf(item), `${text.episode} ${episodeOf(item)}${item.episode_title ? ` · ${item.episode_title}` : ''}`));
        $('offline-episode-select').value = episodeOf(entry);
        $('source-select').replaceChildren(); option($('source-select'), entry.id, `${text.device} · ${entry.origin}`);
        const copies = entries.filter(item => item.status === 'complete' && sameEpisode(item, entry));
        $('audio-wrapper').hidden = copies.length < 2;
        $('audio-select').replaceChildren();
        copies.forEach(item => option($('audio-select'), item.id, [item.audio_label?.toUpperCase() || text.audioSaved, item.origin].join(' · ')));
        $('audio-select').value = entry.id;
        $('offline-audio-label').textContent = `Audio · ${entry.audio_label?.toUpperCase() || text.audioSaved}`;
        $('offline-subtitle-select').replaceChildren(); $('subtitle-list').replaceChildren();
        option($('offline-subtitle-select'), 'off', text.off);
        (entry.subtitles || []).forEach((subtitle, index) => {
            option($('offline-subtitle-select'), index, subtitle.label || subtitle.language);
            const select = button(subtitle.label || subtitle.language, () => { $('offline-subtitle-select').value = index; applySubtitles(); });
            select.className = 'source-item'; $('subtitle-list').append(select);
        });
        const preference = entry.playback?.subtitle_language || 'off';
        const index = (entry.subtitles || []).findIndex(subtitle => subtitle.language === preference);
        $('offline-subtitle-select').value = index < 0 ? 'off' : String(index);
        $('offline-subtitle-size').value = entry.playback?.subtitle_size || 100;
        $('offline-subtitle-offset').value = entry.playback?.subtitle_offset || 0;
        $('offline-speed').value = entry.playback?.rate || 1;
        $('offline-detail-play').textContent = entry.playback?.progress_seconds > 0 ? text.continue : text.play;
        if (autoplay) playEntry(startAt);
        else $('offline-detail-play').focus({preventScroll: true});
    }
    function renderLibrary() {
        revoke(coverUrls); $('offline-list').replaceChildren();
        const query = $('offline-search').value.trim().toLocaleLowerCase(), filter = $('offline-type').value;
        const groups = new Map();
        for (const entry of [...entries].sort((a, b) => b.created_at - a.created_at)) {
            if (filter !== 'all' && kind(entry) !== filter || query && !entry.title.toLocaleLowerCase().includes(query)) continue;
            const key = entry.status === 'complete' ? groupKey(entry) : entry.id;
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(entry);
        }
        $('offline-empty').hidden = groups.size > 0;
        $('offline-empty').textContent = entries.length ? text.noResults : text.empty;
        $('offline-count').textContent = `${groups.size} ${text.savedItems}`;
        for (const group of groups.values()) {
            const entry = group.find(item => item.playback?.progress_seconds > 0) || [...group].sort((a, b) => seasonOf(a) - seasonOf(b) || episodeOf(a) - episodeOf(b))[0];
            const row = node('article', '', 'offline-item'), info = node('div', '', 'offline-item__info'), actions = node('div', '', 'offline-actions');
            const cover = button('', () => { returnFocus = cover; showDetail(entry); });
            cover.className = 'offline-cover'; cover.setAttribute('aria-label', `${text.details} · ${entry.title}`);
            cover.disabled = entry.status !== 'complete'; picture(cover, entry, coverUrls);
            info.append(node('h2', entry.title), node('p', [entry.year, kind(entry) === 'series' ? `${new Set(group.map(item => `${seasonOf(item)}:${episodeOf(item)}`)).size} ${text.episodes}` : text.movies,
                bytes(group.reduce((sum, item) => sum + item.size_bytes, 0))].filter(Boolean).join(' · ')));
            if (entry.status === 'complete') actions.append(button(entry.playback?.progress_seconds > 0 ? text.continue : text.play, event => { returnFocus = event.currentTarget; showDetail(entry, true); }));
            else info.append(node('p', text.partial));
            const remove = button(text.remove, () => {
                confirmRemoval(group);
            });
            remove.dataset.offlineRemove = entry.id; actions.append(remove);
            row.append(cover, info, actions); $('offline-list').append(row);
        }
    }
    function confirmRemoval(group) {
        const confirm = dialog(text.remove, `${group[0].title} · ${group.length > 1 ? text.deleteGroup(group.length) : text.deleting}`);
        const controls = node('div', '', 'offline-actions');
        controls.append(button(text.cancel, () => confirm.close()), button(text.remove, async () => {
            try {
                if (current && group.some(item => item.id === current.id)) closePlayer();
                for (const item of group) await SVOffline.remove(item.id);
                confirm.close(); await refresh();
            } catch (error) { $('offline-status').textContent = error.message; confirm.close(); await refresh(); }
        }));
        confirm.append(controls);
    }
    async function refresh() {
        entries = await SVOffline.list(); renderLibrary();
        const used = entries.reduce((sum, entry) => sum + (entry.storage_bytes || entry.size_bytes), 0);
        let storage = `${bytes(used)} / ${bytes(SVOffline.limitBytes())}`;
        try {
            const estimate = await navigator.storage.estimate();
            if (estimate.quota) storage += ` · ${es ? "Espacio disponible" : "Available space"}: ${bytes(Math.max(0, estimate.quota - estimate.usage))}`;
        } catch (_) {}
        document.getElementById("offline-storage").textContent = storage;
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
        $('offline-search').addEventListener('input', renderLibrary);
        $('offline-type').addEventListener('change', renderLibrary);
        $('offline-refresh-metadata').addEventListener('click', async event => {
            if (!navigator.onLine) { $('offline-status').textContent = text.needsOnline; return; }
            const trigger = event.currentTarget;
            trigger.disabled = true; let count = 0;
            try {
                for (const entry of entries.filter(item => item.status === 'complete')) {
                    $('offline-status').textContent = `${text.refreshing} ${entry.title}`;
                    if (await SVOffline.refreshPresentation(entry.id)) count++;
                }
                await refresh(); $('offline-status').textContent = text.refreshed(count);
            } catch (error) { $('offline-status').textContent = error.message; }
            finally { trigger.disabled = false; }
        });
        $('offline-detail-play').addEventListener('click', () => playEntry());
        $('offline-detail-delete').addEventListener('click', () => { if (current) confirmRemoval([current]); });
        $('offline-placeholder-play').setAttribute('aria-label', text.play);
        $('offline-placeholder-play').addEventListener('click', () => playEntry());
        ['offline-subtitle-select', 'offline-subtitle-size', 'offline-subtitle-offset'].forEach(id => {
            $(id).addEventListener('input', applySubtitles); $(id).addEventListener('change', applySubtitles);
        });
        $('offline-speed').addEventListener('change', () => { $('offline-video').playbackRate = Number($('offline-speed').value); persist(); });
        const switchEpisode = () => {
            const match = entries.find(item => item.status === 'complete' && groupKey(item) === groupKey(current)
                && seasonOf(item) === Number($('offline-season-select').value) && episodeOf(item) === Number($('offline-episode-select').value));
            if (match) showDetail(match, $('offline-video').hasAttribute('src'));
        };
        $('offline-season-select').addEventListener('change', () => {
            const match = entries.find(item => item.status === 'complete' && groupKey(item) === groupKey(current) && seasonOf(item) === Number($('offline-season-select').value));
            if (match) showDetail(match, $('offline-video').hasAttribute('src'));
        });
        $('offline-episode-select').addEventListener('change', switchEpisode);
        $('audio-select').addEventListener('change', () => {
            const match = entries.find(item => item.id === $('audio-select').value);
            if (match) showDetail(match, $('offline-video').hasAttribute('src'), $('offline-video').currentTime);
        });
        ['pause', 'seeked', 'ended', 'ratechange'].forEach(name => $('offline-video').addEventListener(name, persist));
        $('offline-video').addEventListener('timeupdate', () => { if (Date.now() - lastSaved > 5000) { lastSaved = Date.now(); persist(); } });
        try {
            const volume = Number(localStorage.getItem('sv_video_volume') ?? 1);
            if (Number.isFinite(volume) && volume >= 0 && volume <= 1) $('offline-video').volume = volume;
            $('offline-video').muted = localStorage.getItem('sv_video_muted') === 'true';
        } catch (_) {}
        $('offline-video').addEventListener('volumechange', () => {
            try {
                localStorage.setItem('sv_video_volume', String($('offline-video').volume));
                localStorage.setItem('sv_video_muted', String($('offline-video').muted));
            } catch (_) {}
        });
        window.addEventListener('pagehide', () => { persist(); $('offline-video')._svCleanup?.(); });
        window.SVStopSubtitles = () => { $('offline-subtitle-select').value = 'off'; applySubtitles(); };
        window.SVDestroyPlayer = closePlayer;
        window.SVToggleFs = () => {
            const player = $('offline-player');
            if (player.classList.contains('player-box--pseudo-fs')) exitFullscreen();
            else {
                player.classList.add('player-box--pseudo-fs'); document.body.classList.add('offline-fullscreen');
                const request = player.requestFullscreen || player.webkitRequestFullscreen;
                try { if (request) Promise.resolve(request.call(player)).catch(() => {}); } catch (_) {}
            }
        };
        ['fullscreenchange', 'webkitfullscreenchange'].forEach(name => document.addEventListener(name, () => {
            if (!document.fullscreenElement && !document.webkitFullscreenElement) exitFullscreen();
        }));
        const nativeBack = window.SVShell?.nativeBack;
        if (window.SVShell) window.SVShell.nativeBack = () => {
            const modal = document.querySelector('dialog[open]');
            if (modal) { modal.dispatchEvent(new Event('cancel')); modal.close(); return true; }
            if ($('offline-player').classList.contains('player-box--pseudo-fs')) { exitFullscreen(); return true; }
            if (!$('offline-player-panel').hidden) { closePlayer(); return true; }
            return nativeBack?.() || false;
        };
        document.getElementById("offline-video").addEventListener("error", () => { document.getElementById("offline-status").textContent = text.unsupported; });
        try { await SVOffline.ensureWorker(); await refresh(); }
        catch (error) { document.getElementById("offline-status").textContent = error.message; }
    }
    window.SVOfflineUI = {download, refresh};
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
})();
