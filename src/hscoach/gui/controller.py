"""Modèle de file léger, testable sans widget Qt."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from hscoach.exceptions import ReplayInputError
from hscoach.input.common import safe_local_label
from hscoach.input.sources import (
    DirectXmlUrlSource,
    HsReplayPageSource,
    LocalReplaySource,
    RawXmlSource,
    ReplaySource,
    classify_replay_source,
    validate_local_source_candidate,
)


class QueueStatus(StrEnum):
    PENDING = "En attente"
    RUNNING = "Analyse en cours"
    SUCCESS = "Terminé"
    ERROR = "Erreur"
    CANCELLED = "Annulé"


@dataclass(slots=True)
class ReplayQueueItem:
    identifier: str
    source: ReplaySource = field(repr=False)
    status: QueueStatus = QueueStatus.PENDING
    detail: str | None = None

    @property
    def label(self) -> str:
        return self.source.display_label


class ReplayQueue:
    """File ordonnée, sans persistance des chemins ou URL."""

    def __init__(self, *, max_size_bytes: int = 50 * 1024 * 1024) -> None:
        self.items: list[ReplayQueueItem] = []
        self.max_size_bytes = max_size_bytes
        self._identities: set[str] = set()

    def add(self, raw_source: str | Path | ReplaySource) -> ReplayQueueItem:
        source = classify_replay_source(raw_source)
        if isinstance(source, LocalReplaySource):
            validate_local_source_candidate(source)
            self._validate_local_file(source.path)
        identity = self._identity(source)
        if identity in self._identities:
            raise ReplayInputError("Ce replay est déjà présent dans la liste.")
        item = ReplayQueueItem(
            identifier=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
            source=source,
        )
        self.items.append(item)
        self._identities.add(identity)
        return item

    def remove(self, identifier: str) -> bool:
        for index, item in enumerate(self.items):
            if item.identifier == identifier:
                self._identities.discard(self._identity(item.source))
                del self.items[index]
                return True
        return False

    def reset_statuses(self) -> None:
        for item in self.items:
            item.status = QueueStatus.PENDING
            item.detail = None

    def _validate_local_file(self, path: Path) -> None:
        source_label = safe_local_label(path)
        try:
            stat = path.stat()
        except FileNotFoundError as exc:
            raise ReplayInputError(f"Fichier de replay introuvable : {source_label}") from exc
        except OSError as exc:
            raise ReplayInputError(
                f"Impossible d'accéder au replay local : {source_label}"
            ) from exc
        if not path.is_file():
            raise ReplayInputError(f"La source locale n'est pas un fichier : {source_label}")
        if stat.st_size > self.max_size_bytes:
            raise ReplayInputError(
                f"Le replay dépasse la taille maximale autorisée ({self.max_size_bytes} octets)."
            )

    @staticmethod
    def _identity(source: ReplaySource) -> str:
        if isinstance(source, LocalReplaySource):
            return "local:" + os.path.normcase(str(source.path.expanduser().resolve(strict=False)))
        if isinstance(source, DirectXmlUrlSource | HsReplayPageSource):
            return "url:" + hashlib.sha256(source.url.encode("utf-8")).hexdigest()
        if isinstance(source, RawXmlSource):
            return "raw:" + hashlib.sha256(source.data).hexdigest()
        return f"source:{source.kind}:{source.display_label}"


__all__ = ["QueueStatus", "ReplayQueue", "ReplayQueueItem"]
