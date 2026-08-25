"""Chargement sécurisé des replays présents sur le disque local."""

from __future__ import annotations

from pathlib import Path

from hscoach.exceptions import ReplayInputError
from hscoach.input.common import (
    DEFAULT_MAX_SIZE_BYTES,
    LoadedReplay,
    validate_replay_xml,
    validate_size_limit,
)

SUPPORTED_REPLAY_EXTENSIONS = frozenset({".hsreplay", ".xml", ".txt"})


def load_local_replay(
    path: str | Path,
    *,
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
) -> LoadedReplay:
    """Lire et valider un fichier local de replay.

    La taille est contrôlée avant puis pendant la lecture afin de rester sûre si
    le fichier change entre les deux opérations.
    """

    validate_size_limit(max_size_bytes)
    replay_path = Path(path)
    if replay_path.suffix and replay_path.suffix.lower() not in SUPPORTED_REPLAY_EXTENSIONS:
        extensions = ", ".join(sorted(SUPPORTED_REPLAY_EXTENSIONS))
        raise ReplayInputError(f"Extension de replay non prise en charge (attendu : {extensions}).")

    try:
        file_size = replay_path.stat().st_size
    except FileNotFoundError as exc:
        raise ReplayInputError(f"Fichier de replay introuvable : {replay_path.name}") from exc
    except OSError as exc:
        raise ReplayInputError(
            f"Impossible d'accéder au replay local : {replay_path.name}"
        ) from exc

    if not replay_path.is_file():
        raise ReplayInputError(f"La source locale n'est pas un fichier : {replay_path.name}")
    if file_size > max_size_bytes:
        raise ReplayInputError(
            f"Le replay dépasse la taille maximale autorisée ({max_size_bytes} octets)."
        )

    try:
        with replay_path.open("rb") as stream:
            data = stream.read(max_size_bytes + 1)
    except OSError as exc:
        raise ReplayInputError(f"Impossible de lire le replay local : {replay_path.name}") from exc

    validate_replay_xml(data, max_size_bytes)
    return LoadedReplay(data=data, source_label=replay_path.name)


# Alias court conservé pour les appelants qui connaissent déjà le type de source.
load_local = load_local_replay
