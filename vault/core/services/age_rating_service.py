"""
Age rating service — server-side port of Streambert's ageRating.js.

Looks up the TMDB content-rating / release-date certification for a given
movie/series and maps the country-specific cert to a numeric minimum age.
Certification responses use the shared persistent TMDB cache.
"""
from __future__ import annotations

from typing import Optional

from starlette.concurrency import run_in_threadpool

from services.tmdb_service import tmdb_service


# US MPAA → min age
US_MPAA = {"G": 0, "PG": 7, "PG-13": 13, "R": 17, "NC-17": 18}
# DE FSK
DE_FSK = {"0": 0, "6": 6, "12": 12, "16": 16, "18": 18}
# GB BBFC
GB_BBFC = {"U": 0, "PG": 7, "12": 12, "12A": 12, "15": 15, "18": 18}
# FR CNC
FR = {"U": 0, "10": 10, "12": 12, "16": 16, "18": 18}
# AU ACB
AU = {"G": 0, "PG": 7, "M": 12, "MA15+": 15, "R18+": 18, "X18+": 18}
# NZ
NZ = {"G": 0, "PG": 7, "M": 13, "R13": 13, "R15": 15, "R16": 16, "R18": 18}
# BR DEJUS
BR = {"L": 0, "10": 10, "12": 12, "14": 14, "16": 16, "18": 18}
# CA
CA = {"G": 0, "PG": 7, "14A": 14, "18A": 18, "R": 18, "A": 18}
# JP EIRIN
JP = {"G": 0, "PG12": 12, "R15+": 15, "R18+": 18}

MAPS = {
    "US": US_MPAA,
    "DE": DE_FSK,
    "GB": GB_BBFC,
    "FR": FR,
    "AU": AU,
    "NZ": NZ,
    "BR": BR,
    "CA": CA,
    "JP": JP,
}


def cert_to_min_age(country: str, cert: str) -> Optional[int]:
    if not cert:
        return None
    table = MAPS.get((country or "US").upper())
    if not table:
        return None
    return table.get(cert.strip())


async def get_rating(tmdb_id: int, media_type: str, country: str = "US") -> dict:
    """Return {cert, min_age, country} for a movie or TV series."""
    country = (country or "US").upper()
    media_type = "tv" if media_type == "tv" else "movie"
    data = await run_in_threadpool(tmdb_service.get_certifications, tmdb_id, media_type)
    cert = None

    if media_type == "tv":
        for row in data.get("results", []):
            if (row.get("iso_3166_1") or "").upper() == country:
                cert = row.get("rating") or None
                break
    else:
        for row in data.get("results", []):
            if (row.get("iso_3166_1") or "").upper() != country:
                continue
            for rd in row.get("release_dates", []) or []:
                if rd.get("certification"):
                    cert = rd["certification"]
                    break
            if cert:
                break

    min_age = cert_to_min_age(country, cert) if cert else None
    return {"cert": cert, "min_age": min_age, "country": country}
