/**
 * StreamVault — Shared toast notification component.
 * Provides a global showToast() with icons, auto-dismiss, and manual close.
 * Auto-creates the container if missing, so any page can use it.
 *
 * Usage:
 *   showToast("Download queued", "Breaking Bad S01E01", "ok");
 *   showToast("Error", "Could not connect to TMDB", "error");
 *   showToast("Saved", null, "ok");   // body is optional
 *   showToast("Just a message");      // mode defaults to "info"
 *
 * Modes: "ok"|"success", "error", "warn", "info"
 */
(function () {
    "use strict";

    const ICONS = {
        ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>',
        success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>',
        error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
        warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><line x1="12" y1="2" x2="12" y2="13"/><circle cx="12" cy="17.5" r="1" fill="currentColor" stroke="none"/></svg>',
        info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><line x1="12" y1="11" x2="12" y2="17"/><circle cx="12" cy="7" r="1" fill="currentColor" stroke="none"/></svg>',
    };

    const CLOSE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

    function escHtml(s) {
        return String(s == null ? "" : s)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    function getContainer() {
        let host = document.getElementById("toast-container") || document.getElementById("toast-host");
        if (!host) {
            host = document.createElement("div");
            host.id = "toast-container";
            host.className = "toast-container";
            document.body.appendChild(host);
        }
        return host;
    }

    /**
     * Show a toast notification.
     * @param {string} title    - Bold header text.
     * @param {string|null} body  - Optional body message.
     * @param {string} mode     - "ok"|"success"|"error"|"warn"|"info"
     * @param {number} duration - ms before auto-dismiss (default 5000)
     */
    function showToast(title, body, mode, duration) {
        mode = mode || "info";
        if (mode === "success") mode = "ok";
        duration = duration || 5000;

        var iconKey = (mode === "ok") ? "ok" : mode;
        var iconHtml = ICONS[iconKey] || ICONS.info;

        var toast = document.createElement("div");
        toast.className = "toast toast--" + mode;
        toast.setAttribute("role", mode === "error" ? "alert" : "status");
        toast.setAttribute("aria-atomic", "true");
        var closeLabel = (window.APP_LANG || document.documentElement.lang) === "es" ? "Cerrar aviso" : "Dismiss notification";
        toast.innerHTML =
            '<span class="toast__icon" aria-hidden="true">' + iconHtml + "</span>" +
            '<div class="toast__body">' +
                "<strong>" + escHtml(title) + "</strong>" +
                (body ? "<span>" + escHtml(body) + "</span>" : "") +
            "</div>" +
            '<button class="toast__close" type="button" aria-label="' + closeLabel + '">' + CLOSE_ICON + "</button>";

        var host = getContainer();
        host.appendChild(toast);

        // Animate in
        requestAnimationFrame(function () {
            toast.classList.add("toast--visible");
        });

        // Close button
        var closeBtn = toast.querySelector(".toast__close");
        if (closeBtn) {
            closeBtn.addEventListener("click", function () {
                dismissToast(toast);
            });
        }

        // Auto-dismiss
        var timer = setTimeout(function () {
            dismissToast(toast);
        }, duration);

        // Pause auto-dismiss on hover
        toast.addEventListener("mouseenter", function () {
            clearTimeout(timer);
        });
        toast.addEventListener("mouseleave", function () {
            timer = setTimeout(function () {
                dismissToast(toast);
            }, 2000);
        });
    }

    function dismissToast(toast) {
        toast.classList.remove("toast--visible");
        setTimeout(function () {
            if (toast.parentNode) toast.remove();
        }, 400);
    }

    // Expose globally
    window.showToast = showToast;

    // Backward-compat alias: pages that used a local `toast(msg, isError)` fn
    // can call window.toastSimple(msg, isError).
    window.toastSimple = function (msg, isError) {
        showToast(isError ? "Error" : "", msg, isError ? "error" : "ok");
    };
})();
