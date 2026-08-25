from hscoach.cli import build_parser, main
from hscoach.config import AppConfig


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
