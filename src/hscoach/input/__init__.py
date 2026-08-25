"""Chargement uniforme et sécurisé des sources de replay."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import httpx

from hscoach.exceptions import ReplayInputError
from hscoach.input.common import DEFAULT_MAX_SIZE_BYTES, LoadedReplay, validate_replay_xml
from hscoach.input.local import load_local_replay
from hscoach.input.remote import DEFAULT_HTTP_TIMEOUT_SECONDS, load_remote_replay

__all__ = [
    "DEFAULT_HTTP_TIMEOUT_SECONDS",
    "DEFAULT_MAX_SIZE_BYTES",
    "LoadedReplay",
    "load_local_replay",
    "load_remote_replay",
    "load_source",
    "validate_replay_xml",
]


def load_source(
    source: str | Path,
    *,
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> LoadedReplay:
    """Charger une source locale ou une URL HTTP(S) directe.

    Le client injecté n'est utilisé que pour les sources distantes.
    """

    if isinstance(source, Path):
        return load_local_replay(source, max_size_bytes=max_size_bytes)

    if not isinstance(source, str):
        raise ReplayInputError("La source du replay doit être un chemin ou une URL.")

    try:
        scheme = urlsplit(source).scheme.lower()
    except (TypeError, ValueError) as exc:
        raise ReplayInputError("La source du replay est invalide.") from exc

    if scheme in {"http", "https"}:
        return load_remote_replay(
            source,
            max_size_bytes=max_size_bytes,
            timeout_seconds=timeout_seconds,
            client=client,
        )
    if "://" in source:
        raise ReplayInputError("Seules les URL HTTP et HTTPS sont autorisées.")
    return load_local_replay(source, max_size_bytes=max_size_bytes)
