from pathlib import Path

import pytest

from hscoach.cards.resolver import CardResolver
from hscoach.models import Card, PlayerSide
from hscoach.replay.options import extract_decisions
from hscoach.replay.parser import parse_replay_data

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"
requires_user_sample = pytest.mark.skipif(
    not SAMPLE.is_file(), reason="Replay utilisateur local non disponible."
)


def decisions_from_real_replay():
    if not SAMPLE.exists():
        pytest.skip("Replay utilisateur local non disponible.")
    context = parse_replay_data(SAMPLE.read_bytes())
    return extract_decisions(context, CardResolver({}))


@requires_user_sample
def test_real_replay_correlates_every_options_packet_with_selection() -> None:
    result = decisions_from_real_replay()

    assert result.count == 25
    assert all(
        decision.selected_option_index is not None
        for decisions in result.by_turn.values()
        for decision in decisions
    )


@requires_user_sample
def test_selected_option_is_marked_without_claiming_strategic_completeness() -> None:
    result = decisions_from_real_replay()
    decisions = [decision for items in result.by_turn.values() for decision in items]

    assert all(sum(option.selected for option in decision.options) == 1 for decision in decisions)
    assert any(
        option.description.startswith("Jouer ") for item in decisions for option in item.options
    )
    assert sum(decision.selected_option_index == 0 for decision in decisions) == 6
    invalid_end_turn = [
        option
        for decision in decisions
        for option in decision.options
        if option.option_type == "Fin du tour" and option.error == "INVALID"
    ]
    assert invalid_end_turn
    assert all(option.available is False for option in invalid_end_turn)


def test_general_choice_is_classified_as_discover_only_from_explicit_card_mechanic() -> None:
    context = parse_replay_data(_choice_fixture())
    result = extract_decisions(
        context,
        CardResolver(
            {
                "SOURCE": Card(id="SOURCE", name="Source", mechanics=("DISCOVER",)),
                "ONE": Card(id="ONE", name="Un"),
                "TWO": Card(id="TWO", name="Deux"),
            }
        ),
        player_entity_id=2,
        opponent_entity_id=3,
    )

    choice = result.choices_by_turn[1][0]
    assert choice.choice_type == "Découverte"
    assert choice.player is PlayerSide.PLAYER
    assert [card.card_id for card in choice.offered] == ["ONE", "TWO"]
    assert [card.card_id for card in choice.chosen] == ["TWO"]
    assert choice.completed is True


def test_new_options_packet_preserves_previous_unanswered_decision() -> None:
    context = parse_replay_data(_consecutive_options_fixture())

    result = extract_decisions(context, CardResolver({}))

    decisions = result.by_turn[1]
    assert len(decisions) == 2
    assert decisions[0].selected_option_index is None
    assert decisions[1].selected_option_index == 1
    assert decisions[0].options[0].selected is False
    assert decisions[1].options[0].selected is True


def _choice_fixture() -> bytes:
    return b"""\
<HSReplay build="1" version="1.7">
<Game id="choice"><GameEntity id="1"/>
<Player id="2" playerID="1" accountHi="0" accountLo="1"/>
<Player id="3" playerID="2" accountHi="0" accountLo="2"/>
<FullEntity id="10" cardID="SOURCE"/>
<FullEntity id="11" cardID="ONE"/>
<FullEntity id="12" cardID="TWO"/>
<TagChange entity="1" tag="20" value="1"/>
<Choices entity="2" id="7" type="2" min="1" max="1" source="10"
 ts="2026-01-01T00:00:01+00:00">
<Choice entity="11"/><Choice entity="12"/>
</Choices>
<SendChoices id="7" type="2"><Choice entity="12"/></SendChoices>
</Game></HSReplay>
"""


def _consecutive_options_fixture() -> bytes:
    return b"""\
<HSReplay build="1" version="1.7">
<Game id="options"><GameEntity id="1"/>
<Player id="2" playerID="1" accountHi="0" accountLo="1"/>
<Player id="3" playerID="2" accountHi="0" accountLo="2"/>
<TagChange entity="1" tag="20" value="1"/>
<Options id="1" ts="2026-01-01T00:00:01+00:00">
<Option index="0" type="2"/>
</Options>
<Options id="2" ts="2026-01-01T00:00:02+00:00">
<Option index="1" type="2"/>
</Options>
<SendOption option="1" subOption="0" target="0" position="0"
 ts="2026-01-01T00:00:03+00:00"/>
</Game></HSReplay>
"""
