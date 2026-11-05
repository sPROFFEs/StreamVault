/* Shared local playback with native video controls and bounded resource retries. */
(() => {
    function currentTime(video) {
        return Number(video.dataset.playbackOffset || 0) + Number(video.currentTime || 0);
    }

    function close(video) {
        video._playbackRequest = null;
        clearTimeout(video._playbackBufferTimer);
        clearTimeout(video._playbackPrepareTimer);
        video._playbackAbort?.abort();
        clearTimeout(video._playbackRetryTimer);
        clearTimeout(video._playbackStallTimer);
        video._playbackRetryTimer = video._playbackStallTimer = null;
        if (video._playbackError) video.removeEventListener("error", video._playbackError);
        if (video._playbackMetadata) video.removeEventListener("loadedmetadata", video._playbackMetadata);
        video.pause();
        video.removeAttribute("src");
        video.load();
        video.dataset.playbackOffset = "0";
        delete video.dataset.playbackDuration;
        delete video.dataset.playbackMode;
    }

    async function open(video, source, {audio, start = 0, onOffset = () => {}, onError = () => {}, retry = 0, autoplay = true} = {}) {
        const rate = Number(video.playbackRate || video.defaultPlaybackRate || 1);
        close(video);
        video._svSetStatus?.("buffering");
        const request = {};
        video._playbackRequest = request;
        const controller = new AbortController();
        video._playbackAbort = controller;
        const params = new URLSearchParams({start: String(Math.max(0, start))});
        if (source.relative_path) params.set("path", source.relative_path);
        if (audio != null) params.set("audio", String(audio));
        let response, playback;
        let timedOut = false;
        const prepareTimer = setTimeout(() => {
            if (video._playbackRequest !== request) return;
            timedOut = true;
            controller.abort();
        }, 15000);
        video._playbackPrepareTimer = prepareTimer;
        try {
            const prepared = source.prepared_playback;
            if (retry === 0 && prepared && start === prepared.start && (audio == null || audio === prepared.audio)) {
                playback = prepared;
                response = {ok: true};
            } else {
                const endpoint = source.playback_url || "/api/downloads/files/playback";
                response = await fetch(`${endpoint}?${params}`, {signal: controller.signal});
                playback = await response.json();
            }
        } catch (error) {
            if (video._playbackRequest !== request || error.name === "AbortError" && !timedOut) return;
            if (timedOut) error = new Error(window.APP_LANG === "es" ? "El servidor tardó demasiado en preparar el vídeo." : "Video preparation timed out.");
            video._svSetStatus?.("error", error.message);
            throw error;
        } finally {
            clearTimeout(prepareTimer);
        }
        if (video._playbackRequest !== request) return;
        if (!response.ok) {
            const error = new Error(playback.detail || "Playback unavailable");
            video._svSetStatus?.("error", error.message);
            throw error;
        }
        video.dataset.playbackDuration = String(playback.duration_seconds || source.duration_seconds || 0);
        video.dataset.playbackOffset = String(playback.offset);
        video.dataset.playbackMode = playback.mode;
        onOffset(playback.offset);
        const isStreaming = playback.mode === "stream";
        let metadataReady = false;
        const stopWatchdog = () => {
            clearTimeout(video._playbackStallTimer);
            video._playbackStallTimer = null;
        };
        video._playbackMetadata = () => {
            metadataReady = true;
            stopWatchdog();
            if (playback.mode === "file" && start > 0) video.currentTime = start;
            video.playbackRate = rate;
            if (!autoplay) { video._svSetStatus?.(""); return; }
            // Live conversions arrive in fragments. Starting at the first one
            // leaves no reserve for encoding or network jitter.
            const deadline = Date.now() + 4000;
            const remaining = Number(playback.duration_seconds || 0) - start;
            const target = remaining > 0 ? Math.min(2.5, Math.max(0, remaining - 0.25)) : 2.5;
            const startBuffered = () => {
                if (video._playbackRequest !== request) return;
                let ahead = 0;
                for (let i = 0; i < (video.buffered?.length || 0); i++) {
                    if (video.buffered.start(i) <= video.currentTime + 0.1 && video.buffered.end(i) > video.currentTime) {
                        ahead = video.buffered.end(i) - video.currentTime;
                        break;
                    }
                }
                if (!isStreaming || !video.buffered || ahead >= target || Date.now() >= deadline) {
                    video.play().catch(error => {
                        if (video._playbackRequest === request) video._svSetStatus?.(error.name === "NotAllowedError" ? "blocked" : "error");
                    });
                } else {
                    video._playbackBufferTimer = setTimeout(startBuffered, 200);
                }
            };
            startBuffered();
        };
        video.addEventListener("loadedmetadata", video._playbackMetadata, {once: true});
        function recover(retryable) {
            if (video._playbackRequest !== request) return;
            stopWatchdog();
            clearTimeout(video._playbackBufferTimer);
            if (video._playbackRetryTimer) return;
            const position = Math.max(start, currentTime(video));
            // A media request can receive a temporary 503 while another player
            // is closing. Unlike fetch(), <video> reports that via an error event.
            if (retry < 2 && retryable) {
                video._svSetStatus?.("buffering");
                video._playbackRetryTimer = setTimeout(() => {
                    if (video._playbackRequest !== request) return;
                    video._playbackRetryTimer = null;
                    open(video, source, {audio, start: position, onOffset, onError, retry: retry + 1, autoplay})
                        .catch(onError);
                }, 1500);
            } else {
                video._svSetStatus?.("error");
                onError(new Error(window.APP_LANG === "es"
                    ? "No se pudo cargar el vídeo local. El servidor no está disponible o el formato no es compatible."
                    : "Local video could not load. The server is unavailable or the format is unsupported."));
            }
        }
        video._playbackError = () => recover([2, 4].includes(video.error?.code));
        video.addEventListener("error", video._playbackError);
        const signal = video._playbackAbort.signal;
        const watchStall = () => {
            if (video.paused || video.ended || video._playbackStallTimer || video._playbackRequest !== request) return;
            const position = currentTime(video);
            video._playbackStallTimer = setTimeout(() => {
                video._playbackStallTimer = null;
                if (video._playbackRequest === request && !video.paused && !video.ended &&
                        video.readyState < 3 && currentTime(video) <= position + .1) recover(true);
            }, 15000);
        };
        ['waiting', 'stalled'].forEach(name => video.addEventListener(name, watchStall, {signal}));
        ['playing', 'pause', 'ended'].forEach(name => video.addEventListener(name, () => {
            if (metadataReady) stopWatchdog();
        }, {signal}));
        video.preload = "auto";
        video.src = playback.url;
        if (autoplay) {
            video._playbackStallTimer = setTimeout(() => {
                video._playbackStallTimer = null;
                if (video._playbackRequest === request && !video.ended) recover(true);
            }, 15000);
        }
    }

    async function seek(video, source, options = {}) {
        const target = Math.max(0, Number(options.start) || 0);
        const relative = target - Number(video.dataset.playbackOffset || 0);
        let buffered = false;
        for (let index = 0; index < (video.buffered?.length || 0); index++) {
            if (relative >= video.buffered.start(index) && relative < video.buffered.end(index) - .25) buffered = true;
        }
        if (video._playbackRequest && video.readyState >= 1 && (video.dataset.playbackMode === 'file' || buffered)) {
            video.currentTime = Math.max(0, relative);
            return;
        }
        return open(video, source, {...options, start: target, autoplay: !video.paused});
    }

    window.SVLocalPlayback = {open, close, currentTime, seek};
})();
