"""Reconstruction prudente du mulligan à partir des choix et des zones réelles."""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree

from hearthstone.enums import ChoiceType, GameTag, Step, Zone

from hscoach.models.card import CardRef, InformationSource, Visibility
from hscoach.models.game import KnowledgeStatus, Mulligan, ParseWarning
from hscoach.replay.gamestate import CardReferenceResolver
from hscoach.replay.parser import ReplayContext


@dataclass(slots=True)
class _EntityAtMulligan:
    entity_id: int
    card_id: str | None = None
    controller: int | None = None
    zone: int | None = None
    tags: dict[GameTag, int] = field(default_factory=dict)


@dataclass(slots=True)
class MulliganResult:
    """Mulligan et avertissements produits pendant sa vérification."""

    mulligan: Mulligan
    warnings: list[ParseWarning] = field(default_factory=list)


def extract_mulligan(
    context: ReplayContext,
    resolver: CardReferenceResolver,
    *,
    player_entity_id: int,
    player_id: int,
) -> MulliganResult:
    """Classer les cartes selon leur zone après le mulligan, pas selon un nom de paquet.

    `ChosenEntities` et `SendChoices` ne sont utilisés que pour contrôler la
    cohérence. La distinction conservée/renvoyée vient de la zone effective des
    entités juste avant le premier `MAIN_READY`.
    """

    game = context.game_xml
    entities: dict[int, _EntityAtMulligan] = {}
    offered_ids: list[int] = []
    offered_card_ids: dict[int, str | None] = {}
    chosen_ids: set[int] = set()
    choice_id: str | None = None
    received_candidates: set[int] = set()
    choices_seen = False

    for element in game.iter():
        _apply_entity_element(element, entities)

        if element.tag == "Choices" and _is_player_mulligan(element, player_entity_id):
            choices_seen = True
            choice_id = element.attrib.get("id")
            offered_ids = [_integer(choice.attrib.get("entity")) for choice in element]
            offered_ids = [entity_id for entity_id in offered_ids if entity_id is not None]
            offered_card_ids = {
                entity_id: entities.get(entity_id, _EntityAtMulligan(entity_id)).card_id
                for entity_id in offered_ids
            }
        elif element.tag == "ChosenEntities" and element.attrib.get("id") == choice_id:
            if element.attrib.get("entity") == str(player_entity_id):
                chosen_ids.update(_choice_entities(element))
        elif element.tag == "SendChoices" and element.attrib.get("id") == choice_id:
            chosen_ids.update(_choice_entities(element))

        if choices_seen and element.tag == "TagChange":
            entity_id = _integer(element.attrib.get("entity"))
            tag = _integer(element.attrib.get("tag"))
            value = _integer(element.attrib.get("value"))
            entity = entities.get(entity_id) if entity_id is not None else None
            if (
                entity is not None
                and tag == int(GameTag.ZONE)
                and value == int(Zone.HAND)
                and entity.controller == player_id
                and entity.entity_id not in offered_ids
            ):
                received_candidates.add(entity.entity_id)

        if _is_first_main_ready(element):
            break

    if not offered_ids:
        warning = ParseWarning(
            code="mulligan_absent",
            message="Aucun paquet de mulligan exploitable n’a été trouvé pour le joueur.",
        )
        return MulliganResult(
            mulligan=Mulligan(
                status=KnowledgeStatus.UNKNOWN,
                source=InformationSource.UNCERTAIN,
            ),
            warnings=[warning],
        )

    offered = [
        resolver.reference(
            offered_card_ids.get(entity_id),
            entity_id=entity_id,
            visibility=(Visibility.KNOWN if offered_card_ids.get(entity_id) else Visibility.HIDDEN),
        )
        for entity_id in offered_ids
    ]
    kept_ids = [
        entity_id
        for entity_id in offered_ids
        if entities.get(entity_id) is not None and entities[entity_id].zone == int(Zone.HAND)
    ]
    returned_ids = [
        entity_id
        for entity_id in offered_ids
        if entities.get(entity_id) is not None and entities[entity_id].zone == int(Zone.DECK)
    ]
    unresolved_ids = set(offered_ids) - set(kept_ids) - set(returned_ids)

    candidate_ids = sorted(received_candidates - set(offered_ids))
    received_ids = candidate_ids if len(candidate_ids) == len(returned_ids) else []
    partially_reconstructed = bool(unresolved_ids) or len(candidate_ids) != len(returned_ids)

    warnings: list[ParseWarning] = []
    if chosen_ids and not chosen_ids.issubset(set(kept_ids)):
        warnings.append(
            ParseWarning(
                code="mulligan_choix_ambigu",
                message=(
                    "Les paquets de choix et les zones finales du mulligan ne concordent pas "
                    "parfaitement ; les zones ont été privilégiées."
                ),
            )
        )
        partially_reconstructed = True
    if partially_reconstructed:
        warnings.append(
            ParseWarning(
                code="mulligan_partiel",
                message="Mulligan partiellement reconstruit ; aucune carte ambiguë n’est classée.",
            )
        )

    mulligan = Mulligan(
        offered=offered,
        kept=(
            None
            if unresolved_ids
            else [_reference_entity(entities, resolver, entity_id) for entity_id in kept_ids]
        ),
        returned=(
            None
            if unresolved_ids
            else [_reference_entity(entities, resolver, entity_id) for entity_id in returned_ids]
        ),
        received=(
            None
            if unresolved_ids or len(candidate_ids) != len(returned_ids)
            else [_reference_entity(entities, resolver, entity_id) for entity_id in received_ids]
        ),
        status=(KnowledgeStatus.PARTIAL if partially_reconstructed else KnowledgeStatus.KNOWN),
        source=(
            InformationSource.UNCERTAIN
            if partially_reconstructed
            else InformationSource.REPLAY_EXPLICIT
        ),
    )
    return MulliganResult(mulligan=mulligan, warnings=warnings)


def _apply_entity_element(
    element: ElementTree.Element,
    entities: dict[int, _EntityAtMulligan],
) -> None:
    if element.tag in {"FullEntity", "GameEntity", "Player"}:
        entity_id = _integer(element.attrib.get("id"))
        if entity_id is None:
            return
        entity = entities.setdefault(entity_id, _EntityAtMulligan(entity_id))
        entity.card_id = element.attrib.get("cardID") or entity.card_id
        for child in element.findall("Tag"):
            _apply_tag(entity, child.attrib.get("tag"), child.attrib.get("value"))
    elif element.tag in {"ShowEntity", "ChangeEntity"}:
        entity_id = _integer(element.attrib.get("entity"))
        if entity_id is None:
            return
        entity = entities.setdefault(entity_id, _EntityAtMulligan(entity_id))
        entity.card_id = element.attrib.get("cardID") or entity.card_id
        for child in element.findall("Tag"):
            _apply_tag(entity, child.attrib.get("tag"), child.attrib.get("value"))
    elif element.tag == "HideEntity":
        entity_id = _integer(element.attrib.get("entity"))
        if entity_id is not None:
            entity = entities.setdefault(entity_id, _EntityAtMulligan(entity_id))
            entity.zone = _integer(element.attrib.get("zone"))
    elif element.tag == "TagChange":
        entity_id = _integer(element.attrib.get("entity"))
        if entity_id is not None:
            entity = entities.setdefault(entity_id, _EntityAtMulligan(entity_id))
            _apply_tag(entity, element.attrib.get("tag"), element.attrib.get("value"))


def _apply_tag(entity: _EntityAtMulligan, raw_tag: str | None, raw_value: str | None) -> None:
    tag_value = _integer(raw_tag)
    value = _integer(raw_value)
    if tag_value is None or value is None:
        return
    try:
        tag = GameTag(tag_value)
    except ValueError:
        return
    entity.tags[tag] = value
    if tag is GameTag.CONTROLLER:
        entity.controller = value
    elif tag is GameTag.ZONE:
        entity.zone = value


def _is_player_mulligan(element: ElementTree.Element, player_entity_id: int) -> bool:
    return element.attrib.get("entity") == str(player_entity_id) and _integer(
        element.attrib.get("type")
    ) == int(ChoiceType.MULLIGAN)


def _is_first_main_ready(element: ElementTree.Element) -> bool:
    return (
        element.tag == "TagChange"
        and _integer(element.attrib.get("tag")) == int(GameTag.STEP)
        and _integer(element.attrib.get("value")) == int(Step.MAIN_READY)
    )


def _choice_entities(element: ElementTree.Element) -> set[int]:
    return {
        value
        for child in element.findall("Choice")
        if (value := _integer(child.attrib.get("entity"))) is not None
    }


def _reference_entity(
    entities: dict[int, _EntityAtMulligan],
    resolver: CardReferenceResolver,
    entity_id: int,
) -> CardRef:
    entity = entities.get(entity_id)
    card_id = entity.card_id if entity is not None else None
    return resolver.reference(
        card_id,
        entity_id=entity_id,
        visibility=Visibility.KNOWN if card_id else Visibility.HIDDEN,
    )


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
