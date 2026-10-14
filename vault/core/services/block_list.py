"""Server-side ad/tracker block list — port of Streambert's BLOCKED_HOSTS
hardcoded set in index.js. Used by the AllManga proxy and any future
stream-proxy route to drop ad requests rather than forward them.

Stats are kept in-process (per-worker) and exposed at /api/block-stats.
"""
from __future__ import annotations

import threading
import time
from collections import Counter
from urllib.parse import urlparse

# Patterns are matched as case-insensitive substring against the request URL.
BLOCKED_HOSTS = [
    # Analytics / trackers
    "google-analytics.com",
    "googletagmanager.com",
    "googletagservices.com",
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "cloudflareinsights.com",
    "scorecardresearch.com",
    "quantserve.com",
    "yandex.ru/metrika",
    "mc.yandex.ru",
    "stats.wp.com",
    "amplitude.com",
    "mixpanel.com",
    "segment.io",
    "newrelic.com",
    "nr-data.net",
    "sentry.io",
    "/collect?",
    "googleads.g.doubleclick",
    # Streambert known stream-site ad domains (50+)
    "sixmossin.com",
    "malocacomals.com",
    "profitableratecpm.com",
    "videasy.users",
    "ads.justpremium",
    "exoclick.com",
    "popads.net",
    "adskeeper.co",
    "propellerads.com",
    "trafficjunky.net",
    "adsterra.com",
    "popcash.net",
    "hilltopads.net",
    "clickadu.com",
    "adsterra.net",
    "onclickperformance.com",
    "rtmark.net",
    "yieldmo.com",
    "criteo.com",
    "outbrain.com",
    "taboola.com",
    "facebook.com/tr",
    "connect.facebook.net",
    "fbevents.com",
    "amazon-adsystem.com",
    "rubiconproject.com",
    "openx.net",
    "appnexus.com",
    "pubmatic.com",
    "moatads.com",
    "media.net",
    "smartadserver.com",
    "casalemedia.com",
    "adnxs.com",
    "indexww.com",
    "betweendigital.com",
    "bidswitch.net",
    "advertising.com",
    "spotxchange.com",
    "rfihub.com",
    "go.affec.tv",
    "adform.net",
    "smaato.net",
    "inmobi.com",
    "mopub.com",
    "applovin.com",
    "vungle.com",
    "tapad.com",
    "krxd.net",
    "demdex.net",
    "tags.tiqcdn.com",
]


_lock = threading.Lock()
_session_counts: Counter = Counter()
_all_time_counts: Counter = Counter()
_started_at = time.time()


def should_block(url: str) -> bool:
    if not url:
        return False
    low = url.lower()
    for pat in BLOCKED_HOSTS:
        if pat in low:
            _record(url)
            return True
    return False


def _record(url: str) -> None:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = "?"
    with _lock:
        _session_counts[host] += 1
        _all_time_counts[host] += 1


def stats() -> dict:
    with _lock:
        return {
            "session_total": sum(_session_counts.values()),
            "all_time_total": sum(_all_time_counts.values()),
            "session_by_host": dict(_session_counts.most_common(40)),
            "all_time_by_host": dict(_all_time_counts.most_common(40)),
            "uptime_seconds": int(time.time() - _started_at),
        }


def reset_session() -> None:
    with _lock:
        _session_counts.clear()
