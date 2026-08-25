from pathlib import Path

import pytest
from hearthstone.enums import CardType, GameTag, Zone

from hscoach.models.action import ActionType, PlayerSide
from hscoach.models.card import CardRef, InformationSource, Visibility
from hscoach.replay.parser import extract_replay_facts, parse_replay_data
from hscoach.replay.timeline import extract_timeline

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"
requires_user_sample = pytest.mark.skipif(
    not SAMPLE.is_file(), reason="Replay utilisateur local non disponible."
)


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


@requires_user_sample
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


def test_effect_events_and_sources_require_an_explicit_block_target() -> None:
    result = extract_timeline(
        parse_replay_data(_effect_events_fixture()),
        FakeResolver(),
        player_entity_id=2,
        opponent_entity_id=3,
    )
    actions = result.turns[0].actions

    expected_types = {
        ActionType.DAMAGE,
        ActionType.HEAL,
        ActionType.BUFF,
        ActionType.DEBUFF,
        ActionType.SUMMON,
        ActionType.SILENCE,
    }
    assert expected_types <= {action.action_type for action in actions}

    targeted = [
        action
        for action in actions
        if action.action_type in expected_types
        and action.target_card is not None
        and action.target_card.entity_id in {10, 30}
    ]
    assert targeted
    assert all(action.source_card is not None for action in targeted)
    assert all(action.source_card.entity_id == 20 for action in targeted if action.source_card)
    assert all(action.metadata.get("source_explicit") is True for action in targeted)

    untargeted = [
        action
        for action in actions
        if action.action_type in {ActionType.DAMAGE, ActionType.SUMMON}
        and action.target_card is not None
        and action.target_card.entity_id in {11, 31}
    ]
    assert {action.target_card.entity_id for action in untargeted if action.target_card} == {11, 31}
    assert all(action.source_card is None for action in untargeted)
    assert all("source_explicit" not in action.metadata for action in untargeted)

    deltas = result.turns[0].entity_deltas
    explicit_delta = next(delta for delta in deltas if delta.entity_id == 10)
    implicit_delta = next(delta for delta in deltas if delta.entity_id == 11)
    assert explicit_delta.source_card is not None
    assert explicit_delta.source_card.entity_id == 20
    assert explicit_delta.metadata["source_explicit"] is True
    assert implicit_delta.source_card is None
    assert "source_explicit" not in implicit_delta.metadata


def test_attack_death_generation_and_transform_are_reconstructed_from_protocol() -> None:
    result = extract_timeline(
        parse_replay_data(_structural_events_fixture()),
        FakeResolver(),
        player_entity_id=2,
        opponent_entity_id=3,
    )
    actions = result.turns[0].actions

    for action_type in (
        ActionType.ATTACK,
        ActionType.DEATH,
        ActionType.CREATE_CARD,
        ActionType.TRANSFORM,
    ):
        assert any(action.action_type is action_type for action in actions)

    generated = next(action for action in actions if action.action_type is ActionType.CREATE_CARD)
    assert generated.source_card is not None
    assert generated.source_card.entity_id == 20
    assert generated.target_card is not None
    assert generated.target_card.created_by_entity_id == 20

    transformed = next(action for action in actions if action.action_type is ActionType.TRANSFORM)
    assert transformed.source_card is not None
    assert transformed.source_card.card_id == "ATTACKER"
    assert transformed.target_card is not None
    assert transformed.target_card.card_id == "TRANSFORMED"


def _structural_events_fixture() -> bytes:
    zone = int(GameTag.ZONE)
    controller = int(GameTag.CONTROLLER)
    card_type = int(GameTag.CARDTYPE)
    creator = int(GameTag.CREATOR)
    turn = int(GameTag.TURN)
    step = int(GameTag.STEP)
    current_player = int(GameTag.CURRENT_PLAYER)
    xml = f"""\
<HSReplay build="1" version="1.7">
<Game id="structural"><GameEntity id="1"/>
<Player id="2" playerID="1" accountHi="0" accountLo="1"/>
<Player id="3" playerID="2" accountHi="0" accountLo="2"/>
<FullEntity id="10" cardID="DEFENDER"><Tag tag="{zone}" value="{int(Zone.PLAY)}"/>
<Tag tag="{controller}" value="2"/><Tag tag="{card_type}" value="{int(CardType.MINION)}"/>
</FullEntity>
<FullEntity id="20" cardID="ATTACKER"><Tag tag="{zone}" value="{int(Zone.PLAY)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.MINION)}"/>
</FullEntity>
<TagChange entity="2" tag="{current_player}" value="1"/>
<TagChange entity="1" tag="{turn}" value="1"/>
<TagChange entity="1" tag="{step}" value="6"/>
<TagChange entity="1" tag="{step}" value="10"/>
<Block entity="20" type="1" target="10"/>
<FullEntity id="30" cardID="GENERATED"><Tag tag="{zone}" value="{int(Zone.SETASIDE)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.SPELL)}"/>
<Tag tag="{creator}" value="20"/></FullEntity>
<Block entity="0" type="6"><TagChange entity="10" tag="{zone}"
 value="{int(Zone.GRAVEYARD)}"/></Block>
<ChangeEntity entity="20" cardID="TRANSFORMED"/>
</Game></HSReplay>
"""
    return xml.encode()


def _effect_events_fixture() -> bytes:
    return b"""\
<HSReplay build="1" version="1.7">
<Game ts="2026-01-01T12:00:00+00:00" id="effects" format="2" type="7">
<GameEntity id="1">
<Tag tag="202" value="1"/><Tag tag="49" value="1"/><Tag tag="53" value="1"/>
</GameEntity>
<Player id="2" playerID="1" accountHi="0" accountLo="1" name="Joueur">
<Tag tag="50" value="1"/><Tag tag="202" value="2"/><Tag tag="49" value="1"/>
</Player>
<Player id="3" playerID="2" accountHi="0" accountLo="2" name="Adversaire">
<Tag tag="50" value="2"/><Tag tag="202" value="2"/><Tag tag="49" value="1"/>
</Player>
<FullEntity id="10" cardID="TARGET_EXPLICIT">
<Tag tag="49" value="1"/><Tag tag="50" value="1"/><Tag tag="202" value="4"/>
<Tag tag="47" value="1"/><Tag tag="45" value="5"/><Tag tag="44" value="0"/>
<Tag tag="188" value="0"/>
</FullEntity>
<FullEntity id="11" cardID="TARGET_IMPLICIT">
<Tag tag="49" value="1"/><Tag tag="50" value="1"/><Tag tag="202" value="4"/>
<Tag tag="47" value="1"/><Tag tag="45" value="5"/><Tag tag="44" value="0"/>
</FullEntity>
<FullEntity id="20" cardID="SOURCE">
<Tag tag="49" value="1"/><Tag tag="50" value="1"/><Tag tag="202" value="4"/>
</FullEntity>
<FullEntity id="30" cardID="SUMMON_EXPLICIT">
<Tag tag="49" value="6"/><Tag tag="50" value="1"/><Tag tag="202" value="4"/>
</FullEntity>
<FullEntity id="31" cardID="SUMMON_IMPLICIT">
<Tag tag="49" value="6"/><Tag tag="50" value="1"/><Tag tag="202" value="4"/>
</FullEntity>
<TagChange entity="2" tag="23" value="1"/>
<TagChange entity="1" tag="20" value="1"/>
<TagChange entity="1" tag="19" value="6"/>
<TagChange entity="1" tag="19" value="10"/>
<Block entity="20" type="3" target="10">
<TagChange entity="10" tag="44" value="2"/>
</Block>
<Block entity="20" type="3" target="10">
<TagChange entity="10" tag="44" value="0"/>
<TagChange entity="10" tag="45" value="7"/>
<TagChange entity="10" tag="47" value="3"/>
<TagChange entity="10" tag="47" value="2"/>
<TagChange entity="10" tag="188" value="1"/>
</Block>
<Block entity="20" type="3">
<TagChange entity="11" tag="44" value="1"/>
</Block>
<Block entity="20" type="3" target="30">
<TagChange entity="30" tag="49" value="1"/>
</Block>
<Block entity="20" type="3">
<TagChange entity="31" tag="49" value="1"/>
</Block>
</Game>
</HSReplay>
"""


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
