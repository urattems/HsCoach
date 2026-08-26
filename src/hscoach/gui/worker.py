"""Pont QThread minimal vers le service applicatif synchrone."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot

from hscoach.application import AnalysisRequest, AnalysisService, CancellationToken

LOGGER = logging.getLogger(__name__)


class AnalysisWorker(QObject):
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        service: AnalysisService,
        request: AnalysisRequest,
        cancellation: CancellationToken,
    ) -> None:
        super().__init__()
        self._service = service
        self._request = request
        self._cancellation = cancellation

    @Slot()
    def run(self) -> None:
        try:
            batch = self._service.analyze_batch(
                self._request,
                progress=self.progress.emit,
                cancellation=self._cancellation,
            )
        except Exception:
            LOGGER.debug("Erreur interne du worker d'analyse.", exc_info=True)
            self.failed.emit("Une erreur interne inattendue a interrompu l'analyse.")
            return
        self.finished.emit(batch)


__all__ = ["AnalysisWorker"]
