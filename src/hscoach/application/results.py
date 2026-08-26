"""Résultats et événements de progression applicatifs sans dépendance Qt."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from hscoach.models import GameAnalysis
from hscoach.output import ExportedReports


class AnalysisStatus(StrEnum):
    """État final d'une source dans un batch."""

    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


class ProgressStage(StrEnum):
    """Étape factuelle atteinte par le moteur."""

    STARTED = "started"
    REPLAY_READY = "replay_ready"
    CARDS_READY = "cards_ready"
    GAME_RECONSTRUCTED = "game_reconstructed"
    REPORTS_GENERATED = "reports_generated"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BATCH_COMPLETE = "batch_complete"


@dataclass(frozen=True, slots=True)
class AnalysisProgress:
    """Notification discrète ; aucun pourcentage artificiel n'est calculé."""

    stage: ProgressStage
    source_index: int
    total_sources: int
    completed_sources: int
    source_label: str
    message: str


@dataclass(slots=True)
class AnalysisResult:
    """Résultat isolé d'une seule source."""

    source_label: str
    status: AnalysisStatus
    analysis: GameAnalysis | None = None
    reports: ExportedReports | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is AnalysisStatus.SUCCESS


@dataclass(slots=True)
class BatchAnalysisResult:
    """Résultats d'un batch, y compris les erreurs et annulations partielles."""

    results: list[AnalysisResult] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(result.status is AnalysisStatus.SUCCESS for result in self.results)

    @property
    def error_count(self) -> int:
        return sum(result.status is AnalysisStatus.ERROR for result in self.results)

    @property
    def cancelled_count(self) -> int:
        return sum(result.status is AnalysisStatus.CANCELLED for result in self.results)
