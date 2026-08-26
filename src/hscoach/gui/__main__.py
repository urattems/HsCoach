"""Point d'entrée de ``python -m hscoach.gui`` et ``hscoach-gui``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hscoach-gui", add_help=True)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Créer la fenêtre, avec fermeture automatique pour le smoke test du build."""

    args = build_parser().parse_args(argv)
    try:
        from PySide6.QtCore import QCoreApplication, QTimer
        from PySide6.QtWidgets import QApplication

        from hscoach.gui.main_window import MainWindow
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("PySide6"):
            print(
                "Erreur : l'interface graphique nécessite PySide6. "
                "Installez le paquet avec l'option gui.",
                file=sys.stderr,
            )
            return 2
        raise

    QCoreApplication.setOrganizationName("HSCoach")
    QCoreApplication.setOrganizationDomain("hscoach.local")
    QCoreApplication.setApplicationName("HSCoach")

    existing = QApplication.instance()
    app = existing or QApplication([sys.argv[0]])
    app.setApplicationDisplayName("Hearthstone Replay Analyzer")
    window = MainWindow()
    window.show()
    if args.smoke_test:
        QTimer.singleShot(250, window.close)
        QTimer.singleShot(500, app.quit)
    if existing is not None:
        return 0
    return app.exec()


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
