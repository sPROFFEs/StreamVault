/* Public release notes only. Native installation always requires Android confirmation. */
(() => {
    'use strict';
    const $ = id => document.getElementById(id), es = document.documentElement.lang === 'es';
    const client = window.SV_ANDROID;
    let release;
    if (client) $('about-client').textContent = `${client.version} (${client.version_code}) · Android`;
    function render(data) {
        release = data;
        $('about-release').hidden = !data.version;
        if (!data.version) return;
        $('about-release-title').textContent = `StreamVault ${data.version}`;
        $('about-notes').textContent = data.notes || '';
        const official = /^https:\/\/github\.com\/sPROFFEs\/StreamVault\/releases\/tag\/v\d+\.\d+\.\d+$/;
        if (official.test(data.url)) $('about-release-link').href = data.url;
        const apk = data.android;
        const valid = apk && /^https:\/\/github\.com\/sPROFFEs\/StreamVault\/releases\/download\/v\d+\.\d+\.\d+\/streamvault-\d+\.\d+\.\d+-android\.apk$/.test(apk.url);
        $('about-apk').hidden = !valid || !!window.StreamVaultAndroid;
        if (valid) $('about-apk').href = apk.url;
        $('about-install').hidden = !valid || !window.StreamVaultAndroid || apk.version_code <= client?.version_code;
        $('about-apk-status').textContent = valid && client ? (apk.version_code > client.version_code
            ? (es ? 'Hay una actualización del APK disponible.' : 'An Android update is available.')
            : (es ? 'El APK instalado está actualizado.' : 'The installed APK is up to date.')) : '';
    }
    async function check() {
        $('about-check').disabled = true;
        $('about-status').textContent = es ? 'Buscando actualizaciones…' : 'Checking for updates…';
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20000);
        try {
            if (window.StreamVaultAndroid) window.StreamVaultAndroid.postMessage(JSON.stringify({type: 'check-update'}));
            const response = await fetch('/api/about', {signal: controller.signal, credentials: 'same-origin', cache: 'no-store', redirect: 'error'});
            if (!response.ok) throw new Error('Unavailable');
            const data = await response.json();
            $('about-server').textContent = data.server_version;
            render(data.release);
            $('about-status').textContent = data.release.state === 'unavailable'
                ? (es ? 'No se ha podido comprobar GitHub. Inténtalo más tarde.' : 'Could not check GitHub. Try again later.')
                : (es ? 'Comprobación completada. Las actualizaciones del servidor se gestionan en Administración.' : 'Check complete. Manage server updates in Administration.');
        } catch (_) { $('about-status').textContent = es ? 'Sin conexión con el servidor. Puedes seguir usando la biblioteca offline.' : 'Server unavailable. You can still use the offline library.'; }
        finally { clearTimeout(timer); $('about-check').disabled = false; }
    }
    window.addEventListener('sv-android-update', event => {
        if (event.detail?.release) render(event.detail.release);
        if (event.detail?.message) $('about-apk-status').textContent = event.detail.message;
    });
    $('about-check').addEventListener('click', check);
    $('about-install').addEventListener('click', () => {
        if (release?.android && window.StreamVaultAndroid) window.StreamVaultAndroid.postMessage(JSON.stringify({type: 'install-update'}));
    });
    check();
})();
