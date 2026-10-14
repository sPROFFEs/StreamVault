/* Shared poster cards for the home rails and catalog. */
(function () {
    "use strict";

    function imgUrl(path, size) {
        if (window.SVArtwork) return window.SVArtwork.url(path, size || "w342");
        return path ? (/^https?:\/\//i.test(path) ? path : `https://image.tmdb.org/t/p/${size || "w342"}${path}`) : "";
    }
    function escapeHTML(s) {
        return String(s).replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }

    function badge(item, mt, text) {
        const date = item.release_date || item.first_air_date || "";
        if (date) {
            const y = parseInt(date.slice(0, 4), 10);
            const now = new Date().getFullYear();
            if (y && y > now) return { label: text.soon, cls: "card-badge--blue" };
        }
        if (item.is_anime) return { label: "Anime", cls: "" };
        if (mt === "tv") return { label: text.series, cls: "" };
        return null;
    }

    function ratingBadge(rating) {
        if (!rating) return "";
        if (rating.min_age == null) return "";
        const restricted = rating.min_age >= 16;
        const icon = restricted
            ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>`
            : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l8 4v6c0 5-4 9-8 10-4-1-8-5-8-10V6l8-4z"/></svg>`;
        return `<div class="age-badge${restricted ? " restricted" : ""}">${icon}${rating.min_age}+</div>`;
    }

    function render(item, opts = {}) {
        const spanish = (window.APP_LANG || document.documentElement.lang) === "es";
        const text = spanish
            ? { movie: "Película", series: "Serie", soon: "Próximamente", details: "Ver detalles", downloads: "Descargado", noImage: "Sin carátula", untitled: "Sin título", episodes: "episodios" }
            : { movie: "Movie", series: "Series", soon: "Coming soon", details: "View details", downloads: "Downloaded", noImage: "No artwork", untitled: "Untitled", episodes: "episodes" };
        const mediaType = item.media_type || (item.first_air_date ? "tv" : "movie");
        const mt = mediaType === "series" ? "tv" : mediaType;
        const id = item.tmdb_id || item.id;
        const title = item.title || item.name || text.untitled;
        const date = item.release_date || item.first_air_date || "";
        const year = date ? date.slice(0, 4) : "";
        const progressPct = item.progress_pct || 0;
        const watched = !!item.watched;
        const b = badge(item, mt, text);
        const score = Number(item.vote_average || (typeof item.rating === "number" ? item.rating : 0));
        const poster = item.poster_path ? imgUrl(item.poster_path, "w342") : item.poster_url;

        const a = document.createElement("a");
        a.className = "poster-card media-card--modern";
        if (item.locked) a.classList.add("locked");
        a.href = opts.href || (id ? `/media/${id}?type=${mt}` : "/downloads");
        a.dataset.id = String(id);
        a.dataset.mediaType = mt;
        a.setAttribute("aria-label", `${title} · ${mt === "tv" ? text.series : text.movie}${year ? ` · ${year}` : ""}`);

        a.innerHTML = `
            <div class="poster-card__art card-poster">
                ${poster ? `<img src="${escapeHTML(poster)}" alt="" loading="lazy" decoding="async" width="342" height="513">` : ""}
                <div class="no-poster media-card__placeholder"${poster ? " hidden" : ""}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 8h18M8 3v5M16 3v5M10 12l5 3-5 3z"/></svg><span>${text.noImage}</span></div>
                <div class="card-overlay" aria-hidden="true"><div class="card-play"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></div><span>${text.details}</span></div>
                ${item.relative_path ? `<span class="card-badge card-badge--green">${text.downloads}</span>` : b ? `<span class="card-badge ${b.cls}">${b.label}</span>` : ""}
                ${ratingBadge(item.rating)}
                ${watched ? `<span class="card-watched"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg></span>` : ""}
                ${progressPct > 0 ? `<div class="card-progress"><div class="card-progress-fill" style="width:${Math.min(100, progressPct)}%"></div></div>` : ""}
            </div>
            <div class="poster-card__body card-info">
                <div class="poster-card__title card-title" title="${escapeHTML(title)}">${escapeHTML(title)}</div>
                <div class="poster-card__meta card-year">
                    <span>${year ? `${escapeHTML(year)} <span aria-hidden="true">·</span> ` : ""}${item.episode_count ? `${item.episode_count} ${text.episodes}` : mt === "tv" ? text.series : text.movie}</span>
                    ${Number.isFinite(score) && score > 0 ? `<span class="media-card__score"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="m12 2 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z"/></svg>${score.toFixed(1)}</span>` : ""}
                </div>
            </div>
        `;

        const image = a.querySelector("img");
        image?.addEventListener("error", () => {
            image.hidden = true;
            a.querySelector(".media-card__placeholder").hidden = false;
        }, { once: true });

        if (opts.contextMenu !== false) a.addEventListener("contextmenu", (e) => {
            e.preventDefault();
            showContextMenu(e.clientX, e.clientY, [
                {
                    label: watched ? "Mark not watched" : "Mark watched",
                    onClick: () => toggleWatched(item, !watched, opts.onWatchedChange),
                },
                {
                    label: "Remove from list",
                    danger: true,
                    onClick: () => removeFromFavorites(id, mt, opts.onRemoved),
                },
            ]);
        });

        return a;
    }

    function renderInto(root, items, opts = {}) {
        if (!root) return;
        root.innerHTML = "";
        items.forEach((it) => root.appendChild(render(it, opts)));
    }

    async function toggleWatched(item, watched, cb) {
        try {
            await fetch("/api/library/history", {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    tmdb_id: item.tmdb_id || item.id,
                    media_type: item.media_type || "movie",
                    title: item.title || item.name || "",
                    poster_path: item.poster_path,
                    backdrop_path: item.backdrop_path,
                    progress_seconds: watched ? 9999 : 0,
                    duration_seconds: 0,
                    completed: !!watched,
                }),
            });
            cb && cb(watched);
        } catch (e) {}
    }

    async function removeFromFavorites(id, mt, cb) {
        try {
            await fetch(`/api/library/favorites/${mt}/${id}`, {
                method: "DELETE",
                credentials: "same-origin",
            });
            cb && cb();
        } catch (e) {}
    }

    function showContextMenu(x, y, items) {
        document.querySelectorAll(".context-menu").forEach((m) => m.remove());
        const menu = document.createElement("div");
        menu.className = "context-menu";
        menu.style.left = `${x}px`;
        menu.style.top = `${y}px`;
        items.forEach((it) => {
            const row = document.createElement("div");
            row.className = "context-menu__item" + (it.danger ? " context-menu__item--danger" : "");
            row.textContent = it.label;
            row.addEventListener("click", () => {
                menu.remove();
                it.onClick && it.onClick();
            });
            menu.appendChild(row);
        });
        document.body.appendChild(menu);
        const close = (ev) => { if (!menu.contains(ev.target)) menu.remove(); };
        setTimeout(() => document.addEventListener("click", close, { once: true }), 0);
    }

    window.SVMediaCard = { render, renderInto };
})();
