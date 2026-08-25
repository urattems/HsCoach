"""Interface en ligne de commande française."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from hscoach.config import AppConfig

LOGGER = logging.getLogger("hscoach")


def build_parser() -> argparse.ArgumentParser:
    """Construire le parseur d'arguments sans exécuter de commande."""

    parser = argparse.ArgumentParser(
        prog="hscoach",
        description="Transformer un replay Hearthstone en rapports factuels en français.",
    )
    parser.add_argument("--verbose", action="store_true", help="Afficher les diagnostics DEBUG.")
    subparsers = parser.add_subparsers(dest="command")

    analyse = subparsers.add_parser("analyser", help="Analyser un fichier ou une URL XML.")
    analyse.add_argument("source", help="Chemin du replay ou URL HTTP/HTTPS directe.")
    analyse.add_argument(
        "--allow-en-fallback",
        action="store_true",
        help="Autoriser explicitement un nom anglais si la traduction française manque.",
    )

    inspect = subparsers.add_parser("inspecter", help="Afficher les diagnostics d'un replay.")
    inspect.add_argument("source", help="Chemin du replay ou URL HTTP/HTTPS directe.")

    refresh = subparsers.add_parser(
        "actualiser-cartes", help="Actualiser le cache HearthstoneJSON frFR."
    )
    refresh.add_argument("--locale", default="frFR", help="Locale à télécharger (défaut : frFR).")

    subparsers.add_parser("configuration", help="Afficher la configuration active.")
    return parser


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s — %(message)s",
    )


def _show_configuration(config: AppConfig) -> None:
    print("Configuration active")
    print(f"- Locale : {config.locale}")
    print("- Anonymisation : oui")
    print(f"- Taille maximale : {config.max_download_size_mb} Mo")
    print(f"- Timeout HTTP : {config.http_timeout_seconds:g} s")
    print(f"- Dossier de sortie : {config.output_directory}")
    print(f"- Dossier de cache : {config.cache_directory}")


def _interactive_choice() -> str:
    print("Hearthstone Replay Analyzer")
    print()
    print("1. Analyser un fichier de replay")
    print("2. Analyser une URL XML")
    print("3. Actualiser les données des cartes")
    print("4. Afficher la configuration")
    print("5. Quitter")
    return input("Votre choix : ").strip()


def main(argv: Sequence[str] | None = None) -> int:
    """Exécuter la coque CLI; les traitements seront branchés aux étapes suivantes."""

    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    config = AppConfig()

    if args.command == "configuration":
        _show_configuration(config)
        return 0
    if args.command in {"analyser", "inspecter", "actualiser-cartes"}:
        print("Commande initialisée ; le traitement sera activé à l'étape suivante.")
        return 0

    choice = _interactive_choice()
    if choice == "4":
        _show_configuration(config)
    elif choice == "5":
        print("Au revoir.")
    else:
        print("Fonction en cours d'implémentation.")
    return 0


def entrypoint() -> None:
    """Point d'entrée console qui convertit le code retour en statut processus."""

    raise SystemExit(main())
