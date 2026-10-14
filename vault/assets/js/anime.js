const LANG = window.APP_LANG === "es" ? "es" : "en";
const TEXT = {
    en: {
        noResults: "No results.",
        noScore: "no score",
        anime: "Anime",
        noSynopsis: "No synopsis.",
        streaming: "streaming",
        external: "external",
        reference: "reference",
        malEntry: "MAL entry",
        open: "Open",
        noOfficialSources: "No official sources attached.",
    },
    es: {
        noResults: "No hay resultados.",
        noScore: "sin score",
        anime: "Anime",
        noSynopsis: "Sin sinopsis.",
        streaming: "streaming",
        external: "externo",
        reference: "referencia",
        malEntry: "Ficha MAL",
        open: "Abrir",
        noOfficialSources: "No hay fuentes oficiales asociadas.",
    },
}[LANG];

const animeGrid = document.getElementById("anime-grid");
const animeModal = document.getElementById("anime-modal");
const animeMediaBox = document.getElementById("anime-media-box");
const animeTitle = document.getElementById("anime-title");
const animeMeta = document.getElementById("anime-meta");
const animeDescription = document.getElementById("anime-description");
const animeSources = document.getElementById("anime-sources");
const animeSourceCount = document.getElementById("anime-source-count");

const animeCache = new Map();

async function fetchJson(url, fallback = null) {
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error(url, error);
        return fallback;
    }
}

function renderAnimeGrid(items) {
    if (!items?.length) {
        animeGrid.innerHTML = `<div class="empty-state">${TEXT.noResults}</div>`;
        return;
    }

    animeGrid.innerHTML = items.map((item) => {
        animeCache.set(String(item.id), item);
        return `
            <button class="catalog-card" type="button" data-anime-id="${item.id}">
                <img src="${item.poster_url || "https://placehold.co/500x750/181818/f3f3f3?text=Anime"}" alt="${escapeHtml(item.title)}" loading="lazy">
                <span class="catalog-card__title">${escapeHtml(item.title)}</span>
                <span class="catalog-card__meta">${item.year || item.season || ""} · ${item.score || TEXT.noScore}</span>
            </button>
        `;
    }).join("");

    animeGrid.querySelectorAll("[data-anime-id]").forEach((button) => {
        button.addEventListener("click", () => openAnime(button.dataset.animeId));
    });
}

async function loadSeason() {
    renderAnimeGrid(await fetchJson("/anime/api/season", []));
}

async function loadTop() {
    renderAnimeGrid(await fetchJson("/anime/api/top", []));
}

async function searchAnime(query) {
    if (!query.trim()) return loadSeason();
    renderAnimeGrid(await fetchJson(`/anime/api/search?q=${encodeURIComponent(query.trim())}`, []));
}

async function openAnime(id) {
    const detail = await fetchJson(`/anime/api/${id}`, null);
    if (!detail) return;

    animeModal.classList.add("active");
    animeTitle.textContent = detail.title || TEXT.anime;
    animeMeta.textContent = [detail.year, detail.season, detail.score ? `${detail.score}/10` : "", (detail.genres || []).slice(0, 3).join(", ")].filter(Boolean).join(" · ");
    animeDescription.textContent = detail.synopsis || TEXT.noSynopsis;
    const embedUrl = formatEmbedUrl(detail.trailer_url);
    animeMediaBox.innerHTML = embedUrl
        ? `<iframe src="${embedUrl}" allow="autoplay; fullscreen" allowfullscreen webkitallowfullscreen="true" mozallowfullscreen="true" style="width: 100%; height: 100%; border: 0;"></iframe>`
        : `<img class="detail-poster" src="${detail.poster_url || "https://placehold.co/1200x675/181818/f3f3f3?text=Anime"}" alt="${escapeHtml(detail.title)}">`;

    const sourceItems = [
        ...(detail.streaming || []).map((item) => ({ name: item.name, url: item.url, type: TEXT.streaming })),
        ...(detail.external || []).slice(0, 10).map((item) => ({ name: item.name, url: item.url, type: TEXT.external })),
        detail.url ? [{ name: TEXT.malEntry, url: detail.url, type: TEXT.reference }] : [],
    ].filter((item) => (
        item &&
        item.name &&
        item.url &&
        item.name !== "undefined" &&
        item.url !== "undefined"
    ));
    animeSourceCount.textContent = String(sourceItems.length);
    animeSources.innerHTML = sourceItems.length
        ? sourceItems.map((item) => `
            <div class="source-item">
                <div>
                    <strong>${escapeHtml(item.name)}</strong>
                    <div class="muted">${escapeHtml(item.type)}</div>
                </div>
                <a class="button" href="${item.url}" target="_blank" rel="noreferrer">${TEXT.open}</a>
            </div>
        `).join("")
        : `<div class="empty-state">${TEXT.noOfficialSources}</div>`;
}

function closeAnime() {
    animeModal.classList.remove("active");
    animeMediaBox.innerHTML = "";
}

function formatEmbedUrl(url) {
    if (!url) return "";
    let videoId = "";
    if (url.includes("youtube.com/watch")) {
        try { videoId = new URL(url).searchParams.get("v"); } catch(e){}
    } else if (url.includes("youtu.be/")) {
        videoId = url.split("youtu.be/")[1]?.split("?")[0];
    } else if (url.includes("youtube.com/embed/")) {
        return url;
    }
    if (videoId) return `https://www.youtube.com/embed/${videoId}?autoplay=1`;
    return url;
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

document.getElementById("anime-season").addEventListener("click", loadSeason);
document.getElementById("anime-top").addEventListener("click", loadTop);
document.getElementById("anime-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") searchAnime(event.target.value);
});
document.getElementById("anime-close").addEventListener("click", closeAnime);
animeModal.addEventListener("click", (event) => {
    if (event.target === animeModal) closeAnime();
});

window.addEventListener("DOMContentLoaded", loadSeason);
