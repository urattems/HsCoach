from pathlib import Path

from hscoach.cards import HearthstoneJSON
from hscoach.cli import build_parser, main
from hscoach.config import AppConfig

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"


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


def test_inspect_command_displays_real_replay_diagnostics(monkeypatch, capsys) -> None:
    monkeypatch.setattr(HearthstoneJSON, "load", lambda self: {})

    assert main(["inspecter", str(SAMPLE)]) == 0

    output = capsys.readouterr().out
    assert "Replay valide : oui" in output
    assert "Build Hearthstone : 248348" in output
    assert "Données de deck : oui" in output
    assert "Mulligan détecté : oui" in output
    assert "Options détectées : oui" in output


def test_missing_replay_reports_a_french_error(capsys) -> None:
    assert main(["inspecter", "absent.hsreplay"]) == 2

    error = capsys.readouterr().err
    assert "Erreur :" in error
    assert "introuvable" in error


def test_refresh_command_reports_card_count(monkeypatch, capsys) -> None:
    monkeypatch.setattr(HearthstoneJSON, "refresh", lambda self: {"A": object()})

    assert main(["actualiser-cartes"]) == 0

    assert "1 carte" in capsys.readouterr().out
