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
    Provenance,
    RecordedChoice,
    RecordedOption,
    ReplayMetadata,
    SideState,
    StateDelta,
    TurnPhase,
    TurnState,
    ValueDelta,
    Visibility,
    ZoneDelta,
)
from hscoach.output import ExportedReports, export_analysis
from hscoach.output.json_export import analysis_to_dict
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


def test_llm_groups_beatrix_but_full_json_keeps_both_protocol_events() -> None:
    analysis = _analysis()
    beatrix = CardRef(
        entity_id=70,
        card_id="BEATRIX",
        name="Commandante Beatrix",
        card_type="MINION",
    )
    description = "Commandante Beatrix déclenche son effet de début de partie."
    analysis.start_of_game_events = [
        GameAction(
            sequence=1,
            action_type=ActionType.START_GAME_EFFECT,
            player=PlayerSide.PLAYER,
            description=description,
            source_card=beatrix,
            metadata={"protocol_only_reveal": False},
        ),
        GameAction(
            sequence=2,
            action_type=ActionType.START_GAME_EFFECT,
            player=PlayerSide.PLAYER,
            description=description,
            source_card=beatrix,
            metadata={"protocol_only_reveal": True},
        ),
    ]

    full = analysis_to_dict(analysis)
    compact = analysis_to_llm_dict(analysis)

    assert len(full["start_of_game_events"]) == 2
    assert len(compact["start_of_game_events"]) == 1
    assert compact["start_of_game_events"][0]["details"]["protocol_occurrences"] == 2


def test_llm_excludes_technical_entities_but_keeps_their_gameplay_delta() -> None:
    analysis = _analysis()
    maiev = analysis.player.deck[0].card
    technical = CardRef(
        entity_id=90,
        card_id="TECH_001e",
        name="Cost - 2",
        card_type="ENCHANTMENT",
    )
    technical_action = GameAction(
        sequence=10,
        action_type=ActionType.CARD_CREATED,
        player=PlayerSide.SYSTEM,
        description="Cost - 2 entre dans la partie.",
        target_card=technical,
        technical=True,
    )
    analysis.turns[0].actions.append(technical_action)
    analysis.important_events.append(technical_action)
    analysis.turns[0].entity_deltas.extend(
        [
            EntityDelta(
                sequence=11,
                entity_id=maiev.entity_id or 12,
                side=PlayerSide.PLAYER,
                phase=TurnPhase.ACTION_PHASE_END,
                attribute="health",
                value=ValueDelta(before=3, after=5, delta=2),
                card=maiev,
                source_card=technical,
            ),
            EntityDelta(
                sequence=12,
                entity_id=90,
                side=PlayerSide.SYSTEM,
                phase=TurnPhase.ACTION_PHASE_END,
                attribute="attack",
                value=ValueDelta(before=0, after=1, delta=1),
                card=technical,
                technical=True,
            ),
        ]
    )

    full = analysis_to_dict(analysis)
    compact = analysis_to_llm_dict(analysis)
    rendered = json.dumps(compact, ensure_ascii=False)

    assert any(action["technical"] for action in full["turns"][0]["actions"])
    assert [action["seq"] for action in compact["turns"][0]["actions"]] == [8]
    assert compact["important_events"] == [8]
    changes = compact["turns"][0]["state_changes"]["entity_changes"]
    assert [change[0] for change in changes] == [9, 11]
    assert "TECH_001e" not in rendered
    assert "Cost - 2" not in rendered


def test_llm_serializes_structured_provenance_for_a_real_creation() -> None:
    analysis = _analysis()
    generated = CardRef(
        entity_id=13,
        card_id="GENERATED",
        name="Carte générée",
        provenance=Provenance(
            creator_entity_id=12,
            creator_card_id="JAIL_850",
        ),
    )
    creation = GameAction(
        sequence=13,
        action_type=ActionType.CARD_CREATED,
        player=PlayerSide.PLAYER,
        description="Carte générée entre dans la partie.",
        source_card=analysis.player.deck[0].card,
        target_card=generated,
        metadata={"event_type": "CARD_CREATED"},
    )
    analysis.turns[0].actions.append(creation)
    analysis.important_events.append(creation)

    compact = analysis_to_llm_dict(analysis)

    assert generated.created_by_entity_id == 12
    assert compact["cards"]["entities"]["13"]["provenance"] == {
        "creator_entity_id": None,
        "creator_card_id": "JAIL_850",
        "confidence": "replay_explicit",
    }
    assert compact["important_events"] == [8, 13]


def test_llm_hidden_handles_are_opaque_stable_and_do_not_link_a_later_reveal() -> None:
    hidden_first = CardRef(
        entity_id=830001,
        card_id=None,
        name="Carte inconnue",
        visibility=Visibility.HIDDEN,
        provenance=Provenance(creator_entity_id=830002, creator_card_id="SOURCE"),
    )
    hidden_second = CardRef(
        entity_id=830002,
        card_id=None,
        name="Carte inconnue",
        visibility=Visibility.HIDDEN,
    )
    revealed = CardRef(
        entity_id=830001,
        card_id="REVEALED",
        name="Carte révélée",
    )
    hidden_state = BoardState(
        player=SideState(side=PlayerSide.PLAYER),
        opponent=SideState(
            side=PlayerSide.OPPONENT,
            hand=[hidden_first, hidden_second, hidden_first],
            hidden_hand_count=2,
        ),
    )
    hidden_action = GameAction(
        sequence=1,
        action_type=ActionType.DRAW,
        player=PlayerSide.OPPONENT,
        description="ADVERSAIRE pioche une carte inconnue.",
        target_card=hidden_first,
        metadata={
            "entity_id": 830001,
            "target_entity_id": 830001,
            "source_entity_id": 830002,
            "offered_entity_ids": [830001, 830002],
            "chosen_entity_ids": [830001],
            "protocol_events": [
                {
                    "entity": 830001,
                    "entity_id": 830001,
                    "auxiliary_entity_ids": [830001, 830002],
                    "target_entity_id": 830002,
                }
            ],
        },
    )
    hidden_delta = EntityDelta(
        sequence=2,
        entity_id=830001,
        side=PlayerSide.OPPONENT,
        phase=TurnPhase.ACTION_PHASE_END,
        attribute="zone",
        value=ValueDelta(before="DECK", after="HAND"),
        card=hidden_first,
        source_card=hidden_second,
        metadata={"entity_id": 830001, "creator_entity_id": 830002},
    )
    analysis = GameAnalysis(
        metadata=ReplayMetadata(game_id="temporal-hidden"),
        player=Player(side=PlayerSide.PLAYER, entity_id=2, player_id=1),
        opponent=Player(side=PlayerSide.OPPONENT, entity_id=3, player_id=2),
        turns=[
            TurnState(
                turn_number=1,
                round_number=1,
                active_player=PlayerSide.OPPONENT,
                action_phase_start_state=hidden_state,
                action_phase_end_state=hidden_state,
                actions=[hidden_action],
                decisions=[
                    Decision(
                        sequence=3,
                        timestamp=None,
                        options=[
                            RecordedOption(
                                index=1,
                                option_type="Action",
                                description="Action inconnue",
                                entity=hidden_first,
                                targets=[hidden_second],
                                selected=True,
                            )
                        ],
                        selected_option_index=1,
                        selected_target_entity_id=830002,
                    )
                ],
                choices=[
                    RecordedChoice(
                        sequence=4,
                        timestamp=None,
                        choice_type="Choix général",
                        player=PlayerSide.OPPONENT,
                        offered=[hidden_first, hidden_second],
                        chosen=[hidden_first],
                        source_card=hidden_second,
                        completed=True,
                    )
                ],
                entity_deltas=[hidden_delta],
                state_deltas=[
                    StateDelta(
                        from_phase=TurnPhase.ACTION_PHASE_START,
                        to_phase=TurnPhase.ACTION_PHASE_END,
                        zones=[
                            ZoneDelta(
                                entity_id=830001,
                                side=PlayerSide.OPPONENT,
                                from_zone="DECK",
                                to_zone="HAND",
                                card=hidden_first,
                            )
                        ],
                    )
                ],
            ),
            TurnState(
                turn_number=2,
                round_number=1,
                active_player=PlayerSide.OPPONENT,
                actions=[
                    GameAction(
                        sequence=5,
                        action_type=ActionType.PLAY_CARD,
                        player=PlayerSide.OPPONENT,
                        description="ADVERSAIRE joue Carte révélée.",
                        source_card=revealed,
                        metadata={"entity_id": 830001},
                    )
                ],
            ),
        ],
    )

    first_render = render_llm_json(analysis)
    second_render = render_llm_json(analysis)
    first = json.loads(first_render)
    historic_turn = json.dumps(first["turns"][0], ensure_ascii=False)

    assert first_render == second_render
    assert "hidden:830001" not in first_render
    assert "hidden:830002" not in first_render
    assert "830001" not in historic_turn
    assert "830002" not in historic_turn
    assert first["cards"]["entities"]["hidden:h1"] == {
        "card": "hidden",
        "visibility": "hidden",
    }
    assert first["cards"]["entities"]["hidden:h2"] == {
        "card": "hidden",
        "visibility": "hidden",
    }
    assert first["turns"][0]["action_phase_start"]["opponent"]["hand"] == [
        "hidden:h1",
        "hidden:h2",
        "hidden:h1",
    ]
    assert first["turns"][0]["decisions"][0]["selected_target_entity"] == "hidden:h2"
    assert "creator_entity_id" not in json.dumps(first, ensure_ascii=False)
    assert first["turns"][1]["actions"][0]["source_card"] == 830001
    assert first["cards"]["entities"]["830001"]["card"] == "REVEALED"
    assert "hidden:h1" not in json.dumps(first["cards"]["entities"]["830001"], ensure_ascii=False)
    assert "830001" not in json.dumps(first["cards"]["entities"]["hidden:h1"], ensure_ascii=False)


def test_llm_selected_option_wins_over_an_invalid_protocol_marker() -> None:
    analysis = _analysis()
    decision = analysis.turns[0].decisions[0]
    decision.selected_option_index = 0
    decision.options = [
        RecordedOption(
            index=0,
            option_type="Fin du tour",
            description="Terminer le tour",
            error="INVALID",
            available=False,
            selected=True,
        ),
        RecordedOption(
            index=1,
            option_type="Action",
            description="Option indisponible",
            error="INVALID",
            available=False,
        ),
    ]

    options = analysis_to_llm_dict(analysis)["turns"][0]["decisions"][0]["options"]

    assert options["chosen"] == [[0, "Fin du tour", None, [], "INVALID"]]
    assert options["unavailable"] == [[1, "Action", None, [], "INVALID"]]
