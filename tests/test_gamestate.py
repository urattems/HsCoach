from pathlib import Path

import pytest
from hearthstone.enums import GameTag

from hscoach.cards.resolver import CardResolver
from hscoach.models import Card, PlayerSide, Visibility
from hscoach.replay.gamestate import capture_turn_snapshots
from hscoach.replay.parser import extract_replay_facts, parse_replay_data

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"
requires_user_sample = pytest.mark.skipif(
    not SAMPLE.is_file(), reason="Replay utilisateur local non disponible."
)


def snapshots_from_real_replay():
    if not SAMPLE.exists():
        pytest.skip("Replay utilisateur local non disponible.")
    context = parse_replay_data(SAMPLE.read_bytes())
    facts = extract_replay_facts(context)
    resolver = CardResolver(
        {
            "JAIL_850": Card(
                id="JAIL_850",
                name="Maiev la Gardienne",
                attack=1,
                health=3,
            )
        }
    )
    return capture_turn_snapshots(
        context,
        resolver,
        friendly_player_id=facts.player.player_id,
    )


@requires_user_sample
def test_real_replay_has_one_snapshot_per_half_turn() -> None:
    result = snapshots_from_real_replay()

    assert len(result) == 12
    assert result[0].active_player is PlayerSide.PLAYER
    assert result[1].active_player is PlayerSide.OPPONENT
    assert all(snapshot.turn_end_state is not None for snapshot in result[:-1])
    assert result[-1].action_phase_end_state is None
    assert result[-1].turn_end_state is None
    assert {warning.code for warning in result.warnings} >= {
        "snapshot_fin_actions_absent",
        "snapshot_fin_tour_absent",
    }


@requires_user_sample
def test_opponent_hidden_hand_never_exposes_future_identity() -> None:
    first_turn = snapshots_from_real_replay()[0]
    assert first_turn.action_phase_start_state is not None
    opponent = first_turn.action_phase_start_state.opponent

    assert opponent.hidden_hand_count == 4
    assert len(opponent.hand) == 5
    hidden_cards = [card for card in opponent.hand if card.visibility is Visibility.HIDDEN]
    assert len(hidden_cards) == 4
    assert all(card.card_id is None for card in hidden_cards)


@requires_user_sample
def test_current_buffed_stats_come_from_reconstructed_gamestate() -> None:
    fifth_turn = snapshots_from_real_replay()[4]
    assert fifth_turn.action_phase_end_state is not None
    maiev = next(
        minion
        for minion in fifth_turn.action_phase_end_state.player.board
        if minion.card.card_id == "JAIL_850"
    )

    assert maiev.card.name == "Maiev la Gardienne"
    assert (maiev.attack, maiev.health, maiev.max_health) == (1, 7, 7)
    assert maiev.health != maiev.card.health


@requires_user_sample
def test_draw_is_between_turn_start_and_action_phase_start() -> None:
    first_turn = snapshots_from_real_replay()[0]
    assert first_turn.turn_start_state is not None
    assert first_turn.action_phase_start_state is not None

    hand_at_turn_start = {card.entity_id for card in first_turn.turn_start_state.player.hand}
    hand_when_deciding = {
        card.entity_id for card in first_turn.action_phase_start_state.player.hand
    }

    assert 29 not in hand_at_turn_start
    assert 29 in hand_when_deciding


@requires_user_sample
def test_end_of_turn_trigger_is_visible_only_in_turn_end_state() -> None:
    third_turn = snapshots_from_real_replay()[2]
    fourth_turn = snapshots_from_real_replay()[3]
    assert third_turn.action_phase_end_state is not None
    assert third_turn.turn_end_state is not None
    assert fourth_turn.turn_start_state is not None

    before_trigger = _minion(third_turn.action_phase_end_state, 12)
    after_trigger = _minion(third_turn.turn_end_state, 12)
    following_turn = _minion(fourth_turn.turn_start_state, 12)

    assert (before_trigger.attack, before_trigger.health) == (1, 2)
    assert (after_trigger.attack, after_trigger.health) == (2, 3)
    assert (following_turn.attack, following_turn.health) == (2, 3)


@requires_user_sample
def test_maiev_dormant_regression_keeps_acolyte_effective_stats() -> None:
    snapshots = snapshots_from_real_replay()
    fifth_turn = snapshots[4]
    seventh_turn = snapshots[6]

    dormant_after_maiev = _minion(fifth_turn.action_phase_end_state, 17)
    before_waking = _minion(seventh_turn.turn_start_state, 17)
    after_waking = _minion(seventh_turn.action_phase_start_state, 17)
    assert (
        dormant_after_maiev.attack,
        dormant_after_maiev.health,
        dormant_after_maiev.max_health,
        dormant_after_maiev.dormant,
    ) == (4, 5, 5, True)
    assert (
        before_waking.attack,
        before_waking.health,
        before_waking.dormant,
    ) == (4, 5, True)
    assert (
        after_waking.attack,
        after_waking.health,
        after_waking.dormant,
    ) == (4, 5, False)


def test_synthetic_phase_boundaries_capture_only_observed_states() -> None:
    result = _capture_synthetic(
        '<TagChange entity="1" tag="19" value="6"/>'
        '<TagChange entity="10" tag="45" value="3"/>'
        '<TagChange entity="1" tag="19" value="10"/>'
        '<TagChange entity="10" tag="45" value="5"/>'
        '<TagChange entity="1" tag="19" value="12"/>'
        '<TagChange entity="10" tag="45" value="7"/>'
        '<TagChange entity="1" tag="19" value="16"/>'
    )
    snapshot = result[0]

    assert _minion(snapshot.turn_start_state, 10).health == 2
    assert _minion(snapshot.action_phase_start_state, 10).health == 3
    assert _minion(snapshot.action_phase_end_state, 10).health == 5
    assert _minion(snapshot.turn_end_state, 10).health == 7
    assert not result.warnings


def test_dormant_snapshots_keep_cached_effective_stats_and_real_buffs() -> None:
    attack = int(GameTag.ATK)
    health = int(GameTag.HEALTH)
    dormant = int(GameTag.DORMANT)
    result = _capture_synthetic(
        f'<TagChange entity="10" tag="{attack}" value="4"/>'
        f'<TagChange entity="10" tag="{health}" value="5"/>'
        '<TagChange entity="1" tag="19" value="6"/>'
        f'<CachedTagForDormantChange entity="10" tag="{attack}" value="4"/>'
        f'<CachedTagForDormantChange entity="10" tag="{health}" value="5"/>'
        f'<TagChange entity="10" tag="{dormant}" value="1"/>'
        f'<TagChange entity="10" tag="{attack}" value="1"/>'
        f'<TagChange entity="10" tag="{health}" value="2"/>'
        '<TagChange entity="1" tag="19" value="10"/>'
        f'<CachedTagForDormantChange entity="10" tag="{attack}" value="5"/>'
        f'<CachedTagForDormantChange entity="10" tag="{health}" value="6"/>'
        '<TagChange entity="1" tag="19" value="12"/>'
        f'<TagChange entity="10" tag="{dormant}" value="0"/>'
        f'<CachedTagForDormantChange entity="10" tag="{attack}" value="0"/>'
        f'<CachedTagForDormantChange entity="10" tag="{health}" value="0"/>'
        f'<TagChange entity="10" tag="{attack}" value="5"/>'
        f'<TagChange entity="10" tag="{health}" value="6"/>'
        '<TagChange entity="1" tag="19" value="16"/>'
    )
    snapshot = result[0]

    turn_start = _minion(snapshot.turn_start_state, 10)
    action_start = _minion(snapshot.action_phase_start_state, 10)
    action_end = _minion(snapshot.action_phase_end_state, 10)
    turn_end = _minion(snapshot.turn_end_state, 10)
    assert (turn_start.attack, turn_start.health, turn_start.dormant) == (4, 5, False)
    assert (action_start.attack, action_start.health, action_start.dormant) == (4, 5, True)
    assert (action_end.attack, action_end.health, action_end.dormant) == (5, 6, True)
    assert (turn_end.attack, turn_end.health, turn_end.dormant) == (5, 6, False)


def test_main_next_is_an_explicit_warned_fallback_for_missing_cleanup() -> None:
    result = _capture_synthetic(
        '<TagChange entity="1" tag="19" value="6"/>'
        '<TagChange entity="1" tag="19" value="10"/>'
        '<TagChange entity="1" tag="19" value="12"/>'
        '<TagChange entity="10" tag="45" value="7"/>'
        '<TagChange entity="1" tag="19" value="13"/>'
    )

    assert _minion(result[0].turn_end_state, 10).health == 7
    assert "snapshot_fin_tour_main_next" in {warning.code for warning in result.warnings}


def test_final_wrapup_does_not_fabricate_end_for_conceded_action_phase() -> None:
    result = _capture_synthetic(
        '<TagChange entity="1" tag="19" value="6"/>'
        '<TagChange entity="1" tag="19" value="10"/>'
        '<TagChange entity="1" tag="19" value="14"/>'
    )

    assert result[0].action_phase_end_state is None
    assert result[0].turn_end_state is None
    assert "snapshot_fin_tour_final_wrapup" not in {warning.code for warning in result.warnings}


def test_final_wrapup_is_warned_fallback_only_after_explicit_main_end() -> None:
    result = _capture_synthetic(
        '<TagChange entity="1" tag="19" value="6"/>'
        '<TagChange entity="1" tag="19" value="10"/>'
        '<TagChange entity="1" tag="19" value="12"/>'
        '<TagChange entity="10" tag="45" value="7"/>'
        '<TagChange entity="1" tag="19" value="14"/>'
    )

    assert _minion(result[0].turn_end_state, 10).health == 7
    assert "snapshot_fin_tour_final_wrapup" in {warning.code for warning in result.warnings}


def _minion(state, entity_id):
    assert state is not None
    return next(
        minion
        for minion in state.player.board + state.opponent.board
        if minion.card.entity_id == entity_id
    )


def _capture_synthetic(events: str):
    xml = f"""\
<HSReplay build="1" version="1.7">
<Game ts="2026-01-01T12:00:00+00:00" id="phases" format="2" type="7">
<GameEntity id="1">
<Tag tag="202" value="1"/><Tag tag="49" value="1"/><Tag tag="53" value="1"/>
</GameEntity>
<Player id="2" playerID="1" accountHi="0" accountLo="1" name="Joueur">
<Tag tag="23" value="1"/><Tag tag="50" value="1"/><Tag tag="202" value="2"/>
<Tag tag="49" value="1"/>
</Player>
<Player id="3" playerID="2" accountHi="0" accountLo="2" name="Adversaire">
<Tag tag="50" value="2"/><Tag tag="202" value="2"/><Tag tag="49" value="1"/>
</Player>
<FullEntity id="10" cardID="TEST_001">
<Tag tag="49" value="1"/><Tag tag="50" value="1"/><Tag tag="202" value="4"/>
<Tag tag="47" value="1"/><Tag tag="45" value="2"/><Tag tag="53" value="10"/>
</FullEntity>
<TagChange entity="1" tag="20" value="1"/>
{events}
</Game>
</HSReplay>
"""
    context = parse_replay_data(xml.encode())
    return capture_turn_snapshots(
        context,
        CardResolver({"TEST_001": Card(id="TEST_001", name="Carte test")}),
        friendly_player_id=1,
    )
