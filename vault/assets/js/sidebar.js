/* StreamVault sidebar — watchlist strip, drag-and-drop reorder, shortcuts. */
(function () {
    "use strict";

    const $ = (sel, root) => (root || document).querySelector(sel);
    const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

    const STORAGE_KEY = "sv_saved_order";

    function loadOrder() {
        try {
            return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
        } catch (e) {
            return [];
        }
    }
    function saveOrder(order) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(order));
    }

    function tmdbImg(path, size) {
        if (!path) return "";
        return `https://image.tmdb.org/t/p/${size || "w154"}${path}`;
    }

    async function fetchFavorites() {
        try {
            const res = await fetch("/api/library/favorites", { credentials: "same-origin" });
            if (!res.ok) return [];
            const data = await res.json();
            return Array.isArray(data) ? data : (data.items || []);
        } catch (e) {
            return [];
        }
    }

    function applyOrder(items, order) {
        const idx = new Map(order.map((id, i) => [id, i]));
        const ordered = [...items].sort((a, b) => {
            const keyA = `${a.media_type || "movie"}:${a.tmdb_id || a.id}`;
            const keyB = `${b.media_type || "movie"}:${b.tmdb_id || b.id}`;
            const ai = idx.has(keyA) ? idx.get(keyA) : Infinity;
            const bi = idx.has(keyB) ? idx.get(keyB) : Infinity;
            if (ai === bi) return 0;
            return ai - bi;
        });
        return ordered;
    }

    function renderSaved(items) {
        const root = $("#sidebar-saved");
        if (!root) return;
        root.innerHTML = "";
        items.forEach((item) => {
            const key = `${item.media_type || "movie"}:${item.tmdb_id || item.id}`;
            const id = item.tmdb_id || item.id;
            const mt = item.media_type || "movie";
            const a = document.createElement("a");
            a.className = "saved-thumb";
            a.href = `/media/${id}?type=${mt}`;
            a.draggable = true;
            a.dataset.key = key;
            a.title = item.title || item.name || "";
            if (item.poster_path) {
                const img = document.createElement("img");
                img.src = tmdbImg(item.poster_path, "w154");
                img.alt = "";
                img.loading = "lazy";
                a.appendChild(img);
            } else {
                const div = document.createElement("div");
                div.className = "no-img";
                div.textContent = (item.title || "?").slice(0, 1).toUpperCase();
                a.appendChild(div);
            }
            attachDrag(a);
            attachContextMenu(a, item);
            root.appendChild(a);
        });
    }

    let dragSrc = null;
    function attachDrag(el) {
        el.addEventListener("dragstart", (e) => {
            dragSrc = el;
            el.classList.add("dragging");
            e.dataTransfer.effectAllowed = "move";
        });
        el.addEventListener("dragend", () => {
            el.classList.remove("dragging");
            dragSrc = null;
            persistOrder();
        });
        el.addEventListener("dragover", (e) => {
            e.preventDefault();
            if (!dragSrc || dragSrc === el) return;
            const rect = el.getBoundingClientRect();
            const before = e.clientY < rect.top + rect.height / 2;
            const parent = el.parentNode;
            parent.insertBefore(dragSrc, before ? el : el.nextSibling);
        });
    }

    function persistOrder() {
        const root = $("#sidebar-saved");
        if (!root) return;
        const order = $$(".saved-thumb", root).map((el) => el.dataset.key);
        saveOrder(order);
    }

    function attachContextMenu(el, item) {
        el.addEventListener("contextmenu", (e) => {
            e.preventDefault();
            showContextMenu(e.clientX, e.clientY, [
                {
                    label: "Remove from list",
                    danger: true,
                    onClick: async () => {
                        const id = item.tmdb_id || item.id;
                        const mt = item.media_type || "movie";
                        await fetch(`/api/library/favorites/${mt}/${id}`, {
                            method: "DELETE",
                            credentials: "same-origin",
                        });
                        await renderFromAPI();
                    },
                },
            ]);
        });
    }

    function showContextMenu(x, y, items) {
        closeContextMenu();
        const menu = document.createElement("div");
        menu.className = "context-menu";
        menu.style.left = `${x}px`;
        menu.style.top = `${y}px`;
        items.forEach((it) => {
            const row = document.createElement("div");
            row.className = "context-menu__item" + (it.danger ? " context-menu__item--danger" : "");
            row.textContent = it.label;
            row.addEventListener("click", () => {
                closeContextMenu();
                it.onClick && it.onClick();
            });
            menu.appendChild(row);
        });
        document.body.appendChild(menu);
        const close = (ev) => {
            if (!menu.contains(ev.target)) closeContextMenu();
        };
        setTimeout(() => document.addEventListener("click", close, { once: true }), 0);
    }
    function closeContextMenu() {
        $$(".context-menu").forEach((m) => m.remove());
    }

    async function renderFromAPI() {
        const items = await fetchFavorites();
        const order = loadOrder();
        renderSaved(applyOrder(items, order));
    }

    /* ── Global keyboard shortcuts ───────────────────────────────────── */
    function initShortcuts() {
        document.addEventListener("keydown", (e) => {
            const ae = document.activeElement;
            const inField =
                ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" || ae.isContentEditable);
            const mod = e.ctrlKey || e.metaKey;

            if (mod && e.key.toLowerCase() === "f") {
                e.preventDefault();
                window.SVSearchModal && window.SVSearchModal.open();
                return;
            }
            if (mod && e.key.toLowerCase() === "z" && !inField) {
                e.preventDefault();
                window.SVShell ? window.SVShell.back() : history.back();
                return;
            }
            if (e.key === "Escape") {
                closeContextMenu();
                return;
            }
            if (e.key === "?" && !inField) {
                e.preventDefault();
                window.SVShortcutsModal && window.SVShortcutsModal.toggle();
            }
        });

        const trigger = $("#search-trigger");
        if (trigger) {
            trigger.addEventListener("click", () => window.SVSearchModal && window.SVSearchModal.open());
        }
        const mobileTrigger = $("#mobile-search-trigger");
        if (mobileTrigger) {
            mobileTrigger.addEventListener("click", () => window.SVSearchModal && window.SVSearchModal.open());
        }

        /* Mobile "More" sheet — open/close */
        const moreTrigger = $("#mobile-more-trigger");
        const moreSheet = $("#mobile-more-sheet");
        if (moreTrigger && moreSheet) {
            moreTrigger.addEventListener("click", () => {
                window.SVShell.open(moreSheet, {trigger: moreTrigger, display: "block"});
            });
            moreSheet.querySelectorAll("[data-close-more]").forEach((el) => {
                el.addEventListener("click", () => window.SVShell.close(moreSheet));
            });
        }
        const sc = $("#shortcuts-trigger");
        if (sc) {
            sc.addEventListener("click", () => window.SVShortcutsModal && window.SVShortcutsModal.toggle());
        }
        const bs = $("#block-stats-trigger");
        if (bs) {
            bs.addEventListener("click", () => window.SVBlockStats && window.SVBlockStats.open());
        }
    }

    function init() {
        initShortcuts();
        if ($("#sidebar-saved")) renderFromAPI();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
