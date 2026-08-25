from pathlib import Path

import pytest

from hscoach.exceptions import ReplayParseError
from hscoach.replay.parser import extract_replay_facts, parse_replay_data

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"


def test_real_replay_metadata_players_and_deck() -> None:
    context = parse_replay_data(SAMPLE.read_bytes(), source_label=SAMPLE.name)
    facts = extract_replay_facts(context)

    assert facts.metadata.game_id == "151665"
    assert facts.metadata.build == "248348"
    assert facts.metadata.game_format == "Standard"
    assert facts.metadata.game_type == "Partie classée"
    assert facts.metadata.result == "Victoire"
    assert facts.metadata.turn_count == 6
    assert facts.player.player_id == 1
    assert facts.player.card_class == "Chaman"
    assert facts.player.hero_card_id == "HERO_02"
    assert sum(count for _, count in facts.player.deck) == 30
    assert facts.opponent.card_class == "Paladin"
    assert not facts.opponent.deck


def test_private_player_attributes_never_enter_structural_models() -> None:
    facts = extract_replay_facts(parse_replay_data(SAMPLE.read_bytes()))
    serialized_repr = repr((facts.player, facts.opponent))

    assert "accountHi" not in serialized_repr
    assert "accountLo" not in serialized_repr
    assert "#" not in serialized_repr


def test_hsreplay_without_game_is_rejected() -> None:
    with pytest.raises(ReplayParseError, match="aucune partie"):
        parse_replay_data(b'<HSReplay build="1" version="1.7"/>')
