/* StreamVault TrendingCarousel — 5-slot focus carousel, IntersectionObserver
   pause-when-offscreen, dot pagination, mouse-wheel/swipe, auto-cycle. */
(function () {
    "use strict";

    const SLOT_OFFSETS = [-2, -1, 0, 1, 2];
    const AUTO_CYCLE_MS = 4500;

    function imgUrl(path, size) {
        return path ? `https://image.tmdb.org/t/p/${size || "w500"}${path}` : "";
    }
    function bgUrl(path) {
        return path ? `https://image.tmdb.org/t/p/original${path}` : "";
    }

    class TrendingCarousel {
        constructor(host, items, opts = {}) {
            this.host = host;
            this.items = items || [];
            this.opts = opts;
            this.index = 0;
            this.timer = null;
            this.visibleRatio = 0;
            this.io = null;
            this.render();
            this.attach();
        }

        render() {
            this.host.classList.add("trending-carousel");
            this.host.innerHTML = `
                <button class="trending-arrow trending-arrow--prev" aria-label="Previous">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
                </button>
                <div class="trending-stage" data-stage></div>
                <button class="trending-arrow trending-arrow--next" aria-label="Next">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>
                </button>
                <div class="trending-dots" data-dots></div>
            `;
            this.stage = this.host.querySelector("[data-stage]");
            this.dots = this.host.querySelector("[data-dots]");

            this.slides = this.items.map((item, i) => {
                const a = document.createElement("a");
                a.className = "trending-slide";
                a.href = `/media/${item.id}?type=${item.media_type || "movie"}`;
                a.innerHTML = `
                    ${item.poster_path ? `<img src="${imgUrl(item.poster_path)}" alt="" loading="lazy">` : ""}
                    <div class="trending-meta">
                        <h3 class="trending-meta__title"></h3>
                        <div class="trending-meta__sub"></div>
                    </div>
                `;
                a.querySelector(".trending-meta__title").textContent =
                    item.title || item.name || "";
                const date = item.release_date || item.first_air_date || "";
                const year = date ? date.slice(0, 4) : "";
                const rating = item.vote_average ? "★ " + item.vote_average.toFixed(1) : "";
                a.querySelector(".trending-meta__sub").textContent =
                    [year, rating].filter(Boolean).join(" · ");
                this.stage.appendChild(a);
                return a;
            });

            this.items.forEach((_, i) => {
                const d = document.createElement("button");
                d.className = "trending-dot" + (i === 0 ? " active" : "");
                d.addEventListener("click", () => this.go(i));
                this.dots.appendChild(d);
            });

            this.updatePositions();
        }

        updatePositions() {
            const n = this.items.length;
            if (!n) return;
            this.slides.forEach((el, i) => {
                let rel = ((i - this.index + n) % n);
                if (rel > n / 2) rel -= n;
                if (SLOT_OFFSETS.includes(rel)) {
                    el.dataset.pos = String(rel);
                } else {
                    el.dataset.pos = "hidden";
                }
            });
            Array.from(this.dots.children).forEach((d, i) =>
                d.classList.toggle("active", i === this.index)
            );
            if (typeof this.opts.onChange === "function") {
                this.opts.onChange(this.items[this.index], this.index);
            }
        }

        go(i) {
            const n = this.items.length;
            if (!n) return;
            this.index = ((i % n) + n) % n;
            this.updatePositions();
            this.restartAutoCycle();
        }
        next() { this.go(this.index + 1); }
        prev() { this.go(this.index - 1); }

        startAutoCycle() {
            this.stopAutoCycle();
            this.timer = setInterval(() => {
                if (this.visibleRatio > 0.5 && !document.hidden) this.next();
            }, AUTO_CYCLE_MS);
        }
        stopAutoCycle() {
            if (this.timer) clearInterval(this.timer);
            this.timer = null;
        }
        restartAutoCycle() {
            if (this.timer) {
                this.stopAutoCycle();
                this.startAutoCycle();
            }
        }

        attach() {
            this.host.querySelector(".trending-arrow--prev").addEventListener("click", () => this.prev());
            this.host.querySelector(".trending-arrow--next").addEventListener("click", () => this.next());

            this.host.addEventListener("wheel", (e) => {
                if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) return;
                e.preventDefault();
                if (e.deltaX > 12) this.next();
                else if (e.deltaX < -12) this.prev();
            }, { passive: false });

            // Touch swipe
            let touchX = null;
            this.host.addEventListener("touchstart", (e) => {
                touchX = e.touches[0].clientX;
            }, { passive: true });
            this.host.addEventListener("touchend", (e) => {
                if (touchX == null) return;
                const dx = (e.changedTouches[0].clientX || 0) - touchX;
                if (dx < -40) this.next();
                else if (dx > 40) this.prev();
                touchX = null;
            });

            this.io = new IntersectionObserver((entries) => {
                for (const en of entries) this.visibleRatio = en.intersectionRatio;
            }, { threshold: [0, 0.25, 0.5, 0.75, 1] });
            this.io.observe(this.host);

            this.startAutoCycle();
        }

        destroy() {
            this.stopAutoCycle();
            if (this.io) this.io.disconnect();
            this.host.innerHTML = "";
        }
    }

    window.SVTrendingCarousel = TrendingCarousel;
})();
