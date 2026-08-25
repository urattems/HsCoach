"""Extraction des options enregistrées par le client et des choix envoyés."""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree

from hearthstone.enums import CardType, GameTag, OptionType, Zone

from hscoach.models.action import Decision, RecordedOption
from hscoach.models.card import Visibility
from hscoach.replay.gamestate import CardReferenceResolver
from hscoach.replay.parser import ReplayContext


@dataclass(slots=True)
class DecisionResult:
    """Décisions groupées par numéro de demi-tour protocolaire."""

    by_turn: dict[int, list[Decision]] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return sum(len(items) for items in self.by_turn.values())


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
) -> DecisionResult:
    """Corréler chaque `Options` au `SendOption` suivant sans extrapolation stratégique."""

    result = DecisionResult()
    entities: dict[int, _OptionEntity] = {}
    pending: tuple[int, Decision] | None = None
    turn_number = 0
    sequence = 0
    game_entity = context.game_xml.find("GameEntity")
    game_entity_id = game_entity.attrib.get("id") if game_entity is not None else "1"

    for element in context.game_xml.iter():
        _update_entity(element, entities)
        if element.tag == "TagChange" and _tag_number(element) == int(GameTag.TURN):
            if element.attrib.get("entity") == game_entity_id:
                turn_number = _integer(element.attrib.get("value")) or turn_number
        elif element.tag == "Options":
            sequence += 1
            decision = Decision(
                sequence=sequence,
                timestamp=element.attrib.get("ts"),
                options=[_option(item, entities, resolver) for item in element.findall("Option")],
            )
            pending = (turn_number, decision)
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


def _tag_number(element: ElementTree.Element) -> int | None:
    return _integer(element.attrib.get("tag"))


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
