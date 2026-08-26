"""Couche applicative partagée par la CLI et les interfaces utilisateur."""

from hscoach.application.cancellation import CancellationToken
from hscoach.application.requests import AnalysisRequest
from hscoach.application.results import (
    AnalysisProgress,
    AnalysisResult,
    AnalysisStatus,
    BatchAnalysisResult,
    ProgressStage,
)
from hscoach.application.service import AnalysisService

__all__ = [
    "AnalysisProgress",
    "AnalysisRequest",
    "AnalysisResult",
    "AnalysisService",
    "AnalysisStatus",
    "BatchAnalysisResult",
    "CancellationToken",
    "ProgressStage",
]
