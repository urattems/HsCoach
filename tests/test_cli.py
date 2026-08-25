from pathlib import Path

import pytest

import hscoach.cli as cli
from hscoach.cards import HearthstoneJSON
from hscoach.cli import build_parser, main
from hscoach.config import AppConfig
from hscoach.models import GameAnalysis, Player, PlayerSide, ReplayMetadata
from hscoach.output import ExportedReports

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"
requires_user_sample = pytest.mark.skipif(
    not SAMPLE.is_file(), reason="Replay utilisateur local non disponible."
)


def test_cli_parses_analyse_command() -> None:
    args = build_parser().parse_args(["analyser", "partie.hsreplay"])

    assert args.command == "analyser"
    assert args.source == "partie.hsreplay"
    assert args.allow_en_fallback is False


def test_configuration_defaults_are_safe() -> None:
    config = AppConfig()

    assert config.locale == "frFR"
    assert config.anonymize is True
    assert config.allow_en_fallback is False
    assert config.max_download_size_bytes == 50 * 1024 * 1024


def test_configuration_command_is_in_french(capsys) -> None:
    assert main(["configuration"]) == 0

    output = capsys.readouterr().out
    assert "Configuration active" in output
    assert "Anonymisation : oui" in output


@requires_user_sample
def test_inspect_command_displays_real_replay_diagnostics(monkeypatch, capsys) -> None:
    monkeypatch.setattr(HearthstoneJSON, "load", lambda self: {})

    assert main(["inspecter", str(SAMPLE)]) == 0

    output = capsys.readouterr().out
    assert "Replay valide : oui" in output
    assert "Build Hearthstone : 248348" in output
    assert "Données de deck : oui" in output
    assert "Mulligan détecté : oui" in output
    assert "Options détectées : oui" in output


def test_missing_replay_reports_one_french_error(capsys, caplog) -> None:
    assert main(["inspecter", "absent.hsreplay"]) == 2

    error = capsys.readouterr().err
    assert error.count("Erreur :") == 1
    assert error.count("introuvable") == 1
    assert "ERROR" not in error
    assert not caplog.records


def test_refresh_command_reports_card_count(monkeypatch, capsys) -> None:
    monkeypatch.setattr(HearthstoneJSON, "refresh", lambda self: {"A": object()})

    assert main(["actualiser-cartes"]) == 0

    assert "1 carte" in capsys.readouterr().out


def test_analyse_command_exports_both_reports_in_french(monkeypatch, capsys, tmp_path) -> None:
    analysis = GameAnalysis(
        metadata=ReplayMetadata(game_id="42", turn_count=3),
        player=Player(
            side=PlayerSide.PLAYER,
            entity_id=2,
            player_id=1,
            card_class="Chaman",
        ),
        opponent=Player(
            side=PlayerSide.OPPONENT,
            entity_id=3,
            player_id=2,
            card_class="Mage",
        ),
    )
    reports = ExportedReports(
        markdown=tmp_path / "42" / "game_summary.md",
        json=tmp_path / "42" / "game_analysis.json",
    )
    monkeypatch.setattr(cli, "_load_analysis", lambda *args, **kwargs: analysis)
    monkeypatch.setattr(cli, "export_analysis", lambda *args, **kwargs: reports)

    assert main(["analyser", "replay.hsreplay"]) == 0

    output = capsys.readouterr().out
    assert "Analyse du replay..." in output
    assert "✓ Replay chargé" in output
    assert "✓ Données anonymisées" in output
    assert "Rapports créés :" in output
    assert "game_summary.md" in output
    assert "game_analysis.json" in output
