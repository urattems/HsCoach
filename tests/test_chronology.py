from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hscoach.cards.resolver import CardResolver
from hscoach.models import (
    ActionType,
    EntityDelta,
    GameAction,
    PlayerSide,
    RecordedChoice,
    TurnPhase,
    ValueDelta,
)
from hscoach.replay.parser import (
    _attach_choice_actions,
    _normalize_action_sequences,
    extract_replay_facts,
    parse_replay_data,
)
from hscoach.replay.timeline import extract_timeline

MINIMAL_REPLAY = Path(__file__).parent / "fixtures" / "minimal_replay.hsreplay"


def _action(sequence: int, label: str, timestamp: str | None = None) -> GameAction:
    return GameAction(sequence, ActionType.PLAY_CARD, PlayerSide.PLAYER, label, timestamp)


def _choice(number: int, label: str, timestamp: str | None = None) -> RecordedChoice:
    return RecordedChoice(
        sequence=number,
        timestamp=timestamp,
        choice_type=label,
        player=PlayerSide.PLAYER,
        completed=True,
    )


@pytest.mark.parametrize(
    ("choice_orders", "expected"),
    [
        (
            [5],
            ["Choix A terminé sans entité choisie explicite.", "action A", "action B", "action C"],
        ),
        (
            [25],
            ["action A", "action B", "Choix A terminé sans entité choisie explicite.", "action C"],
        ),
        (
            [35],
            ["action A", "action B", "action C", "Choix A terminé sans entité choisie explicite."],
        ),
    ],
)
def test_choice_local_counter_never_controls_beginning_middle_or_end(
    choice_orders, expected
) -> None:
    actions = [_action(40, "action A"), _action(41, "action B"), _action(42, "action C")]
    choice = _choice(1, "Choix A")
    turn = SimpleNamespace(actions=actions, entity_deltas=[])
    protocol_orders = {
        id(action): order for action, order in zip(actions, [10, 20, 30], strict=True)
    }
    protocol_orders.update(_attach_choice_actions(turn, [choice], {id(choice): choice_orders[0]}))

    _normalize_action_sequences([], [turn], protocol_orders)

    assert [action.description for action in turn.actions] == expected


def test_multiple_missing_timestamp_choices_use_protocol_encounter_order() -> None:
    actions = [_action(40, "action A"), _action(41, "action B"), _action(42, "action C")]
    choices = [_choice(1, "Choix A"), _choice(2, "Choix B")]
    turn = SimpleNamespace(actions=actions, entity_deltas=[])
    protocol_orders = {
        id(action): order for action, order in zip(actions, [10, 30, 50], strict=True)
    }
    protocol_orders.update(
        _attach_choice_actions(turn, choices, {id(choices[0]): 20, id(choices[1]): 40})
    )

    _normalize_action_sequences([], [turn], protocol_orders)

    assert [action.description for action in turn.actions] == [
        "action A",
        "Choix A terminé sans entité choisie explicite.",
        "action B",
        "Choix B terminé sans entité choisie explicite.",
        "action C",
    ]


@pytest.mark.parametrize(
    "choice_timestamp",
    [None, "2026-01-01T00:00:01+00:00"],
)
def test_mixed_or_identical_timestamps_do_not_override_protocol_order(choice_timestamp) -> None:
    timestamp = "2026-01-01T00:00:01+00:00"
    actions = [_action(40, "avant", timestamp), _action(41, "après", timestamp)]
    choice = _choice(1, "Choix", choice_timestamp)
    turn = SimpleNamespace(actions=actions, entity_deltas=[])
    protocol_orders = {id(actions[0]): 10, id(actions[1]): 30}
    protocol_orders.update(_attach_choice_actions(turn, [choice], {id(choice): 20}))

    _normalize_action_sequences([], [turn], protocol_orders)

    assert [action.description for action in turn.actions] == [
        "avant",
        "Choix terminé sans entité choisie explicite.",
        "après",
    ]


def test_discover_opponent_side_and_delta_mapping_survive_merge() -> None:
    before = _action(1, "carte jouée")
    after = _action(2, "carte ajoutée")
    choice = RecordedChoice(
        sequence=1,
        timestamp=None,
        choice_type="Découverte",
        player=PlayerSide.OPPONENT,
        completed=True,
    )
    delta = EntityDelta(
        sequence=2,
        entity_id=10,
        side=PlayerSide.OPPONENT,
        phase=TurnPhase.ACTION_PHASE_END,
        attribute="zone",
        value=ValueDelta("DECK", "HAND"),
    )
    turn = SimpleNamespace(actions=[before, after], entity_deltas=[delta])
    protocol_orders = {id(before): 10, id(after): 30}
    protocol_orders.update(_attach_choice_actions(turn, [choice], {id(choice): 20}))
    important_events = [after]

    _normalize_action_sequences([], [turn], protocol_orders)

    assert [action.action_type for action in turn.actions] == [
        ActionType.PLAY_CARD,
        ActionType.DISCOVER,
        ActionType.PLAY_CARD,
    ]
    assert turn.actions[1].player is PlayerSide.OPPONENT
    assert delta.sequence == after.sequence
    assert important_events[0].sequence in {action.sequence for action in turn.actions}
    assert len({action.sequence for action in turn.actions}) == len(turn.actions)


def test_packet_tree_actions_share_the_xml_protocol_order_domain() -> None:
    context = parse_replay_data(MINIMAL_REPLAY.read_bytes())
    facts = extract_replay_facts(context)
    timeline = extract_timeline(
        context,
        CardResolver({}),
        player_entity_id=facts.player.entity_id,
        opponent_entity_id=facts.opponent.entity_id,
    )
    actions = [action for turn in timeline.turns for action in turn.actions]

    assert actions
    assert all(id(action) in timeline.action_protocol_orders for action in actions)
    assert not any(warning.code == "ordre_protocole_indisponible" for warning in timeline.warnings)
