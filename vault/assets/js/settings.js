/* StreamVault Settings — single-page searchable settings UI.
   - Wires every input to /api/preferences (PUT-on-change, debounced).
   - In-page find via CSS.highlights API with a graceful fallback.
   - Section anchors via the left nav with IntersectionObserver active state. */
(function () {
    "use strict";

    const $ = (s, r) => (r || document).querySelector(s);
    const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

    const ACCENTS = ["red", "blue", "purple", "green", "orange", "pink"];
    const es = document.documentElement.lang === "es";
    const t = (en, spanish) => es ? spanish : en;
    const accentLabels = es ? ["Rojo", "Azul", "Morado", "Verde", "Naranja", "Rosa"]
        : ["Red", "Blue", "Purple", "Green", "Orange", "Pink"];

    let prefs = null;
    let saveTimer = null;
    let pendingPatch = {};
    let inFlight = null;

    function saveStatus(message, error = false) {
        $("#settings-save-status").textContent = message;
        $("#settings-save-status").closest(".settings-save-feedback").classList.toggle("is-error", error);
        $("#settings-save-retry").hidden = !error;
    }

    function applyPrefsToControls() {
        if (!prefs) return;
        const current = { ...prefs, ...inFlight, ...pendingPatch };
        const set = (id, val) => { const el = $(id); if (el && val != null) el.value = val; };
        const check = (id, val) => { const el = $(id); if (el && val != null) el.checked = !!val; };

        // Accent picker
        const picker = $("#accent-picker");
        if (picker && !picker.dataset.built) {
            picker.dataset.built = "1";
            ACCENTS.forEach((name, index) => {
                const sw = document.createElement("button");
                sw.type = "button";
                sw.setAttribute("aria-label", accentLabels[index]);
                sw.className = "accent-swatch";
                sw.dataset.accent = name;
                const a = window.SVAppearance.ACCENTS[name];
                sw.style.background = a.color;
                sw.addEventListener("click", () => savePatch({ accent: name }));
                picker.appendChild(sw);
            });
        }
        $$(".accent-swatch").forEach((sw) => {
            const active = sw.dataset.accent === current.accent;
            sw.classList.toggle("active", active);
            sw.setAttribute("aria-pressed", String(active));
        });

        set("#font-size", current.font_size);
        check("#opt-compact", current.compact_mode);
        check("#opt-no-anim", current.no_anim);
        set("#opt-home-view", current.home_view);
        set("#opt-default-source", current.default_source);
        check("#opt-source-failover", current.auto_source_failover);
        check("#opt-torrent-sources", current.torrent_sources);
        set("#opt-invidious", current.trailer_invidious || "");
        set("#opt-aniskip", current.aniskip_mode);
        set("#opt-rating-country", current.rating_country);
        set("#opt-age-limit", current.age_limit);
        set("#opt-lib-sort", current.library_sort);
        check("#opt-history", current.history_enabled);
        check("#opt-notif-dl", current.notif_download_done);
        check("#opt-notif-ep", current.notif_new_episodes);
        set("#opt-subtitle-lang", current.subtitle_lang || "en");
    }

    function savePatch(patch) {
        Object.assign(pendingPatch, patch);
        applyPrefsToControls();
        saveStatus(t("Saving…", "Guardando…"));
        clearTimeout(saveTimer);
        saveTimer = setTimeout(flushPatch, 200);
    }

    async function flushPatch() {
        if (inFlight || !Object.keys(pendingPatch).length) return;
        const patch = pendingPatch;
        pendingPatch = {};
        inFlight = patch;
        let saved = false;
        try {
            prefs = await window.SVAppearance.savePrefs(patch);
            saved = true;
            saveStatus(t("Saved", "Guardado"));
        } catch (e) {
            // Retain edits for an explicit retry; later edits take priority.
            pendingPatch = { ...patch, ...pendingPatch };
            saveStatus(t("Could not save. Your changes are ready to retry.", "No se pudo guardar. Tus cambios están listos para reintentar."), true);
        } finally {
            inFlight = null;
            applyPrefsToControls();
            if (saved && Object.keys(pendingPatch).length) {
                saveStatus(t("Saving…", "Guardando…"));
                clearTimeout(saveTimer);
                saveTimer = setTimeout(flushPatch, 200);
            }
        }
    }

    function wireControls() {
        const bindSelect = (id, key) => {
            const el = $(id); if (!el) return;
            el.addEventListener("change", () => savePatch({ [key]: el.value }));
        };
        const bindCheckbox = (id, key) => {
            const el = $(id); if (!el) return;
            el.addEventListener("change", () => savePatch({ [key]: el.checked }));
        };
        bindSelect("#font-size", "font_size");
        bindCheckbox("#opt-compact", "compact_mode");
        bindCheckbox("#opt-no-anim", "no_anim");
        bindSelect("#opt-home-view", "home_view");
        bindSelect("#opt-default-source", "default_source");
        bindCheckbox("#opt-source-failover", "auto_source_failover");
        bindCheckbox("#opt-torrent-sources", "torrent_sources");
        bindSelect("#opt-aniskip", "aniskip_mode");
        bindSelect("#opt-rating-country", "rating_country");
        bindSelect("#opt-age-limit", "age_limit");
        bindSelect("#opt-lib-sort", "library_sort");
        bindSelect("#opt-subtitle-lang", "subtitle_lang");
        bindCheckbox("#opt-history", "history_enabled");
        bindCheckbox("#opt-notif-dl", "notif_download_done");
        bindCheckbox("#opt-notif-ep", "notif_new_episodes");

        const inv = $("#opt-invidious");
        if (inv) {
            inv.addEventListener("change", () => savePatch({ trailer_invidious: inv.value.trim() }));
        }

        fetch("/api/preferences/subtitles", { credentials: "same-origin" })
            .then((response) => {
                if (!response.ok) throw new Error();
                return response.json();
            })
            .then((keys) => {
                if (keys.opensubtitles_api_key_set) {
                    $("#opensubtitles-key-status").textContent = keys.opensubtitles_api_key_source === "environment"
                        ? t("Server key active", "Clave del servidor activa") : t("Saved", "Guardado");
                }
            }).catch(() => {
                $("#opensubtitles-key-status").textContent = t("Key status unavailable. You can retry with Save.", "Estado de la clave no disponible. Puedes reintentar con Guardar.");
            });

        ["opensubtitles", "subdl", "wyzie"].forEach((provider) => {
            const button = $("#save-" + provider);
            const input = $("#key-" + provider);
            const status = $("#" + provider + "-key-status");
            button.addEventListener("click", async () => {
                button.disabled = true;
                status.textContent = t("Saving…", "Guardando…");
                input.removeAttribute("aria-invalid");
                try {
                    const response = await fetch("/api/preferences/subtitles", {
                        method: "PUT", credentials: "same-origin",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ [provider + "_api_key"]: input.value }),
                    });
                    const keys = await response.json();
                    if (!response.ok) throw new Error(keys.detail || t("Save failed. Try again.", "No se pudo guardar. Reinténtalo."));
                    input.value = "";
                    status.textContent = provider === "opensubtitles" && keys.opensubtitles_api_key_source === "environment"
                        ? t("Server key active", "Clave del servidor activa") : t("Saved", "Guardado");
                } catch (error) {
                    input.setAttribute("aria-invalid", "true");
                    status.textContent = error.message;
                } finally {
                    button.disabled = false;
                }
            });
        });
        $("#settings-save-retry").addEventListener("click", () => {
            if (Object.keys(pendingPatch).length) {
                saveStatus(t("Saving…", "Guardando…"));
                void flushPatch();
            } else {
                void loadPrefs();
            }
        });
    }

    function wireNav() {
        $$("#settings-nav a").forEach((a) => {
            a.addEventListener("click", (e) => {
                e.preventDefault();
                const target = $(`#sec-${a.dataset.jump}`);
                if (target) {
                    target.focus({ preventScroll: true });
                    target.scrollIntoView({ behavior: "auto", block: "start" });
                }
            });
        });
        // active state via IntersectionObserver
        const io = new IntersectionObserver((entries) => {
            for (const en of entries) {
                if (en.isIntersecting) {
                    const id = en.target.id.replace(/^sec-/, "");
                    $$("#settings-nav a").forEach((a) => {
                        const active = a.dataset.jump === id;
                        a.classList.toggle("active", active);
                        if (active) a.setAttribute("aria-current", "location");
                        else a.removeAttribute("aria-current");
                    });
                    break;
                }
            }
        }, { rootMargin: "-30% 0px -60% 0px", threshold: 0 });
        $$(".settings-section").forEach((s) => io.observe(s));
    }

    // ── Find-in-page via CSS.highlights ─────────────────────────────────────
    let highlight = null;
    function wireFind() {
        const input = $("#settings-find");
        if (!input) return;

        const supported =
            typeof CSS !== "undefined" && "highlights" in CSS && typeof Highlight !== "undefined";
        if (supported) {
            highlight = new Highlight();
            CSS.highlights.set("search-match", highlight);
            // sheet for ::highlight added in app.css
        }

        input.addEventListener("input", () => {
            const q = input.value.trim().toLowerCase();
            if (highlight) highlight.clear();
            if (!q) { $("#settings-find-status").textContent = ""; return; }
            const walker = document.createTreeWalker(
                $(".settings-layout"),
                NodeFilter.SHOW_TEXT,
                { acceptNode: (n) => {
                    if (!n.nodeValue || !n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
                    if (n.parentElement && n.parentElement.closest(".settings-search,.settings-save-feedback,#settings-nav,.btn,.icon-btn,.context-menu"))
                        return NodeFilter.FILTER_REJECT;
                    return NodeFilter.FILTER_ACCEPT;
                } }
            );
            let node;
            const ranges = [];
            while ((node = walker.nextNode())) {
                const text = node.nodeValue.toLowerCase();
                let i = text.indexOf(q);
                while (i !== -1) {
                    const r = new Range();
                    r.setStart(node, i);
                    r.setEnd(node, i + q.length);
                    ranges.push(r);
                    i = text.indexOf(q, i + q.length);
                }
            }
            if (highlight) ranges.forEach((r) => highlight.add(r));
            $("#settings-find-status").textContent = ranges.length
                ? `${ranges.length} ${t("matches", "coincidencias")}`
                : t("No matching settings. Try another search.", "No hay ajustes coincidentes. Prueba otra búsqueda.");

            // Always: scroll to first match
            if (ranges.length) {
                const first = ranges[0];
                const el = first.startContainer.parentElement;
                if (el) el.scrollIntoView({ block: "center", behavior: "auto" });
                // Make sure surrounding section is visible
                el?.closest(".settings-section")?.scrollIntoView({ block: "start", behavior: "auto" });
            }
        });
    }

    async function loadPrefs() {
        try {
            prefs = await window.SVAppearance.loadPrefs();
            if (!prefs) throw new Error();
            saveStatus(t("Preferences save automatically. API keys use Save.", "Las preferencias se guardan automáticamente. Las claves API usan Guardar."));
        } catch (error) {
            prefs = prefs || window.SVPrefs || {};
            saveStatus(t("Could not load settings. Retry to refresh them.", "No se pudieron cargar los ajustes. Reintenta para actualizarlos."), true);
        }
        applyPrefsToControls();
    }

    async function init() {
        await loadPrefs();
        wireControls();
        wireNav();
        wireFind();
    }

    window.addEventListener("sv:prefs-loaded", (e) => { prefs = e.detail; applyPrefsToControls(); });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
