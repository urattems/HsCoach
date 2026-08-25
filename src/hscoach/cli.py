"""Interface en ligne de commande française."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace

from hscoach.cards import HearthstoneJSON
from hscoach.config import AppConfig
from hscoach.exceptions import HSCoachError
from hscoach.input import load_source
from hscoach.models import GameAnalysis
from hscoach.replay.parser import analyze_replay_data

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


def _load_analysis(
    source: str,
    config: AppConfig,
    *,
    allow_en_fallback: bool = False,
) -> GameAnalysis:
    loaded = load_source(
        source,
        max_size_bytes=config.max_download_size_bytes,
        timeout_seconds=config.http_timeout_seconds,
    )
    cards = HearthstoneJSON(
        config.cache_directory,
        locale=config.locale,
        timeout=config.http_timeout_seconds,
    ).load()
    english_cards = None
    if allow_en_fallback:
        english_cards = HearthstoneJSON(
            config.cache_directory,
            locale="enUS",
            timeout=config.http_timeout_seconds,
        ).load()
    return analyze_replay_data(
        loaded.data,
        cards,
        english_cards_by_id=english_cards,
        allow_en_fallback=allow_en_fallback,
        source_label=loaded.source_label,
        max_size_bytes=config.max_download_size_bytes,
    )


def _show_diagnostics(analysis: GameAnalysis) -> None:
    diagnostics = analysis.diagnostics
    print("Replay valide : oui")
    print(f"Build Hearthstone : {analysis.metadata.build or 'inconnu'}")
    print(f"Nombre d'entités : {diagnostics.entity_count}")
    print(f"Nombre d'événements : {diagnostics.event_count}")
    print(f"Nombre de demi-tours : {diagnostics.turn_count}")
    print(f"Card IDs résolus : {diagnostics.resolved_card_count}")
    print(f"Card IDs inconnus : {diagnostics.unresolved_card_count}")
    print(f"Données de deck : {'oui' if diagnostics.has_player_deck else 'non'}")
    print(f"Mulligan détecté : {'oui' if diagnostics.has_mulligan else 'non'}")
    print(f"Options détectées : {'oui' if diagnostics.has_options else 'non'}")


def _card_count_label(count: int) -> str:
    return f"{count} {'carte' if count == 1 else 'cartes'}"


def _run_interactive(config: AppConfig) -> int:
    choice = _interactive_choice()
    if choice == "1":
        return main(["analyser", input("Chemin du replay : ").strip()])
    if choice == "2":
        return main(["analyser", input("URL XML directe : ").strip()])
    if choice == "3":
        cards = HearthstoneJSON(config.cache_directory, locale=config.locale).refresh()
        print(f"Données des cartes actualisées : {_card_count_label(len(cards))}.")
    elif choice == "4":
        _show_configuration(config)
    elif choice == "5":
        print("Au revoir.")
    else:
        print("Choix invalide.")
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Exécuter la CLI et retourner un code de statut exploitable par le système."""

    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    config = AppConfig()

    try:
        if args.command == "configuration":
            _show_configuration(config)
            return 0
        if args.command == "actualiser-cartes":
            localized_config = replace(config, locale=args.locale)
            cards = HearthstoneJSON(
                localized_config.cache_directory,
                locale=localized_config.locale,
                timeout=localized_config.http_timeout_seconds,
            ).refresh()
            print(f"Données des cartes actualisées : {_card_count_label(len(cards))}.")
            return 0
        if args.command == "inspecter":
            analysis = _load_analysis(args.source, config)
            _show_diagnostics(analysis)
            return 0
        if args.command == "analyser":
            analysis = _load_analysis(
                args.source,
                config,
                allow_en_fallback=args.allow_en_fallback,
            )
            print(
                f"Replay chargé : {analysis.metadata.game_id} — "
                "les exports seront écrits à l'étape suivante."
            )
            return 0
        return _run_interactive(config)
    except HSCoachError as exc:
        LOGGER.error("%s", exc)
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2


def entrypoint() -> None:
    """Point d'entrée console qui convertit le code retour en statut processus."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
