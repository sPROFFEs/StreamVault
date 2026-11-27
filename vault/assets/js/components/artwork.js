/* Shared artwork URLs and reusable, accessible missing-image states. */
(function () {
    "use strict";

    function url(path, size = "w500") {
        if (typeof path !== "string" || !path.trim()) return "";
        path = path.trim();
        if (/^https?:\/\//i.test(path)) return path;
        if (path.startsWith("//")) return `https:${path}`;
        if (path.includes(":")) return "";
        if (path.startsWith("/assets/") || path.startsWith("/api/")) return path;
        return `https://image.tmdb.org/t/p/${size}${path.startsWith("/") ? path : `/${path}`}`;
    }

    function setImage(image, path, { size = "w500", alt = "", placeholder } = {}) {
        const source = url(path, size);
        image.alt = alt;
        image.hidden = !source;
        if (placeholder) placeholder.hidden = !!source;
        image.onerror = () => {
            image.hidden = true;
            if (placeholder) placeholder.hidden = false;
        };
        const candidates = posterSrcset(path);
        if (candidates) {
            image.sizes = "(max-width: 640px) 33vw, 220px";
            image.srcset = candidates;
        } else image.removeAttribute("srcset");
        if (source) image.src = source;
        else image.removeAttribute("src");
    }

    function posterSrcset(path) {
        if (!/^\/[A-Za-z0-9_-]+\.(jpg|png|webp)$/i.test(path || "")) return "";
        return [185, 342, 500].map(width => `${url(path, `w${width}`)} ${width}w`).join(", ");
    }

    function setBackdrop(element, path) {
        if (!element) return;
        const source = url(path, "w1280");
        element.style.backgroundImage = source ? `url(${JSON.stringify(source)})` : "none";
    }

    window.SVArtwork = { url, setImage, setBackdrop, posterSrcset };
})();
