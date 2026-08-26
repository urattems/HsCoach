"""Annulation coopérative des travaux applicatifs non encore lancés."""

from __future__ import annotations

from threading import Event


class CancellationToken:
    """Jeton thread-safe ; une analyse déjà lancée est autorisée à se terminer."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        """Demander l'annulation des éléments de file encore en attente."""

        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Indiquer si une annulation a été demandée."""

        return self._event.is_set()
