/* Folder-based administration of the shared download pool. */
(() => {
    "use strict";
    document.addEventListener("DOMContentLoaded", () => {
        const $ = id => document.getElementById(id);
        const panel = $("admin-panel-files");
        if (!panel) return;
        const es = window.APP_LANG === "es";
        const t = (en, spanish) => es ? spanish : en;
        const escape = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
        const icons = {
            folder: '<path d="M3 7V5a2 2 0 012-2h5l2 3h7a2 2 0 012 2v11a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/>',
            video: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M10 8l6 4-6 4z"/>',
            file: '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6M8 13h8M8 17h5"/>',
            download: '<path d="M12 3v12m-5-5l5 5 5-5M4 17v4h16v-4"/>',
            trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
        };
        const icon = kind => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">${icons[kind] || icons.file}</svg>`;
        let data = null, controller = null, requestId = 0, loadedQuery = null;
        let dirty = true, deleting = false, loading = false, notificationSource = null, refreshTimer = null;
        let previewController = null, previewTrigger = null;
        let cleanerData = null, cleanerTrigger = null, cleanerLoading = false;
        const selected = new Set();
        const cleanerSelected = new Set();
        let view = "list";
        try { view = localStorage.getItem("sv-admin-file-view") === "grid" ? "grid" : "list"; } catch (_) {}

        function fileParams() {
            const params = new URLSearchParams();
            new URLSearchParams(location.search).forEach((value, key) => {
                if (key.startsWith("download_")) params.set(key, value);
            });
            return params;
        }

        function navigate(changes) {
            if (deleting) return;
            const url = new URL(location.href);
            url.searchParams.set("tab", "files");
            for (const [key, value] of Object.entries(changes)) {
                if (value === "" || value === null) url.searchParams.delete(key);
                else url.searchParams.set(key, String(value));
            }
            history.pushState({}, "", url);
            void load();
        }

        function openFolder(path) {
            navigate({download_path: path, download_q: "", download_page: 1});
        }

        async function load() {
            if (panel.hidden || deleting) { dirty = true; return; }
            const id = ++requestId;
            controller?.abort();
            controller = new AbortController();
            const activeController = controller;
            const timeout = setTimeout(() => activeController.abort(), 20000);
            loading = true;
            data = null;
            ["fm-up", "fm-previous", "fm-next"].forEach(id => { $(id).disabled = true; });
            $("fm-summary").textContent = "";
            $("fm-error").hidden = true;
            $("fm-list").setAttribute("aria-busy", "true");
            $("fm-list").innerHTML = `<div class="admin-explorer__empty">${t("Loading files…", "Cargando archivos…")}</div>`;
            selected.clear();
            updateSelection();
            try {
                const response = await fetch(`/admin/api/downloads/list?${fileParams()}`, {signal: controller.signal, cache: "no-store"});
                const result = await response.json();
                if (id !== requestId) return;
                if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
                data = result;
                // Deletion can move the last page back; persist the actual page.
                const url = new URL(location.href);
                url.searchParams.set("download_page", String(data.page));
                history.replaceState({}, "", url);
                loadedQuery = fileParams().toString();
                dirty = false;
                render();
            } catch (error) {
                if (id !== requestId) return;
                dirty = true;
                $("fm-list").innerHTML = "";
                $("fm-error").textContent = error.name === "AbortError"
                    ? t("Loading timed out. Try Refresh.", "La carga ha tardado demasiado. Pulsa Actualizar.") : error.message;
                $("fm-error").hidden = false;
            } finally {
                clearTimeout(timeout);
                if (id === requestId) {
                    loading = false;
                    $("fm-list").setAttribute("aria-busy", "false");
                    updateSelection();
                }
            }
        }

        function render() {
            $("fm-search").value = data.query;
            $("fm-kind").value = data.kind;
            $("fm-sort").value = `${data.sort}:${data.descending ? "desc" : "asc"}`;
            $("fm-clear").hidden = !data.query && data.kind === "all";
            $("fm-up").disabled = data.parent === null;
            $("fm-breadcrumbs").innerHTML = data.breadcrumbs.map((crumb, index) =>
                `<button type="button" data-open-folder="${escape(crumb.path)}" ${index === data.breadcrumbs.length - 1 ? 'aria-current="location"' : ""} title="${escape(crumb.name)}">${escape(index === 0 ? t("Downloads", "Descargas") : crumb.name)}</button>`
            ).join('<span aria-hidden="true">/</span>');
            $("fm-list").innerHTML = data.items.length ? data.items.map(item => {
                const path = escape(item.relative_path), name = escape(item.name);
                return `<article class="admin-explorer__entry" data-entry="${path}">
                    <div class="admin-explorer__check">${item.is_directory ? "" : `<input type="checkbox" data-select="${path}" aria-label="${escape(t("Select ", "Seleccionar ") + item.name)}">`}</div>
                    <button class="admin-explorer__name" type="button" ${item.is_directory ? "data-open-folder" : "data-open-file"}="${path}" title="${name}" aria-label="${name}">
                        <span class="admin-explorer__icon admin-explorer__icon--${item.is_directory ? "folder" : "file"}">${icon(item.kind)}</span>
                        <span><strong>${name}</strong><small>${escape(data.query ? item.folder : item.is_directory ? t("Folder", "Carpeta") : item.name.split(".").pop().toUpperCase())}</small></span>
                    </button>
                    <span class="admin-explorer__size">${escape(item.size_label)}</span>
                    <time class="admin-explorer__modified">${escape(item.modified_label)}</time>
                    <div class="admin-explorer__actions">${item.is_directory ? "" : `
                        <a class="admin-explorer__action" href="/admin/api/downloads/file?relative_path=${encodeURIComponent(item.relative_path)}" download title="${t("Download", "Descargar")}" aria-label="${escape(t("Download ", "Descargar ") + item.name)}">${icon("download")}</a>
                        <button class="admin-explorer__action admin-explorer__action--danger" type="button" data-delete-file="${path}" title="${t("Delete", "Eliminar")}" aria-label="${escape(t("Delete ", "Eliminar ") + item.name)}">${icon("trash")}</button>`}</div>
                </article>`;
            }).join("") : `<div class="admin-explorer__empty">${icon("folder")}<strong>${data.query || data.kind !== "all" ? t("No matching files", "No hay archivos coincidentes") : t("This folder is empty", "Esta carpeta está vacía")}</strong><span>${t("Refresh or choose another folder.", "Actualiza o elige otra carpeta.")}</span></div>`;
            $("fm-summary").textContent = `${data.total_folders} ${t("folders", "carpetas")} · ${data.total_files} ${t("files", "archivos")} · ${data.total_bytes_label}`;
            $("fm-page").textContent = `${data.page} / ${data.pages}`;
            $("fm-previous").disabled = data.page <= 1;
            $("fm-next").disabled = data.page >= data.pages;
            updateSelection();
            setView(view);
        }

        function updateSelection() {
            const files = loading ? [] : data?.items.filter(item => !item.is_directory) || [];
            $("fm-selection").textContent = selected.size ? `${selected.size} ${t("selected", "seleccionados")}` : t("Select files", "Seleccionar archivos");
            $("fm-delete-selected").hidden = !selected.size;
            $("fm-select-all").checked = files.length > 0 && files.every(item => selected.has(item.relative_path));
            $("fm-select-all").indeterminate = selected.size > 0 && !$("fm-select-all").checked;
            $("fm-select-all").disabled = !files.length;
            $("fm-list").querySelectorAll("[data-select]").forEach(input => {
                input.checked = selected.has(input.dataset.select);
                input.closest("[data-entry]").classList.toggle("is-selected", input.checked);
            });
        }

        function setView(next) {
            view = next;
            $("fm-list").dataset.view = view;
            $("fm-columns").hidden = view === "grid";
            panel.querySelectorAll("[data-fm-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.fmView === view)));
            try { localStorage.setItem("sv-admin-file-view", view); } catch (_) {}
        }

        function updateCleanerSelection() {
            const items = cleanerData?.items || [];
            $("fm-cleaner-select-all").checked = items.length > 0 && items.every(item => cleanerSelected.has(item.relative_path));
            $("fm-cleaner-select-all").indeterminate = cleanerSelected.size > 0 && !$("fm-cleaner-select-all").checked;
            $("fm-cleaner-select-all").disabled = cleanerLoading || !items.length;
            $("fm-cleaner-run").disabled = cleanerLoading || !cleanerSelected.size;
            $("fm-cleaner-list").querySelectorAll("[data-cleanup-select]").forEach(input => {
                input.checked = cleanerSelected.has(input.dataset.cleanupSelect);
            });
        }

        function renderCleaner() {
            const items = cleanerData?.items || [];
            const total = items.length;
            $("fm-cleaner-summary").textContent = total
                ? `${total} ${t("candidates", "candidatos")} · ${cleanerData.total_bytes_label}`
                : t("Nothing safe to clean was found.", "No se han encontrado residuos seguros para limpiar.");
            $("fm-cleaner-list").innerHTML = items.length ? items.map(item => {
                const path = escape(item.relative_path);
                return `<label class="admin-cleaner-row"><input type="checkbox" data-cleanup-select="${path}"><span class="admin-cleaner-row__icon">${icon(item.is_directory ? "folder" : "file")}</span><span class="admin-cleaner-row__name"><strong>${escape(item.name)}</strong><small>${escape(item.relative_path)} · ${escape(item.reason)}</small></span><span class="admin-cleaner-row__size">${escape(item.size_label)}</span></label>`;
            }).join("") : `<div class="admin-explorer__empty">${icon("folder")}<strong>${t("Storage is tidy", "El almacenamiento está limpio")}</strong></div>`;
            $("fm-cleaner-count").textContent = total ? String(total) : "";
            $("fm-cleaner-count").hidden = !total;
            updateCleanerSelection();
        }

        async function loadCleaner() {
            cleanerLoading = true;
            $("fm-cleaner-list").setAttribute("aria-busy", "true");
            $("fm-cleaner-list").innerHTML = `<div class="admin-explorer__empty">${t("Scanning storage…", "Analizando almacenamiento…")}</div>`;
            updateCleanerSelection();
            try {
                const response = await fetch("/admin/api/downloads/cleanup", {cache: "no-store"});
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
                cleanerData = result;
                cleanerSelected.clear();
                result.items.forEach(item => cleanerSelected.add(item.relative_path));
                renderCleaner();
            } catch (error) {
                cleanerData = {items: [], total_bytes_label: "0 B"};
                cleanerSelected.clear();
                $("fm-cleaner-summary").textContent = error.message;
                $("fm-cleaner-list").innerHTML = "";
                updateCleanerSelection();
            } finally {
                cleanerLoading = false;
                $("fm-cleaner-list").setAttribute("aria-busy", "false");
                updateCleanerSelection();
            }
        }

        function cleanupCleaner() {
            $("fm-cleaner-overlay").hidden = true;
            $("fm-cleaner-overlay").classList.remove("preview-overlay--open");
            cleanerTrigger?.focus();
        }

        function closeCleaner() {
            if (window.SVShell) window.SVShell.close($("fm-cleaner-overlay"));
            else cleanupCleaner();
        }

        async function runCleaner() {
            if (cleanerLoading || !cleanerSelected.size) return;
            if (!confirm(t("Remove the selected abandoned files and empty folders?", "¿Eliminar los archivos abandonados y carpetas vacías seleccionados?"))) return;
            cleanerLoading = true;
            updateCleanerSelection();
            try {
                const response = await fetch("/admin/api/downloads/cleanup", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({paths: [...cleanerSelected]}),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
                await load();
                await loadCleaner();
                $("fm-cleaner-summary").textContent = `${result.deleted.length} ${t("items removed", "elementos eliminados")}`;
            } catch (error) {
                $("fm-cleaner-summary").textContent = error.message;
            } finally {
                cleanerLoading = false;
                updateCleanerSelection();
            }
        }

        async function deleteFiles(paths) {
            if (deleting || !paths.length) return;
            const label = paths.length === 1 ? paths[0].split("/").pop() : `${paths.length} ${t("files", "archivos")}`;
            if (!confirm(t(`Permanently delete ${label}?`, `¿Eliminar definitivamente ${label}?`))) return;
            deleting = true;
            $("fm-delete-selected").disabled = true;
            let failure = null;
            try {
                for (const path of paths) {
                    const response = await fetch(`/admin/downloads/delete?relative_path=${encodeURIComponent(path)}`, {method: "DELETE"});
                    if (!response.ok) {
                        const result = await response.json().catch(() => ({}));
                        throw new Error(result.detail || `HTTP ${response.status}`);
                    }
                    selected.delete(path);
                }
            } catch (error) { failure = error.message; }
            finally {
                deleting = false;
                $("fm-delete-selected").disabled = false;
                await load();
                if (failure) {
                    $("fm-error").textContent = t("Some files could not be deleted: ", "No se pudieron eliminar todos los archivos: ") + failure;
                    $("fm-error").hidden = false;
                }
            }
        }

        async function preview(item, trigger) {
            previewController?.abort();
            const current = new AbortController();
            previewController = current;
            previewTrigger = trigger;
            $("preview-title").textContent = item.name;
            $("preview-body").textContent = t("Loading…", "Cargando…");
            $("preview-overlay").hidden = false;
            $("preview-overlay").classList.add("preview-overlay--open");
            if (window.SVShell) {
                window.SVShell.open($("preview-overlay"), {
                    initialFocus: $("preview-close"), trigger, onClose: cleanupPreview,
                });
            } else $("preview-close").focus();
            const url = `/api/downloads/files/preview?path=${encodeURIComponent(item.relative_path)}`;
            try {
                if (/\.(jpe?g|png|webp)$/i.test(item.name)) {
                    const img = document.createElement("img");
                    img.src = url;
                    img.alt = item.name;
                    $("preview-body").replaceChildren(img);
                } else {
                    const response = await fetch(url, {signal: current.signal});
                    if (!response.ok) throw new Error(t("Preview unavailable", "Vista previa no disponible"));
                    const text = await response.text();
                    if (current.signal.aborted) return;
                    const pre = document.createElement("pre");
                    pre.textContent = text;
                    $("preview-body").replaceChildren(pre);
                }
            } catch (error) { if (!current.signal.aborted) $("preview-body").textContent = error.message; }
        }

        function cleanupPreview() {
            previewController?.abort();
            $("preview-overlay").hidden = true;
            $("preview-overlay").classList.remove("preview-overlay--open");
            $("preview-body").replaceChildren();
            previewTrigger?.focus();
        }

        function closePreview() {
            if (window.SVShell) window.SVShell.close($("preview-overlay"));
            else cleanupPreview();
        }

        panel.addEventListener("click", event => {
            const folder = event.target.closest("[data-open-folder]");
            if (folder) return openFolder(folder.dataset.openFolder);
            const viewButton = event.target.closest("[data-fm-view]");
            if (viewButton) return setView(viewButton.dataset.fmView);
            const remove = event.target.closest("[data-delete-file]");
            if (remove) return void deleteFiles([remove.dataset.deleteFile]);
            const file = event.target.closest("[data-open-file]");
            if (file) {
                const item = data.items.find(entry => entry.relative_path === file.dataset.openFile);
                if (item.previewable) void preview(item, file);
                else {
                    if (selected.has(item.relative_path)) selected.delete(item.relative_path);
                    else selected.add(item.relative_path);
                    updateSelection();
                }
            }
        });
        $("fm-list").addEventListener("change", event => {
            const input = event.target.closest("[data-select]");
            if (!input) return;
            if (input.checked) selected.add(input.dataset.select); else selected.delete(input.dataset.select);
            updateSelection();
        });
        $("fm-select-all").addEventListener("change", event => {
            selected.clear();
            if (event.target.checked) data.items.filter(item => !item.is_directory).forEach(item => selected.add(item.relative_path));
            updateSelection();
        });
        $("fm-delete-selected").onclick = () => void deleteFiles([...selected]);
        $("fm-cleaner").onclick = () => {
            cleanerTrigger = $("fm-cleaner");
            $("fm-cleaner-overlay").hidden = false;
            $("fm-cleaner-overlay").classList.add("preview-overlay--open");
            if (window.SVShell) {
                window.SVShell.open($("fm-cleaner-overlay"), {
                    initialFocus: $("fm-cleaner-close"), trigger: cleanerTrigger, onClose: cleanupCleaner,
                });
            } else $("fm-cleaner-close").focus();
            void loadCleaner();
        };
        $("fm-cleaner-close").onclick = closeCleaner;
        $("fm-cleaner-cancel").onclick = closeCleaner;
        $("fm-cleaner-run").onclick = () => void runCleaner();
        $("fm-cleaner-list").addEventListener("change", event => {
            const input = event.target.closest("[data-cleanup-select]");
            if (!input) return;
            if (input.checked) cleanerSelected.add(input.dataset.cleanupSelect);
            else cleanerSelected.delete(input.dataset.cleanupSelect);
            updateCleanerSelection();
        });
        $("fm-cleaner-select-all").addEventListener("change", event => {
            cleanerSelected.clear();
            if (event.target.checked) (cleanerData?.items || []).forEach(item => cleanerSelected.add(item.relative_path));
            updateCleanerSelection();
        });
        $("fm-cleaner-overlay").onclick = event => { if (event.target === $("fm-cleaner-overlay")) closeCleaner(); };
        $("fm-cleaner-overlay").onkeydown = event => {
            if (!window.SVShell && event.key === "Escape") closeCleaner();
        };
        $("fm-root").onclick = () => openFolder("");
        $("fm-up").onclick = () => { if (data && data.parent !== null) openFolder(data.parent); };
        $("fm-refresh").onclick = () => void load();
        $("fm-previous").onclick = () => navigate({download_page: data.page - 1});
        $("fm-next").onclick = () => navigate({download_page: data.page + 1});
        $("fm-clear").onclick = () => navigate({download_q: "", download_kind: "all", download_page: 1});
        $("fm-form").onsubmit = event => {
            event.preventDefault();
            navigate({download_q: $("fm-search").value.trim(), download_kind: $("fm-kind").value, download_page: 1});
        };
        $("fm-kind").onchange = () => $("fm-form").requestSubmit();
        $("fm-sort").onchange = () => {
            const [sort, order] = $("fm-sort").value.split(":");
            navigate({download_sort: sort, download_order: order, download_page: 1});
        };
        $("preview-close").onclick = closePreview;
        $("preview-overlay").onclick = event => { if (event.target === $("preview-overlay")) closePreview(); };
        $("preview-overlay").onkeydown = event => {
            if (!window.SVShell && event.key === "Escape") closePreview();
        };
        document.addEventListener("admin:tab-change", event => {
            if (event.detail.tab !== "files") return;
            if (dirty || loadedQuery !== fileParams().toString()) void load();
            if (!notificationSource && !location.hostname.endsWith(".trycloudflare.com")) {
                notificationSource = new EventSource("/api/downloads/notifications");
                notificationSource.onmessage = event => {
                    try {
                        if (!["download_complete", "library_updated"].includes(JSON.parse(event.data).type)) return;
                        dirty = true;
                        clearTimeout(refreshTimer);
                        refreshTimer = setTimeout(() => { if (!panel.hidden) void load(); }, 500);
                    } catch (_) {}
                };
            }
        });
        setView(view);
    });
})();
