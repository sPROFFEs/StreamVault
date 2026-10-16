/* Explicit device downloads. Shared by the page and service worker; never caches sessions. */
(() => {
    "use strict";
    const CHUNK_SIZE = 1024 * 1024;
    const DEFAULT_LIMIT = 5 * 1024 ** 3;
    let dbPromise, workerPromise, busy = false;

    function database() {
        if (!dbPromise) dbPromise = new Promise((resolve, reject) => {
            const request = indexedDB.open("streamvault-offline-v1", 1);
            request.onupgradeneeded = () => {
                request.result.createObjectStore("entries", {keyPath: "id"});
                request.result.createObjectStore("chunks", {keyPath: ["id", "index"]});
            };
            request.onsuccess = () => {
                const db = request.result;
                db.onversionchange = () => { db.close(); dbPromise = null; };
                resolve(db);
            };
            request.onerror = () => { dbPromise = null; reject(request.error); };
        });
        return dbPromise;
    }

    async function read(store, key) {
        const db = await database();
        return new Promise((resolve, reject) => {
            const table = db.transaction(store).objectStore(store);
            const request = key === undefined ? table.getAll() : table.get(key);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    async function write(stores, operation) {
        const db = await database();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(stores, "readwrite");
            tx.oncomplete = resolve;
            tx.onabort = tx.onerror = () => reject(tx.error || new Error("Offline storage unavailable"));
            operation(tx);
        });
    }

    function list() { return read("entries"); }
    function mediaUrl(id) { return `/__offline_media/${encodeURIComponent(id)}`; }
    function limitBytes() {
        try { return Number(localStorage.getItem("sv_offline_limit")) || DEFAULT_LIMIT; }
        catch (_) { return DEFAULT_LIMIT; }
    }
    function setLimitGiB(value) {
        if (!Number.isFinite(value) || value < .25 || value > 100) throw new Error("Choose 0.25–100 GiB");
        localStorage.setItem("sv_offline_limit", String(Math.floor(value * 1024 ** 3)));
    }

    async function deleteEntry(id) {
        await write(["entries", "chunks"], tx => {
            tx.objectStore("entries").delete(id);
            tx.objectStore("chunks").delete(IDBKeyRange.bound([id, 0], [id, Number.MAX_SAFE_INTEGER]));
        });
    }

    async function remove(id) {
        if (!navigator.locks) return deleteEntry(id);
        return navigator.locks.request("sv-offline-download", {ifAvailable: true}, lock => {
            if (!lock) throw new Error("A device download is running; cancel it before deleting files");
            return deleteEntry(id);
        });
    }

    function offlineWorker(worker) {
        if (!worker) return Promise.resolve(false);
        return new Promise(resolve => {
            const channel = new MessageChannel();
            const finish = ready => { clearTimeout(timer); channel.port1.close(); resolve(ready); };
            const timer = setTimeout(() => finish(false), 1000);
            channel.port1.onmessage = event => finish(event.data?.offline === true);
            try { worker.postMessage({type: "SV_OFFLINE_READY"}, [channel.port2]); }
            catch (_) { finish(false); }
        });
    }

    async function ensureWorker() {
        if (!workerPromise) workerPromise = (async () => {
            if (!globalThis.isSecureContext || !navigator.serviceWorker) throw new Error("Offline mode requires HTTPS and service workers");
            // Reopening a saved library must not re-register over the network.
            // Negotiate capability so an older installed shell is still upgraded.
            let registration = await navigator.serviceWorker.getRegistration("/");
            if (!await offlineWorker(registration?.active)) registration = await navigator.serviceWorker.register("/sw.js");
            const installing = registration.installing || registration.waiting;
            if (installing && installing.state !== "activated") await new Promise((resolve, reject) => {
                const timer = setTimeout(() => reject(new Error("Offline setup timed out; reload and try again")), 15000);
                installing.addEventListener("statechange", () => {
                    if (installing.state === "activated") { clearTimeout(timer); resolve(); }
                    if (installing.state === "redundant") { clearTimeout(timer); reject(new Error("Offline setup failed")); }
                });
            });
            await navigator.serviceWorker.ready;
            if (!await offlineWorker(registration.active)) throw new Error("Reload to enable offline mode");
            if (!navigator.serviceWorker.controller || navigator.serviceWorker.controller !== registration.active) {
                await new Promise((resolve, reject) => {
                    const timer = setTimeout(() => reject(new Error("Reload to enable offline mode")), 10000);
                    navigator.serviceWorker.addEventListener("controllerchange", () => { clearTimeout(timer); resolve(); }, {once: true});
                });
            }
        })().catch(error => { workerPromise = null; throw error; });
        return workerPromise;
    }

    function checkedUrl(value) {
        const url = new URL(value, location.origin);
        const local = /^\/api\/downloads\/files\/(playback|stream|remux|match|subtitle|embedded-subtitle)$/;
        const remote = /^\/api\/federation\/peers\/[1-9]\d*\/media\/[a-f0-9]{64}\/(metadata|playback|stream|subtitles\/\d+)$/;
        if (url.origin !== location.origin || url.username || url.password || url.hash || !(local.test(url.pathname) || remote.test(url.pathname))) {
            throw new Error("Invalid offline media URL");
        }
        return url.pathname + url.search;
    }

    async function json(url, signal) {
        const response = await fetch(checkedUrl(url), {signal, credentials: "same-origin", redirect: "error", cache: "no-store"});
        if (!response.ok) throw new Error(`Download preparation failed (${response.status})`);
        return response.json();
    }

    async function quota() {
        const estimate = await navigator.storage?.estimate?.() || {};
        return Math.max(0, Number(estimate.quota ?? Infinity) - Number(estimate.usage || 0));
    }

    async function save(file, {audio, signal, onProgress = () => {}} = {}) {
        if (busy) throw new Error("Another device download is running");
        busy = true;
        const run = async lock => {
            if (navigator.locks && !lock) throw new Error("Another device download is running");
            if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
            await ensureWorker();
            const sourceId = file.playback_url || file.relative_path;
            if (!sourceId) throw new Error("Choose a local or federated file");
            const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify([sourceId, audio ?? null])));
            const sourceKey = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
            const entries = await list();
            const existing = entries.find(entry => entry.source_key === sourceKey && entry.status === "complete");
            if (existing) return existing;
            const used = entries.reduce((sum, entry) => sum + (entry.storage_bytes || entry.size_bytes), 0);
            const budget = Math.min(limitBytes() - used, await quota());
            if (budget <= 0) throw new Error("Device storage limit reached; remove an offline video first");
            // Persistence is a browser decision, not a guarantee. Failure must
            // not prevent an otherwise supported download.
            try { await navigator.storage?.persist?.(); } catch (_) {}
            let source = file;
            if (!Array.isArray(source.audio_tracks)) {
                const metadata = source.metadata_url
                    ? await json(source.metadata_url, signal)
                    : (await json(`/api/downloads/files/match?title=&path=${encodeURIComponent(source.relative_path)}`, signal)).match;
                if (!metadata) throw new Error("File is no longer available");
                source = {...source, ...metadata};
            }
            const params = new URLSearchParams({start: "0"});
            if (!source.playback_url) params.set("path", source.relative_path);
            if (audio != null) params.set("audio", String(audio));
            const playback = await json(`${source.playback_url || "/api/downloads/files/playback"}?${params}`, signal);
            if (!['file', 'stream'].includes(playback.mode) || Number(playback.offset || 0) !== 0) throw new Error("Invalid offline playback descriptor");
            const response = await fetch(checkedUrl(playback.url), {signal, credentials: "same-origin", redirect: "error", cache: "no-store"});
            const type = (response.headers.get("Content-Type") || "").split(";", 1)[0].toLowerCase();
            if (!response.ok || !response.body || !/^(video\/(mp4|webm|quicktime|x-m4v)|application\/octet-stream)$/.test(type)) {
                await response.body?.cancel().catch(() => {});
                throw new Error("Video download unavailable");
            }
            const total = Number(response.headers.get("Content-Length") || 0);
            if (total > budget) { await response.body.cancel(); throw new Error("Not enough device storage; increase the limit or remove a video"); }
            const entry = {id: crypto.randomUUID(), source_key: sourceKey, title: String(file.title || file.name || "Video").slice(0, 255),
                episode: file.season_number != null && file.episode_number != null ? `S${String(file.season_number).padStart(2, '0')}E${String(file.episode_number).padStart(2, '0')}` : "",
                origin: String(file.peer_name || "Local server").slice(0, 80), status: "downloading", size_bytes: 0,
                content_type: type === "application/octet-stream" ? "video/mp4" : type,
                duration_seconds: Number(playback.duration_seconds || source.duration_seconds || 0),
                created_at: Date.now(), subtitles: []};
            const reader = response.body.getReader();
            let buffer = new Uint8Array(CHUNK_SIZE), filled = 0, index = 0, loaded = 0;
            const flush = async () => {
                entry.size_bytes += filled;
                await write(["entries", "chunks"], tx => {
                    tx.objectStore("entries").put(entry);
                    tx.objectStore("chunks").put({id: entry.id, index: index++, blob: new Blob([buffer.subarray(0, filled)])});
                });
                filled = 0;
                buffer = new Uint8Array(CHUNK_SIZE);
            };
            try {
                while (true) {
                    if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
                    const {done, value} = await reader.read();
                    if (done) break;
                    loaded += value.byteLength;
                    if (loaded > budget) throw new Error("Device storage limit reached");
                    for (let offset = 0; offset < value.length;) {
                        const length = Math.min(CHUNK_SIZE - filled, value.length - offset);
                        buffer.set(value.subarray(offset, offset + length), filled);
                        filled += length;
                        offset += length;
                        if (filled === CHUNK_SIZE) await flush();
                    }
                    onProgress({loaded, total, title: entry.title});
                }
                if (!loaded || (total && total !== loaded)) throw new Error("Download interrupted; no complete video was saved");
                if (filled) await flush();
                // Include bounded VTT copies; missing subtitles never invalidate
                // a complete video. External providers are intentionally excluded.
                for (const track of (source.subtitles || []).slice(0, 10)) {
                    try {
                        const subtitle = await fetch(checkedUrl(track.url), {signal, credentials: "same-origin", redirect: "error"});
                        if (!subtitle.ok || !subtitle.headers.get("Content-Type")?.startsWith("text/vtt")) continue;
                        const sr = subtitle.body.getReader(), parts = [];
                        let size = 0;
                        try {
                            while (true) {
                                const part = await sr.read();
                                if (part.done) break;
                                size += part.value.length;
                                if (size > 1024 * 1024 || loaded + size > budget) throw new Error("Subtitle too large");
                                parts.push(part.value);
                            }
                            entry.subtitles.push({language: String(track.language || "und").slice(0, 32), label: String(track.label || "Subtitles").slice(0, 255), text: await new Blob(parts).text()});
                            loaded += size;
                        } finally { await sr.cancel().catch(() => {}); }
                    } catch (error) { if (signal?.aborted) throw error; }
                }
                if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
                entry.storage_bytes = loaded;
                entry.status = "complete";
                await write(["entries"], tx => tx.objectStore("entries").put(entry));
                onProgress({loaded, total: loaded, title: entry.title});
                return entry;
            } catch (error) {
                await deleteEntry(entry.id);
                throw error;
            } finally { await reader.cancel().catch(() => {}); }
        };
        try {
            return navigator.locks ? await navigator.locks.request("sv-offline-download", {ifAvailable: true}, run) : await run(true);
        } finally { busy = false; }
    }

    async function serve(request) {
        const id = new URL(request.url).pathname.split('/').pop();
        const entry = await read("entries", id);
        if (!entry || entry.status !== "complete") return new Response("Offline video not found", {status: 404});
        const size = entry.size_bytes;
        let start = 0, end = size - 1;
        const range = request.headers.get("Range");
        if (range) {
            const match = /^bytes=(\d*)-(\d*)$/.exec(range);
            if (!match || (!match[1] && !match[2])) return new Response(null, {status: 416, headers: {"Content-Range": `bytes */${size}`}});
            start = match[1] ? Number(match[1]) : Math.max(0, size - Number(match[2]));
            end = match[1] && match[2] ? Math.min(Number(match[2]), end) : end;
            if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end || start >= size) return new Response(null, {status: 416, headers: {"Content-Range": `bytes */${size}`}});
        }
        const headers = {"Content-Type": entry.content_type, "Content-Length": String(end - start + 1), "Accept-Ranges": "bytes", "Cache-Control": "private, no-store"};
        if (range) headers["Content-Range"] = `bytes ${start}-${end}/${size}`;
        let position = start;
        const body = request.method === "HEAD" ? null : new ReadableStream({
            async pull(controller) {
                try {
                    if (position > end) { controller.close(); return; }
                    const part = await read("chunks", [id, Math.floor(position / CHUNK_SIZE)]);
                    if (!part) throw new Error("Offline data missing; download this video again");
                    const offset = position % CHUNK_SIZE;
                    const length = Math.min(part.blob.size - offset, end - position + 1);
                    controller.enqueue(new Uint8Array(await part.blob.slice(offset, offset + length).arrayBuffer()));
                    position += length;
                } catch (error) { controller.error(error); }
            },
        });
        return new Response(body, {status: range ? 206 : 200, headers});
    }

    globalThis.SVOffline = {database, list, remove, save, serve, mediaUrl, limitBytes, setLimitGiB, ensureWorker};
})();
