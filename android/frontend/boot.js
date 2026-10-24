/* Installed UI; authentication always belongs to the actual configured server/Access. */
(() => {
    'use strict';
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
    const target = location.pathname + location.search;
    document.getElementById('mobile-hosted').href = target + (location.search ? '&' : '?') + 'hosted=1';
    async function boot() {
        try {
            const response = await fetch('/api/mobile/bootstrap', {signal: controller.signal, credentials: 'same-origin', redirect: 'manual', cache: 'no-store'});
            if ([0, 401, 403].includes(response.status) || response.type === 'opaqueredirect') {
                location.replace('/login?next=' + encodeURIComponent(target)); return;
            }
            if (!response.ok) throw new Error('Server unavailable · Servidor no disponible');
            const data = await response.json();
            if (data.protocol !== 1 || !/^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$/.test(data.username)
                    || typeof data.is_admin !== 'boolean' || typeof data.can_download !== 'boolean') {
                throw new Error('Update the server or use the server interface · Actualiza el servidor o usa su interfaz');
            }
            const params = new URLSearchParams(location.search), path = location.pathname;
            let name = path.substring(1);
            if (path === '/browse') name = params.get('view') === 'favorites' ? 'browse-favorites' : params.get('type') === 'tv' ? 'browse-tv' : 'browse-movie';
            if (/^\/media\/\d+$/.test(path)) name = params.get('type') === 'tv' ? 'media-tv' : 'media-movie';
            if (!['portal', 'browse-favorites', 'browse-tv', 'browse-movie', 'downloads', 'media-tv', 'media-movie', 'iptv', 'anime', 'youtube', 'about'].includes(name)) throw new Error('Unsupported page');
            const role = data.is_admin ? 'admin' : data.can_download ? 'downloader' : 'user';
            const lang = data.language === 'es' ? 'es' : 'en';
            const page = await fetch(`/_streamvault/pages/${lang}-${role}-${name}.html`, {signal: controller.signal, cache: 'no-store'});
            if (!page.ok) throw new Error('Installed interface unavailable');
            let html = await page.text();
            html = html.replaceAll('SV_MOBILE_USERNAME', data.username).replaceAll('SV_MOBILE_TMDB_ID', /^\/media\/\d+$/.test(path) ? path.split('/').pop() : '0');
            clearTimeout(timer);
            document.open(); document.write(html); document.close();
        } catch (error) {
            document.getElementById('mobile-status').textContent = 'Connection unavailable · Conexión no disponible';
            document.getElementById('mobile-error-detail').textContent = error.name === 'AbortError' ? 'Connection timed out · Tiempo de conexión agotado' : error.message;
            document.getElementById('mobile-error').hidden = false;
        } finally { clearTimeout(timer); }
    }
    boot();
})();
