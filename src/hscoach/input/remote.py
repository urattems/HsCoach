"""Téléchargement sécurisé d'un replay depuis une URL XML directe."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

from hscoach.exceptions import ReplayInputError
from hscoach.input.common import (
    DEFAULT_MAX_SIZE_BYTES,
    LoadedReplay,
    validate_replay_xml,
    validate_size_limit,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_HTTP_TIMEOUT_SECONDS = 20.0
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


class _DropHttpxQueryLogs(logging.Filter):
    """Bloquer les lignes internes de httpx qui contiendraient une query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        return not any(isinstance(arg, httpx.URL) and bool(arg.query) for arg in args)


# httpx journalise sinon l'URL complète de chaque réponse au niveau INFO. Le log
# applicatif ci-dessous fournit déjà l'hôte utile, sans paramètres sensibles.
_HTTPX_QUERY_FILTER = _DropHttpxQueryLogs()
logging.getLogger("httpx").addFilter(_HTTPX_QUERY_FILTER)


def load_remote_replay(
    url: str,
    *,
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> LoadedReplay:
    """Télécharger en streaming puis valider un replay HTTP(S).

    Un client :class:`httpx.Client` peut être injecté pour les tests ou pour
    mutualiser un pool de connexions. Il reste alors la propriété de l'appelant.
    Aucun message ou log ne contient la query string de l'URL.
    """

    validate_size_limit(max_size_bytes)
    host = safe_remote_label(url)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int | float)
        or timeout_seconds <= 0
    ):
        raise ReplayInputError("Le délai maximal HTTP doit être strictement positif.")

    LOGGER.info("Téléchargement du replay depuis %s", host)
    if client is not None:
        data = _stream_download(
            client,
            url,
            host=host,
            max_size_bytes=max_size_bytes,
            timeout_seconds=timeout_seconds,
        )
    else:
        with httpx.Client(follow_redirects=True) as owned_client:
            data = _stream_download(
                owned_client,
                url,
                host=host,
                max_size_bytes=max_size_bytes,
                timeout_seconds=timeout_seconds,
            )

    validate_replay_xml(data, max_size_bytes)
    return LoadedReplay(data=data, source_label=host)


def safe_remote_label(url: str) -> str:
    """Extraire un hôte affichable sans chemin, query string ni identifiants."""

    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        # Accéder à ``port`` valide aussi sa syntaxe sans jamais restituer l'URL.
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ReplayInputError("L'URL du replay est invalide.") from exc

    if scheme not in _ALLOWED_URL_SCHEMES:
        raise ReplayInputError("Seules les URL HTTP et HTTPS sont autorisées.")
    if not host:
        raise ReplayInputError("L'URL du replay ne contient pas d'hôte valide.")
    if parsed.username is not None or parsed.password is not None:
        raise ReplayInputError("Les identifiants intégrés dans une URL ne sont pas autorisés.")

    try:
        normalized_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ReplayInputError("L'URL du replay contient un nom d'hôte invalide.") from exc
    if port is not None:
        return f"{normalized_host}:{port}"
    return normalized_host


def _stream_download(
    client: httpx.Client,
    url: str,
    *,
    host: str,
    max_size_bytes: int,
    timeout_seconds: float,
) -> bytes:
    chunks = bytearray()
    try:
        with client.stream(
            "GET",
            url,
            timeout=timeout_seconds,
            follow_redirects=True,
        ) as response:
            _raise_for_status(response.status_code, host)
            content_length = _content_length(response)
            if content_length is not None and content_length > max_size_bytes:
                raise _size_error(max_size_bytes)

            for chunk in response.iter_bytes():
                if len(chunks) + len(chunk) > max_size_bytes:
                    raise _size_error(max_size_bytes)
                chunks.extend(chunk)
    except ReplayInputError:
        raise
    except httpx.TimeoutException:
        raise ReplayInputError(
            f"Le téléchargement du replay depuis {host} a dépassé le délai autorisé."
        ) from None
    except httpx.HTTPError:
        raise ReplayInputError(
            f"Impossible de télécharger le replay depuis {host} en raison d'une erreur HTTP."
        ) from None

    return bytes(chunks)


def _content_length(response: httpx.Response) -> int | None:
    raw_value = response.headers.get("content-length")
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        return None
    return value if value >= 0 else None


def _raise_for_status(status_code: int, host: str) -> None:
    if 200 <= status_code < 300:
        return
    if status_code in {401, 403}:
        raise ReplayInputError(
            f"Le serveur {host} a refusé le replay (HTTP {status_code}) ; "
            "l'URL signée est peut-être expirée."
        )
    if status_code == 404:
        raise ReplayInputError(f"Replay distant introuvable sur {host} (HTTP 404).")
    raise ReplayInputError(f"Le serveur {host} a répondu avec le statut HTTP {status_code}.")


def _size_error(max_size_bytes: int) -> ReplayInputError:
    return ReplayInputError(
        f"Le replay distant dépasse la taille maximale autorisée ({max_size_bytes} octets)."
    )


# Alias court conservé pour les appelants qui connaissent déjà le type de source.
load_remote = load_remote_replay
