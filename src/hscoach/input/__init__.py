"""Chargement uniforme et sécurisé des sources de replay."""

from __future__ import annotations

from pathlib import Path

import httpx

from hscoach.input.common import DEFAULT_MAX_SIZE_BYTES, LoadedReplay, validate_replay_xml
from hscoach.input.local import load_local_replay
from hscoach.input.remote import DEFAULT_HTTP_TIMEOUT_SECONDS, load_remote_replay
from hscoach.input.sources import (
    DirectXmlUrlSource,
    HsReplayPageSource,
    LocalReplaySource,
    ReplaySource,
    classify_replay_source,
)

__all__ = [
    "DEFAULT_HTTP_TIMEOUT_SECONDS",
    "DEFAULT_MAX_SIZE_BYTES",
    "DirectXmlUrlSource",
    "HsReplayPageSource",
    "LoadedReplay",
    "LocalReplaySource",
    "ReplaySource",
    "classify_replay_source",
    "load_local_replay",
    "load_remote_replay",
    "load_source",
    "validate_replay_xml",
]


def load_source(
    source: str | Path | ReplaySource,
    *,
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> LoadedReplay:
    """Charger une source via les résolveurs publics V3.

    Le client injecté n'est utilisé que pour les sources distantes. Une page
    publique HSReplay reconnue est refusée localement par son résolveur, sans
    effectuer de requête HTTP.
    """

    resolved = classify_replay_source(source)
    return resolved.load(
        max_size_bytes=max_size_bytes,
        timeout_seconds=timeout_seconds,
        client=client,
    )
