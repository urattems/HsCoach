from pathlib import Path

from hscoach.cards.resolver import CardResolver
from hscoach.models import Card, PlayerSide, Visibility
from hscoach.replay.gamestate import capture_turn_snapshots
from hscoach.replay.parser import extract_replay_facts, parse_replay_data

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"


def snapshots_from_real_replay():
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


def test_real_replay_has_one_snapshot_per_half_turn() -> None:
    snapshots = snapshots_from_real_replay()

    assert len(snapshots) == 12
    assert snapshots[0].active_player is PlayerSide.PLAYER
    assert snapshots[1].active_player is PlayerSide.OPPONENT
    assert snapshots[-1].end_state is not None


def test_opponent_hidden_hand_never_exposes_future_identity() -> None:
    first_turn = snapshots_from_real_replay()[0]
    opponent = first_turn.start_state.opponent

    assert opponent.hidden_hand_count == 4
    assert len(opponent.hand) == 5
    hidden_cards = [card for card in opponent.hand if card.visibility is Visibility.HIDDEN]
    assert len(hidden_cards) == 4
    assert all(card.card_id is None for card in hidden_cards)


def test_current_buffed_stats_come_from_reconstructed_gamestate() -> None:
    fifth_turn = snapshots_from_real_replay()[4]
    maiev = next(
        minion
        for minion in fifth_turn.end_state.player.board
        if minion.card.card_id == "JAIL_850"
    )

    assert maiev.card.name == "Maiev la Gardienne"
    assert (maiev.attack, maiev.health, maiev.max_health) == (1, 7, 7)
    assert maiev.health != maiev.card.health
