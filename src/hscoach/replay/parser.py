"""Chargement officiel HSReplay et extraction des faits structurels."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import Any
from xml.etree import ElementTree

from hearthstone.enums import GameTag, PlayState
from hslog.export import FriendlyPlayerExporter
from hsreplay.document import HSReplayDocument

from hscoach.cards.localization import card_class_fr, format_fr, game_type_fr
from hscoach.exceptions import ReplayParseError
from hscoach.input import validate_replay_xml
from hscoach.models.game import ParseWarning, ReplayMetadata


@dataclass(slots=True, frozen=True)
class RawPlayer:
    """Faits non sensibles d'un joueur avant résolution des cartes."""

    entity_id: int
    player_id: int
    hero_entity_id: int | None
    hero_card_id: str | None
    card_class: str
    deck: tuple[tuple[str, int], ...] = ()


@dataclass(slots=True)
class ReplayContext:
    """Document validé, arbre officiel et XML complémentaire ciblé."""

    document: Any
    packet_tree: Any
    root: ElementTree.Element
    game_xml: ElementTree.Element
    source_label: str
    warnings: list[ParseWarning] = field(default_factory=list)


@dataclass(slots=True)
class ReplayFacts:
    """Métadonnées, côtés et deck explicitement présents dans le replay."""

    context: ReplayContext
    metadata: ReplayMetadata
    player: RawPlayer
    opponent: RawPlayer


def parse_replay_data(
    data: bytes,
    *,
    source_label: str = "mémoire",
    max_size_bytes: int = 50 * 1024 * 1024,
) -> ReplayContext:
    """Prévalider puis parser les octets avec l'implémentation officielle HearthSim."""

    root = validate_replay_xml(data, max_size_bytes=max_size_bytes)
    try:
        document = HSReplayDocument.from_xml_file(BytesIO(data))
        packet_trees = document.to_packet_tree()
    except Exception as exc:
        raise ReplayParseError(
            "Le document XML ne respecte pas le format HSReplay pris en charge."
        ) from exc

    game_nodes = root.findall("Game")
    if not document.games or not packet_trees or not game_nodes:
        raise ReplayParseError("Le document HSReplay ne contient aucune partie.")

    warnings: list[ParseWarning] = []
    if len(game_nodes) > 1:
        warnings.append(
            ParseWarning(
                code="plusieurs_parties",
                message="Le document contient plusieurs parties ; seule la première est analysée.",
            )
        )
    return ReplayContext(
        document=document,
        packet_tree=packet_trees[0],
        root=root,
        game_xml=game_nodes[0],
        source_label=source_label,
        warnings=warnings,
    )


def extract_replay_facts(context: ReplayContext) -> ReplayFacts:
    """Extraire joueurs, deck et métadonnées sans conserver les attributs privés."""

    players = _extract_players(context)
    if len(players) != 2:
        raise ReplayParseError("Le replay doit contenir exactement deux joueurs exploitables.")

    friendly_player_id = _find_friendly_player_id(context, players)
    player = next((item for item in players if item.player_id == friendly_player_id), None)
    if player is None:
        raise ReplayParseError("Le joueur local ne peut pas être identifié de façon fiable.")
    opponent = next(item for item in players if item.player_id != friendly_player_id)
    metadata = _extract_metadata(context, player)
    return ReplayFacts(context=context, metadata=metadata, player=player, opponent=opponent)


def _extract_players(context: ReplayContext) -> list[RawPlayer]:
    entity_data = _initial_entities(context.game_xml)
    players: list[RawPlayer] = []
    for player_xml in context.game_xml.findall("Player"):
        try:
            entity_id = int(player_xml.attrib["id"])
            player_id = int(player_xml.attrib["playerID"])
        except (KeyError, ValueError) as exc:
            raise ReplayParseError("Un joueur contient des identifiants invalides.") from exc

        tags = _tags(player_xml)
        hero_entity_id = tags.get(GameTag.HERO_ENTITY)
        hero_card_id: str | None = None
        class_value: int | None = None
        if hero_entity_id is not None and hero_entity_id in entity_data:
            hero_card_id, hero_tags = entity_data[hero_entity_id]
            class_value = hero_tags.get(GameTag.CLASS)

        deck_counter = Counter(
            card.attrib["id"]
            for card in player_xml.findall("./Deck/Card")
            if card.attrib.get("id")
        )
        players.append(
            RawPlayer(
                entity_id=entity_id,
                player_id=player_id,
                hero_entity_id=hero_entity_id,
                hero_card_id=hero_card_id,
                card_class=card_class_fr(class_value),
                deck=tuple(sorted(deck_counter.items())),
            )
        )
    return players


def _find_friendly_player_id(context: ReplayContext, players: list[RawPlayer]) -> int:
    try:
        friendly = FriendlyPlayerExporter(context.packet_tree).export()
        if friendly is not None:
            return int(friendly)
    except Exception:
        context.warnings.append(
            ParseWarning(
                code="detection_joueur_degradee",
                message="Le joueur local est identifié grâce au deck explicite du replay.",
            )
        )

    with_deck = [player for player in players if player.deck]
    if len(with_deck) == 1:
        return with_deck[0].player_id
    raise ReplayParseError("Le point de vue du joueur local est ambigu dans ce replay.")


def _extract_metadata(context: ReplayContext, player: RawPlayer) -> ReplayMetadata:
    game = context.game_xml
    result = _result_for_player(game, player.entity_id)
    turn_count = _turn_count(game)
    return ReplayMetadata(
        game_id=game.attrib.get("id", "partie-inconnue"),
        build=str(getattr(context.document, "build", "") or "") or None,
        replay_version=str(getattr(context.document, "version", "") or "") or None,
        started_at=game.attrib.get("ts"),
        game_format=format_fr(game.attrib.get("format")),
        game_type=game_type_fr(game.attrib.get("type")),
        scenario_id=game.attrib.get("scenarioID"),
        result=result,
        turn_count=turn_count,
        duration_seconds=_duration_seconds(game),
    )


def _initial_entities(
    game: ElementTree.Element,
) -> dict[int, tuple[str | None, dict[GameTag, int]]]:
    entities: dict[int, tuple[str | None, dict[GameTag, int]]] = {}
    for element in game.iter("FullEntity"):
        try:
            entity_id = int(element.attrib["id"])
        except (KeyError, ValueError):
            continue
        entities[entity_id] = (element.attrib.get("cardID") or None, _tags(element))
    return entities


def _tags(element: ElementTree.Element) -> dict[GameTag, int]:
    tags: dict[GameTag, int] = {}
    for tag in element.findall("Tag"):
        try:
            enum_tag = GameTag(int(tag.attrib["tag"]))
            tags[enum_tag] = int(tag.attrib["value"])
        except (KeyError, ValueError):
            continue
    return tags


def _result_for_player(game: ElementTree.Element, entity_id: int) -> str:
    final_state: PlayState | None = None
    for change in game.iter("TagChange"):
        if change.attrib.get("entity") != str(entity_id):
            continue
        if change.attrib.get("tag") != str(int(GameTag.PLAYSTATE)):
            continue
        try:
            final_state = PlayState(int(change.attrib["value"]))
        except (KeyError, ValueError):
            continue
    if final_state is PlayState.WON:
        return "Victoire"
    if final_state is PlayState.LOST:
        return "Défaite"
    if final_state is PlayState.CONCEDED:
        return "Concession"
    if final_state is PlayState.TIED:
        return "Égalité"
    return "Inconnu"


def _turn_count(game: ElementTree.Element) -> int:
    player_entities = {player.attrib.get("id") for player in game.findall("Player")}
    turns = []
    for change in game.iter("TagChange"):
        if change.attrib.get("entity") not in player_entities:
            continue
        if change.attrib.get("tag") == str(int(GameTag.TURN)):
            try:
                turns.append(int(change.attrib["value"]))
            except (KeyError, ValueError):
                continue
    return max(turns, default=0)


def _duration_seconds(game: ElementTree.Element) -> float | None:
    timestamps: list[datetime] = []
    for element in game.iter():
        value = element.attrib.get("ts")
        if not value:
            continue
        try:
            timestamps.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(timestamps) < 2:
        return None
    return max(0.0, (max(timestamps) - min(timestamps)).total_seconds())
