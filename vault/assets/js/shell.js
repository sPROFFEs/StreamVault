/* Shared navigation, modal focus and remote navigation. Page overlays may opt
   in with SVShell.open(root, {initialFocus, trigger, onClose}) / close(root). */
(function () {
    "use strict";

    const focusSelector = 'a[href], button, input, select, textarea, [tabindex]';
    const stack = [];
    const isolation = new Map();
    let serial = 0;
    let closing = false;
    let previousOverflow = false;
    let destination = null;

    function visible(element) {
        return element && element.isConnected && !element.closest('[hidden], [inert]') &&
            getComputedStyle(element).visibility !== "hidden" && element.getClientRects().length > 0;
    }

    function controls(root) {
        return Array.from(root.querySelectorAll(focusSelector)).filter(element =>
            element.tabIndex >= 0 && !element.disabled && visible(element));
    }

    function main() {
        return document.querySelector('main, .media-layout');
    }

    function focus(element) {
        if (visible(element)) element.focus({preventScroll: true});
    }

    function isolate() {
        isolation.forEach((inert, element) => { element.inert = inert; });
        isolation.clear();
        const top = stack.at(-1);
        if (!top) {
            document.body.classList.toggle("shell-overlay-open", previousOverflow);
            return;
        }
        // Isolate siblings at each ancestor, so opt-in page overlays need not
        // be direct body children. Preserve pre-existing inert state.
        let active = top.root;
        while (active && active !== document.body) {
            Array.from(active.parentElement.children).forEach(element => {
                if (element !== active) {
                    isolation.set(element, element.inert);
                    element.inert = true;
                }
            });
            active = active.parentElement;
        }
        document.body.classList.add("shell-overlay-open");
    }

    function open(root, options = {}) {
        if (!root || stack.some(entry => entry.root === root) || closing) return;
        if (!stack.length) previousOverflow = document.body.classList.contains("shell-overlay-open");
        const dialog = root.matches('[role="dialog"]') ? root : root.querySelector('[role="dialog"]');
        if (!dialog) return;
        const entry = {
            root, dialog, options, id: `sv-${Date.now()}-${++serial}`,
            restore: options.trigger || document.activeElement,
        };
        // A same-URL entry lets browser/remote Back dismiss the overlay first.
        history.pushState({...history.state, svShellOverlay: entry.id}, "", location.href);
        stack.push(entry);
        root.dataset.shellOverlay = "";
        root.hidden = false;
        root.style.display = options.display || "flex";
        root.style.zIndex = String(1100 + stack.length);
        dialog.tabIndex = -1;
        if (options.trigger) options.trigger.setAttribute("aria-expanded", "true");
        isolate();
        focus(options.initialFocus || controls(dialog)[0] || dialog);
    }

    function dismiss() {
        const entry = stack.pop();
        if (!entry) return;
        entry.root.hidden = true;
        entry.root.style.display = "none";
        if (entry.options.trigger) entry.options.trigger.setAttribute("aria-expanded", "false");
        isolate();
        if (entry.options.onClose) entry.options.onClose();
        const parent = stack.at(-1);
        const fallback = parent ? controls(parent.dialog)[0] || parent.dialog : document.querySelector(".top-nav__brand") || main();
        focus(visible(entry.restore) ? entry.restore : fallback);
    }

    function close(root) {
        const top = stack.at(-1);
        if (!top || (root && top.root !== root) || closing) return;
        closing = true;
        history.back();
    }

    window.addEventListener("popstate", event => {
        closing = false;
        const id = event.state && event.state.svShellOverlay;
        while (stack.length && stack.at(-1).id !== id) dismiss();
        if (destination && !stack.length) {
            const url = destination;
            destination = null;
            location.assign(url);
        }
    });

    function navigate(url) {
        if (!stack.length) { location.assign(url); return; }
        if (closing) return;
        destination = url;
        closing = true;
        // Remove overlay entries before leaving, so one Back returns to the page.
        history.go(-stack.length);
    }

    document.addEventListener("click", event => {
        if (!stack.length || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        const link = event.target.closest("a[href]");
        if (!link || !stack.at(-1).dialog.contains(link) || link.hasAttribute("download") ||
            (link.target && link.target !== "_self")) return;
        event.preventDefault();
        navigate(link.href);
    });

    function field(element) {
        return element && (element.matches('input, textarea, select, video, audio, [role="slider"]') || element.isContentEditable);
    }

    function back(event) {
        return ["Escape", "BrowserBack", "GoBack"].includes(event.key) ||
            [10009, 461].includes(event.keyCode) ||
            (event.key === "Backspace" && !field(event.target));
    }

    // Capture modal dismissal before page handlers; close only the top overlay.
    document.addEventListener("keydown", event => {
        const top = stack.at(-1);
        if (!top) {
            if (["BrowserBack", "GoBack"].includes(event.key) || [10009, 461].includes(event.keyCode)) {
                event.preventDefault();
                history.back();
            }
            return;
        }
        if (back(event) || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && !field(event.target))) {
            event.preventDefault();
            event.stopImmediatePropagation();
            close(top.root);
        } else if (event.key === "Tab") {
            const items = controls(top.dialog);
            const first = items[0] || top.dialog;
            const last = items.at(-1) || top.dialog;
            if (event.shiftKey && (document.activeElement === first || document.activeElement === top.dialog)) {
                event.preventDefault();
                focus(last);
            } else if (!event.shiftKey && (document.activeElement === last || !top.dialog.contains(document.activeElement))) {
                event.preventDefault();
                focus(first);
            }
        }
    }, true);

    document.addEventListener("focusin", event => {
        const top = stack.at(-1);
        if (top && !top.dialog.contains(event.target)) focus(controls(top.dialog)[0] || top.dialog);
    });

    function moveFocus(event) {
        const top = stack.at(-1);
        // Target handlers and native editing have already run. Keep modal keys
        // away from background document-level player shortcuts.
        if (top && event.key.startsWith("Arrow")) event.stopImmediatePropagation();
        if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
            !event.key.startsWith("Arrow") || field(event.target)) return;
        const active = document.activeElement;
        if (!top && active.closest('.sv-player, .player-box, [data-native-navigation]')) return;
        const nav = active.closest('.top-nav, .mobile-tabbar');
        let next;
        if (nav && !top && ["ArrowDown", "ArrowUp"].includes(event.key)) {
            const items = main() ? controls(main()) : [];
            next = event.key === "ArrowDown" ? items[0] : items.at(-1);
        } else {
            const scope = top ? top.dialog : active.closest('[role="dialog"]') || nav || document.body;
            const from = active.getBoundingClientRect();
            const horizontal = ["ArrowLeft", "ArrowRight"].includes(event.key);
            const sign = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1;
            const x = from.x + from.width / 2;
            const y = from.y + from.height / 2;
            let best = Infinity;
            controls(scope).forEach(element => {
                if (element === active || element.classList.contains("skip-link")) return;
                const rect = element.getBoundingClientRect();
                const dx = rect.x + rect.width / 2 - x;
                const dy = rect.y + rect.height / 2 - y;
                const forward = (horizontal ? dx : dy) * sign;
                const cross = Math.abs(horizontal ? dy : dx);
                if (forward <= 1) return;
                const score = forward + cross * 3;
                if (score < best) { best = score; next = element; }
            });
        }
        if (next) {
            event.preventDefault();
            event.stopImmediatePropagation();
            focus(next);
            next.scrollIntoView({block: "nearest", inline: "nearest"});
        }
    }

    document.addEventListener("keydown", moveFocus);

    function init() {
        const content = main();
        if (content) {
            if (!content.id) content.id = "main-content";
            content.tabIndex = -1;
            if (content.tagName !== "MAIN") content.setAttribute("role", "main");
            const skip = document.querySelector(".skip-link");
            if (skip) {
                skip.href = `#${content.id}`;
                skip.addEventListener("click", () => { content.focus(); });
            }
        }
        const compact = matchMedia("(max-width: 640px)");
        function syncNavigation() {
            const top = stack.at(-1);
            if (!compact.matches && top && top.root.id === "mobile-more-sheet") close(top.root);
            else if (!stack.length && !visible(document.activeElement)) focus(document.querySelector(".top-nav__brand"));
        }
        compact.addEventListener("change", syncNavigation);
        // Keep the query/controller alive and handle rapid viewport updates.
        window.addEventListener("resize", syncNavigation);

        function handleScroll() {
            const nav = document.querySelector(".top-nav");
            if (nav) nav.classList.toggle("top-nav--scrolled", window.scrollY > 20);
        }
        window.addEventListener("scroll", handleScroll, { passive: true });
        handleScroll();
    }

    // Android asks the page to consume transient UI before native history/exit.
    // Return a boolean; do not navigate when the page has nothing to dismiss.
    function nativeBack() {
        if (stack.length) { close(); return true; }
        const exit = document.querySelector(".player-box--pseudo-fs #pseudo-fs-exit");
        if (exit) { exit.click(); return true; }
        const exitFullscreen = document.exitFullscreen || document.webkitExitFullscreen;
        if (exitFullscreen && (document.fullscreenElement || document.webkitFullscreenElement)) {
            Promise.resolve(exitFullscreen.call(document)).catch(() => {});
            return true;
        }
        return false;
    }

    window.SVShell = {open, close, navigate, nativeBack, isOpen: root => stack.some(entry => entry.root === root),
        back: () => stack.length ? close() : history.back()};
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
