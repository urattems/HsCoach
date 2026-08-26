"""Interface desktop optionnelle de HSCoach."""

from __future__ import annotations


def entrypoint() -> None:
    """Charger Qt uniquement lorsque l'entrée GUI est réellement invoquée."""

    from hscoach.gui.__main__ import entrypoint as run

    run()


__all__ = ["entrypoint"]
