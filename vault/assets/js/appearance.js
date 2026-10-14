/* StreamVault appearance — applies per-user UI prefs at boot.
   Pulled once from /api/preferences and applied to <html>/<body>/CSS vars. */
(function () {
    "use strict";

    const ACCENTS = {
        red:    { color: "#e50914", color2: "#ff1a24", dim: "rgba(229,9,20,0.15)", glow: "0 0 30px rgba(229,9,20,0.3)" },
        blue:   { color: "#3b82f6", color2: "#5fa3ff", dim: "rgba(59,130,246,0.15)", glow: "0 0 30px rgba(59,130,246,0.3)" },
        purple: { color: "#a855f7", color2: "#c084fc", dim: "rgba(168,85,247,0.15)", glow: "0 0 30px rgba(168,85,247,0.3)" },
        green:  { color: "#22c55e", color2: "#4ade80", dim: "rgba(34,197,94,0.15)", glow: "0 0 30px rgba(34,197,94,0.3)" },
        orange: { color: "#f97316", color2: "#fb923c", dim: "rgba(249,115,22,0.15)", glow: "0 0 30px rgba(249,115,22,0.3)" },
        pink:   { color: "#ec4899", color2: "#f472b6", dim: "rgba(236,72,153,0.15)", glow: "0 0 30px rgba(236,72,153,0.3)" },
    };

    // Filled controls need darker tones than text accents for white-label contrast.
    const ACTIONS = {
        red: ["#c7101a", "#ad0e17", "#ff7279"],
        blue: ["#1d4ed8", "#1e40af", "#93c5fd"],
        purple: ["#7e22ce", "#6b21a8", "#d8b4fe"],
        green: ["#166534", "#14532d", "#86efac"],
        orange: ["#9a3412", "#7c2d12", "#fdba74"],
        pink: ["#be185d", "#9d174d", "#f9a8d4"],
    };

    const FONT_SIZE = { sm: 0.92, normal: 1.0, lg: 1.10 };

    function applyAccent(name) {
        const a = ACCENTS[name] || ACCENTS.red;
        const r = document.documentElement.style;
        r.setProperty("--red", a.color);
        r.setProperty("--red2", a.color2);
        r.setProperty("--red-dim", a.dim);
        r.setProperty("--red-glow", a.glow);
        r.setProperty("--accent", a.color);
        const action = ACTIONS[name] || ACTIONS.red;
        r.setProperty("--action-bg", action[0]);
        r.setProperty("--action-hover", action[1]);
        r.setProperty("--accent-text", action[2]);
    }
    function applyFontSize(name) {
        const f = FONT_SIZE[name] || 1.0;
        document.documentElement.style.fontSize = `${f * 16}px`;
    }
    function applyBodyToggles(prefs) {
        document.body.classList.toggle("compact-mode", !!prefs.compact_mode);
        document.body.classList.toggle("no-anim", !!prefs.no_anim);
    }

    let prefsRequest = null;
    let prefsRevision = 0;

    function loadPrefs() {
        if (prefsRequest) return prefsRequest;
        const revision = prefsRevision;
        const request = (async () => {
            let prefs = null;
            try {
                const r = await fetch("/api/preferences", { credentials: "same-origin" });
                if (r.ok) prefs = await r.json();
            } catch (_) {}
            // A saved preference must win over an older read still in flight.
            if (revision !== prefsRevision) return window.SVPrefs;
            if (!prefs) prefsRequest = null;
            return prefs;
        })();
        prefsRequest = request;
        return request;
    }

    async function init() {
        // Read localStorage cache for instant apply (avoid theme flash)
        try {
            const cached = JSON.parse(localStorage.getItem("sv_prefs") || "null");
            if (cached) {
                applyAccent(cached.accent);
                applyFontSize(cached.font_size);
                applyBodyToggles(cached);
            }
        } catch (e) {}

        const prefs = await loadPrefs();
        if (!prefs) return;
        localStorage.setItem("sv_prefs", JSON.stringify(prefs));
        applyAccent(prefs.accent);
        applyFontSize(prefs.font_size);
        applyBodyToggles(prefs);
        window.SVPrefs = prefs;
        window.dispatchEvent(new CustomEvent("sv:prefs-loaded", { detail: prefs }));
    }

    async function savePrefs(patch) {
        const r = await fetch("/api/preferences", {
            method: "PUT",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(patch),
        });
        if (!r.ok) throw new Error("save failed");
        const out = await r.json();
        localStorage.setItem("sv_prefs", JSON.stringify(out));
        applyAccent(out.accent);
        applyFontSize(out.font_size);
        applyBodyToggles(out);
        window.SVPrefs = out;
        prefsRevision += 1;
        prefsRequest = Promise.resolve(out);
        window.dispatchEvent(new CustomEvent("sv:prefs-loaded", { detail: out }));
        return out;
    }

    window.SVAppearance = { applyAccent, applyFontSize, applyBodyToggles, loadPrefs, savePrefs, ACCENTS };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
