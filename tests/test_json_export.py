import json
from pathlib import Path

import pytest

from hscoach.exceptions import ExportError
from hscoach.models import (
    ActionType,
    GameAction,
    GameAnalysis,
    InformationSource,
    ParseWarning,
    Player,
    PlayerSide,
    ReplayMetadata,
)
from hscoach.output.json_export import analysis_to_dict, export_json, render_json, safe_game_id


def _analysis(*, game_id: str = "partie-42") -> GameAnalysis:
    action = GameAction(
        sequence=1,
        action_type=ActionType.DRAW,
        player=PlayerSide.PLAYER,
        description="JOUEUR pioche Éclair.",
        information_source=InformationSource.REPLAY_EXPLICIT,
        metadata={"tags": {"zêta", "alpha"}},
    )
    return GameAnalysis(
        metadata=ReplayMetadata(
            game_id=game_id,
            build="248348",
            game_format="Standard",
            result="Victoire",
        ),
        player=Player(side=PlayerSide.PLAYER, entity_id=2, player_id=1, card_class="Chaman"),
        opponent=Player(
            side=PlayerSide.OPPONENT,
            entity_id=3,
            player_id=2,
            card_class="Paladin",
        ),
        important_events=[action],
        unresolved_cards=["CARTE_Z", "CARTE_A"],
        warnings=[ParseWarning(code="limite", message="État partiellement reconstruit")],
    )


def test_analysis_to_dict_uses_schema_2_and_stable_root_keys() -> None:
    document = analysis_to_dict(_analysis())

    assert list(document) == [
        "schema_version",
        "game",
        "player",
        "opponent",
        "mulligan",
        "start_of_game_events",
        "turns",
        "important_events",
        "unresolved_cards",
        "warnings",
        "diagnostics",
    ]
    assert document["schema_version"] == "2.0"
    assert document["game"]["game_id"] == "partie-42"
    assert document["player"]["side"] == "PLAYER"
    assert document["important_events"][0]["action_type"] == "Pioche"
    assert document["important_events"][0]["information_source"] == "replay_explicit"
    assert document["important_events"][0]["metadata"]["tags"] == ["alpha", "zêta"]


def test_render_json_is_deterministic_utf8_and_strict() -> None:
    analysis = _analysis()

    first = render_json(analysis)
    second = render_json(analysis)

    assert first == second
    assert first.endswith("\n")
    assert "Éclair" in first
    assert "\\u00c9clair" not in first
    assert json.loads(first)["warnings"][0]["message"] == "État partiellement reconstruit"


def test_export_json_writes_expected_file_atomically(tmp_path: Path) -> None:
    destination = export_json(_analysis(), tmp_path)

    assert destination == tmp_path / "partie-42" / "game_analysis.json"
    assert json.loads(destination.read_text(encoding="utf-8"))["game"]["result"] == "Victoire"
    assert not list(destination.parent.glob("*.tmp"))

    first_bytes = destination.read_bytes()
    assert export_json(_analysis(), tmp_path).read_bytes() == first_bytes


def test_hostile_game_id_cannot_escape_output_directory(tmp_path: Path) -> None:
    hostile_id = "../../rapport-secret"

    destination = export_json(_analysis(game_id=hostile_id), tmp_path)

    assert destination.parent.parent == tmp_path.resolve()
    assert destination.parent.name == safe_game_id(hostile_id)
    assert destination.parent.name.startswith("partie-")
    assert not (tmp_path.parent / "rapport-secret").exists()


def test_export_refuses_sensitive_content_before_writing(tmp_path: Path) -> None:
    analysis = _analysis()
    analysis.warnings.append(ParseWarning(code="privacy", message="accountHi=123"))

    with pytest.raises(ExportError, match="donnée sensible"):
        export_json(analysis, tmp_path)

    assert not list(tmp_path.rglob("game_analysis.json"))


def test_unknown_schema_version_is_rejected() -> None:
    analysis = _analysis()
    analysis.schema_version = "3.0"

    with pytest.raises(ExportError, match="version du schéma JSON"):
        analysis_to_dict(analysis)
