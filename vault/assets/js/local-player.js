/* Shared local playback with native video controls and bounded resource retries. */
(() => {
    function currentTime(video) {
        return Number(video.dataset.playbackOffset || 0) + Number(video.currentTime || 0);
    }

    function close(video) {
        video._playbackRequest = null;
        clearTimeout(video._playbackBufferTimer);
        video._playbackAbort?.abort();
        clearTimeout(video._playbackRetryTimer);
        if (video._playbackError) video.removeEventListener("error", video._playbackError);
        if (video._playbackMetadata) video.removeEventListener("loadedmetadata", video._playbackMetadata);
        video.pause();
        video.removeAttribute("src");
        video.load();
        video.dataset.playbackOffset = "0";
        delete video.dataset.playbackDuration;
    }

    async function open(video, source, {audio, start = 0, onOffset = () => {}, onError = () => {}, retry = 0} = {}) {
        close(video);
        video._svSetStatus?.("buffering");
        const request = {};
        video._playbackRequest = request;
        video._playbackAbort = new AbortController();
        const params = new URLSearchParams({start: String(Math.max(0, start))});
        if (source.relative_path) params.set("path", source.relative_path);
        if (audio != null) params.set("audio", String(audio));
        let response, playback;
        try {
            const endpoint = source.playback_url || "/api/downloads/files/playback";
            response = await fetch(`${endpoint}?${params}`, {signal: video._playbackAbort.signal});
            playback = await response.json();
        } catch (error) {
            if (error.name === "AbortError" || video._playbackRequest !== request) return;
            video._svSetStatus?.("error", error.message);
            throw error;
        }
        if (video._playbackRequest !== request) return;
        if (!response.ok) {
            const error = new Error(playback.detail || "Playback unavailable");
            video._svSetStatus?.("error", error.message);
            throw error;
        }
        video.dataset.playbackDuration = String(playback.duration_seconds || source.duration_seconds || 0);
        video.dataset.playbackOffset = String(playback.offset);
        onOffset(playback.offset);
        const isStreaming = playback.mode === "stream";
        video._playbackMetadata = () => {
            if (playback.mode === "file" && start > 0) video.currentTime = start;
            // Live conversions arrive in fragments. Starting at the first one
            // leaves no reserve for encoding or network jitter.
            const deadline = Date.now() + 8000;
            const remaining = Number(playback.duration_seconds || 0) - start;
            const target = remaining > 0 ? Math.min(5, Math.max(0, remaining - 0.25)) : 5;
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
        video._playbackError = () => {
            if (video._playbackRequest !== request) return;
            const position = Math.max(start, currentTime(video));
            // A media request can receive a temporary 503 while another player
            // is closing. Unlike fetch(), <video> reports that via an error event.
            if (retry < 2 && [2, 4].includes(video.error?.code)) {
                video._svSetStatus?.("buffering");
                video._playbackRetryTimer = setTimeout(() => {
                    if (video._playbackRequest !== request) return;
                    open(video, source, {audio, start: position, onOffset, onError, retry: retry + 1})
                        .catch(onError);
                }, 1500);
            } else {
                video._svSetStatus?.("error");
                onError(new Error(window.APP_LANG === "es"
                    ? "No se pudo cargar el vídeo local. El servidor no está disponible o el formato no es compatible."
                    : "Local video could not load. The server is unavailable or the format is unsupported."));
            }
        };
        video.addEventListener("error", video._playbackError);
        video.preload = "auto";
        video.src = playback.url;
    }

    window.SVLocalPlayback = {open, close, currentTime};
})();
