"""
AllManga.to (a.k.a. allanime.day) episode resolver — Python port of
Streambert's src/ipc/allmanga.js.

Given (title, season, episode, translation_type), returns a direct mp4/m3u8
URL + referer that StreamVault can play in an HTML5 <video> via the stream
proxy route.

Algorithm:
  1. Hardcoded show-IDs (Jojo S1–S6).
  2. Split-season map (Spy×Family).
  3. AniList sequel-chain lookup so S2+ search by the correct sequel title.
  4. allanime.day GQL search with multiple title candidates.
  5. allanime.day GQL episode source-URL list (GET-with-APQ first, falls back
     to POST). Encrypted "tobeparsed" blob is AES-256-CTR with key
     SHA256("Xot36i3lK3:v1").
  6. Hex-decode each `--xxxx...` sourceUrl with ANI-CLI hex map.
  7. Walk providers in priority order (S-mp4 / Luf-Mp4 / Yt-mp4 / Default /
     Sl-Hls); fetch clock.json; pick highest-resolution link.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx
from Crypto.Cipher import AES

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) "
    "Gecko/20100101 Firefox/121.0"
)

ALLANIME_HEX_MAP: Dict[str, str] = {
    "79": "A", "7a": "B", "7b": "C", "7c": "D", "7d": "E", "7e": "F", "7f": "G",
    "70": "H", "71": "I", "72": "J", "73": "K", "74": "L", "75": "M", "76": "N",
    "77": "O", "68": "P", "69": "Q", "6a": "R", "6b": "S", "6c": "T", "6d": "U",
    "6e": "V", "6f": "W", "60": "X", "61": "Y", "62": "Z",
    "59": "a", "5a": "b", "5b": "c", "5c": "d", "5d": "e", "5e": "f", "5f": "g",
    "50": "h", "51": "i", "52": "j", "53": "k", "54": "l", "55": "m", "56": "n",
    "57": "o", "48": "p", "49": "q", "4a": "r", "4b": "s", "4c": "t", "4d": "u",
    "4e": "v", "4f": "w", "40": "x", "41": "y", "42": "z",
    "08": "0", "09": "1", "0a": "2", "0b": "3", "0c": "4", "0d": "5", "0e": "6",
    "0f": "7", "00": "8", "01": "9",
    "15": "-", "16": ".", "67": "_", "46": "~", "02": ":", "17": "/", "07": "?",
    "1b": "#", "63": "[", "65": "]", "78": "@", "19": "!", "1c": "$", "1e": "&",
    "10": "(", "11": ")", "12": "*", "13": "+", "14": ",", "03": ";", "05": "=",
    "1d": "%",
}

ALLANIME_KEY = hashlib.sha256(b"Xot36i3lK3:v1").digest()

PROVIDER_PRIORITY = ["S-mp4", "Luf-Mp4", "Yt-mp4", "Default", "Sl-Hls"]

SEARCH_GQL = (
    "query($search:SearchInput $limit:Int $page:Int "
    "$translationType:VaildTranslationTypeEnumType "
    "$countryOrigin:VaildCountryOriginEnumType){"
    "shows(search:$search limit:$limit page:$page "
    "translationType:$translationType countryOrigin:$countryOrigin){"
    "edges{_id name availableEpisodes __typename}}}"
)
EPISODE_GQL = (
    "query($showId:String! $translationType:VaildTranslationTypeEnumType! "
    "$episodeString:String!){episode(showId:$showId "
    "translationType:$translationType episodeString:$episodeString){"
    "episodeString sourceUrls}}"
)
EPISODE_GQL_HASH = (
    "d405d0edd690624b66baba3068e0edc3ac90f1597d898a1ec8db4e5c43c00fec"
)

HARDCODED_SHOW_IDS: Dict[str, List[str]] = {
    "jojo's bizarre adventure": [
        "MeX4czvkwKGo3zdDp",
        "zyqDjR8te4z6taKyk",
        "GTAQH8Z9K6WbAdXsS",
        "JS9PzKiPanesGRvs5",
        "b6xFsr7MDSMcJArB9",
        "pwduJkjBLytqiWCvM",
    ],
}

SPLIT_SEASONS: Dict[str, Dict[int, List[Dict[str, Any]]]] = {
    "spy x family": {
        1: [
            {"from": 1, "showId": None, "offset": 0},
            {"from": 13, "showId": "H8Aey6QXE7HSqwvW3", "offset": 12},
        ],
    },
}


def decode_allanime_url(encoded: str) -> str:
    if encoded.startswith("--"):
        encoded = encoded[2:]
    result = []
    for i in range(0, len(encoded), 2):
        pair = encoded[i:i + 2]
        result.append(ALLANIME_HEX_MAP.get(pair, pair))
    return "".join(result).replace("\\u002F", "/").replace("\\|", "")


def decode_tobeparsed(blob: str) -> List[Dict[str, Any]]:
    """Decrypt the AES-256-CTR encrypted source list."""
    try:
        buf = base64.b64decode(blob)
        nonce = buf[1:13]                         # 12-byte prefix
        ct = buf[13:len(buf) - 16]
        cipher = AES.new(ALLANIME_KEY, AES.MODE_CTR, nonce=nonce, initial_value=2)
        plain = cipher.decrypt(ct).decode("utf-8", errors="ignore")
    except Exception as e:
        logger.debug("decode_tobeparsed failed: %s", e)
        return []

    sources: List[Dict[str, Any]] = []
    for chunk in re.split(r"[{}]", plain):
        m_url = re.search(r'"sourceUrl"\s*:\s*"(--[^"]+)"', chunk)
        m_name = re.search(r'"sourceName"\s*:\s*"([^"]+)"', chunk)
        m_prio = re.search(r'"priority"\s*:\s*([0-9.]+)', chunk)
        if m_url:
            sources.append({
                "sourceUrl": m_url.group(1),
                "sourceName": m_name.group(1) if m_name else "",
                "priority": float(m_prio.group(1)) if m_prio else 0.0,
            })
    return sources


def parse_episode_source_urls(body: str) -> Optional[List[Dict[str, Any]]]:
    """Try the encrypted path first; fall back to standard JSON."""
    m = re.search(r'"tobeparsed"\s*:\s*"([^"]+)"', body)
    if m:
        sources = decode_tobeparsed(m.group(1))
        if sources:
            return sources
    try:
        data = json.loads(body)
        return data.get("data", {}).get("episode", {}).get("sourceUrls") or None
    except Exception:
        return None


def sanitize_title(t: str) -> str:
    t = re.sub(r"[''`´]", "", t)
    t = re.sub(r"[:!.]", "", t)
    return re.sub(r"\s+", " ", t).strip()


# ── HTTP helpers ──────────────────────────────────────────────────────────────

ALLMANGA_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://allmanga.to",
    "Origin": "https://allmanga.to",
    "Accept": "*/*",
}


async def _post_gql(client: httpx.AsyncClient, variables: dict, query: str) -> str:
    body = json.dumps({"variables": variables, "query": query})
    r = await client.post(
        "https://api.allanime.day/api",
        content=body,
        headers={**ALLMANGA_HEADERS, "Content-Type": "application/json"},
        timeout=12,
    )
    return r.text


async def _get_episode_gql(client: httpx.AsyncClient, variables: dict) -> str:
    """GET with APQ first (ani-cli fix; Origin: youtu-chan.com bypasses CF);
    fall back to POST if GET response is empty or lacks `tobeparsed`."""
    extensions = json.dumps({
        "persistedQuery": {"version": 1, "sha256Hash": EPISODE_GQL_HASH}
    })
    url = (
        "https://api.allanime.day/api"
        f"?variables={quote(json.dumps(variables))}"
        f"&extensions={quote(extensions)}"
    )
    try:
        r = await client.get(
            url,
            headers={**ALLMANGA_HEADERS, "Origin": "https://youtu-chan.com"},
            timeout=12,
        )
        if r.text and "tobeparsed" in r.text:
            return r.text
    except Exception:
        pass
    return await _post_gql(client, variables, EPISODE_GQL)


async def _follow_redirects(client: httpx.AsyncClient, url: str, max_hops: int = 10) -> str:
    """For Yt-mp4 / fast4speed.rsvp — chase the redirect chain to the final URL
    without downloading the body."""
    current = url
    for _ in range(max_hops):
        try:
            r = await client.head(
                current,
                headers={**ALLMANGA_HEADERS, "Referer": "https://allmanga.to"},
                follow_redirects=False,
                timeout=10,
            )
        except Exception:
            return current
        if 300 <= r.status_code < 400 and "location" in r.headers:
            loc = r.headers["location"]
            if loc.startswith("http"):
                current = loc
            else:
                p = urlparse(current)
                current = f"{p.scheme}://{p.netloc}{loc}" if loc.startswith("/") else current + loc
        else:
            return current
    return current


async def _yt_dlp_resolve(youtube_url: str) -> Optional[str]:
    """Resolve a YouTube URL to a direct stream via yt-dlp (must be on PATH)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--no-playlist", "-f",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "-g", youtube_url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return None
        line = stdout.decode("utf-8").strip().splitlines()
        return line[0] if line else None
    except Exception:
        return None


# ── AniList sequel resolver ───────────────────────────────────────────────────

ANILIST_SEASON_QUERY = (
    "query($search:String){Media(search:$search,type:ANIME,sort:SEARCH_MATCH){"
    "title{english romaji}episodes relations{edges{relationType node{type "
    "format title{english romaji}episodes startDate{year}seasonYear}}}}}"
)


async def anilist_season_title(client: httpx.AsyncClient, base_title: str, season: int) -> dict:
    fallback = {"title": base_title, "romaji": None, "episodes": None,
                "nextTitle": None, "nextRomaji": None}
    try:
        r = await client.post(
            "https://graphql.anilist.co",
            json={"query": ANILIST_SEASON_QUERY, "variables": {"search": base_title}},
            timeout=8,
        )
        media = r.json().get("data", {}).get("Media")
        if not media:
            return fallback
    except Exception:
        return fallback

    s1_romaji = (media.get("title") or {}).get("romaji")
    s1_episodes = media.get("episodes")
    edges = (media.get("relations") or {}).get("edges", []) or []
    sequels = [
        e for e in edges
        if e.get("relationType") == "SEQUEL"
        and (e.get("node") or {}).get("type") == "ANIME"
        and (e.get("node") or {}).get("format") in ("TV", "TV_SHORT")
    ]
    sequels.sort(key=lambda e: (e.get("node") or {}).get("startDate", {}).get("year")
                 or (e.get("node") or {}).get("seasonYear") or 9999)

    def _title(node): return (node.get("title") or {}).get("english") or (node.get("title") or {}).get("romaji")
    def _romaji(node): return (node.get("title") or {}).get("romaji")

    if season <= 1:
        nxt = sequels[0]["node"] if sequels else None
        return {
            "title": (media.get("title") or {}).get("english") or base_title,
            "romaji": s1_romaji,
            "episodes": s1_episodes,
            "nextTitle": _title(nxt) if nxt else None,
            "nextRomaji": _romaji(nxt) if nxt else None,
        }
    target = sequels[season - 2] if season - 2 < len(sequels) else None
    if not target:
        return {**fallback, "romaji": s1_romaji}
    nxt = sequels[season - 1]["node"] if season - 1 < len(sequels) else None
    return {
        "title": _title(target["node"]) or base_title,
        "romaji": _romaji(target["node"]) or s1_romaji,
        "episodes": (target["node"].get("episodes") or None),
        "nextTitle": _title(nxt) if nxt else None,
        "nextRomaji": _romaji(nxt) if nxt else None,
    }


# ── Provider walk ─────────────────────────────────────────────────────────────

async def _try_source_urls(client: httpx.AsyncClient, source_urls: List[dict]) -> Optional[dict]:
    decoded = []
    for s in source_urls:
        url = s.get("sourceUrl") or ""
        if not url.startswith("--"):
            continue
        path = decode_allanime_url(url).replace("/clock", "/clock.json")
        decoded.append({
            "sourceName": s.get("sourceName") or "",
            "priority": s.get("priority") or 0,
            "path": path,
        })
    decoded.sort(key=lambda d: (
        PROVIDER_PRIORITY.index(d["sourceName"]) if d["sourceName"] in PROVIDER_PRIORITY else 99
    ))

    for src in decoded:
        fetch_url = src["path"]
        if fetch_url.startswith("//"):
            fetch_url = "https:" + fetch_url
        elif fetch_url.startswith("/"):
            fetch_url = "https://allanime.day" + fetch_url
        elif not fetch_url.startswith("http"):
            fetch_url = "https://allanime.day/" + fetch_url

        try:
            if "fast4speed.rsvp" in fetch_url or src["sourceName"] == "Yt-mp4":
                final = await _follow_redirects(client, fetch_url)
                if not final:
                    continue
                host = (urlparse(final).hostname or "").lower()
                is_googlevideo = host == "googlevideo.com" or host.endswith(".googlevideo.com")
                if (re.search(r"\.(mp4|webm|mkv|m3u8)(\?|$)", final, re.I)
                        or is_googlevideo
                        or ("youtube.com/watch" not in final and "youtu.be/" not in final)):
                    return {
                        "ok": True,
                        "url": final,
                        "resolution": "?",
                        "sourceName": src["sourceName"],
                        "isDirectMp4": ".m3u8" not in final,
                        "referer": "https://allmanga.to",
                    }
                yt = await _yt_dlp_resolve(final)
                if yt:
                    return {
                        "ok": True,
                        "url": yt,
                        "resolution": "?",
                        "sourceName": src["sourceName"],
                        "isDirectMp4": True,
                        "referer": "https://www.youtube.com",
                    }
                continue

            r = await client.get(fetch_url, headers=ALLMANGA_HEADERS, timeout=10)
            if r.status_code != 200 or not r.text:
                continue
            try:
                payload = r.json()
            except Exception:
                continue
            links = payload.get("links") or []
            links = [l for l in links if l.get("link")]
            if not links:
                continue
            mp4 = [l for l in links if ".m3u8" not in l["link"] and "master." not in l["link"]]
            pool = mp4 if mp4 else links
            def _height(l):
                try:
                    return int(re.match(r"\d+", str(l.get("resolutionStr") or "0")).group(0))
                except Exception:
                    return 0
            best = sorted(pool, key=_height, reverse=True)[0]
            return {
                "ok": True,
                "url": best["link"],
                "resolution": str(best.get("resolutionStr") or "?"),
                "sourceName": src["sourceName"],
                "isDirectMp4": ".m3u8" not in best["link"],
                "referer": "https://allmanga.to",
            }
        except Exception as e:
            logger.debug("provider %s failed: %s", src["sourceName"], e)
            continue
    return None


async def _resolve_from_show_id(client: httpx.AsyncClient, show_id: str, ep_str: str, dub_sub: str) -> Optional[dict]:
    candidates = [ep_str]
    if "." not in ep_str:
        candidates.append(ep_str + ".0")
    for attempt in candidates:
        body = await _get_episode_gql(client, {
            "showId": show_id, "translationType": dub_sub, "episodeString": attempt,
        })
        if not body:
            continue
        urls = parse_episode_source_urls(body)
        if urls:
            return await _try_source_urls(client, urls)
    return None


# ── Top-level entry point ────────────────────────────────────────────────────

async def resolve_episode(
    title: str,
    season_number: int = 1,
    episode_number: int = 1,
    is_movie: bool = False,
    translation_type: str = "sub",
) -> Dict[str, Any]:
    """Resolve an anime episode to a direct stream URL."""
    season = season_number or 1
    dub_sub = "dub" if translation_type == "dub" else "sub"
    async with httpx.AsyncClient() as client:
        # 1. Split seasons
        if not is_movie:
            parts = SPLIT_SEASONS.get(title.lower(), {}).get(season)
            if parts:
                active = parts[0]
                for p in parts:
                    if episode_number >= p["from"]:
                        active = p
                part_ep = episode_number - active["offset"]
                if active.get("showId"):
                    res = await _resolve_from_show_id(client, active["showId"], str(part_ep), dub_sub)
                    if res:
                        return res

        # 2. Hardcoded show IDs
        if not is_movie:
            ids = HARDCODED_SHOW_IDS.get(title.lower())
            if ids:
                sid = ids[season - 1] if season - 1 < len(ids) else ids[-1]
                res = await _resolve_from_show_id(client, sid, str(episode_number), dub_sub)
                if res:
                    return res

        # 3. AniList season resolution
        if is_movie:
            anilist = {"title": title, "romaji": None, "episodes": None,
                       "nextTitle": None, "nextRomaji": None}
        else:
            anilist = await anilist_season_title(client, title, season)

        search_title = anilist["title"]
        adj_episode = episode_number
        if (not is_movie and anilist.get("episodes")
                and episode_number > anilist["episodes"] and anilist.get("nextTitle")):
            adj_episode = episode_number - anilist["episodes"]
            search_title = anilist["nextTitle"]
        ep_str = "1" if is_movie else str(adj_episode)

        # 4. Build candidate set
        candidate_set: List[str] = []
        for c in [
            search_title, sanitize_title(search_title),
            anilist.get("romaji") if search_title == anilist.get("title") else None,
            anilist.get("nextRomaji") if search_title == anilist.get("nextTitle") else None,
            title, sanitize_title(title),
        ]:
            if c and c not in candidate_set:
                candidate_set.append(c)

        # 5. Search AllManga
        edges = None
        matched_title = search_title
        for c in candidate_set:
            body = await _post_gql(client, {
                "search": {
                    "allowAdult": True, "allowUnknown": False, "query": c.lower(),
                },
                "limit": 40, "page": 1,
                "translationType": dub_sub, "countryOrigin": "ALL",
            }, SEARCH_GQL)
            try:
                e = (json.loads(body).get("data") or {}).get("shows", {}).get("edges") or []
            except Exception:
                continue
            if e:
                edges = e
                matched_title = c
                break

        if not edges:
            return {"ok": False, "error": f"No results for: {search_title}"}

        title_lower = matched_title.lower()
        anime = next(
            (e for e in edges if (e.get("name") or "").lower() == title_lower),
            edges[0],
        )

        # 6. Episode source URLs
        ep_candidates = [ep_str]
        if "." not in ep_str:
            ep_candidates.append(ep_str + ".0")

        sources = None
        for attempt in ep_candidates:
            body = await _get_episode_gql(client, {
                "showId": anime["_id"],
                "translationType": dub_sub,
                "episodeString": attempt,
            })
            if not body:
                continue
            urls = parse_episode_source_urls(body)
            if urls:
                sources = urls
                break
        if not sources:
            return {"ok": False, "error": f"No sourceUrls for ep {ep_str}"}

        result = await _try_source_urls(client, sources)
        if result:
            return {**result, "searchTitle": search_title}
        return {"ok": False, "error": "No playable link found"}
