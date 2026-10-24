/* No arbitrary native operations: messages are origin/main-frame scoped in Android. */
(() => {
    'use strict';
    if (window !== window.top) return;
    const send = payload => window.StreamVaultAndroid?.postMessage(JSON.stringify(payload));
    function playing() {
        const active = [...document.querySelectorAll('video')].some(video => !video.paused && !video.ended);
        document.body.classList.toggle('native-video-playing', active);
        send({type: 'playback', playing: active});
    }
    ['play', 'pause', 'ended', 'emptied'].forEach(name => document.addEventListener(name, playing, true));
    window.addEventListener('pagehide', () => send({type: 'playback', playing: false}));
    // Preserve the existing IDB/range worker, but update its public shell from
    // the installed bundle. Never clear cookies, storage or device videos.
    navigator.serviceWorker?.getRegistration('/').then(registration => registration?.update()).catch(() => {});
    document.addEventListener('click', event => {
        const link = event.target.closest?.('a[href]');
        if (link && new URL(link.href).origin === location.origin && new URL(link.href).pathname === '/assets/offline.html') {
            event.preventDefault(); location.href = '/_streamvault/offline';
        }
    });
    window.addEventListener('sv-android-update', event => {
        const release = event.detail?.release;
        if (!release?.android || !window.SV_ANDROID || release.android.version_code <= window.SV_ANDROID.version_code
                || location.pathname === '/about' || document.getElementById('android-update-notice')) return;
        try { if (Number(localStorage.getItem('sv_apk_update_snooze') || 0) > Date.now()) return; } catch (_) {}
        const panel = document.createElement('aside'), label = document.createElement('span'), link = document.createElement('a'), dismiss = document.createElement('button');
        panel.id = 'android-update-notice'; panel.setAttribute('role', 'status');
        const es = document.documentElement.lang === 'es';
        label.textContent = `${es ? 'Actualización Android disponible' : 'Android update available'} · ${release.version}`;
        link.href = '/about'; link.textContent = es ? 'Ver actualización' : 'View update';
        dismiss.type = 'button'; dismiss.textContent = '×'; dismiss.setAttribute('aria-label', es ? 'Recordar mañana' : 'Remind me tomorrow');
        dismiss.onclick = () => { try { localStorage.setItem('sv_apk_update_snooze', String(Date.now() + 86400000)); } catch (_) {} panel.remove(); };
        panel.append(label, link, dismiss); document.body.append(panel);
    });
    const username = document.querySelector('.mobile-more-sheet__logout button')?.textContent.match(/\(([^)]+)\)/)?.[1];
    if (username && document.querySelector('.top-nav__user')) document.querySelector('.top-nav__user').textContent = username[0].toUpperCase();
    if (document.querySelector('.offline-heading')) {
        const about = document.createElement('a'); about.href = '/about';
        about.textContent = document.documentElement.lang === 'es' ? 'Acerca de y actualizaciones' : 'About & updates';
        document.querySelector('.offline-heading').append(about);
    }
    send({type: 'client-ready'});
})();
