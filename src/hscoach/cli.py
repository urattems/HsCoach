"""Interface en ligne de commande française."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from hscoach.application import AnalysisRequest, AnalysisService, AnalysisStatus
from hscoach.config import AppConfig
from hscoach.exceptions import HSCoachError
from hscoach.models import GameAnalysis, KnowledgeStatus
from hscoach.output import ExportedReports

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
    return AnalysisService(config).inspect(
        source,
        allow_en_fallback=allow_en_fallback,
    )


def _analyse_and_export(
    source: str,
    config: AppConfig,
    *,
    allow_en_fallback: bool = False,
) -> tuple[GameAnalysis, ExportedReports]:
    """Passer par le service partagé tout en conservant le contrat CLI historique."""

    request = AnalysisRequest(
        sources=(source,),
        output_directory=config.output_directory,
        export_markdown=True,
        export_full_json=True,
        export_llm_json=True,
        allow_en_fallback=allow_en_fallback,
    )
    batch = AnalysisService(config).analyze_batch(request)
    if not batch.results:
        raise HSCoachError("L'analyse du replay n'a produit aucun résultat.")
    result = batch.results[0]
    if result.status is not AnalysisStatus.SUCCESS:
        raise HSCoachError(result.error_message or "L'analyse du replay a échoué.")
    if result.analysis is None or result.reports is None:
        raise HSCoachError("L'analyse du replay est incomplète.")
    return result.analysis, result.reports


def _show_diagnostics(analysis: GameAnalysis) -> None:
    diagnostics = analysis.diagnostics
    print("Replay valide : oui")
    print(f"Build Hearthstone : {analysis.metadata.build or 'inconnu'}")
    print(f"Nombre d'entités : {diagnostics.entity_count}")
    print(f"Nombre d'événements : {diagnostics.event_count}")
    print(f"Nombre de demi-tours : {diagnostics.turn_count}")
    print(f"Classe du joueur : {diagnostics.player_class}")
    print(f"Classe adverse : {diagnostics.opponent_class}")
    print(f"Actions enregistrées : {diagnostics.action_count}")
    print(f"Deltas d'état : {diagnostics.state_delta_count}")
    print(f"Améliorations : {diagnostics.buff_count}")
    print(f"Événements de dégâts : {diagnostics.damage_count}")
    print(f"Événements de soins : {diagnostics.heal_count}")
    print(f"Cartes créées : {diagnostics.created_card_count}")
    print(f"Options enregistrées : {diagnostics.option_count}")
    print(f"Événements non classifiés : {diagnostics.unknown_action_count}")
    print(f"Card IDs résolus : {diagnostics.resolved_card_count}")
    print(f"Card IDs inconnus : {diagnostics.unresolved_card_count}")
    print(f"Données de deck : {'oui' if diagnostics.has_player_deck else 'non'}")
    print(f"Mulligan détecté : {'oui' if diagnostics.has_mulligan else 'non'}")
    print(f"Statut du mulligan : {diagnostics.mulligan_status.value}")
    print(f"Options détectées : {'oui' if diagnostics.has_options else 'non'}")
    print(f"Complétude des snapshots : {diagnostics.game_state_completeness}")


def _card_count_label(count: int) -> str:
    return f"{count} {'carte' if count == 1 else 'cartes'}"


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _show_analysis_success(analysis: GameAnalysis, reports: ExportedReports) -> None:
    deck_count = sum(item.count for item in analysis.player.deck)
    mulligan_status = {
        KnowledgeStatus.KNOWN: "reconstruit",
        KnowledgeStatus.PARTIAL: "partiellement reconstruit",
        KnowledgeStatus.UNKNOWN: "non déterminé",
    }[analysis.mulligan.status]
    print("✓ Replay chargé")
    print(f"✓ {_card_count_label(deck_count)} du deck identifiées")
    print("✓ Données frFR chargées")
    print(f"✓ Mulligan {mulligan_status}")
    print(
        f"✓ {analysis.metadata.turn_count} tours complets, "
        f"{analysis.diagnostics.turn_count} demi-tours analysés"
    )
    print(
        f"✓ {analysis.diagnostics.action_count} actions et "
        f"{analysis.diagnostics.state_delta_count} deltas reconstruits"
    )
    print("✓ Données anonymisées")
    print()
    print("Rapports créés :")
    print()
    if reports.markdown is not None:
        print(_display_path(reports.markdown))
    if reports.json is not None:
        print(_display_path(reports.json))
    if reports.llm is not None:
        print(_display_path(reports.llm))


def _run_interactive(config: AppConfig) -> int:
    choice = _interactive_choice()
    if choice == "1":
        return main(["analyser", input("Chemin du replay : ").strip()])
    if choice == "2":
        return main(["analyser", input("URL XML directe : ").strip()])
    if choice == "3":
        cards = AnalysisService(config).refresh_cards()
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
            cards = AnalysisService(config).refresh_cards(locale=args.locale)
            print(f"Données des cartes actualisées : {_card_count_label(len(cards))}.")
            return 0
        if args.command == "inspecter":
            analysis = _load_analysis(args.source, config)
            _show_diagnostics(analysis)
            return 0
        if args.command == "analyser":
            print("Analyse du replay...")
            print()
            analysis, reports = _analyse_and_export(
                args.source,
                config,
                allow_en_fallback=args.allow_en_fallback,
            )
            _show_analysis_success(analysis, reports)
            return 0
        return _run_interactive(config)
    except HSCoachError as exc:
        if args.verbose:
            LOGGER.debug("Détail de l'erreur gérée par la CLI.", exc_info=True)
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2


def entrypoint() -> None:
    """Point d'entrée console qui convertit le code retour en statut processus."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
