from pathlib import Path

import pytest
from hearthstone.enums import CardType, GameTag, Zone

from hscoach.models.action import ActionType, GameAction, PlayerSide
from hscoach.models.card import CardRef, InformationSource, Visibility
from hscoach.replay.parser import extract_replay_facts, parse_replay_data
from hscoach.replay.timeline import extract_timeline, gameplay_start_event_groups

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"
MAGE_SAMPLE = next(
    (path for path in (Path(__file__).parents[1] / "samples").glob("*_vs_mage")),
    Path("replay-utilisateur-absent"),
)
requires_user_sample = pytest.mark.skipif(
    not SAMPLE.is_file(), reason="Replay utilisateur local non disponible."
)
requires_mage_sample = pytest.mark.skipif(
    not MAGE_SAMPLE.is_file(), reason="Replay Chaman contre Mage local non disponible."
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
            card_type="ENCHANTMENT" if card_id == "TECH_001e" else None,
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
        ActionType.CARD_CREATED,
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
        if action.action_type is ActionType.CARD_CREATED
        and action.source_card is not None
        and action.target_card is not None
    )
    assert creation.target_card.created_by_entity_id == creation.source_card.entity_id
    assert result.entity_card_ids[12] == "CATA_556"

    creation_targets = {
        action.target_card.card_id
        for action in all_actions
        if action.action_type is ActionType.CARD_CREATED and action.target_card is not None
    }
    assert {"GAME_005", "JAIL_328"}.isdisjoint(creation_targets)
    scarlet = next(
        action.source_card
        for action in all_actions
        if action.action_type is ActionType.PLAY_CARD
        and action.source_card is not None
        and action.source_card.card_id == "JAIL_328"
    )
    assert scarlet.provenance is not None
    assert scarlet.provenance.creator_entity_id == 39

    beatrix_raw = [
        action
        for action in result.start_of_game_events
        if action.source_card is not None and action.source_card.card_id == "JAIL_397"
    ]
    beatrix_gameplay = [
        group
        for group in gameplay_start_event_groups(result.start_of_game_events)
        if group[0].source_card is not None and group[0].source_card.card_id == "JAIL_397"
    ]
    assert len(beatrix_raw) == 2
    assert len(beatrix_gameplay) == 1
    assert beatrix_gameplay[0][1] == 2

    acolyte_dormant_actions = [
        action
        for action in all_actions
        if action.target_card is not None
        and action.target_card.entity_id == 17
        and action.action_type in {ActionType.BECOMES_DORMANT, ActionType.AWAKENS}
    ]
    assert [action.action_type for action in acolyte_dormant_actions] == [
        ActionType.BECOMES_DORMANT,
        ActionType.AWAKENS,
    ]


@requires_mage_sample
def test_real_mage_replay_keeps_ultraxion_and_hero_power_semantics() -> None:
    facts = extract_replay_facts(parse_replay_data(MAGE_SAMPLE.read_bytes()))
    result = extract_timeline(
        facts.context,
        FakeResolver(),
        player_entity_id=facts.player.entity_id,
        opponent_entity_id=facts.opponent.entity_id,
    )
    actions = result.start_of_game_events + [
        action for turn in result.turns for action in turn.actions
    ]

    assert not any(
        action.action_type is ActionType.CARD_CREATED
        and action.target_card is not None
        and action.target_card.entity_id in {69, 70, 71}
        for action in result.start_of_game_events
    )
    ultraxion_play = next(
        action
        for action in actions
        if action.action_type is ActionType.PLAY_CARD
        and action.source_card is not None
        and action.source_card.card_id == "CATA_565"
    )
    real_soldiers = [
        action
        for action in actions
        if action.action_type is ActionType.CARD_CREATED
        and action.target_card is not None
        and action.target_card.card_id == "CATA_565t"
        and action.source_card is not None
        and action.source_card.entity_id == ultraxion_play.source_card.entity_id
    ]
    assert len(real_soldiers) == 2

    turn_14 = next(
        turn
        for turn in result.turns
        if turn.round_number == 14 and turn.active_player is PlayerSide.PLAYER
    )
    hero_powers = [
        action
        for action in turn_14.actions
        if action.action_type is ActionType.HERO_POWER
        and action.source_card is not None
        and action.source_card.card_id == "CATA_190p"
    ]
    assert len(hero_powers) == 1
    assert hero_powers[0].metadata["protocol_events"][0]["block_type"] == "POWER"


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
        ActionType.CARD_CREATED,
        ActionType.TRANSFORM,
    ):
        assert any(action.action_type is action_type for action in actions)

    generated = next(action for action in actions if action.action_type is ActionType.CARD_CREATED)
    assert generated.source_card is not None
    assert generated.source_card.entity_id == 20
    assert generated.target_card is not None
    assert generated.target_card.created_by_entity_id == 20
    assert generated.target_card.provenance is not None
    assert generated.target_card.provenance.creator_entity_id == 20
    assert generated.target_card.provenance.creator_card_id == "ATTACKER"

    transformed = next(action for action in actions if action.action_type is ActionType.TRANSFORM)
    assert transformed.source_card is not None
    assert transformed.source_card.card_id == "ATTACKER"
    assert transformed.target_card is not None
    assert transformed.target_card.card_id == "TRANSFORMED"


def test_creator_provenance_does_not_fabricate_late_creation_events() -> None:
    result = extract_timeline(
        parse_replay_data(_creation_provenance_fixture()),
        FakeResolver(),
        player_entity_id=2,
        opponent_entity_id=3,
    )
    actions = result.turns[0].actions
    creations = [action for action in actions if action.action_type is ActionType.CARD_CREATED]

    assert ActionType.CREATE_CARD is ActionType.CARD_CREATED
    assert {
        action.target_card.entity_id for action in creations if action.target_card is not None
    } == {42, 43, 44}

    for action in creations:
        assert action.metadata["event_type"] == "CARD_CREATED"
        assert action.target_card is not None
        assert action.target_card.provenance is not None
        assert action.target_card.provenance.creator_entity_id == 20
        assert action.target_card.provenance.creator_card_id == "SOURCE"

    technical_creation = next(
        action
        for action in creations
        if action.target_card is not None and action.target_card.entity_id == 44
    )
    assert technical_creation.technical is True
    assert technical_creation not in result.important_events

    added_cards = {
        action.target_card.entity_id: action.target_card
        for action in actions
        if action.action_type is ActionType.ADD_TO_HAND
        and action.target_card is not None
        and action.target_card.entity_id in {40, 41}
    }
    assert set(added_cards) == {40, 41}
    assert all(card.provenance is not None for card in added_cards.values())
    assert all(
        card.provenance.creator_entity_id == 20
        for card in added_cards.values()
        if card.provenance is not None
    )


def test_auxiliary_setaside_entity_from_inactive_creator_is_not_gameplay_creation() -> None:
    result = extract_timeline(
        parse_replay_data(_auxiliary_setaside_fixture()),
        FakeResolver(),
        player_entity_id=2,
        opponent_entity_id=3,
    )
    actions = result.turns[0].actions

    assert result.entity_card_ids[42] == "AUXILIARY"
    assert not any(
        action.action_type is ActionType.CARD_CREATED
        and action.target_card is not None
        and action.target_card.entity_id == 42
        for action in actions
    )


def test_full_entity_inside_play_block_without_creator_is_not_creation() -> None:
    result = extract_timeline(
        parse_replay_data(_uncorrelated_full_entity_fixture()),
        FakeResolver(),
        player_entity_id=2,
        opponent_entity_id=3,
    )

    assert result.entity_card_ids[42] == "UNRELATED"
    assert not any(
        action.action_type is ActionType.CARD_CREATED
        and action.target_card is not None
        and action.target_card.entity_id == 42
        for action in result.turns[0].actions
    )


def test_created_entity_entering_hand_keeps_factual_add_to_hand_with_provenance() -> None:
    result = extract_timeline(
        parse_replay_data(_created_to_hand_fixture()),
        FakeResolver(),
        player_entity_id=2,
        opponent_entity_id=3,
    )
    actions = result.turns[0].actions

    added = next(action for action in actions if action.action_type is ActionType.ADD_TO_HAND)
    assert added.target_card is not None
    assert added.target_card.entity_id == 42
    assert added.target_card.provenance is not None
    assert added.target_card.provenance.creator_entity_id == 20
    assert not any(action.action_type is ActionType.CARD_CREATED for action in actions)


def test_new_token_entering_play_keeps_factual_summon_without_auxiliary_creation() -> None:
    result = extract_timeline(
        parse_replay_data(_summoned_token_fixture()),
        FakeResolver(),
        player_entity_id=2,
        opponent_entity_id=3,
    )
    actions = result.turns[0].actions

    summon = next(action for action in actions if action.action_type is ActionType.SUMMON)
    assert summon.source_card is not None
    assert summon.source_card.entity_id == 20
    assert summon.target_card is not None
    assert summon.target_card.entity_id == 42
    assert not any(action.action_type is ActionType.CARD_CREATED for action in actions)


@pytest.mark.parametrize("activation_count", [1, 2])
def test_nested_hero_power_blocks_emit_one_action_per_root_activation(
    activation_count: int,
) -> None:
    result = extract_timeline(
        parse_replay_data(_hero_power_fixture(activation_count)),
        FakeResolver(),
        player_entity_id=2,
        opponent_entity_id=3,
    )
    actions = [
        action for action in result.turns[0].actions if action.action_type is ActionType.HERO_POWER
    ]

    assert len(actions) == activation_count
    assert all(action.metadata["block_type"] == "PLAY" for action in actions)
    assert all(action.metadata["protocol_events"][0]["block_type"] == "POWER" for action in actions)


def test_dormant_projection_is_technical_but_cached_buffs_remain_gameplay() -> None:
    result = extract_timeline(
        parse_replay_data(_dormant_fixture()),
        FakeResolver(),
        player_entity_id=2,
        opponent_entity_id=3,
    )
    actions = result.turns[0].actions

    dormant = next(action for action in actions if action.action_type is ActionType.BECOMES_DORMANT)
    awakens = next(action for action in actions if action.action_type is ActionType.AWAKENS)
    assert dormant.description == "Carte DORMANT_MINION passe à l’état Dormant."
    assert awakens.description == "Carte DORMANT_MINION se réveille."

    technical_stats = [
        action
        for action in actions
        if action.technical and action.action_type in {ActionType.BUFF, ActionType.DEBUFF}
    ]
    assert [action.metadata["technical_reason"] for action in technical_stats] == [
        "dormant_projection",
        "dormant_projection",
        "dormant_restore",
        "dormant_restore",
    ]

    gameplay_buffs = [
        action
        for action in actions
        if not action.technical and action.action_type is ActionType.BUFF
    ]
    assert [action.metadata["stats_after"] for action in gameplay_buffs] == [
        "5/5",
        "5/6",
    ]
    assert len([delta for delta in result.turns[0].entity_deltas if delta.technical]) == 4
    assert (
        len(
            [
                delta
                for delta in result.turns[0].entity_deltas
                if not delta.technical and delta.attribute in {"attack", "max_health"}
            ]
        )
        == 2
    )


def test_start_event_groups_only_merge_protocol_reveals_not_real_triggers() -> None:
    source = CardRef(entity_id=39, card_id="JAIL_397", name="Commandante Beatrix")
    description = "Commandante Beatrix déclenche son effet de début de partie."
    two_real_triggers = [
        GameAction(
            sequence=sequence,
            action_type=ActionType.START_GAME_EFFECT,
            player=PlayerSide.OPPONENT,
            description=description,
            source_card=source,
            metadata={"protocol_only_reveal": False},
        )
        for sequence in (1, 2)
    ]

    assert [
        (action.sequence, occurrences)
        for action, occurrences in gameplay_start_event_groups(two_real_triggers)
    ] == [(1, 1), (2, 1)]

    reveal_copy = GameAction(
        sequence=2,
        action_type=ActionType.START_GAME_EFFECT,
        player=PlayerSide.OPPONENT,
        description=description,
        source_card=source,
        metadata={"protocol_only_reveal": True},
    )
    assert [
        (action.sequence, occurrences)
        for action, occurrences in gameplay_start_event_groups([two_real_triggers[0], reveal_copy])
    ] == [(1, 2)]


def _creation_provenance_fixture() -> bytes:
    zone = int(GameTag.ZONE)
    controller = int(GameTag.CONTROLLER)
    card_type = int(GameTag.CARDTYPE)
    creator = int(GameTag.CREATOR)
    turn = int(GameTag.TURN)
    step = int(GameTag.STEP)
    current_player = int(GameTag.CURRENT_PLAYER)
    xml = f"""\
<HSReplay build="1" version="1.7">
<Game id="provenance"><GameEntity id="1"/>
<Player id="2" playerID="1" accountHi="0" accountLo="1"/>
<Player id="3" playerID="2" accountHi="0" accountLo="2"/>
<FullEntity id="20" cardID="SOURCE"><Tag tag="{zone}" value="{int(Zone.PLAY)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.MINION)}"/>
</FullEntity>
<FullEntity id="40" cardID="LATE_CREATOR"><Tag tag="{zone}" value="{int(Zone.SETASIDE)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.SPELL)}"/>
</FullEntity>
<FullEntity id="41"><Tag tag="{zone}" value="{int(Zone.SETASIDE)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.SPELL)}"/>
</FullEntity>
<TagChange entity="2" tag="{current_player}" value="1"/>
<TagChange entity="1" tag="{turn}" value="1"/>
<TagChange entity="1" tag="{step}" value="6"/>
<TagChange entity="1" tag="{step}" value="10"/>
<TagChange entity="40" tag="{creator}" value="20"/>
<TagChange entity="40" tag="{zone}" value="{int(Zone.HAND)}"/>
<ShowEntity entity="41" cardID="SHOW_REVEALED">
<Tag tag="{creator}" value="20"/><Tag tag="{zone}" value="{int(Zone.HAND)}"/>
</ShowEntity>
<Block entity="20" type="3">
<FullEntity id="42"/>
<ShowEntity entity="42" cardID="RUNTIME_PAIRED">
<Tag tag="{creator}" value="20"/><Tag tag="{zone}" value="{int(Zone.SETASIDE)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.SPELL)}"/>
</ShowEntity>
</Block>
<Block entity="20" type="3">
<FullEntity id="43" cardID="RUNTIME_DIRECT">
<Tag tag="{creator}" value="20"/><Tag tag="{zone}" value="{int(Zone.SETASIDE)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.SPELL)}"/>
</FullEntity>
</Block>
<Block entity="20" type="3">
<FullEntity id="44" cardID="TECH_001e">
<Tag tag="{creator}" value="20"/><Tag tag="{zone}" value="{int(Zone.SETASIDE)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.ENCHANTMENT)}"/>
</FullEntity>
</Block>
</Game></HSReplay>
"""
    return xml.encode()


def _created_to_hand_fixture() -> bytes:
    zone = int(GameTag.ZONE)
    controller = int(GameTag.CONTROLLER)
    card_type = int(GameTag.CARDTYPE)
    creator = int(GameTag.CREATOR)
    turn = int(GameTag.TURN)
    step = int(GameTag.STEP)
    current_player = int(GameTag.CURRENT_PLAYER)
    return f"""\
<HSReplay build="1" version="1.7">
<Game id="created-to-hand"><GameEntity id="1"/>
<Player id="2" playerID="1" accountHi="0" accountLo="1"/>
<Player id="3" playerID="2" accountHi="0" accountLo="2"/>
<FullEntity id="20" cardID="SOURCE"><Tag tag="{zone}" value="{int(Zone.PLAY)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.MINION)}"/>
</FullEntity>
<TagChange entity="2" tag="{current_player}" value="1"/>
<TagChange entity="1" tag="{turn}" value="1"/>
<TagChange entity="1" tag="{step}" value="6"/>
<TagChange entity="1" tag="{step}" value="10"/>
<Block entity="20" type="3" target="42">
<FullEntity id="42" cardID="GENERATED"><Tag tag="{zone}" value="{int(Zone.SETASIDE)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.SPELL)}"/>
<Tag tag="{creator}" value="20"/></FullEntity>
<TagChange entity="42" tag="{zone}" value="{int(Zone.HAND)}"/>
</Block>
</Game></HSReplay>
""".encode()


def _auxiliary_setaside_fixture() -> bytes:
    zone = int(GameTag.ZONE)
    controller = int(GameTag.CONTROLLER)
    card_type = int(GameTag.CARDTYPE)
    creator = int(GameTag.CREATOR)
    turn = int(GameTag.TURN)
    step = int(GameTag.STEP)
    current_player = int(GameTag.CURRENT_PLAYER)
    return f"""\
<HSReplay build="1" version="1.7">
<Game id="auxiliary-setaside"><GameEntity id="1"/>
<Player id="2" playerID="1" accountHi="0" accountLo="1"/>
<Player id="3" playerID="2" accountHi="0" accountLo="2"/>
<FullEntity id="20" cardID="INACTIVE_SOURCE">
<Tag tag="{zone}" value="{int(Zone.HAND)}"/><Tag tag="{controller}" value="1"/>
<Tag tag="{card_type}" value="{int(CardType.MINION)}"/></FullEntity>
<TagChange entity="2" tag="{current_player}" value="1"/>
<TagChange entity="1" tag="{turn}" value="1"/>
<TagChange entity="1" tag="{step}" value="6"/>
<TagChange entity="1" tag="{step}" value="10"/>
<Block entity="20" type="3"><Block entity="20" type="3">
<FullEntity id="42" cardID="AUXILIARY">
<Tag tag="{zone}" value="{int(Zone.SETASIDE)}"/><Tag tag="{controller}" value="1"/>
<Tag tag="{card_type}" value="{int(CardType.MINION)}"/>
<Tag tag="{creator}" value="20"/></FullEntity>
</Block></Block>
</Game></HSReplay>
""".encode()


def _uncorrelated_full_entity_fixture() -> bytes:
    zone = int(GameTag.ZONE)
    controller = int(GameTag.CONTROLLER)
    card_type = int(GameTag.CARDTYPE)
    turn = int(GameTag.TURN)
    step = int(GameTag.STEP)
    current_player = int(GameTag.CURRENT_PLAYER)
    return f"""\
<HSReplay build="1" version="1.7">
<Game id="uncorrelated-full-entity"><GameEntity id="1"/>
<Player id="2" playerID="1" accountHi="0" accountLo="1"/>
<Player id="3" playerID="2" accountHi="0" accountLo="2"/>
<FullEntity id="20" cardID="SOURCE"><Tag tag="{zone}" value="{int(Zone.HAND)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.MINION)}"/>
</FullEntity>
<TagChange entity="2" tag="{current_player}" value="1"/>
<TagChange entity="1" tag="{turn}" value="1"/>
<TagChange entity="1" tag="{step}" value="6"/>
<TagChange entity="1" tag="{step}" value="10"/>
<Block entity="20" type="7">
<FullEntity id="42" cardID="UNRELATED">
<Tag tag="{zone}" value="{int(Zone.SETASIDE)}"/><Tag tag="{controller}" value="1"/>
<Tag tag="{card_type}" value="{int(CardType.SPELL)}"/></FullEntity>
</Block>
</Game></HSReplay>
""".encode()


def _summoned_token_fixture() -> bytes:
    zone = int(GameTag.ZONE)
    controller = int(GameTag.CONTROLLER)
    card_type = int(GameTag.CARDTYPE)
    creator = int(GameTag.CREATOR)
    turn = int(GameTag.TURN)
    step = int(GameTag.STEP)
    current_player = int(GameTag.CURRENT_PLAYER)
    return f"""\
<HSReplay build="1" version="1.7">
<Game id="summoned-token"><GameEntity id="1"/>
<Player id="2" playerID="1" accountHi="0" accountLo="1"/>
<Player id="3" playerID="2" accountHi="0" accountLo="2"/>
<FullEntity id="20" cardID="SOURCE"><Tag tag="{zone}" value="{int(Zone.PLAY)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.MINION)}"/>
</FullEntity>
<TagChange entity="2" tag="{current_player}" value="1"/>
<TagChange entity="1" tag="{turn}" value="1"/>
<TagChange entity="1" tag="{step}" value="6"/>
<TagChange entity="1" tag="{step}" value="10"/>
<Block entity="20" type="3" target="42">
<FullEntity id="42" cardID="TOKEN"><Tag tag="{zone}" value="{int(Zone.PLAY)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.MINION)}"/>
<Tag tag="{creator}" value="20"/></FullEntity>
</Block>
</Game></HSReplay>
""".encode()


def _hero_power_fixture(activation_count: int) -> bytes:
    zone = int(GameTag.ZONE)
    controller = int(GameTag.CONTROLLER)
    card_type = int(GameTag.CARDTYPE)
    turn = int(GameTag.TURN)
    step = int(GameTag.STEP)
    current_player = int(GameTag.CURRENT_PLAYER)
    activations = "\n".join(
        '<Block entity="10" type="7"><Block entity="10" type="3"/></Block>'
        for _ in range(activation_count)
    )
    return f"""\
<HSReplay build="1" version="1.7">
<Game id="hero-power"><GameEntity id="1"/>
<Player id="2" playerID="1" accountHi="0" accountLo="1"/>
<Player id="3" playerID="2" accountHi="0" accountLo="2"/>
<FullEntity id="10" cardID="HERO_POWER"><Tag tag="{zone}" value="{int(Zone.PLAY)}"/>
<Tag tag="{controller}" value="1"/>
<Tag tag="{card_type}" value="{int(CardType.HERO_POWER)}"/></FullEntity>
<TagChange entity="2" tag="{current_player}" value="1"/>
<TagChange entity="1" tag="{turn}" value="1"/>
<TagChange entity="1" tag="{step}" value="6"/>
<TagChange entity="1" tag="{step}" value="10"/>
{activations}
</Game></HSReplay>
""".encode()


def _dormant_fixture() -> bytes:
    zone = int(GameTag.ZONE)
    controller = int(GameTag.CONTROLLER)
    card_type = int(GameTag.CARDTYPE)
    attack = int(GameTag.ATK)
    health = int(GameTag.HEALTH)
    dormant = int(GameTag.DORMANT)
    turn = int(GameTag.TURN)
    step = int(GameTag.STEP)
    current_player = int(GameTag.CURRENT_PLAYER)
    xml = f"""\
<HSReplay build="1" version="1.7">
<Game id="dormant"><GameEntity id="1"/>
<Player id="2" playerID="1" accountHi="0" accountLo="1"/>
<Player id="3" playerID="2" accountHi="0" accountLo="2"/>
<FullEntity id="10" cardID="DORMANT_MINION">
<Tag tag="{zone}" value="{int(Zone.PLAY)}"/><Tag tag="{controller}" value="1"/>
<Tag tag="{card_type}" value="{int(CardType.MINION)}"/>
<Tag tag="{attack}" value="4"/><Tag tag="{health}" value="5"/>
</FullEntity>
<TagChange entity="2" tag="{current_player}" value="1"/>
<TagChange entity="1" tag="{turn}" value="1"/>
<TagChange entity="1" tag="{step}" value="6"/>
<TagChange entity="1" tag="{step}" value="10"/>
<CachedTagForDormantChange entity="10" tag="{attack}" value="4"/>
<CachedTagForDormantChange entity="10" tag="{health}" value="5"/>
<TagChange entity="10" tag="{dormant}" value="1"/>
<TagChange entity="10" tag="{attack}" value="1"/>
<TagChange entity="10" tag="{health}" value="2"/>
<CachedTagForDormantChange entity="10" tag="{attack}" value="5"/>
<CachedTagForDormantChange entity="10" tag="{health}" value="6"/>
<TagChange entity="10" tag="{dormant}" value="0"/>
<CachedTagForDormantChange entity="10" tag="{attack}" value="0"/>
<CachedTagForDormantChange entity="10" tag="{health}" value="0"/>
<TagChange entity="10" tag="{attack}" value="5"/>
<TagChange entity="10" tag="{health}" value="6"/>
</Game></HSReplay>
"""
    return xml.encode()


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
<Block entity="20" type="3">
<FullEntity id="30" cardID="GENERATED"><Tag tag="{zone}" value="{int(Zone.SETASIDE)}"/>
<Tag tag="{controller}" value="1"/><Tag tag="{card_type}" value="{int(CardType.SPELL)}"/>
<Tag tag="{creator}" value="20"/></FullEntity>
</Block>
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
