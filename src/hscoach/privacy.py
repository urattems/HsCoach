"""Dernière barrière contre la divulgation de données privées dans les rapports."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import unquote_plus, urlsplit

from hscoach.exceptions import ExportError

SENSITIVE_FIELD_NAMES = frozenset(
    {
        "accountHi",
        "accountLo",
        "BattleTag",
        "battle_tag",
        "X-Amz-Credential",
        "X-Amz-Algorithm",
        "X-Amz-Security-Token",
        "X-Amz-Signature",
    }
)
_SIGNED_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_BATTLETAG_PATTERN = re.compile(r"(?<![\w])[^\s#\"']{1,32}#\d{4,10}(?!\d)")
_SIGNED_QUERY_MARKERS = tuple(
    marker.casefold()
    for marker in (
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Security-Token",
        "X-Amz-Signature",
    )
)


def redact_sensitive_text(text: str, *, identifiers: Iterable[str] = ()) -> str:
    """Masquer URLs signées, BattleTags et identifiants explicitement fournis."""

    redacted = _SIGNED_URL_PATTERN.sub(_redact_url_match, text)
    redacted = _BATTLETAG_PATTERN.sub("JOUEUR", redacted)
    for identifier in sorted((value for value in identifiers if value), key=len, reverse=True):
        redacted = redacted.replace(identifier, "[IDENTIFIANT MASQUÉ]")
    return redacted


def assert_shareable_text(text: str, *, identifiers: Iterable[str] = ()) -> None:
    """Refuser l'écriture lorsqu'un marqueur sensible est encore présent."""

    forbidden = [*SENSITIVE_FIELD_NAMES, *(value for value in identifiers if value)]
    violations = [marker for marker in forbidden if marker.casefold() in text.casefold()]
    if _BATTLETAG_PATTERN.search(text):
        violations.append("BattleTag")
    if any(_is_signed_url(match.group(0)) for match in _SIGNED_URL_PATTERN.finditer(text)):
        violations.append("URL signée")
    if violations:
        raise ExportError("Le rapport contient encore une donnée sensible et n'a pas été écrit.")


def _redact_url_match(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    if not _is_signed_url(raw_url):
        return raw_url
    parsed = urlsplit(raw_url)
    host = parsed.hostname or "hôte distant"
    return f"{parsed.scheme}://{host}/[URL SIGNÉE MASQUÉE]"


def _is_signed_url(raw_url: str) -> bool:
    """Reconnaître les marqueurs AWS même percent-encodés dans la query string."""

    try:
        query = urlsplit(raw_url).query
    except ValueError:
        return False
    # Deux passes couvrent aussi une query string encodée comme valeur d'un autre
    # paramètre, sans décoder ni restituer le secret lui-même.
    for _ in range(2):
        query = unquote_plus(query)
    folded_query = query.casefold()
    return any(marker in folded_query for marker in _SIGNED_QUERY_MARKERS)
