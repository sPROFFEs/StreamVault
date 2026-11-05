/* StreamVault Custom Video Player — Stremio-inspired Cinematic UI & Controls */
(function() {
    "use strict";

    function fmtTime(s) {
        if (!s || isNaN(s) || !isFinite(s) || s < 0) return "0:00";
        s = Math.floor(s);
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const sc = s % 60;
        return h > 0
            ? `${h}:${m.toString().padStart(2, "0")}:${sc.toString().padStart(2, "0")}`
            : `${m}:${sc.toString().padStart(2, "0")}`;
    }

    function initCustomPlayer(video, container, options = {}) {
        if (!video || !container) return;

        const returnFocus = video._svReturnFocus?.isConnected ? video._svReturnFocus : document.activeElement;
        video._svCleanup?.();
        const events = new AbortController();
        const listen = (target, name, callback, capture = false) =>
            target.addEventListener(name, callback, {signal: events.signal, capture});
        const previousTabindex = container.getAttribute("tabindex");
        container.tabIndex = 0;
        video.removeAttribute("controls");
        const existing = container.querySelector(".sv-player");
        if (existing) existing.remove();

        const titleText = options.title || "";
        const knownDuration = Number(options.duration || 0);

        const ui = document.createElement("div");
        ui.className = "sv-player";
        ui.innerHTML = `
            <div class="sv-player__top">
                <button class="sv-player__back" title="Cerrar (Esc)" id="sv-back">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg>
                </button>
                <div class="sv-player__title"></div>
            </div>

            <div class="sv-player__status" role="status" aria-live="polite" hidden></div>
            <div class="sv-player__bottom">
                <div class="sv-player__timeline">
                    <span id="sv-time-current">0:00</span>
                    <input type="range" class="sv-player__range" id="sv-seek" min="0" max="100" value="0" step="0.05">
                    <span id="sv-time-remaining">-0:00</span>
                </div>
                <div class="sv-player__controls">
                    <div class="sv-player__group">
                        <button class="sv-btn" id="sv-play" title="Play / Pause (Space)">
                            <svg id="sv-icon-play" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                            <svg id="sv-icon-pause" viewBox="0 0 24 24" fill="currentColor" hidden><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
                        </button>
                        <button class="sv-btn sv-btn--small" id="sv-rewind" title="-10s (←)">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 17l-5-5 5-5M18 17l-5-5 5-5"/></svg>
                        </button>
                        <button class="sv-btn sv-btn--small" id="sv-forward" title="+10s (→)">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 17l5-5-5-5M6 17l5-5-5-5"/></svg>
                        </button>
                        <div class="sv-volume-group">
                            <button class="sv-btn sv-btn--small" id="sv-mute" title="Mute (M)">
                                <svg id="sv-icon-vol" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M19.07 4.93a10 10 0 010 14.14M15.54 8.46a5 5 0 010 7.07"/></svg>
                                <svg id="sv-icon-mute" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" hidden><path d="M11 5L6 9H2v6h4l5 4V5z"/><line x1="23" y1="1" x2="1" y2="23"/></svg>
                            </button>
                            <input type="range" class="sv-volume-slider" id="sv-volume" min="0" max="1" step="0.05" value="1" title="Volume (↑/↓)">
                        </div>
                    </div>

                    <div class="sv-player__group">
                        <!-- Sources / Servers Menu -->
                        <div class="sv-menu-wrapper" id="sv-source-wrap">
                            <button class="sv-btn sv-btn--small" id="sv-source-btn" title="Fuentes / Servidores">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>
                            </button>
                            <div class="sv-menu" id="sv-source-menu"></div>
                        </div>

                        <!-- Audio Tracks Menu -->
                        <div class="sv-menu-wrapper" id="sv-audio-wrap">
                            <button class="sv-btn sv-btn--small" id="sv-audio-btn" title="Pistas de Audio">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 00-3 3v7a3 3 0 006 0V5a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8"/></svg>
                            </button>
                            <div class="sv-menu" id="sv-audio-menu"></div>
                        </div>

                        <!-- Subtitles Menu -->
                        <div class="sv-menu-wrapper" id="sv-sub-wrap">
                            <button class="sv-btn sv-btn--small" id="sv-sub-btn" title="Subtítulos">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 10h4M14 10h4M6 14h12"/></svg>
                            </button>
                            <div class="sv-menu" id="sv-sub-menu"></div>
                        </div>

                        <div class="sv-menu-wrapper" id="sv-speed-wrap">
                            <button class="sv-btn sv-btn--small" id="sv-speed-btn">1×</button>
                            <div class="sv-menu" id="sv-speed-menu"></div>
                        </div>
                        <button class="sv-btn sv-btn--small" id="sv-pip" hidden>
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="2"/><rect x="11" y="11" width="8" height="6" rx="1"/></svg>
                        </button>
                        <!-- Fullscreen -->
                        <button class="sv-btn sv-btn--small" id="sv-fullscreen" title="Pantalla Completa (F)">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/></svg>
                        </button>
                    </div>
                </div>
            </div>
        `;
        container.appendChild(ui);

        const $ = (s) => ui.querySelector(s);
        const playBtn = $("#sv-play"), iconPlay = $("#sv-icon-play"), iconPause = $("#sv-icon-pause");
        const seek = $("#sv-seek"), timeCurrent = $("#sv-time-current"), timeRemaining = $("#sv-time-remaining");
        const muteBtn = $("#sv-mute"), iconVol = $("#sv-icon-vol"), iconMute = $("#sv-icon-mute"), volSlider = $("#sv-volume");
        const rewindBtn = $("#sv-rewind"), forwardBtn = $("#sv-forward");
        const fsBtn = $("#sv-fullscreen"), backBtn = $("#sv-back");
        const speedBtn = $("#sv-speed-btn"), speedMenu = $("#sv-speed-menu"), pipBtn = $("#sv-pip");

        const spanish = window.APP_LANG === "es" || document.documentElement.lang === "es";
        const labels = spanish ? {
            play: "Reproducir", pause: "Pausar", back: "Cerrar reproductor", seek: "Buscar", volume: "Volumen",
            mute: "Silenciar", unmute: "Activar sonido", rewind: "Retroceder 10 segundos", forward: "Avanzar 10 segundos",
            sources: "Fuentes", audio: "Pistas de audio", subtitles: "Subtítulos", off: "Desactivados",
            fullscreen: "Pantalla completa", exitFullscreen: "Salir de pantalla completa",
            buffering: "Cargando…", error: "Vídeo no disponible o formato no compatible. Prueba otra fuente.",
            blocked: "Pulsa reproducir para iniciar el vídeo.",
            speed: "Velocidad de reproducción", pip: "Imagen en imagen", pipError: "Imagen en imagen no disponible"
        } : {
            play: "Play", pause: "Pause", back: "Close player", seek: "Seek", volume: "Volume",
            mute: "Mute", unmute: "Unmute", rewind: "Rewind 10 seconds", forward: "Forward 10 seconds",
            sources: "Sources", audio: "Audio tracks", subtitles: "Subtitles", off: "Off",
            fullscreen: "Fullscreen", exitFullscreen: "Exit fullscreen",
            buffering: "Buffering…", error: "Video unavailable or unsupported format. Try another source.",
            blocked: "Press play to start the video.",
            speed: "Playback speed", pip: "Picture in picture", pipError: "Picture in picture unavailable"
        };
        function label(control, text) {
            control.setAttribute("aria-label", text);
            control.title = text;
        }
        [[playBtn, labels.play], [backBtn, labels.back], [seek, labels.seek], [volSlider, labels.volume],
         [rewindBtn, labels.rewind], [forwardBtn, labels.forward], [muteBtn, labels.mute],
         [fsBtn, labels.fullscreen], [pipBtn, labels.pip]].forEach(([control, text]) => label(control, text));
        ui.querySelectorAll("button").forEach(button => { button.type = "button"; });
        $(".sv-player__title").textContent = titleText;
        $(".sv-player__title").title = titleText;
        const status = $(".sv-player__status");
        let isDragging = false, destroyed = false, hideTimer = null, keyboardInteraction = false;
        let stallWatchdog = null, lastProgressTime = Date.now(), lastCurrentTime = -1, stallRecoveryCount = 0, isRecovering = false;
        function setStatus(kind, message) {
            status.textContent = message || labels[kind] || "";
            status.hidden = !status.textContent;
            showControls();
        }
        video._svSetStatus = setStatus;

        function getEffectiveDuration() {
            const offset = Number(video.dataset.playbackOffset || 0);
            // Fragmented MP4 can report only the first fragment's duration,
            // including at offset zero. The descriptor knows the full movie.
            const total = Number(video.dataset.playbackDuration || 0) || knownDuration || Number(options.getDuration?.() || 0);
            if (Number.isFinite(total) && total > 0) return total;
            if (Number.isFinite(video.duration) && video.duration > 0) return offset + video.duration;
            return knownDuration || Number(options.getDuration?.() || 0);
        }

        function getEffectiveCurrentTime() {
            return Number(video.dataset.playbackOffset || 0) + Number(video.currentTime || 0);
        }

        function updatePlayState() {
            const paused = video.paused || video.ended;
            // SVG elements do not reflect the HTMLElement.hidden property.
            iconPlay.toggleAttribute("hidden", !paused);
            iconPause.toggleAttribute("hidden", paused);
            label(playBtn, paused ? labels.play : labels.pause);
            playBtn.setAttribute("aria-pressed", String(!paused));
        }

        function paintSeek(pct) {
            seek.style.setProperty("--sv-progress", `${pct}%`);
        }
        function updateTimeAndProgress() {
            if (isDragging) return;
            const cur = getEffectiveCurrentTime(), dur = getEffectiveDuration();
            const currentText = fmtTime(cur), remainingText = dur > 0 ? "-" + fmtTime(Math.max(0, dur - cur)) : "0:00";
            // timeupdate touches only the timeline, never menus or the control tree.
            if (timeCurrent.textContent !== currentText) timeCurrent.textContent = currentText;
            if (timeRemaining.textContent !== remainingText) timeRemaining.textContent = remainingText;
            const pct = dur > 0 ? Math.min(100, Math.max(0, cur / dur * 100)) : 0;
            seek.disabled = rewindBtn.disabled = forwardBtn.disabled = !(dur > 0);
            seek.value = pct;
            seek.setAttribute("aria-valuetext", `${currentText} / ${fmtTime(dur)}`);
            paintSeek(pct);
            let bufferedEnd = cur;
            for (let index = 0; index < (video.buffered?.length || 0); index++) {
                if (video.buffered.start(index) <= video.currentTime + .25 && video.buffered.end(index) >= video.currentTime) {
                    bufferedEnd = Number(video.dataset.playbackOffset || 0) + video.buffered.end(index);
                    break;
                }
            }
            seek.style.setProperty('--sv-buffered', `${dur > 0 ? Math.min(100, Math.max(pct, bufferedEnd / dur * 100)) : 0}%`);
        }
        function updateVolumeUI() {
            const muted = video.muted || video.volume === 0;
            volSlider.value = video.muted ? 0 : video.volume;
            iconVol.toggleAttribute("hidden", muted);
            iconMute.toggleAttribute("hidden", !muted);
            label(muteBtn, muted ? labels.unmute : labels.mute);
            muteBtn.setAttribute("aria-pressed", String(muted));
            volSlider.setAttribute("aria-valuetext", `${Math.round(volSlider.value * 100)}%`);
        }

        ["play", "pause", "ended"].forEach(name => listen(video, name, updatePlayState));
        listen(video, "play", () => {
            lastProgressTime = Date.now();
            lastCurrentTime = Number(video.currentTime || 0);
            isRecovering = false;
            showControls();
        });
        listen(video, "playing", () => {
            lastProgressTime = Date.now();
            lastCurrentTime = Number(video.currentTime || 0);
            stallRecoveryCount = 0;
            isRecovering = false;
            setStatus("");
        });
        ["timeupdate", "progress", "loadedmetadata", "durationchange", "seeked"].forEach(name => listen(video, name, () => {
            updateTimeAndProgress();
            checkStall();
        }));
        listen(video, "volumechange", updateVolumeUI);
        ["waiting", "stalled"].forEach(name => listen(video, name, () => {
            setStatus("buffering");
            if (Date.now() - lastProgressTime >= 1500) checkStall();
        }));
        listen(video, "seeking", () => setStatus("buffering"));
        ["canplay", "seeked"].forEach(name => listen(video, name, () => setStatus("")));
        listen(video, "error", () => setStatus("error"));
        listen(video, "loadstart", () => setStatus("buffering"));
        function checkStall() {
            // Local and federated playback own their bounded reconnects.
            // A second watchdog must not seek backwards while they buffer.
            if (destroyed || isDragging || video.paused || video.ended || video._playbackRequest) return;
            const now = Date.now();
            const curTime = Number(video.currentTime || 0);

            // Forward progression confirms video decoder is healthy
            if (curTime > lastCurrentTime && (curTime - lastCurrentTime) >= 0.15) {
                lastCurrentTime = curTime;
                lastProgressTime = now;
                stallRecoveryCount = 0;
                isRecovering = false;
                return;
            }

            const stalledDuration = now - lastProgressTime;
            if (stalledDuration >= 2500 && !isRecovering) {
                isRecovering = true;
                stallRecoveryCount++;

                if (stallRecoveryCount > 4) {
                    setStatus("buffering");
                    return;
                }

                if (stallRecoveryCount === 1) {
                    try {
                        const nudge = Math.max(0, curTime - 0.2);
                        lastCurrentTime = nudge;
                        video.currentTime = nudge;
                        video.play()?.catch?.(() => {});
                    } catch (_) {}
                    isRecovering = false;
                    return;
                }

                const effCurrent = getEffectiveCurrentTime();
                const target = Math.max(0, Math.round((effCurrent - 4) * 100) / 100);
                setStatus("buffering");
                lastCurrentTime = target - Number(video.dataset.playbackOffset || 0);
                seekTo(target);
                lastProgressTime = now;
                isRecovering = false;
            }
        }
        stallWatchdog = setInterval(checkStall, 1000);


        async function togglePlay() {
            try {
                if (video.paused || video.ended) await video.play();
                else video.pause();
            } catch (error) {
                if (!destroyed) setStatus(error.name === "NotAllowedError" ? "blocked" : "error");
            }
        }
        playBtn.onclick = togglePlay;
        listen(container, "click", event => {
            if (event.target === container || event.target === video) togglePlay();
        });

        function seekTo(target) {
            const dur = getEffectiveDuration();
            if (!(dur > 0)) return;
            target = Math.min(dur, Math.max(0, target));
            try {
                if (options.onSeek) {
                    Promise.resolve(options.onSeek(target)).catch(error => { if (!destroyed) setStatus("error", error.message); });
                } else {
                    video.currentTime = Math.max(0, target - Number(video.dataset.playbackOffset || 0));
                }
            } catch (error) { setStatus("error", error.message); }
            showControls();
        }
        seek.oninput = () => {
            isDragging = true;
            timeCurrent.textContent = fmtTime(seek.value / 100 * getEffectiveDuration());
            paintSeek(seek.value);
            showControls();
        };
        seek.onchange = () => {
            isDragging = false;
            seekTo(seek.value / 100 * getEffectiveDuration());
        };
        listen(seek, "pointercancel", () => { isDragging = false; updateTimeAndProgress(); showControls(); });
        rewindBtn.onclick = () => seekTo(getEffectiveCurrentTime() - 10);
        forwardBtn.onclick = () => seekTo(getEffectiveCurrentTime() + 10);
        muteBtn.onclick = () => {
            if (video.muted || video.volume === 0) {
                video.muted = false;
                if (video.volume === 0) video.volume = 1;
            } else video.muted = true;
        };
        volSlider.oninput = () => { video.muted = false; video.volume = Number(volSlider.value); };
        fsBtn.onclick = () => window.SVToggleFs?.();
        backBtn.onclick = () => window.SVDestroyPlayer?.();
        pipBtn.hidden = !(document.pictureInPictureEnabled && video.requestPictureInPicture && !video.disablePictureInPicture);
        pipBtn.onclick = async () => {
            try {
                if (document.pictureInPictureElement === video) await document.exitPictureInPicture();
                else await video.requestPictureInPicture();
            } catch (_) { label(pipBtn, labels.pipError); }
        };

        function showControls(event) {
            if (destroyed) return;
            if (event?.type.startsWith("pointer") || event?.type === "click") keyboardInteraction = false;
            if (event?.type === "focusin") keyboardInteraction = event.target.matches(":focus-visible");
            container.classList.add("sv-active");
            container.classList.remove("sv-hide-cursor");
            clearTimeout(hideTimer);
            hideTimer = setTimeout(() => {
                if (!video.paused && !video.ended && status.hidden && !isDragging &&
                    !(keyboardInteraction && container.contains(document.activeElement)) && !ui.querySelector(".sv-menu--open")) {
                    container.classList.remove("sv-active");
                    container.classList.add("sv-hide-cursor");
                }
            }, 3000);
        }
        ["pointermove", "pointerdown", "click", "focusin", "focusout"].forEach(name => listen(container, name, showControls));
        listen(video, "pause", showControls);
        listen(window, "resize", showControls);
        listen(window, "orientationchange", showControls);
        function syncFullscreen() {
            const active = !!(document.fullscreenElement || document.webkitFullscreenElement || container.classList.contains("player-box--pseudo-fs"));
            label(fsBtn, active ? labels.exitFullscreen : labels.fullscreen);
            fsBtn.setAttribute("aria-pressed", String(active));
        }
        listen(document, "fullscreenchange", syncFullscreen);
        listen(document, "webkitfullscreenchange", syncFullscreen);

        const sourceWrap = $("#sv-source-wrap"), sourceBtn = $("#sv-source-btn"), sourceMenu = $("#sv-source-menu");
        const audioWrap = $("#sv-audio-wrap"), audioBtn = $("#sv-audio-btn"), audioMenu = $("#sv-audio-menu");
        const subWrap = $("#sv-sub-wrap"), subBtn = $("#sv-sub-btn"), subMenu = $("#sv-sub-menu");
        const menus = [[sourceBtn, sourceMenu, labels.sources], [audioBtn, audioMenu, labels.audio],
                       [subBtn, subMenu, labels.subtitles], [speedBtn, speedMenu, labels.speed]];
        function closeAllMenus(restore = false) {
            menus.forEach(([button, menu]) => {
                const open = menu.classList.contains("sv-menu--open");
                menu.classList.remove("sv-menu--open");
                menu.hidden = true;
                button.setAttribute("aria-expanded", "false");
                if (open && restore && button.isConnected) button.focus();
            });
        }
        menus.forEach(([button, menu, text]) => {
            label(button, text);
            button.setAttribute("aria-controls", menu.id);
            button.setAttribute("aria-expanded", "false");
            menu.hidden = true;
            button.onclick = () => {
                const open = menu.classList.contains("sv-menu--open");
                closeAllMenus();
                if (!open) {
                    menu.hidden = false;
                    menu.classList.add("sv-menu--open");
                    button.setAttribute("aria-expanded", "true");
                    menu.querySelector("button")?.focus();
                }
                showControls();
            };
        });
        listen(container, "click", event => { if (!event.target.closest(".sv-menu-wrapper")) closeAllMenus(); });

        function handleKey(event) {
            const active = document.activeElement;
            if (destroyed || event.defaultPrevented || !container.contains(active)) return;
            keyboardInteraction = true;
            showControls();
            const key = event.key.toLowerCase();
            // Shared dialogs render outside the fullscreen container. Leave
            // fullscreen before their own search/help handlers receive the key.
            if (((event.ctrlKey || event.metaKey) && key === "f" || key === "?") &&
                (document.fullscreenElement || document.webkitFullscreenElement || container.classList.contains("player-box--pseudo-fs"))) {
                window.SVToggleFs?.();
                return;
            }
            if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || active.isContentEditable ||
                active.matches("textarea, select, input:not([type=range])")) return;
            const back = ["escape", "goback", "browserback", "backspace"].includes(key) || [10009, 461].includes(event.keyCode);
            if (back) {
                event.preventDefault();
                event.stopImmediatePropagation();
                if (ui.querySelector(".sv-menu--open")) closeAllMenus(true);
                else if (document.fullscreenElement || document.webkitFullscreenElement || container.classList.contains("player-box--pseudo-fs")) window.SVToggleFs?.();
                else window.SVDestroyPlayer?.();
                return;
            }
            // Native sliders own arrows and Space/Enter on buttons retain native activation.
            if (active.matches("input")) return;
            if (key.startsWith("arrow") && active.matches("button")) {
                const menu = active.closest(".sv-menu");
                const items = Array.from((menu || ui).querySelectorAll("button, input")).filter(item =>
                    !item.disabled && !item.closest("[hidden]") && getComputedStyle(item).visibility !== "hidden");
                const index = items.indexOf(active), direction = ["arrowright", "arrowdown"].includes(key) ? 1 : -1;
                const next = items[index + direction];
                event.preventDefault();
                if (next) next.focus();
                showControls();
                return;
            }
            let handled = true;
            switch (key) {
                case " ": if (active.matches("button")) return; togglePlay(); break;
                case "k": case "mediaplaypause": togglePlay(); break;
                case "f": window.SVToggleFs?.(); break;
                case "m": video.muted = !video.muted; break;
                case "arrowleft": seekTo(getEffectiveCurrentTime() - 10); break;
                case "arrowright": seekTo(getEffectiveCurrentTime() + 10); break;
                case "arrowup": video.volume = Math.min(1, video.volume + .05); break;
                case "arrowdown": video.volume = Math.max(0, video.volume - .05); break;
                default: handled = false;
            }
            if (handled) { event.preventDefault(); showControls(); }
        }
        // Window capture precedes shared shell Back handling; only player focus is owned.
        listen(window, "keydown", handleKey, true);

        function menuItem(menu, text, selected, action) {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = text;
            button.classList.toggle("active", selected);
            button.setAttribute("aria-pressed", String(selected));
            button.onclick = () => { action(); if (!destroyed) closeAllMenus(true); };
            menu.appendChild(button);
        }
        function syncSelect(id, wrapper, menu, minimum) {
            const select = document.getElementById(id);
            wrapper.hidden = !select || select.options.length < minimum ||
                (id === "audio-select" && document.getElementById("audio-wrapper")?.style.display === "none");
            menu.replaceChildren();
            if (wrapper.hidden) return;
            Array.from(select.options).forEach(option => menuItem(menu, option.textContent, option.selected, () => {
                select.value = option.value;
                select.dispatchEvent(new Event("change"));
            }));
        }
        function syncSources() { syncSelect("source-select", sourceWrap, sourceMenu, 1); }
        function syncAudio() { syncSelect("audio-select", audioWrap, audioMenu, 2); }
        function syncSpeed() {
            speedBtn.textContent = `${video.playbackRate || 1}×`;
            speedMenu.replaceChildren();
            [.5, .75, 1, 1.25, 1.5, 1.75, 2].forEach(rate => menuItem(speedMenu, `${rate}×`, video.playbackRate === rate, () => {
                video.defaultPlaybackRate = rate;
                video.playbackRate = rate;
            }));
        }
        listen(video, 'ratechange', syncSpeed);
        function syncSubtitles() {
            const buttons = Array.from(document.querySelectorAll("#subtitle-list .source-item"));
            subWrap.hidden = !buttons.length;
            subMenu.replaceChildren();
            if (!buttons.length) return;
            menuItem(subMenu, labels.off, !buttons.some(button => button.classList.contains("source-item--active")), () => window.SVStopSubtitles?.());
            buttons.forEach(button => menuItem(subMenu, button.textContent.trim(), button.classList.contains("source-item--active"), () => button.click()));
        }
        const observer = new MutationObserver(records => {
            const changed = new Set(records.map(record => record.target.closest("#source-select, #audio-select, #subtitle-list")?.id));
            if (records.some(record => record.target === container)) syncFullscreen();
            if (changed.has("source-select")) syncSources();
            if (changed.has("audio-select")) syncAudio();
            if (changed.has("subtitle-list")) syncSubtitles();
        });
        observer.observe(container, {attributes: true, attributeFilter: ["class"]});
        ["source-select", "audio-select", "subtitle-list"].forEach(id => {
            const target = document.getElementById(id);
            if (!target) return;
            observer.observe(target, {childList: true, subtree: true, attributes: true,
                attributeFilter: id === "subtitle-list" ? ["class"] : ["selected"]});
        });
        ["source-select", "audio-select"].forEach(id => {
            const select = document.getElementById(id);
            if (select) listen(select, "change", id === "source-select" ? syncSources : syncAudio);
        });

        video._svCleanup = () => {
            if (destroyed) return;
            destroyed = true;
            events.abort();
            observer.disconnect();
            clearTimeout(hideTimer);
            clearInterval(stallWatchdog);
            ui.remove();
            container.classList.remove("sv-active", "sv-hide-cursor");
            if (previousTabindex === null) container.removeAttribute("tabindex");
            else container.setAttribute("tabindex", previousTabindex);
            video.setAttribute("controls", "");
            delete video._svSetStatus;
            delete video._svCleanup;
        };
        video._svReturnFocus = returnFocus;
        syncSources(); syncAudio(); syncSubtitles(); syncSpeed();
        updatePlayState(); updateTimeAndProgress(); updateVolumeUI(); syncFullscreen(); showControls();
        playBtn.focus({preventScroll: true});
    }

    window.SVInitCustomPlayer = initCustomPlayer;
})();
