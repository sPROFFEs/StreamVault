/* Coalesce progress samples and serialize writes for each movie or episode. */
(() => {
    "use strict";
    const pending = new Map();

    function send(key, payload) {
        const entry = pending.get(key) || {running: false, latest: null};
        entry.latest = payload;
        pending.set(key, entry);
        if (entry.running) return;
        entry.running = true;
        void drain(key, entry);
    }

    async function drain(key, entry) {
        while (entry.latest) {
            const payload = entry.latest;
            entry.latest = null;
            try {
                await fetch("/api/library/history", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(payload), keepalive: true,
                    signal: typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(10000) : undefined,
                });
            } catch (_) {}
        }
        pending.delete(key);
    }

    function throttle(write) {
        let timer, latest, previous, closed = false;
        const flush = () => {
            clearTimeout(timer);
            timer = null;
            if (!latest) return;
            const current = latest();
            latest = null;
            if (!current || (previous && Math.abs(current[0] - previous[0]) < 1 &&
                    current[1] === previous[1] && current[2] === previous[2])) return;
            previous = current;
            write(...current);
        };
        return {
            schedule(sample) {
                if (closed) return;
                latest = sample;
                if (!timer) timer = setTimeout(flush, 10000);
            },
            finish(sample) { if (!closed) { latest = sample; flush(); } },
            flush,
            close() { flush(); closed = true; },
        };
    }

    window.SVPlaybackHistory = {send, throttle};
})();
