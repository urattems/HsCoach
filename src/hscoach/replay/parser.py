"""Chargement officiel HSReplay et extraction des faits structurels."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
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
from hscoach.models import (
    ActionType,
    Card,
    CardRef,
    DeckCard,
    GameAction,
    GameAnalysis,
    KnowledgeStatus,
    Player,
    PlayerSide,
    ReplayDiagnostics,
    Visibility,
)
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


def analyze_replay_data(
    data: bytes,
    cards_by_id: Mapping[str, Card],
    *,
    english_cards_by_id: Mapping[str, Card] | None = None,
    allow_en_fallback: bool = False,
    source_label: str = "mémoire",
    max_size_bytes: int = 50 * 1024 * 1024,
) -> GameAnalysis:
    """Construire l'analyse factuelle complète à partir d'octets déjà chargés."""

    from hscoach.cards.resolver import CardResolver

    context = parse_replay_data(
        data,
        source_label=source_label,
        max_size_bytes=max_size_bytes,
    )
    facts = extract_replay_facts(context)
    return build_game_analysis(
        facts,
        CardResolver(
            cards_by_id,
            english_cards_by_id=english_cards_by_id,
            allow_en_fallback=allow_en_fallback,
        ),
    )


def build_game_analysis(facts: ReplayFacts, resolver: object) -> GameAnalysis:
    """Assembler cartes, mulligan, chronologie, états et options d'un contexte validé."""

    # Imports locaux : ces modules spécialisés dépendent du ReplayContext public.
    from hscoach.replay.deltas import build_turn_state_deltas
    from hscoach.replay.gamestate import capture_turn_snapshots
    from hscoach.replay.mulligan import extract_mulligan
    from hscoach.replay.options import extract_decisions
    from hscoach.replay.timeline import extract_timeline

    player = _player_model(facts.player, PlayerSide.PLAYER, resolver)
    opponent = _player_model(facts.opponent, PlayerSide.OPPONENT, resolver)
    mulligan_result = extract_mulligan(
        facts.context,
        resolver,
        player_entity_id=facts.player.entity_id,
        player_id=facts.player.player_id,
    )
    timeline = extract_timeline(
        facts.context,
        resolver,
        player_entity_id=facts.player.entity_id,
        opponent_entity_id=facts.opponent.entity_id,
    )
    snapshot_result = capture_turn_snapshots(
        facts.context,
        resolver,
        friendly_player_id=facts.player.player_id,
    )
    decisions = extract_decisions(
        facts.context,
        resolver,
        player_entity_id=facts.player.entity_id,
        opponent_entity_id=facts.opponent.entity_id,
    )

    turns_by_number = {turn.turn_number: turn for turn in timeline.turns}
    for snapshot in snapshot_result:
        turn = turns_by_number.get(snapshot.turn_number)
        if turn is None:
            continue
        turn.turn_start_state = snapshot.turn_start_state
        turn.action_phase_start_state = snapshot.action_phase_start_state
        turn.action_phase_end_state = snapshot.action_phase_end_state
        turn.turn_end_state = snapshot.turn_end_state
        turn.state_deltas = build_turn_state_deltas(turn)
    for turn_number, items in decisions.by_turn.items():
        turn = turns_by_number.get(turn_number)
        if turn is not None:
            turn.decisions.extend(items)
    for turn_number, items in decisions.choices_by_turn.items():
        turn = turns_by_number.get(turn_number)
        if turn is not None:
            turn.choices.extend(items)
            _attach_choice_actions(turn, items)
    _normalize_action_sequences(timeline.start_of_game_events, timeline.turns)

    unresolved_ids = list(resolver.unresolved_ids)
    warnings = [
        *facts.context.warnings,
        *mulligan_result.warnings,
        *timeline.warnings,
        *snapshot_result.warnings,
        *[
            ParseWarning(
                code="carte_non_resolue",
                message=f"Traduction française introuvable : Carte inconnue [{card_id}].",
            )
            for card_id in unresolved_ids
        ],
    ]
    opponent.known_cards = _known_opponent_cards(timeline.turns)
    entity_card_ids = list(timeline.entity_card_ids.values())
    unresolved_occurrences = sum(card_id in set(unresolved_ids) for card_id in entity_card_ids)
    event_count = len(timeline.start_of_game_events) + sum(
        len(turn.actions) for turn in timeline.turns
    )
    actions = [action for turn in timeline.turns for action in turn.actions]
    phase_deltas = [delta for turn in timeline.turns for delta in turn.state_deltas]
    state_delta_count = sum(
        len(delta.entities) + len(delta.heroes) + len(delta.mana) + len(delta.zones)
        for delta in phase_deltas
    )
    available_boundaries = sum(
        state is not None
        for turn in timeline.turns
        for state in (
            turn.turn_start_state,
            turn.action_phase_start_state,
            turn.action_phase_end_state,
            turn.turn_end_state,
        )
    )
    expected_boundaries = len(timeline.turns) * 4
    if available_boundaries == 0:
        completeness = "unknown"
    elif available_boundaries == expected_boundaries:
        completeness = "complete"
    else:
        completeness = "partial"
    diagnostics = ReplayDiagnostics(
        valid=True,
        entity_count=_entity_count(facts.context.game_xml),
        event_count=event_count,
        turn_count=len(timeline.turns),
        resolved_card_count=max(0, len(entity_card_ids) - unresolved_occurrences),
        unresolved_card_count=unresolved_occurrences,
        has_player_deck=bool(player.deck),
        has_mulligan=mulligan_result.mulligan.status is not KnowledgeStatus.UNKNOWN,
        has_options=decisions.count > 0,
        player_class=player.card_class,
        opponent_class=opponent.card_class,
        action_count=len(actions),
        state_delta_count=state_delta_count
        + sum(len(turn.entity_deltas) for turn in timeline.turns),
        buff_count=sum(action.action_type is ActionType.BUFF for action in actions),
        damage_count=sum(action.action_type is ActionType.DAMAGE for action in actions),
        heal_count=sum(action.action_type is ActionType.HEAL for action in actions),
        created_card_count=sum(action.action_type is ActionType.CREATE_CARD for action in actions),
        option_count=decisions.option_count,
        unknown_action_count=sum(
            action.action_type is ActionType.UNCLASSIFIED for action in actions
        ),
        mulligan_status=mulligan_result.mulligan.status,
        game_state_completeness=completeness,
    )
    return GameAnalysis(
        metadata=facts.metadata,
        player=player,
        opponent=opponent,
        mulligan=mulligan_result.mulligan,
        start_of_game_events=timeline.start_of_game_events,
        turns=timeline.turns,
        important_events=timeline.important_events,
        unresolved_cards=unresolved_ids,
        warnings=warnings,
        diagnostics=diagnostics,
    )


def _attach_choice_actions(turn: object, choices: list[object]) -> None:
    for choice in choices:
        action_type = (
            ActionType.DISCOVER if choice.choice_type == "Découverte" else ActionType.CHOICE
        )
        chosen_names = ", ".join(card.name for card in choice.chosen)
        if chosen_names:
            description = f"{choice.choice_type} : {chosen_names} est choisi."
        elif choice.completed:
            description = f"{choice.choice_type} terminé sans entité choisie explicite."
        else:
            description = f"{choice.choice_type} proposé ; réponse absente du replay."
        turn.actions.append(
            GameAction(
                sequence=0,
                action_type=action_type,
                player=choice.player,
                description=description,
                timestamp=choice.timestamp,
                source_card=choice.source_card,
                target_card=choice.chosen[0] if len(choice.chosen) == 1 else None,
                metadata={
                    "choice_type": choice.choice_type,
                    "offered_entity_ids": [card.entity_id for card in choice.offered],
                    "chosen_entity_ids": [card.entity_id for card in choice.chosen],
                    "completed": choice.completed,
                },
            )
        )


def _normalize_action_sequences(start_events: list[GameAction], turns: list[object]) -> None:
    """Renuméroter après fusion XML ciblée, sans ordre aléatoire."""

    sequence = 0
    for action in sorted(start_events, key=lambda item: item.sequence):
        sequence += 1
        action.sequence = sequence
    for turn in turns:
        original_order = {id(action): index for index, action in enumerate(turn.actions)}
        original_sequences = {id(action): action.sequence for action in turn.actions}
        turn.actions.sort(
            key=lambda action: (
                action.timestamp is not None,
                action.timestamp or "",
                original_order[id(action)],
            )
        )
        sequence_mapping: dict[int, int] = {}
        for action in turn.actions:
            sequence += 1
            old_sequence = original_sequences[id(action)]
            if old_sequence > 0:
                sequence_mapping[old_sequence] = sequence
            action.sequence = sequence
        for delta in turn.entity_deltas:
            if delta.sequence in sequence_mapping:
                delta.sequence = sequence_mapping[delta.sequence]


def _player_model(raw: RawPlayer, side: PlayerSide, resolver: object) -> Player:
    hero = (
        resolver.reference(raw.hero_card_id, entity_id=raw.hero_entity_id)
        if raw.hero_card_id
        else None
    )
    deck = [DeckCard(card=resolver.reference(card_id), count=count) for card_id, count in raw.deck]
    return Player(
        side=side,
        entity_id=raw.entity_id,
        player_id=raw.player_id,
        card_class=raw.card_class,
        hero=hero,
        deck=deck,
    )


def _known_opponent_cards(turns: list[object]) -> list[CardRef]:
    known: dict[str, CardRef] = {}
    for turn in turns:
        for action in turn.actions:
            if action.player is not PlayerSide.OPPONENT:
                continue
            card = action.source_card
            if card and card.visibility is Visibility.KNOWN and card.card_id:
                known.setdefault(card.card_id, card)
    return [known[card_id] for card_id in sorted(known)]


def _entity_count(game: ElementTree.Element) -> int:
    entity_ids: set[int] = set()
    for element in game.iter():
        attribute = "id" if element.tag in {"GameEntity", "Player", "FullEntity"} else "entity"
        if element.tag not in {
            "GameEntity",
            "Player",
            "FullEntity",
            "ShowEntity",
            "ChangeEntity",
            "HideEntity",
            "TagChange",
        }:
            continue
        try:
            entity_ids.add(int(element.attrib[attribute]))
        except (KeyError, ValueError):
            continue
    return len(entity_ids)


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
            card.attrib["id"] for card in player_xml.findall("./Deck/Card") if card.attrib.get("id")
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
