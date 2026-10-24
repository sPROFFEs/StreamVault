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
    function updatePlayback(id, values) {
        const playback = {};
        for (const [key, min, max] of [["progress_seconds", 0, 864000], ["subtitle_size", 50, 200],
                ["subtitle_offset", -120, 120], ["rate", .25, 3]]) {
            const value = Number(values[key]);
            if (Number.isFinite(value) && value >= min && value <= max) playback[key] = value;
        }
        if (typeof values.subtitle_language === "string") playback.subtitle_language = values.subtitle_language.slice(0, 32);
        return write(["entries"], tx => {
            const table = tx.objectStore("entries"), request = table.get(id);
            request.onsuccess = () => {
                // Never resurrect a copy removed in another tab.
                if (request.result?.status === "complete") table.put({...request.result,
                    playback: {...request.result.playback, ...playback}, last_played_at: Date.now()});
            };
        });
    }
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
        if (url.origin !== location.origin || url.username || url.password || url.hash || !(local.test(url.pathname) || remote.test(url.pathname) || url.pathname === "/api/subtitles/fetch")) {
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

    async function prepare(file, signal) {
        if (Array.isArray(file.audio_tracks)) return file;
        const metadata = file.metadata_url ? await json(file.metadata_url, signal)
            : (await json(`/api/downloads/files/match?title=&path=${encodeURIComponent(file.relative_path)}`, signal)).match;
        if (!metadata) throw new Error("File is no longer available");
        return {...file, ...metadata, audio_tracks: Array.isArray(metadata.audio_tracks) ? metadata.audio_tracks : []};
    }

    async function subtitleCopies(tracks, signal, budget) {
        const items = []; let bytes = 0;
        for (const track of [...new Map(tracks.map(item => [item.url, item])).values()].slice(0, 10)) {
            let reader, response;
            const controller = new AbortController(), abort = () => controller.abort();
            signal?.addEventListener("abort", abort, {once: true});
            const timer = setTimeout(abort, 12000);
            try {
                if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
                response = await fetch(checkedUrl(track.url), {signal: controller.signal, credentials: "same-origin", redirect: "error", cache: "no-store"});
                if (!response.ok || !response.headers.get("Content-Type")?.startsWith("text/vtt") || !response.body) continue;
                reader = response.body.getReader();
                const parts = []; let size = 0;
                while (true) {
                    const part = await reader.read();
                    if (part.done) break;
                    size += part.value.length;
                    if (size > 1024 * 1024 || bytes + size > budget) throw new Error("Subtitle too large");
                    parts.push(part.value);
                }
                const text = await new Blob(parts).text();
                if (!text.trimStart().startsWith("WEBVTT") || !text.includes("-->")) continue;
                items.push({language: subtitleLanguage(track.language) || "und",
                    label: String(track.label || "Subtitles").slice(0, 255), text});
                bytes += size;
            } catch (error) { if (signal?.aborted) throw error; }
            finally {
                clearTimeout(timer); signal?.removeEventListener("abort", abort);
                if (reader) await reader.cancel().catch(() => {});
                else await response?.body?.cancel().catch(() => {});
            }
        }
        return {items, bytes};
    }

    function subtitleLanguage(value) {
        const code = String(value || "").trim().toLowerCase().split(/[-_]/)[0];
        return ({eng: "en", english: "en", spa: "es", spanish: "es", español: "es", fra: "fr", fre: "fr",
            french: "fr", deu: "de", ger: "de", german: "de", por: "pt", portuguese: "pt", ita: "it", italian: "it"})[code]
            || (/^[a-z]{2}$/.test(code) ? code : "");
    }

    function subtitleLanguages(values) {
        if (!Array.isArray(values)) {
            try { values = JSON.parse(localStorage.getItem("sv_offline_subtitle_languages")); } catch (_) {}
        }
        if (!Array.isArray(values)) values = [globalThis.APP_LANG || globalThis.document?.documentElement.lang || navigator.language, "en"];
        return [...new Set(values.map(subtitleLanguage).filter(Boolean))].slice(0, 3);
    }

    const subtitleBytes = items => new Blob(items.map(item => item.text)).size;
    const subtitleKey = item => `${subtitleLanguage(item.language) || item.language}:${item.label}`;

    async function collectSubtitles(file, tracks, languages, previous, signal, budget) {
        if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
        const controller = new AbortController(), abort = () => controller.abort();
        signal?.addEventListener("abort", abort, {once: true});
        // A failed provider cannot leave a completed video waiting indefinitely.
        const timer = setTimeout(abort, 30000);
        let items = [...previous];
        const add = copies => {
            for (const track of copies) {
                const index = items.findIndex(item => subtitleKey(track) === subtitleKey(item));
                const merged = index < 0 ? [...items, track] : items.map((item, at) => at === index ? track : item);
                if (merged.length > 10) continue;
                if (subtitleBytes(merged) <= budget) items = merged;
            }
        };
        try {
            for (const track of [...new Map(tracks.map(item => [item.url, item])).values()].slice(0, 10)) {
                if (controller.signal.aborted) break;
                const replaced = items.filter(item => subtitleKey(item) === subtitleKey(track));
                const available = Math.max(0, budget - subtitleBytes(items) + subtitleBytes(replaced));
                if (!available) continue;
                const copies = await subtitleCopies([track], controller.signal, available);
                // Commit each successful caption to the result immediately so
                // one later timeout cannot discard earlier downloads.
                add(copies.items);
            }
            const wanted = subtitleLanguages(languages).filter(lang => !items.some(item => subtitleLanguage(item.language) === lang));
            const series = ["tv", "series"].includes(file.media_type) || !!file.episode;
            if (!/^\d{1,12}$/.test(String(file.tmdb_id || "")) || series && !(file.season_number > 0 && file.episode_number > 0)) return items;
            const results = await Promise.all(wanted.map(async language => {
                const params = new URLSearchParams({tmdb_id: file.tmdb_id, media_type: series ? "tv" : "movie", language});
                if (series) { params.set("season", file.season_number); params.set("episode", file.episode_number); }
                try {
                    const response = await fetch(`/api/subtitles/search?${params}`, {signal: controller.signal,
                        credentials: "same-origin", redirect: "error", cache: "no-store"});
                    const data = response.ok ? await response.json() : {};
                    return [language, (Array.isArray(data.items) ? data.items : []).filter(item =>
                        subtitleLanguage(item.language) === language && typeof item.play_url === "string")];
                } catch (_) { return [language, []]; }
            }));
            for (const [language, candidates] of results) {
                if (items.length >= 10 || controller.signal.aborted) break;
                // Try another release/provider if the first caption is broken.
                for (const track of candidates.slice(0, 3)) {
                    const copies = await subtitleCopies([{url: track.play_url, language,
                        label: `${language.toUpperCase()} · ${track.provider || "Subtitles"}`}], controller.signal,
                    Math.max(0, budget - subtitleBytes(items)));
                    if (copies.items.length) { add(copies.items); break; }
                }
            }
        } catch (error) { if (signal?.aborted) throw error; }
        finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
        if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
        return items;
    }

    async function storeSubtitles(entry, tracks, languages, signal, used) {
        const previous = entry.subtitles || [];
        const budget = Math.max(0, Math.min(limitBytes() - used, await quota()) + subtitleBytes(previous));
        const subtitles = await collectSubtitles(entry, tracks, languages, previous, signal, budget);
        if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
        const storage_bytes = (entry.storage_bytes || entry.size_bytes) - subtitleBytes(previous) + subtitleBytes(subtitles);
        let updated = entry;
        await write(["entries"], tx => {
            const table = tx.objectStore("entries"), request = table.get(entry.id);
            request.onsuccess = () => {
                if (request.result?.status === "complete") {
                    updated = {...request.result, tmdb_id: entry.tmdb_id, subtitles, storage_bytes}; table.put(updated);
                }
            };
        });
        return updated;
    }

    async function refreshSubtitles(id, {languages, signal} = {}) {
        const run = async lock => {
            if (navigator.locks && !lock) throw new Error("A device download is running");
            const entries = await list(), entry = entries.find(item => item.id === id && item.status === "complete");
            if (!entry) return null;
            const metadata = await presentation(entry, signal);
            // Only public film/episode identity is needed; no old server URL or token is retained.
            return storeSubtitles({...entry, tmdb_id: metadata.tmdb_id}, [], languages, signal,
                entries.reduce((sum, item) => sum + (item.storage_bytes || item.size_bytes), 0));
        };
        return navigator.locks ? navigator.locks.request("sv-offline-download", {ifAvailable: true}, run) : run(true);
    }

    async function presentation(file, signal, force = false) {
        let data = file;
        const series = ["tv", "series"].includes(file.media_type) || !!file.episode || Number(file.season_number) > 0;
        // Enrichment is best effort and only runs during an explicit download,
        // never while opening the offline library. Do not cache API responses.
        const controller = new AbortController();
        const abort = () => controller.abort();
        signal?.addEventListener("abort", abort, {once: true});
        const timer = setTimeout(abort, 3000);
        try {
            if (force || !file.overview && !file.poster_path && !file.poster_url) {
                const kind = series ? "tv" : "movie";
                const lang = globalThis.document?.documentElement.lang === "es" ? "es-ES" : "en-US";
                const endpoint = /^\d+$/.test(String(file.tmdb_id || ""))
                    ? `/api/tmdb/${kind}/${file.tmdb_id}?lang=${lang}`
                    : `/api/tmdb/search/${kind}?q=${encodeURIComponent(file.title || file.name || "")}&lang=${lang}`;
                const response = await fetch(endpoint, {signal: controller.signal, credentials: "same-origin", redirect: "error", cache: "no-store"});
                if (response.ok) {
                    const result = await response.json();
                    const normal = value => String(value || "").toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
                    const match = Array.isArray(result) ? result.find(item =>
                        [item.title, item.name, item.original_title, item.original_name].some(title => normal(title) === normal(file.title))
                        && (!file.year || String(item.release_date || item.first_air_date || "").startsWith(String(file.year)))) : result;
                    if (match) data = {...file, ...match};
                }
            }
        } catch (_) {} finally {
            clearTimeout(timer); signal?.removeEventListener("abort", abort);
        }
        return {
            media_type: series ? "series" : "movie",
            tmdb_id: /^\d{1,12}$/.test(String(file.tmdb_id || data.id || "")) ? String(file.tmdb_id || data.id) : "",
            overview: String(data.overview || "").slice(0, 10000),
            year: String(data.year || data.release_date || data.first_air_date || "").slice(0, 4),
            genres: (Array.isArray(data.genres) ? data.genres : []).slice(0, 8).map(item => String(item.name || item).slice(0, 80)),
            season_number: Number(file.season_number || 0), episode_number: Number(file.episode_number || 0),
            episode_title: String(file.episode_title || "").slice(0, 255),
            _poster: data.poster_path || data.poster_url, _backdrop: data.backdrop_path,
        };
    }

    async function artwork(value, signal, budget, size) {
        if (typeof value !== "string" || !value || budget <= 0 || signal?.aborted) return null;
        let url;
        try {
            url = new URL(value.startsWith("/assets/") || value.startsWith("https://") ? value
                : `https://image.tmdb.org/t/p/${size}${value.startsWith("/") ? value : `/${value}`}`, location.origin);
        } catch (_) { return null; }
        if (url.username || url.password || url.search || url.hash || !(
            url.origin === location.origin && url.pathname.startsWith("/assets/") ||
            url.protocol === "https:" && url.host === "image.tmdb.org" && url.pathname.startsWith("/t/p/"))) return null;
        const controller = new AbortController(), abort = () => controller.abort();
        signal?.addEventListener("abort", abort, {once: true});
        const timer = setTimeout(abort, 4000);
        let reader, response;
        try {
            response = await fetch(url, {signal: controller.signal, credentials: "omit", redirect: "error", cache: "no-store"});
            const type = response.headers.get("Content-Type")?.split(";", 1)[0];
            if (!response.ok || !/^image\/(jpeg|png|webp|gif)$/.test(type) || !response.body) return null;
            reader = response.body.getReader();
            const parts = [];
            let loaded = 0;
            while (true) {
                const {done, value: part} = await reader.read();
                if (done) break;
                loaded += part.length;
                if (loaded > Math.min(1024 * 1024, budget)) return null;
                parts.push(part);
            }
            return loaded ? new Blob(parts, {type}) : null;
        } catch (_) { return null; } finally {
            clearTimeout(timer); signal?.removeEventListener("abort", abort);
            if (reader) await reader.cancel().catch(() => {});
            else await response?.body?.cancel().catch(() => {});
        }
    }

    async function refreshPresentation(id, {signal} = {}) {
        const run = async lock => {
            if (navigator.locks && !lock) throw new Error("A device download is running");
            const entry = await read("entries", id);
            if (!entry || entry.status !== "complete") return false;
            const {_poster, _backdrop, ...fields} = await presentation(entry, signal, true);
            const items = await list();
            const used = items.reduce((sum, item) => sum + (item.storage_bytes || item.size_bytes), 0);
            const previous = (entry.poster?.size || 0) + (entry.backdrop?.size || 0);
            const budget = Math.max(0, Math.min(limitBytes() - used, await quota()) + previous);
            let poster = await artwork(_poster, signal, budget, "w342") || entry.poster;
            let backdrop = await artwork(_backdrop, signal, budget - (poster?.size || 0), "w780") || entry.backdrop;
            if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
            if ((poster?.size || 0) + (backdrop?.size || 0) > Math.max(previous, budget)) {
                poster = entry.poster; backdrop = entry.backdrop;
            }
            const added = (poster?.size || 0) + (backdrop?.size || 0);
            if (poster === entry.poster && backdrop === entry.backdrop &&
                    !['overview', 'year', 'tmdb_id'].some(key => fields[key] && fields[key] !== entry[key])) return false;
            await write(["entries"], tx => {
                const table = tx.objectStore("entries"), request = table.get(id);
                request.onsuccess = () => {
                    if (request.result?.status === "complete") table.put({...request.result, ...fields, poster, backdrop,
                        storage_bytes: (request.result.storage_bytes || request.result.size_bytes) - previous + added});
                };
            });
            return true;
        };
        return navigator.locks ? navigator.locks.request("sv-offline-download", {ifAvailable: true}, run) : run(true);
    }

    async function save(file, {audio, subtitles = [], languages, signal, onProgress = () => {}} = {}) {
        if (busy) throw new Error("Another device download is running");
        busy = true;
        const run = async lock => {
            if (navigator.locks && !lock) throw new Error("Another device download is running");
            if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
            await ensureWorker();
            const sourceId = file.playback_url || file.relative_path;
            if (!sourceId) throw new Error("Choose a local or federated file");
            const source = await prepare(file, signal);
            audio ??= source.audio_tracks?.[0]?.stream_index;
            const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify([sourceId, audio ?? null])));
            const sourceKey = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
            const entries = await list();
            let existing = entries.find(entry => entry.source_key === sourceKey && entry.status === "complete");
            if (!existing && audio === source.audio_tracks?.[0]?.stream_index) {
                const legacyDigest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify([sourceId, null])));
                const legacyKey = [...new Uint8Array(legacyDigest)].map(b => b.toString(16).padStart(2, "0")).join("");
                existing = entries.find(entry => entry.status === "complete" && entry.audio_index == null && entry.source_key === legacyKey);
            }
            const used = entries.reduce((sum, entry) => sum + (entry.storage_bytes || entry.size_bytes), 0);
            if (existing) {
                const metadata = await presentation(file, signal);
                return storeSubtitles({...existing, tmdb_id: metadata.tmdb_id || existing.tmdb_id}, [...subtitles, ...(source.subtitles || [])], languages, signal, used);
            }
            const budget = Math.min(limitBytes() - used, await quota());
            if (budget <= 0) throw new Error("Device storage limit reached; remove an offline video first");
            // Persistence is a browser decision, not a guarantee. Failure must
            // not prevent an otherwise supported download.
            try { await navigator.storage?.persist?.(); } catch (_) {}
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
                audio_index: audio ?? source.audio_tracks?.[0]?.stream_index ?? null,
                audio_label: String((source.audio_tracks || []).find(track => track.stream_index === audio)?.language
                    || source.audio_tracks?.[0]?.language || "").slice(0, 32),
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
                const metadata = await presentation(file, signal);
                const {_poster, _backdrop, ...fields} = metadata;
                Object.assign(entry, fields);
                // Include file tracks and search missing preferred languages.
                // Store VTT bytes in the entry, never provider URLs/credentials.
                entry.subtitles = await collectSubtitles(entry, [...subtitles, ...(source.subtitles || [])], languages, [], signal, budget - loaded);
                loaded += subtitleBytes(entry.subtitles);
                entry.poster = await artwork(_poster, signal, budget - loaded, "w342");
                loaded += entry.poster?.size || 0;
                entry.backdrop = await artwork(_backdrop, signal, budget - loaded, "w780");
                loaded += entry.backdrop?.size || 0;
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

    globalThis.SVOffline = {database, list, remove, save, serve, mediaUrl, limitBytes, setLimitGiB, ensureWorker, updatePlayback, prepare, refreshPresentation, refreshSubtitles, subtitleLanguages};
})();
