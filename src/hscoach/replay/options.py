"""Extraction des options enregistrées par le client et des choix envoyés."""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree

from hearthstone.enums import CardType, ChoiceType, GameTag, OptionType, Zone

from hscoach.models.action import Decision, PlayerSide, RecordedChoice, RecordedOption
from hscoach.models.card import Visibility
from hscoach.replay.gamestate import CardReferenceResolver
from hscoach.replay.parser import ReplayContext


@dataclass(slots=True)
class DecisionResult:
    """Décisions groupées par numéro de demi-tour protocolaire."""

    by_turn: dict[int, list[Decision]] = field(default_factory=dict)
    choices_by_turn: dict[int, list[RecordedChoice]] = field(default_factory=dict)
    choice_protocol_orders: dict[int, int] = field(default_factory=dict, repr=False)

    @property
    def count(self) -> int:
        return sum(len(items) for items in self.by_turn.values())

    @property
    def option_count(self) -> int:
        return sum(len(decision.options) for items in self.by_turn.values() for decision in items)

    @property
    def choice_count(self) -> int:
        return sum(len(items) for items in self.choices_by_turn.values())


@dataclass(slots=True)
class _OptionEntity:
    entity_id: int
    card_id: str | None = None
    controller: int | None = None
    zone: int | None = None
    card_type: int | None = None


def extract_decisions(
    context: ReplayContext,
    resolver: CardReferenceResolver,
    *,
    player_entity_id: int | None = None,
    opponent_entity_id: int | None = None,
) -> DecisionResult:
    """Corréler chaque `Options` au `SendOption` suivant sans extrapolation stratégique."""

    result = DecisionResult()
    entities: dict[int, _OptionEntity] = {}
    pending: tuple[int, Decision] | None = None
    turn_number = 0
    sequence = 0
    choice_sequence = 0
    pending_choices: dict[str, tuple[int, RecordedChoice]] = {}
    game_entity = context.game_xml.find("GameEntity")
    game_entity_id = game_entity.attrib.get("id") if game_entity is not None else "1"

    for protocol_order, element in enumerate(context.game_xml.iter()):
        _update_entity(element, entities)
        if element.tag == "TagChange" and _tag_number(element) == int(GameTag.TURN):
            if element.attrib.get("entity") == game_entity_id:
                turn_number = _integer(element.attrib.get("value")) or turn_number
        elif element.tag == "Options":
            if pending is not None:
                result.by_turn.setdefault(pending[0], []).append(pending[1])
            sequence += 1
            decision = Decision(
                sequence=sequence,
                timestamp=element.attrib.get("ts"),
                options=[_option(item, entities, resolver) for item in element.findall("Option")],
            )
            pending = (turn_number, decision)
        elif element.tag == "Choices":
            raw_choice_type = _integer(element.attrib.get("type"))
            if raw_choice_type == int(ChoiceType.MULLIGAN):
                continue
            choice_id = element.attrib.get("id")
            if not choice_id:
                continue
            choice_sequence += 1
            source_entity_id = _integer(element.attrib.get("source"))
            source_ref = _reference(entities.get(source_entity_id), resolver)
            choice = RecordedChoice(
                sequence=choice_sequence,
                timestamp=element.attrib.get("ts"),
                choice_type=_choice_type(raw_choice_type, source_ref),
                player=_choice_side(
                    _integer(element.attrib.get("entity")),
                    player_entity_id,
                    opponent_entity_id,
                ),
                offered=[
                    ref
                    for item in element.findall("Choice")
                    if (
                        ref := _reference(
                            entities.get(_integer(item.attrib.get("entity"))), resolver
                        )
                    )
                    is not None
                ],
                source_card=source_ref,
            )
            pending_choices[choice_id] = (turn_number, choice)
            result.choice_protocol_orders[id(choice)] = protocol_order
        elif element.tag in {"SendChoices", "ChosenEntities"}:
            choice_id = element.attrib.get("id")
            pending_choice = pending_choices.get(choice_id or "")
            if pending_choice is None:
                continue
            choice = pending_choice[1]
            chosen = [
                ref
                for item in element.findall("Choice")
                if (ref := _reference(entities.get(_integer(item.attrib.get("entity"))), resolver))
                is not None
            ]
            if chosen:
                choice.chosen = chosen
            choice.completed = True
            if choice not in result.choices_by_turn.get(pending_choice[0], []):
                result.choices_by_turn.setdefault(pending_choice[0], []).append(choice)
        elif element.tag == "SendOption" and pending is not None:
            selected_index = _integer(element.attrib.get("option"))
            decision = pending[1]
            decision.selected_option_index = selected_index
            decision.selected_suboption_index = _integer(element.attrib.get("subOption"))
            decision.selected_target_entity_id = _integer(element.attrib.get("target"))
            decision.selected_position = _integer(element.attrib.get("position"))
            for option in decision.options:
                option.selected = option.index == selected_index
            result.by_turn.setdefault(pending[0], []).append(decision)
            pending = None

    if pending is not None:
        result.by_turn.setdefault(pending[0], []).append(pending[1])
    for turn, choice in pending_choices.values():
        if choice not in result.choices_by_turn.get(turn, []):
            result.choices_by_turn.setdefault(turn, []).append(choice)
    return result


def _option(
    element: ElementTree.Element,
    entities: dict[int, _OptionEntity],
    resolver: CardReferenceResolver,
) -> RecordedOption:
    index = _integer(element.attrib.get("index")) or 0
    option_type = _option_type(element.attrib.get("type"))
    entity_id = _integer(element.attrib.get("entity"))
    entity = entities.get(entity_id) if entity_id is not None else None
    ref = _reference(entity, resolver)
    targets = [
        target_ref
        for target in element.iter("Target")
        if (target_ref := _reference(entities.get(_integer(target.attrib.get("entity"))), resolver))
        is not None
    ]
    return RecordedOption(
        index=index,
        option_type=option_type,
        description=_describe_option(option_type, entity, ref),
        entity=ref,
        targets=targets,
        error=element.attrib.get("error"),
        available=element.attrib.get("error") is None,
    )


def _describe_option(
    option_type: str,
    entity: _OptionEntity | None,
    ref: object | None,
) -> str:
    if option_type == "Fin du tour":
        return "Terminer le tour"
    name = getattr(ref, "name", "Action enregistrée") if ref is not None else "Action enregistrée"
    if entity is None:
        return name
    if entity.zone == int(Zone.HAND):
        return f"Jouer {name}"
    if entity.card_type == int(CardType.HERO_POWER):
        return f"Utiliser le pouvoir héroïque : {name}"
    if entity.zone == int(Zone.PLAY):
        return f"Agir avec {name}"
    return f"Activer {name}"


def _reference(
    entity: _OptionEntity | None,
    resolver: CardReferenceResolver,
) -> object | None:
    if entity is None:
        return None
    visibility = Visibility.KNOWN if entity.card_id else Visibility.HIDDEN
    return resolver.reference(
        entity.card_id,
        entity_id=entity.entity_id,
        visibility=visibility,
    )


def _update_entity(
    element: ElementTree.Element,
    entities: dict[int, _OptionEntity],
) -> None:
    if element.tag in {"FullEntity", "GameEntity", "Player"}:
        entity_id = _integer(element.attrib.get("id"))
    elif element.tag in {"ShowEntity", "ChangeEntity", "TagChange", "HideEntity"}:
        entity_id = _integer(element.attrib.get("entity"))
    else:
        return
    if entity_id is None:
        return

    entity = entities.setdefault(entity_id, _OptionEntity(entity_id))
    if element.tag in {"FullEntity", "ShowEntity", "ChangeEntity"}:
        entity.card_id = element.attrib.get("cardID") or entity.card_id
        for tag in element.findall("Tag"):
            _apply_entity_tag(entity, tag.attrib.get("tag"), tag.attrib.get("value"))
    elif element.tag in {"GameEntity", "Player"}:
        for tag in element.findall("Tag"):
            _apply_entity_tag(entity, tag.attrib.get("tag"), tag.attrib.get("value"))
    elif element.tag == "TagChange":
        _apply_entity_tag(entity, element.attrib.get("tag"), element.attrib.get("value"))
    elif element.tag == "HideEntity":
        entity.zone = _integer(element.attrib.get("zone"))


def _apply_entity_tag(
    entity: _OptionEntity,
    raw_tag: str | None,
    raw_value: str | None,
) -> None:
    tag = _integer(raw_tag)
    value = _integer(raw_value)
    if tag is None or value is None:
        return
    if tag == int(GameTag.CONTROLLER):
        entity.controller = value
    elif tag == int(GameTag.ZONE):
        entity.zone = value
    elif tag == int(GameTag.CARDTYPE):
        entity.card_type = value


def _option_type(raw_value: str | None) -> str:
    value = _integer(raw_value)
    if value == int(OptionType.END_TURN):
        return "Fin du tour"
    if value == int(OptionType.POWER):
        return "Action"
    return "Option non classifiée"


def _choice_type(raw_value: int | None, source_ref: object | None) -> str:
    mechanics = getattr(source_ref, "mechanics", ()) if source_ref is not None else ()
    if "DISCOVER" in mechanics:
        return "Découverte"
    if raw_value == int(ChoiceType.TARGET):
        return "Choix de cible"
    if raw_value == int(ChoiceType.GENERAL):
        return "Choix général"
    return "Choix non classifié"


def _choice_side(
    entity_id: int | None,
    player_entity_id: int | None,
    opponent_entity_id: int | None,
) -> PlayerSide:
    if entity_id == player_entity_id:
        return PlayerSide.PLAYER
    if entity_id == opponent_entity_id:
        return PlayerSide.OPPONENT
    return PlayerSide.SYSTEM


def _tag_number(element: ElementTree.Element) -> int | None:
    return _integer(element.attrib.get("tag"))


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
