"""
Cross-origin m3u8 sniffer — server-side port of Streambert's
session.webRequest.onBeforeRequest interceptor.

Streambert can read the m3u8 URL fired by VidSrc/Videasy/2Embed because the
embed runs inside an Electron <webview> in the same process. A browser
can't do that. We do it server-side: a headless Chromium (Playwright)
loads the embed URL, listens to all network requests, and returns the
first `.m3u8` URL that flies past — plus the Referer and User-Agent the
server used, so the StreamVault download manager can replay the request.

Provides:
  - sniff(url, *, timeout, max_wait_idle) -> dict with the m3u8 + headers
  - background `playwright install chromium` is the operator's job; if
    Playwright is not installed the service returns {ok:false, reason}.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Default per-call timeout (s). The embed players take ~3-8s to fire the
# m3u8 request — we wait up to MAX_TIMEOUT_S for the first hit.
DEFAULT_TIMEOUT_S = 25
# Once we've seen something media-looking, keep listening this long for a
# better candidate (master.m3u8 > chunklist.m3u8).
IDLE_AFTER_HIT_S = 2.0
# Streambert's BLOCKED_HOSTS-equivalent — drop these so we don't waste
# time loading ads while the player initializes.
DROP_PATTERNS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "googlesyndication.com", "googleadservices.com", "scorecardresearch.com",
    "yandex.ru/metrika", "mc.yandex.ru", "popads", "popcash", "adsterra",
    "exoclick", "hilltopads", "clickadu", "propellerads", "trafficjunky",
    "outbrain.com", "taboola.com", "criteo.com", "doubleverify.com",
    "facebook.com/tr", "amazon-adsystem.com", "rubiconproject.com",
    "openx.net", "appnexus.com", "pubmatic.com", "rfihub.com", "moatads.com",
    "sixmossin.com", "malocacomals.com", "profitableratecpm.com",
)

M3U8_RE = re.compile(r"\.m3u8(?:\?|$)", re.I)
MP4_RE = re.compile(r"\.mp4(?:\?|$)", re.I)


def _playwright_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401
        return True
    except Exception:
        return False


class M3u8Sniffer:
    """Reuse a single Playwright + browser instance across requests."""

    _lock = asyncio.Lock()
    _pw = None
    _browser = None
    _started = False

    async def _ensure_browser(self):
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--mute-audio",
                ],
            )
            self._started = True

    async def shutdown(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._started = False
        self._browser = None
        self._pw = None

    async def sniff(
        self,
        url: str,
        *,
        referer: Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        prefer_master: bool = True,
    ) -> Dict[str, Any]:
        """Load `url` in a headless tab, return the first m3u8/mp4 URL seen.

        Returns:
            {
              "ok": bool,
              "url": "...m3u8" | None,
              "type": "hls" | "mp4" | None,
              "headers": {"Referer": "...", "User-Agent": "...", ...},
              "subtitles": [{"url": "...", "lang": "?"}],
              "blocked": int,  # how many ad requests were dropped
              "reason": "..."  # only when ok is False
            }
        """
        if not _playwright_available():
            return {
                "ok": False,
                "url": None,
                "reason": (
                    "playwright is not installed inside the container. "
                    "Add `playwright` to requirements.txt and run "
                    "`playwright install chromium` in the image."
                ),
            }

        try:
            await self._ensure_browser()
        except Exception as e:
            return {"ok": False, "url": None, "reason": f"chromium launch failed: {e}"}

        from playwright.async_api import Request, Response

        context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )
        if referer:
            await context.set_extra_http_headers({"Referer": referer})

        # State capture
        m3u8_hits: List[Dict[str, Any]] = []
        mp4_hits: List[Dict[str, Any]] = []
        subtitle_hits: List[Dict[str, Any]] = []
        blocked = 0
        first_hit_at: List[float] = []

        async def _route(route, request: Request):
            nonlocal blocked
            req_url = request.url.lower()
            if any(p in req_url for p in DROP_PATTERNS):
                blocked += 1
                await route.abort()
                return
            await route.continue_()

        await context.route("**/*", _route)

        def _on_request(req: Request):
            u = req.url
            ul = u.lower()
            if M3U8_RE.search(ul):
                hit = {
                    "url": u,
                    "type": "hls",
                    "headers": dict(req.headers),
                    "method": req.method,
                }
                m3u8_hits.append(hit)
                if not first_hit_at:
                    first_hit_at.append(time.time())
            elif MP4_RE.search(ul) and "googlevideo" not in ul:
                # Direct mp4 (rare for these sources but worth capturing)
                mp4_hits.append({
                    "url": u,
                    "type": "mp4",
                    "headers": dict(req.headers),
                    "method": req.method,
                })
                if not first_hit_at:
                    first_hit_at.append(time.time())
            elif ".vtt" in ul or ".srt" in ul:
                subtitle_hits.append({"url": u, "lang": _guess_lang(u)})

        context.on("request", _on_request)

        page = await context.new_page()
        # Embed players that detect headless-marker DOM features tend to fail;
        # the launch flag above + a tiny stealth tweak helps with most.
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        deadline = time.time() + timeout_s
        try:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_s * 1000))
            except Exception as e:
                logger.debug("page.goto failed (ok if media already fired): %s", e)

            # Loop: wait until either we have a hit + IDLE_AFTER_HIT_S has
            # passed without a better one, or the deadline expires.
            while time.time() < deadline:
                await asyncio.sleep(0.25)
                if m3u8_hits or mp4_hits:
                    if first_hit_at and (time.time() - first_hit_at[0]) >= IDLE_AFTER_HIT_S:
                        break
            # If we got hits, pick the best
            chosen = _choose_best(m3u8_hits, mp4_hits, prefer_master=prefer_master)
        finally:
            try:
                await page.close()
            except Exception:
                pass
            try:
                await context.close()
            except Exception:
                pass

        if not chosen:
            return {
                "ok": False,
                "url": None,
                "reason": "no media URL captured before timeout",
                "blocked": blocked,
            }

        # Strip per-request hop-by-hop / browser-internal headers we don't
        # want to replay; keep only what's useful for replay.
        replay = _replay_headers(chosen["headers"], default_referer=url)
        return {
            "ok": True,
            "url": chosen["url"],
            "type": chosen["type"],
            "headers": replay,
            "subtitles": subtitle_hits[:8],
            "blocked": blocked,
        }


def _guess_lang(url: str) -> str:
    m = re.search(r"[?&](?:lang|language)=([a-z]{2,3})", url.lower())
    if m:
        return m.group(1)
    m = re.search(r"[._-]([a-z]{2,3})\.(?:vtt|srt)", url.lower())
    return m.group(1) if m else ""


def _choose_best(m3u8_hits, mp4_hits, *, prefer_master: bool) -> Optional[dict]:
    if m3u8_hits:
        if prefer_master:
            # master playlist is usually a tiny ".m3u8" with multiple bandwidth
            # lines. The path often contains "master" or "playlist" but not
            # "chunklist" or a segment ".ts".
            master = [h for h in m3u8_hits if "master" in h["url"].lower() or "playlist" in h["url"].lower()]
            if master:
                return master[0]
            non_chunk = [h for h in m3u8_hits if "chunklist" not in h["url"].lower()]
            if non_chunk:
                return non_chunk[0]
        return m3u8_hits[0]
    if mp4_hits:
        return mp4_hits[0]
    return None


def _replay_headers(headers: Dict[str, str], default_referer: str) -> Dict[str, str]:
    """Filter to a small set that's safe & useful for the download manager
    or the proxy to re-send when fetching the media URL."""
    out: Dict[str, str] = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in ("user-agent", "referer", "origin", "cookie", "x-requested-with"):
            out[k.title()] = v
    out.setdefault("Referer", default_referer)
    out.setdefault(
        "User-Agent",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    return out


# Module-global singleton (lazy-started).
sniffer = M3u8Sniffer()
