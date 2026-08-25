from pathlib import Path

from hscoach.models.action import ActionType, PlayerSide
from hscoach.models.card import CardRef, InformationSource, Visibility
from hscoach.replay.parser import extract_replay_facts, parse_replay_data
from hscoach.replay.timeline import extract_timeline

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"


class FakeResolver:
    def reference(
        self,
        card_id: str | None,
        *,
        entity_id: int | None = None,
        visibility: Visibility = Visibility.KNOWN,
        created_by_entity_id: int | None = None,
        source: InformationSource = InformationSource.REPLAY_EXPLICIT,
    ) -> CardRef:
        if visibility is Visibility.HIDDEN or not card_id:
            return CardRef(
                entity_id=entity_id,
                card_id=None,
                name="Carte inconnue",
                visibility=Visibility.HIDDEN,
                source=source,
                created_by_entity_id=created_by_entity_id,
            )
        return CardRef(
            entity_id=entity_id,
            card_id=card_id,
            name=f"Carte {card_id}",
            visibility=visibility,
            source=source,
            created_by_entity_id=created_by_entity_id,
        )


def test_real_replay_contains_ordered_half_turns_and_main_actions() -> None:
    facts = extract_replay_facts(parse_replay_data(SAMPLE.read_bytes()))
    result = extract_timeline(
        facts.context,
        FakeResolver(),
        player_entity_id=facts.player.entity_id,
        opponent_entity_id=facts.opponent.entity_id,
    )

    assert [turn.turn_number for turn in result.turns] == list(range(1, 13))
    assert [turn.round_number for turn in result.turns[:4]] == [1, 1, 2, 2]
    assert result.turns[0].active_player is PlayerSide.PLAYER
    assert result.turns[1].active_player is PlayerSide.OPPONENT
    assert all(turn.actions[0].action_type is ActionType.START_TURN for turn in result.turns)
    assert all(
        any(action.action_type is ActionType.END_TURN for action in turn.actions)
        for turn in result.turns[:-1]
    )
    assert all(action.action_type is not ActionType.END_TURN for action in result.turns[-1].actions)

    action_types = {action.action_type for turn in result.turns for action in turn.actions}
    assert {
        ActionType.DRAW,
        ActionType.PLAY_CARD,
        ActionType.CAST_SPELL,
        ActionType.ATTACK,
        ActionType.DEATH,
        ActionType.CREATE_CARD,
    } <= action_types
    descriptions = [action.description for turn in result.turns for action in turn.actions]
    assert "Victoire du JOUEUR." in descriptions
    assert "Défaite de l’ADVERSAIRE." in descriptions
    assert any(
        action.action_type is ActionType.START_GAME_EFFECT for action in result.start_of_game_events
    )
    all_actions = [action for turn in result.turns for action in turn.actions]
    assert {"PLAY", "POWER", "ATTACK", "DEATHS"} <= {
        action.metadata.get("block_type") for action in all_actions
    }
    creation = next(
        action
        for action in all_actions
        if action.action_type is ActionType.CREATE_CARD
        and action.source_card is not None
        and action.target_card is not None
    )
    assert creation.target_card.created_by_entity_id == creation.source_card.entity_id
    assert result.entity_card_ids[12] == "CATA_556"


def test_opponent_draw_is_not_retroactively_revealed_when_card_is_played() -> None:
    context = parse_replay_data(_hidden_draw_fixture())
    result = extract_timeline(
        context,
        FakeResolver(),
        player_entity_id=2,
        opponent_entity_id=3,
    )
    actions = result.turns[0].actions
    draw = next(action for action in actions if action.action_type is ActionType.DRAW)
    play = next(action for action in actions if action.action_type is ActionType.PLAY_CARD)

    assert draw.player is PlayerSide.OPPONENT
    assert draw.target_card is not None
    assert draw.target_card.visibility is Visibility.HIDDEN
    assert draw.target_card.card_id is None
    assert "OPP_001" not in draw.description
    assert play.source_card is not None
    assert play.source_card.visibility is Visibility.KNOWN
    assert play.source_card.card_id == "OPP_001"


def _hidden_draw_fixture() -> bytes:
    return b"""\
<HSReplay build="1" version="1.7">
<Game ts="2026-01-01T12:00:00+00:00" id="privacy" format="2" type="7">
<GameEntity id="1">
<Tag tag="202" value="1"/><Tag tag="49" value="1"/><Tag tag="53" value="1"/>
</GameEntity>
<Player id="2" playerID="1" accountHi="0" accountLo="1" name="Joueur">
<Tag tag="50" value="1"/><Tag tag="202" value="2"/><Tag tag="49" value="1"/>
</Player>
<Player id="3" playerID="2" accountHi="0" accountLo="2" name="Adversaire">
<Tag tag="50" value="2"/><Tag tag="202" value="2"/><Tag tag="49" value="1"/>
</Player>
<FullEntity id="10">
<Tag tag="49" value="2"/><Tag tag="50" value="2"/><Tag tag="53" value="10"/>
</FullEntity>
<TagChange entity="3" tag="23" value="1"/>
<TagChange entity="1" tag="20" value="1"/>
<TagChange entity="1" tag="19" value="6"/>
<HideEntity entity="10" zone="3" ts="2026-01-01T12:00:01+00:00"/>
<Block entity="10" type="7" ts="2026-01-01T12:00:02+00:00">
<ShowEntity entity="10" cardID="OPP_001">
<Tag tag="49" value="1"/><Tag tag="50" value="2"/><Tag tag="202" value="4"/>
</ShowEntity>
</Block>
</Game>
</HSReplay>
"""
