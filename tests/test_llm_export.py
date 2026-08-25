import json
from pathlib import Path

import pytest

from hscoach.cli import _show_analysis_success
from hscoach.exceptions import ExportError
from hscoach.models import (
    ActionType,
    BoardState,
    CardRef,
    Decision,
    DeckCard,
    EntityDelta,
    GameAction,
    GameAnalysis,
    HeroState,
    MinionState,
    Mulligan,
    Player,
    PlayerSide,
    RecordedOption,
    ReplayMetadata,
    SideState,
    TurnPhase,
    TurnState,
    ValueDelta,
)
from hscoach.output import ExportedReports, export_analysis
from hscoach.output.llm_json import analysis_to_llm_dict, export_llm_json, render_llm_json


def _analysis() -> GameAnalysis:
    card = CardRef(
        entity_id=12,
        card_id="JAIL_850",
        name="Maiev la Gardienne",
        text="Cri de guerre : gagne des caractéristiques.",
        cost=4,
        attack=1,
        health=3,
        card_type="MINION",
    )
    start = BoardState(
        player=SideState(
            side=PlayerSide.PLAYER,
            mana_available=4,
            mana_used=0,
            hero=HeroState(health=30),
            hand=[card],
        ),
        opponent=SideState(side=PlayerSide.OPPONENT, hero=HeroState(health=27, armor=2)),
    )
    end = BoardState(
        player=SideState(
            side=PlayerSide.PLAYER,
            mana_available=0,
            mana_used=4,
            hero=HeroState(health=30),
            board=[MinionState(card=card, attack=1, health=7, max_health=7)],
        ),
        opponent=start.opponent,
    )
    action = GameAction(
        sequence=8,
        action_type=ActionType.PLAY_CARD,
        player=PlayerSide.PLAYER,
        description="JOUEUR joue Maiev la Gardienne.",
        source_card=card,
    )
    return GameAnalysis(
        metadata=ReplayMetadata(
            game_id="partie-llm",
            build="248348",
            result="Victoire",
            turn_count=4,
        ),
        player=Player(
            side=PlayerSide.PLAYER,
            entity_id=2,
            player_id=1,
            card_class="Chaman",
            deck=[DeckCard(card=card, count=2)],
        ),
        opponent=Player(
            side=PlayerSide.OPPONENT,
            entity_id=3,
            player_id=2,
            card_class="Paladin",
        ),
        mulligan=Mulligan(offered=[card], kept=[], returned=[card], received=[]),
        turns=[
            TurnState(
                turn_number=7,
                round_number=4,
                active_player=PlayerSide.PLAYER,
                turn_start_state=start,
                action_phase_start_state=start,
                actions=[action],
                decisions=[
                    Decision(
                        sequence=7,
                        timestamp=None,
                        options=[
                            RecordedOption(
                                index=1,
                                option_type="POWER",
                                description="Jouer Maiev",
                                entity=card,
                                selected=True,
                            ),
                            RecordedOption(
                                index=2,
                                option_type="END_TURN",
                                description="Fin du tour",
                                error="INVALID",
                                available=False,
                            ),
                        ],
                        selected_option_index=1,
                    )
                ],
                entity_deltas=[
                    EntityDelta(
                        sequence=9,
                        entity_id=12,
                        side=PlayerSide.PLAYER,
                        phase=TurnPhase.ACTION_PHASE_END,
                        attribute="HEALTH",
                        value=ValueDelta(before=3, after=7, delta=4),
                        card=card,
                    )
                ],
                action_phase_end_state=end,
                turn_end_state=end,
            )
        ],
        important_events=[action],
    )


def test_llm_document_has_own_schema_and_centralized_cards() -> None:
    document = analysis_to_llm_dict(_analysis())

    assert list(document) == [
        "schema_version",
        "game",
        "cards",
        "player_deck",
        "mulligan",
        "start_of_game_events",
        "turns",
        "important_events",
        "warnings",
    ]
    assert document["schema_version"] == "hscoach-llm/1.0"
    assert document["cards"]["definitions"]["JAIL_850"]["name"] == "Maiev la Gardienne"
    assert document["cards"]["entities"]["12"]["card"] == "JAIL_850"
    assert document["player_deck"] == [{"card": 12, "count": 2}]
    assert document["turns"][0]["action_phase_start"]["player"]["hand"] == [12]
    assert document["turns"][0]["actions"][0]["source_card"] == 12
    decision_options = document["turns"][0]["decisions"][0]["options"]
    assert decision_options["chosen"][0][0:3] == [1, "POWER", 12]
    assert decision_options["unavailable"][0][-1] == "INVALID"
    assert document["turns"][0]["state_changes"]["entity_changes"][0][5:8] == [3, 7, 4]


def test_llm_render_is_compact_deterministic_and_keeps_null_boundaries() -> None:
    analysis = _analysis()
    analysis.turns[0].turn_end_state = None

    first = render_llm_json(analysis)

    assert first == render_llm_json(analysis)
    assert first.endswith("\n")
    assert len(first.encode("utf-8")) < 10_000
    assert "\n  " not in first
    assert json.loads(first)["turns"][0]["turn_end"] is None


def test_llm_export_and_combined_export_write_third_report(tmp_path: Path) -> None:
    direct = export_llm_json(_analysis(), tmp_path / "direct")
    reports = export_analysis(_analysis(), tmp_path / "combined")

    assert direct.name == "game_llm.json"
    assert json.loads(direct.read_text(encoding="utf-8"))["schema_version"] == "hscoach-llm/1.0"
    assert reports.llm is not None
    assert reports.llm.name == "game_llm.json"
    assert reports.llm.is_file()


def test_llm_export_refuses_private_signed_url(tmp_path: Path) -> None:
    analysis = _analysis()
    analysis.important_events[0].metadata["url"] = (
        "https://example.test/replay?X-Amz-Signature=secret"
    )

    with pytest.raises(ExportError, match="donnée sensible"):
        export_llm_json(analysis, tmp_path)

    assert not list(tmp_path.rglob("game_llm.json"))


def test_cli_success_lists_llm_report(capsys, tmp_path: Path) -> None:
    reports = ExportedReports(
        markdown=tmp_path / "game_summary.md",
        json=tmp_path / "game_analysis.json",
        llm=tmp_path / "game_llm.json",
    )

    _show_analysis_success(_analysis(), reports)

    assert "game_llm.json" in capsys.readouterr().out
