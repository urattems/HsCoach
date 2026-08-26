"""Comparaison déterministe des snapshots reconstruits d'un demi-tour."""

from __future__ import annotations

from dataclasses import dataclass

from hscoach.models import (
    BoardState,
    EntityDelta,
    HeroDelta,
    InformationSource,
    ManaDelta,
    MinionState,
    PlayerSide,
    StateDelta,
    TurnPhase,
    TurnState,
    ValueDelta,
    ZoneDelta,
)
from hscoach.models.card import CardRef


@dataclass(slots=True, frozen=True)
class _VisibleEntity:
    side: PlayerSide
    zone: str
    card: CardRef
    minion: MinionState | None = None


def build_turn_state_deltas(turn: TurnState) -> list[StateDelta]:
    """Comparer les trois intervalles sémantiques du demi-tour.

    Un intervalle dont une frontière manque est conservé avec ``complete=False``.
    Cela rend l'absence visible au lieu de remplacer un snapshot par un autre instant.
    """

    boundaries = (
        (
            TurnPhase.TURN_START,
            turn.turn_start_state,
            TurnPhase.ACTION_PHASE_START,
            turn.action_phase_start_state,
        ),
        (
            TurnPhase.ACTION_PHASE_START,
            turn.action_phase_start_state,
            TurnPhase.ACTION_PHASE_END,
            turn.action_phase_end_state,
        ),
        (
            TurnPhase.ACTION_PHASE_END,
            turn.action_phase_end_state,
            TurnPhase.TURN_END,
            turn.turn_end_state,
        ),
    )
    sequence = 0
    deltas: list[StateDelta] = []
    for from_phase, before, to_phase, after in boundaries:
        delta, sequence = compare_board_states(
            before,
            after,
            from_phase=from_phase,
            to_phase=to_phase,
            sequence_start=sequence,
        )
        deltas.append(delta)
    return deltas


def compare_board_states(
    before: BoardState | None,
    after: BoardState | None,
    *,
    from_phase: TurnPhase,
    to_phase: TurnPhase,
    sequence_start: int = 0,
) -> tuple[StateDelta, int]:
    """Produire les différences observables sans attribuer de cause implicite."""

    result = StateDelta(from_phase=from_phase, to_phase=to_phase)
    if before is None or after is None:
        result.complete = False
        return result, sequence_start

    sequence = sequence_start
    before_entities = _visible_entities(before)
    after_entities = _visible_entities(after)
    all_entity_ids = sorted(before_entities.keys() | after_entities.keys())

    for entity_id in all_entity_ids:
        old = before_entities.get(entity_id)
        new = after_entities.get(entity_id)
        if old is not None and new is not None and old.zone != new.zone:
            result.zones.append(
                ZoneDelta(
                    entity_id=entity_id,
                    side=new.side,
                    from_zone=old.zone,
                    to_zone=new.zone,
                    card=new.card,
                )
            )
        elif old is None and new is not None:
            result.zones.append(
                ZoneDelta(
                    entity_id=entity_id,
                    side=new.side,
                    from_zone="UNKNOWN",
                    to_zone=new.zone,
                    card=new.card,
                )
            )
        elif old is not None and new is None:
            result.zones.append(
                ZoneDelta(
                    entity_id=entity_id,
                    side=old.side,
                    from_zone=old.zone,
                    to_zone="UNKNOWN",
                    card=old.card,
                )
            )

        if old is None or new is None or old.minion is None or new.minion is None:
            continue
        for attribute in (
            "attack",
            "health",
            "max_health",
            "taunt",
            "divine_shield",
            "stealth",
            "frozen",
            "silenced",
            "dormant",
        ):
            old_value = getattr(old.minion, attribute)
            new_value = getattr(new.minion, attribute)
            if old_value == new_value:
                continue
            sequence += 1
            result.entities.append(
                EntityDelta(
                    sequence=sequence,
                    entity_id=entity_id,
                    side=new.side,
                    phase=to_phase,
                    attribute=attribute,
                    value=_value_delta(old_value, new_value),
                    card=new.card,
                    information_source=InformationSource.GAMESTATE_RECONSTRUCTED,
                )
            )

    for side in (PlayerSide.PLAYER, PlayerSide.OPPONENT):
        old_side = before.player if side is PlayerSide.PLAYER else before.opponent
        new_side = after.player if side is PlayerSide.PLAYER else after.opponent
        hero = HeroDelta(side=side)
        for attribute in ("health", "armor", "attack"):
            old_value = getattr(old_side.hero, attribute)
            new_value = getattr(new_side.hero, attribute)
            if old_value != new_value:
                setattr(hero, attribute, _value_delta(old_value, new_value))
        if hero.health is not None or hero.armor is not None or hero.attack is not None:
            result.heroes.append(hero)

        mana = ManaDelta(side=side)
        if old_side.mana_available != new_side.mana_available:
            mana.available = _value_delta(old_side.mana_available, new_side.mana_available)
        if old_side.mana_used != new_side.mana_used:
            mana.used = _value_delta(old_side.mana_used, new_side.mana_used)
        if mana.available is not None or mana.used is not None:
            result.mana.append(mana)

    return result, sequence


def _visible_entities(state: BoardState) -> dict[int, _VisibleEntity]:
    entities: dict[int, _VisibleEntity] = {}
    for side_state in (state.player, state.opponent):
        for card in side_state.hand:
            if card.entity_id is not None:
                entities[card.entity_id] = _VisibleEntity(side_state.side, "HAND", card)
        for minion in side_state.board:
            if minion.card.entity_id is not None:
                entities[minion.card.entity_id] = _VisibleEntity(
                    side_state.side,
                    "PLAY",
                    minion.card,
                    minion,
                )
    return entities


def _value_delta(
    before: int | str | bool | None,
    after: int | str | bool | None,
) -> ValueDelta:
    numeric_delta = None
    if (
        isinstance(before, int)
        and not isinstance(before, bool)
        and isinstance(after, int)
        and not isinstance(after, bool)
    ):
        numeric_delta = after - before
    return ValueDelta(before=before, after=after, delta=numeric_delta)
