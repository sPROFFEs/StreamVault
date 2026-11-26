"""Title normalization and confidence checks shared by local and TMDB matching."""
from datetime import date
from difflib import SequenceMatcher
import re
import unicodedata


def normalized_title(value):
    decomposed = unicodedata.normalize("NFKD", (value or "").casefold())
    return "".join(char for char in decomposed if char.isalnum())


def release_year(value):
    match = re.match(r"^([12][0-9]{3})(?:-|$)", value or "")
    if match:
        year = int(match[1])
        if 1888 <= year <= date.today().year + 2:
            return year
    return None


def choose_tmdb_candidate(results, title, year=None, duration_minutes=None):
    """Only auto-select when the best plausible film/show has a clear lead."""
    needle = normalized_title(title)
    if not needle:
        return None
    ranked = {}
    for candidate in results:
        tmdb_id = candidate.get("id")
        if not tmdb_id:
            continue
        aliases = {normalized_title(candidate.get(key)) for key in ("title", "original_title", "name", "original_name")}
        score = max((1.0 if alias == needle else SequenceMatcher(None, needle, alias, autojunk=False).ratio()
                     for alias in aliases if alias), default=0)
        if needle not in aliases and (score < .93 or len(needle) < 7):
            continue
        candidate_year = release_year(candidate.get("release_date") or candidate.get("first_air_date"))
        if year and candidate_year:
            if abs(year - candidate_year) > 1:
                continue
            score += .20 if year == candidate_year else .05
        runtime = candidate.get("runtime")
        if duration_minutes and isinstance(runtime, (int, float)) and runtime > 0:
            relative_difference = abs(duration_minutes - runtime) / duration_minutes
            score += .08 if relative_difference <= .10 else -.12 if relative_difference >= .30 else 0
        if score < 1.0:
            continue
        if tmdb_id not in ranked or ranked[tmdb_id][0] < score:
            ranked[tmdb_id] = (score, candidate)
    ordered = sorted(ranked.values(), key=lambda result: -result[0])
    if not ordered or (len(ordered) > 1 and ordered[0][0] - ordered[1][0] < .075):
        return None
    return ordered[0][1]
