"""Stable embed-provider registry used by the browser player.

These are public embed routes, not extracted media URLs. The selected route is
loaded in an iframe; downloads use the existing server-side media sniffer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

@dataclass(frozen=True)
class EmbedProviderDefinition:
    id: str
    label: str
    origin: str
    movie_template: str = "/movie/{id}"
    tv_template: str = "/tv/{id}/{s}/{e}"
    failure_event: Optional[str] = None
    failure_origin: Optional[str] = None

    def build_url(
        self,
        tmdb_id: str,
        media_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> str:
        if media_type == "tv":
            if season is None or episode is None:
                raise ValueError("TV sources require season and episode")
            path = self.tv_template.format(id=tmdb_id, s=season, e=episode)
        elif media_type == "movie":
            path = self.movie_template.format(id=tmdb_id)
        else:
            raise ValueError(f"Unsupported media type: {media_type}")
        return f"{self.origin}{path}"


EMBED_PROVIDERS: tuple[EmbedProviderDefinition, ...] = (
    EmbedProviderDefinition("videasy", "Videasy", "https://player.videasy.to"),
    EmbedProviderDefinition("vidlink", "VidLink", "https://vidlink.pro"),
    EmbedProviderDefinition("vidfast", "VidFast", "https://vidfast.vc"),
    EmbedProviderDefinition("vidrock", "VidRock", "https://vidrock.ru"),
)


def normalize_source_preferences(
    preferred_source: Optional[str],
    source_order: Optional[Iterable[str]],
) -> tuple[str, list[str]]:
    provider_ids = [provider.id for provider in EMBED_PROVIDERS]
    valid_ids = set(provider_ids)
    preferred = preferred_source if preferred_source in valid_ids else provider_ids[0]
    order = []
    for provider_id in [*(source_order or []), *provider_ids]:
        if provider_id in valid_ids and provider_id not in order:
            order.append(provider_id)
    return preferred, order


def _ordered_providers(
    preferred_source: Optional[str],
    source_order: Optional[Iterable[str]],
) -> list[EmbedProviderDefinition]:
    by_id = {provider.id: provider for provider in EMBED_PROVIDERS}
    preferred, order = normalize_source_preferences(preferred_source, source_order)
    requested = [preferred, *order]

    ordered = []
    seen = set()
    for provider_id in requested:
        provider = by_id.get(provider_id)
        if provider and provider_id not in seen:
            seen.add(provider_id)
            ordered.append(provider)
    return ordered


def build_embed_streams(
    tmdb_id: str,
    media_type: str = "movie",
    season: Optional[int] = None,
    episode: Optional[int] = None,
    preferred_source: Optional[str] = None,
    source_order: Optional[Iterable[str]] = None,
) -> list[dict]:
    return [
        {
            "url": provider.build_url(tmdb_id, media_type, season, episode),
            "quality": "Multi",
            "source": provider.label,
            "type": "embed",
            "provider_id": provider.id,
            "origin": provider.origin,
            "failure_event": provider.failure_event,
            "failure_origin": provider.failure_origin,
            "download_mode": "sniff",
        }
        for provider in _ordered_providers(preferred_source, source_order)
    ]
