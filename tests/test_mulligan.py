from pathlib import Path
from xml.etree import ElementTree

from hearthstone.enums import GameTag, Step, Zone

from hscoach.cards.resolver import CardResolver
from hscoach.replay.mulligan import extract_mulligan
from hscoach.replay.parser import ReplayContext, extract_replay_facts, parse_replay_data

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"


def test_real_mulligan_does_not_invert_kept_and_returned_cards() -> None:
    context = parse_replay_data(SAMPLE.read_bytes())
    facts = extract_replay_facts(context)
    result = extract_mulligan(
        context,
        CardResolver({}),
        player_entity_id=facts.player.entity_id,
        player_id=facts.player.player_id,
    )

    assert [card.card_id for card in result.mulligan.offered] == [
        "JAIL_850",
        "CATA_556",
        "CORE_ULD_191",
    ]
    assert [card.card_id for card in result.mulligan.kept] == [
        "JAIL_850",
        "CATA_556",
        "CORE_ULD_191",
    ]
    assert result.mulligan.returned == []
    assert result.mulligan.received == []
    assert result.mulligan.partially_reconstructed is False
    assert result.warnings == []


def test_real_chosen_entities_are_confirmed_as_kept_by_zones() -> None:
    context = parse_replay_data(SAMPLE.read_bytes())
    facts = extract_replay_facts(context)
    result = extract_mulligan(
        context,
        CardResolver({}),
        player_entity_id=facts.player.entity_id,
        player_id=facts.player.player_id,
    )

    offered_ids = {card.entity_id for card in result.mulligan.offered}
    kept_ids = {card.entity_id for card in result.mulligan.kept}
    assert kept_ids == offered_ids


def test_returned_and_received_cards_follow_observed_zone_changes() -> None:
    controller = int(GameTag.CONTROLLER)
    zone = int(GameTag.ZONE)
    step = int(GameTag.STEP)
    xml = f"""
    <HSReplay version="1.7"><Game id="test">
      <Player id="2" playerID="1" />
      <FullEntity id="10" cardID="KEEP"><Tag tag="{controller}" value="1" />
        <Tag tag="{zone}" value="{int(Zone.HAND)}" /></FullEntity>
      <FullEntity id="11" cardID="RETURN"><Tag tag="{controller}" value="1" />
        <Tag tag="{zone}" value="{int(Zone.HAND)}" /></FullEntity>
      <FullEntity id="12" cardID="RECEIVE"><Tag tag="{controller}" value="1" />
        <Tag tag="{zone}" value="{int(Zone.DECK)}" /></FullEntity>
      <Choices entity="2" id="1" type="1" min="0" max="2" source="1">
        <Choice entity="10"/><Choice entity="11"/>
      </Choices>
      <ChosenEntities entity="2" id="1"><Choice entity="10"/></ChosenEntities>
      <TagChange entity="11" tag="{zone}" value="{int(Zone.DECK)}" />
      <TagChange entity="12" tag="{zone}" value="{int(Zone.HAND)}" />
      <TagChange entity="1" tag="{step}" value="{int(Step.MAIN_READY)}" />
    </Game></HSReplay>
    """
    root = ElementTree.fromstring(xml)
    context = ReplayContext(
        document=None,
        packet_tree=None,
        root=root,
        game_xml=root.find("Game"),
        source_label="test",
    )

    result = extract_mulligan(
        context,
        CardResolver({}),
        player_entity_id=2,
        player_id=1,
    )

    assert [card.card_id for card in result.mulligan.kept] == ["KEEP"]
    assert [card.card_id for card in result.mulligan.returned] == ["RETURN"]
    assert [card.card_id for card in result.mulligan.received] == ["RECEIVE"]
    assert result.mulligan.partially_reconstructed is False
