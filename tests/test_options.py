from pathlib import Path

from hscoach.cards.resolver import CardResolver
from hscoach.replay.options import extract_decisions
from hscoach.replay.parser import parse_replay_data

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"


def decisions_from_real_replay():
    context = parse_replay_data(SAMPLE.read_bytes())
    return extract_decisions(context, CardResolver({}))


def test_real_replay_correlates_every_options_packet_with_selection() -> None:
    result = decisions_from_real_replay()

    assert result.count == 25
    assert all(
        decision.selected_option_index is not None
        for decisions in result.by_turn.values()
        for decision in decisions
    )


def test_selected_option_is_marked_without_claiming_strategic_completeness() -> None:
    result = decisions_from_real_replay()
    decisions = [decision for items in result.by_turn.values() for decision in items]

    assert all(sum(option.selected for option in decision.options) == 1 for decision in decisions)
    assert any(
        option.description.startswith("Jouer ") for item in decisions for option in item.options
    )
    assert sum(decision.selected_option_index == 0 for decision in decisions) == 6
