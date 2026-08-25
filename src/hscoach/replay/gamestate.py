"""Snapshots d'état reconstruits par l'exporteur officiel hslog."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from hearthstone.enums import CardType, GameTag, Step, Zone
from hslog.export import EntityTreeExporter

from hscoach.models.action import PlayerSide
from hscoach.models.card import CardRef, InformationSource, Visibility
from hscoach.models.state import BoardState, HeroState, MinionState, SideState
from hscoach.replay.parser import ReplayContext


class CardReferenceResolver(Protocol):
    """Surface minimale requise du résolveur de cartes."""

    def reference(
        self,
        card_id: str | None,
        *,
        entity_id: int | None = None,
        visibility: Visibility = Visibility.KNOWN,
        created_by_entity_id: int | None = None,
        source: InformationSource = InformationSource.REPLAY_EXPLICIT,
    ) -> CardRef: ...


@dataclass(slots=True)
class TurnSnapshot:
    """État au début et à la fin d'un demi-tour protocolaire."""

    turn_number: int
    active_player: PlayerSide
    start_state: BoardState
    end_state: BoardState | None = None


class _SnapshotExporter(EntityTreeExporter):
    """EntityTreeExporter enrichi de captures aux étapes MAIN_ACTION/MAIN_END."""

    def __init__(
        self,
        packet_tree: object,
        resolver: CardReferenceResolver,
        friendly_player_id: int,
    ) -> None:
        super().__init__(packet_tree)
        self.resolver = resolver
        self.friendly_player_id = friendly_player_id
        self.snapshots: list[TurnSnapshot] = []

    def handle_tag_change(self, packet: object) -> object:
        entity = super().handle_tag_change(packet)
        if packet.tag != GameTag.STEP:
            return entity
        if packet.value == Step.MAIN_ACTION:
            active_player_id = self._active_player_id()
            if active_player_id is not None:
                turn_number = int(self.game.tags.get(GameTag.TURN, len(self.snapshots) + 1))
                self.snapshots.append(
                    TurnSnapshot(
                        turn_number=turn_number,
                        active_player=self._side(active_player_id),
                        start_state=self._snapshot(),
                    )
                )
        elif packet.value == Step.MAIN_END and self.snapshots:
            self.snapshots[-1].end_state = self._snapshot()
        return entity

    def finish(self) -> list[TurnSnapshot]:
        """Simuler tout le replay et compléter un dernier tour interrompu."""

        self.export()
        if self.snapshots and self.snapshots[-1].end_state is None:
            self.snapshots[-1].end_state = self._snapshot()
        return self.snapshots

    def _active_player_id(self) -> int | None:
        for player in self.game.players:
            if player.tags.get(GameTag.CURRENT_PLAYER):
                return int(player.player_id)
        return None

    def _side(self, player_id: int) -> PlayerSide:
        if player_id == self.friendly_player_id:
            return PlayerSide.PLAYER
        return PlayerSide.OPPONENT

    def _snapshot(self) -> BoardState:
        states: dict[PlayerSide, SideState] = {}
        for player in self.game.players:
            side = self._side(int(player.player_id))
            states[side] = self._side_state(player, side)
        return BoardState(
            player=states.get(PlayerSide.PLAYER, SideState(side=PlayerSide.PLAYER)),
            opponent=states.get(PlayerSide.OPPONENT, SideState(side=PlayerSide.OPPONENT)),
        )

    def _side_state(self, player: object, side: PlayerSide) -> SideState:
        entities = sorted(
            (
                entity
                for entity in self.game.entities
                if int(entity.tags.get(GameTag.CONTROLLER, 0)) == int(player.player_id)
            ),
            key=lambda entity: (entity.tags.get(GameTag.ZONE_POSITION, 0), entity.id),
        )
        hand: list[CardRef] = []
        hidden_hand_count = 0
        board: list[MinionState] = []
        weapon: CardRef | None = None
        hero_power: CardRef | None = None

        for entity in entities:
            zone = entity.tags.get(GameTag.ZONE)
            card_type = entity.tags.get(GameTag.CARDTYPE)
            if zone == Zone.HAND:
                ref = self._entity_ref(entity)
                hand.append(ref)
                if ref.visibility is Visibility.HIDDEN:
                    hidden_hand_count += 1
            elif zone == Zone.PLAY and card_type == CardType.MINION:
                board.append(self._minion_state(entity))
            elif zone == Zone.PLAY and card_type == CardType.WEAPON:
                weapon = replace(
                    self._entity_ref(entity),
                    durability=_current_durability(entity),
                )
            elif zone == Zone.PLAY and card_type == CardType.HERO_POWER:
                hero_power = self._entity_ref(entity)

        hero_entity = _safe_hero(player)
        hero_ref = self._entity_ref(hero_entity) if hero_entity is not None else None
        hero = HeroState(
            card=hero_ref,
            health=_current_health(hero_entity),
            armor=_tag(hero_entity, GameTag.ARMOR),
            attack=_tag(hero_entity, GameTag.ATK),
            weapon=weapon,
            hero_power=hero_power,
        )
        resources = int(player.tags.get(GameTag.RESOURCES, 0))
        temporary = int(player.tags.get(GameTag.TEMP_RESOURCES, 0))
        used = int(player.tags.get(GameTag.RESOURCES_USED, 0))
        return SideState(
            side=side,
            mana_available=max(0, resources + temporary - used),
            mana_used=used,
            hero=hero,
            hand=hand,
            hidden_hand_count=hidden_hand_count,
            board=board,
        )

    def _entity_ref(self, entity: object) -> CardRef:
        card_id = getattr(entity, "card_id", None) or None
        visibility = Visibility.KNOWN if card_id else Visibility.HIDDEN
        return self.resolver.reference(
            card_id,
            entity_id=int(entity.id),
            visibility=visibility,
            created_by_entity_id=_tag(entity, GameTag.CREATOR),
            source=InformationSource.GAMESTATE_RECONSTRUCTED,
        )

    def _minion_state(self, entity: object) -> MinionState:
        maximum_health = _tag(entity, GameTag.HEALTH)
        return MinionState(
            card=self._entity_ref(entity),
            attack=_tag(entity, GameTag.ATK),
            health=_current_health(entity),
            max_health=maximum_health,
            taunt=bool(_tag(entity, GameTag.TAUNT)),
            divine_shield=bool(_tag(entity, GameTag.DIVINE_SHIELD)),
            stealth=bool(_tag(entity, GameTag.STEALTH)),
            frozen=bool(_tag(entity, GameTag.FROZEN)),
            silenced=bool(_tag(entity, GameTag.SILENCED)),
        )


def capture_turn_snapshots(
    context: ReplayContext,
    resolver: CardReferenceResolver,
    *,
    friendly_player_id: int,
) -> list[TurnSnapshot]:
    """Reconstruire les états courants aux frontières de chaque demi-tour."""

    exporter = _SnapshotExporter(context.packet_tree, resolver, friendly_player_id)
    return exporter.finish()


def _safe_hero(player: object) -> object | None:
    try:
        return player.hero
    except (AttributeError, ValueError):
        return None


def _tag(entity: object | None, tag: GameTag) -> int | None:
    if entity is None:
        return None
    value = entity.tags.get(tag)
    return int(value) if value is not None else None


def _current_health(entity: object | None) -> int | None:
    maximum = _tag(entity, GameTag.HEALTH)
    if maximum is None:
        return None
    damage = _tag(entity, GameTag.DAMAGE) or 0
    return maximum - damage


def _current_durability(entity: object | None) -> int | None:
    durability = _tag(entity, GameTag.DURABILITY)
    if durability is None:
        return None
    damage = _tag(entity, GameTag.DAMAGE) or 0
    return durability - damage
