/* Shared local playback with native video controls and bounded resource retries. */
(() => {
    const hevcChecks = new WeakMap();

    function supportsHEVC(video, source) {
        const mediaCapabilities = window.navigator?.mediaCapabilities;
        if (!source.hevc || !mediaCapabilities?.decodingInfo || !video.canPlayType(source.hevc.contentType)) {
            return false;
        }
        // Keep bandwidth headroom for the original stream when the browser
        // has an estimate. The H.264 fallback has a bounded encoder bitrate.
        const connection = window.navigator.connection;
        if (connection?.saveData || connection?.downlink > 0 && source.hevc.bitrate > connection.downlink * 700000) {
            return false;
        }
        if (!hevcChecks.has(source)) {
            let timer;
            const limit = new Promise(resolve => { timer = setTimeout(() => resolve(false), 400); });
            const check = Promise.resolve().then(() => mediaCapabilities.decodingInfo({type: "file", video: source.hevc}))
                .then(info => info.supported && info.smooth && info.powerEfficient)
                .catch(() => false);
            hevcChecks.set(source, Promise.race([check, limit]).finally(() => clearTimeout(timer)));
        }
        return hevcChecks.get(source);
    }

    function mediaTime(video) {
        return Number(video.dataset.playbackOffset || 0) + Number(video.currentTime || 0);
    }

    function currentTime(video) {
        return video._playbackRequest?.pendingPosition ?? mediaTime(video);
    }

    function positionChanged(video) {
        video.dispatchEvent?.(new Event("sv-playback-position"));
    }

    function containsTime(ranges, time, margin = 0) {
        for (let index = 0; index < (ranges?.length || 0); index++) {
            if (time >= ranges.start(index) && time < ranges.end(index) - margin) return true;
        }
        return false;
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

    async function open(video, source, {
        audio, start = 0, onOffset = () => {}, onError = () => {}, retry = 0, autoplay = true, allowHEVC = true,
    } = {}) {
        const rate = Number(video.playbackRate || video.defaultPlaybackRate || 1);
        close(video);
        video._svSetStatus?.("buffering");
        const request = {pendingPosition: start, lastPosition: start, starting: true, autoplay, allowHEVC};
        video._playbackRequest = request;
        video.dataset.playbackDuration = String(source.duration_seconds || 0);
        positionChanged(video);
        const restart = (position, retryCount, play) => open(video, source, {
            audio, start: position, onOffset, onError, retry: retryCount, autoplay: play, allowHEVC: request.allowHEVC,
        });
        request.restart = () => restart(currentTime(video), 0, true);
        function markFailed(error) {
            request.failed = true;
            request.starting = false;
            request.pendingPosition = request.lastPosition;
            video._svSetStatus?.("error", error.message);
            positionChanged(video);
        }
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
            if (allowHEVC && source.hevc && await supportsHEVC(video, source)) params.set("hevc", "true");
            if (video._playbackRequest !== request) return;
            const prepared = source.prepared_playback;
            if (!params.has("hevc") && retry === 0 && prepared && start === prepared.start && (audio == null || audio === prepared.audio)) {
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
            markFailed(error);
            throw error;
        } finally {
            clearTimeout(prepareTimer);
        }
        if (video._playbackRequest !== request) return;
        if (!response.ok) {
            const error = new Error(playback.detail || "Playback unavailable");
            markFailed(error);
            throw error;
        }
        video.dataset.playbackDuration = String(playback.duration_seconds || source.duration_seconds || 0);
        video.dataset.playbackOffset = String(playback.offset);
        video.dataset.playbackMode = playback.mode;
        onOffset(playback.offset);
        const isStreaming = playback.mode === "stream";
        const stopWatchdog = () => {
            clearTimeout(video._playbackStallTimer);
            video._playbackStallTimer = null;
        };
        function commitPosition() {
            request.pendingPosition = null;
            request.lastPosition = mediaTime(video);
            positionChanged(video);
        }
        video._playbackMetadata = () => {
            if (playback.mode === "file" && start > 0) video.currentTime = start;
            else commitPosition();
            video.playbackRate = rate;
            if (!request.autoplay) {
                request.starting = false;
                stopWatchdog();
                video._svSetStatus?.("");
                return;
            }
            // Live conversions arrive in fragments. Starting at the first one
            // leaves no reserve for encoding or network jitter.
            const deadline = Date.now() + 4000;
            const remaining = Number(playback.duration_seconds || 0) - start;
            const target = remaining > 0 ? Math.min(2.5, Math.max(0, remaining - 0.25)) : 2.5;
            const startBuffered = () => {
                if (video._playbackRequest !== request) return;
                if (!request.autoplay) {
                    request.starting = false;
                    stopWatchdog();
                    video._svSetStatus?.("");
                    positionChanged(video);
                    return;
                }
                let ahead = 0;
                for (let i = 0; i < (video.buffered?.length || 0); i++) {
                    if (video.buffered.start(i) <= video.currentTime + 0.1 && video.buffered.end(i) > video.currentTime) {
                        ahead = video.buffered.end(i) - video.currentTime;
                        break;
                    }
                }
                if (!isStreaming || !video.buffered || ahead >= target || Date.now() >= deadline) {
                    request.starting = false;
                    video.play().catch(error => {
                        if (video._playbackRequest === request) {
                            stopWatchdog();
                            video._svSetStatus?.(error.name === "NotAllowedError" ? "blocked" : "error");
                        }
                    });
                } else {
                    video._playbackBufferTimer = setTimeout(startBuffered, 200);
                }
            };
            startBuffered();
        };
        video.addEventListener("loadedmetadata", video._playbackMetadata, {once: true});
        function recover(retryable) {
            if (video._playbackRequest !== request || request.failed) return;
            stopWatchdog();
            clearTimeout(video._playbackBufferTimer);
            if (video._playbackRetryTimer) return;
            // A failed media request can clear currentTime. Keep the last
            // confirmed position (or the latest seek), even after rewinding.
            const position = request.pendingPosition ?? request.lastPosition;
            request.lastPosition = request.pendingPosition = position;
            positionChanged(video);
            // A device can advertise HEVC but fail on a specific bitstream.
            // Keep the H.264 fallback for the rest of this playback session.
            if (playback.video_codec === "hevc") request.allowHEVC = false;
            // A media request can receive a temporary 503 while another player
            // is closing. Unlike fetch(), <video> reports that via an error event.
            if (retry < 2 && retryable) {
                video._svSetStatus?.("buffering");
                video._playbackRetryTimer = setTimeout(() => {
                    if (video._playbackRequest !== request) return;
                    video._playbackRetryTimer = null;
                    restart(position, retry + 1, request.autoplay).catch(onError);
                }, 1500);
                positionChanged(video);
            } else {
                const error = new Error(window.APP_LANG === "es"
                    ? "No se pudo cargar el vídeo local. Pulsa Reproducir para reintentar desde este punto."
                    : "Local video could not load. Press Play to retry from this position.");
                markFailed(error);
                onError(error);
            }
        }
        video._playbackError = () => recover([2, 3, 4].includes(video.error?.code));
        video.addEventListener("error", video._playbackError);
        const signal = controller.signal;
        video.addEventListener("timeupdate", () => {
            if (video.readyState >= 2 && !video.error && !video.seeking && request.pendingPosition == null) {
                request.lastPosition = mediaTime(video);
            }
        }, {signal});
        video.addEventListener("seeked", () => {
            if (video.seeking || video.readyState < 1 || video.error) return;
            if (request.bufferedSeek && request.pendingPosition != null &&
                    Math.abs(mediaTime(video) - request.pendingPosition) > .5) {
                // Some browsers clamp seeks in progressive fragmented MP4
                // even when the target looked buffered.
                return void restart(request.pendingPosition, 0, !video.paused).catch(onError);
            }
            clearTimeout(video._playbackRetryTimer);
            video._playbackRetryTimer = null;
            request.bufferedSeek = false;
            commitPosition();
        }, {signal});
        const watchStall = () => {
            if (video.paused || video.ended || video._playbackStallTimer || video._playbackRequest !== request) return;
            request.autoplay = true;
            const position = currentTime(video);
            video._playbackStallTimer = setTimeout(() => {
                video._playbackStallTimer = null;
                if (video._playbackRequest === request && !video.paused && !video.ended &&
                        video.readyState < 3 && currentTime(video) <= position + .1) {
                    if (request.pendingPosition == null) request.lastPosition = mediaTime(video);
                    recover(true);
                }
            }, 15000);
        };
        ['waiting', 'stalled'].forEach(name => video.addEventListener(name, watchStall, {signal}));
        ['playing', 'pause', 'ended'].forEach(name => video.addEventListener(name, () => {
            if (name === 'playing') request.autoplay = true;
            if (name === 'pause' && !request.starting && !video.error) request.autoplay = false;
            if (name !== 'pause' || !request.starting) stopWatchdog();
        }, {signal}));
        video.preload = "auto";
        video.src = playback.url;
        video._playbackStallTimer = setTimeout(() => {
            video._playbackStallTimer = null;
            if (video._playbackRequest === request && !video.ended) recover(true);
        }, 15000);
    }

    async function seek(video, source, options = {}) {
        const duration = Number(video.dataset.playbackDuration || source.duration_seconds || 0);
        const target = Math.max(0, Math.min(Number(options.start) || 0, duration > .25 ? duration - .25 : Infinity));
        const request = video._playbackRequest;
        const relative = target - Number(video.dataset.playbackOffset || 0);
        const seekable = containsTime(video.buffered, relative, .25) && containsTime(video.seekable, relative);
        if (request && !request.failed && !video.error && video.readyState >= 1 &&
                (video.dataset.playbackMode === 'file' || seekable)) {
            clearTimeout(video._playbackRetryTimer);
            video._playbackRetryTimer = null;
            request.bufferedSeek = video.dataset.playbackMode === 'stream';
            request.pendingPosition = request.lastPosition = target;
            positionChanged(video);
            video.currentTime = Math.max(0, relative);
            return;
        }
        const autoplay = request?.starting ? request.autoplay : !video.paused;
        return open(video, source, {...options, start: target, autoplay, allowHEVC: request?.allowHEVC ?? true});
    }

    function retry(video) {
        return video._playbackRequest?.failed ? video._playbackRequest.restart() : null;
    }

    function togglePendingPlay(video) {
        const request = video._playbackRequest;
        if (!request?.starting && !video._playbackRetryTimer) return false;
        request.autoplay = !request.autoplay;
        positionChanged(video);
        return true;
    }

    window.SVLocalPlayback = {open, close, currentTime, seek, retry, togglePendingPlay};
})();
