"""Snapshots d'état reconstruits par l'exporteur officiel hslog."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import Protocol, overload

from hearthstone.enums import CardType, GameTag, Step, Zone
from hslog.export import EntityTreeExporter

from hscoach.models.action import PlayerSide
from hscoach.models.card import CardRef, InformationSource, Provenance, Visibility
from hscoach.models.game import ParseWarning
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
    """Snapshots capturés uniquement sur des frontières de phase explicites."""

    turn_number: int
    active_player: PlayerSide
    turn_start_state: BoardState | None = None
    action_phase_start_state: BoardState | None = None
    action_phase_end_state: BoardState | None = None
    turn_end_state: BoardState | None = None

    @property
    def start_state(self) -> BoardState | None:
        """Alias V1 : état au moment où les actions deviennent disponibles."""

        return self.action_phase_start_state

    @property
    def end_state(self) -> BoardState | None:
        """Alias V1 : état à la frontière explicite ``MAIN_END``."""

        return self.action_phase_end_state


@dataclass(slots=True)
class SnapshotResult:
    """Snapshots et limites protocolaires rencontrées pendant la reconstruction."""

    snapshots: list[TurnSnapshot] = field(default_factory=list)
    warnings: list[ParseWarning] = field(default_factory=list)

    def __iter__(self) -> Iterator[TurnSnapshot]:
        return iter(self.snapshots)

    def __len__(self) -> int:
        return len(self.snapshots)

    @overload
    def __getitem__(self, index: int) -> TurnSnapshot: ...

    @overload
    def __getitem__(self, index: slice) -> list[TurnSnapshot]: ...

    def __getitem__(self, index: int | slice) -> TurnSnapshot | list[TurnSnapshot]:
        return self.snapshots[index]


class _SnapshotExporter(EntityTreeExporter):
    """Exporter enrichi de quatre captures au plus par demi-tour.

    Aucune copie complète n'est réalisée pour les micro-paquets : ``_snapshot``
    n'est appelée qu'aux changements de ``GameTag.STEP`` documentés ci-dessous.
    """

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
        self.snapshots_by_turn: dict[int, TurnSnapshot] = {}
        self.warnings: list[ParseWarning] = []
        self._warning_keys: set[tuple[str, int]] = set()
        self._dormant_cache: dict[int, dict[GameTag, int]] = {}

    def handle_cached_tag_for_dormant_change(self, packet: object) -> None:
        """Mémoriser les valeurs effectives que le client masque pendant Dormant."""

        entity_id = _packet_entity_id(getattr(packet, "entity", None))
        if entity_id is None:
            return
        try:
            tag = GameTag(packet.tag)
            value = int(packet.value)
        except (TypeError, ValueError):
            return
        self._dormant_cache.setdefault(entity_id, {})[tag] = value

    def handle_tag_change(self, packet: object) -> object:
        entity = super().handle_tag_change(packet)
        if packet.tag != GameTag.STEP:
            return entity

        try:
            step = Step(packet.value)
        except ValueError:
            return entity
        snapshot = self._current_turn_snapshot()
        if snapshot is None:
            return entity

        if step is Step.MAIN_READY and snapshot.turn_start_state is None:
            snapshot.turn_start_state = self._snapshot()
        elif step is Step.MAIN_ACTION and snapshot.action_phase_start_state is None:
            snapshot.action_phase_start_state = self._snapshot()
        elif step is Step.MAIN_END and snapshot.action_phase_end_state is None:
            snapshot.action_phase_end_state = self._snapshot()
        elif step is Step.MAIN_CLEANUP and snapshot.turn_end_state is None:
            # Les triggers de MAIN_END sont déjà résolus lorsque cette frontière
            # est publiée. C'est la fin de demi-tour privilégiée en V2.
            snapshot.turn_end_state = self._snapshot()
        elif step is Step.MAIN_NEXT and snapshot.turn_end_state is None:
            # MAIN_NEXT est postérieur au cleanup. Il reste un fallback défendable
            # lorsque MAIN_CLEANUP manque dans un replay partiel.
            snapshot.turn_end_state = self._snapshot()
            self._warn_fallback(
                snapshot.turn_number,
                "snapshot_fin_tour_main_next",
                "La frontière MAIN_CLEANUP est absente ; l’état de fin du demi-tour "
                "est capturé à MAIN_NEXT.",
            )
        elif (
            step is Step.FINAL_WRAPUP
            and snapshot.turn_end_state is None
            and snapshot.action_phase_end_state is not None
        ):
            # Défendable uniquement si MAIN_END a réellement été observé. Une
            # concession depuis MAIN_ACTION ne constitue pas une fin de tour.
            snapshot.turn_end_state = self._snapshot()
            self._warn_fallback(
                snapshot.turn_number,
                "snapshot_fin_tour_final_wrapup",
                "La partie se termine après MAIN_END sans frontière de cleanup ; "
                "l’état final est capturé à FINAL_WRAPUP.",
            )
        return entity

    def finish(self) -> SnapshotResult:
        """Simuler le replay sans compléter artificiellement une phase absente."""

        self.export()
        for snapshot in self.snapshots:
            self._warn_missing_boundaries(snapshot)
        return SnapshotResult(snapshots=self.snapshots, warnings=self.warnings)

    def _current_turn_snapshot(self) -> TurnSnapshot | None:
        turn_number = int(self.game.tags.get(GameTag.TURN, 0))
        if turn_number <= 0:
            self._warn_once(
                "snapshot_numero_tour_absent",
                0,
                "Une frontière de phase précède tout numéro de demi-tour exploitable.",
            )
            return None
        existing = self.snapshots_by_turn.get(turn_number)
        if existing is not None:
            return existing

        active_player_id = self._active_player_id()
        if active_player_id is None:
            self._warn_once(
                "snapshot_joueur_actif_absent",
                turn_number,
                f"Le joueur actif du demi-tour {turn_number} ne peut pas être établi.",
            )
            return None
        snapshot = TurnSnapshot(
            turn_number=turn_number,
            active_player=self._side(active_player_id),
        )
        self.snapshots_by_turn[turn_number] = snapshot
        self.snapshots.append(snapshot)
        return snapshot

    def _warn_fallback(self, turn_number: int, code: str, message: str) -> None:
        self._warn_once(code, turn_number, f"Demi-tour {turn_number} : {message}")

    def _warn_missing_boundaries(self, snapshot: TurnSnapshot) -> None:
        fields = (
            (
                "turn_start_state",
                "MAIN_READY",
                "snapshot_debut_tour_absent",
                "L’état exact de début du demi-tour n’est pas disponible.",
            ),
            (
                "action_phase_start_state",
                "MAIN_ACTION",
                "snapshot_debut_actions_absent",
                "L’état au moment de décider n’est pas disponible.",
            ),
            (
                "action_phase_end_state",
                "MAIN_END",
                "snapshot_fin_actions_absent",
                "L’état de fin de phase d’action n’est pas disponible.",
            ),
            (
                "turn_end_state",
                "MAIN_CLEANUP",
                "snapshot_fin_tour_absent",
                "L’état après les déclenchements de fin de demi-tour n’est pas disponible.",
            ),
        )
        for attribute, step, code, message in fields:
            if getattr(snapshot, attribute) is not None:
                continue
            self._warn_once(
                code,
                snapshot.turn_number,
                f"Demi-tour {snapshot.turn_number} : frontière {step} absente. {message}",
            )

    def _warn_once(self, code: str, turn_number: int, message: str) -> None:
        key = (code, turn_number)
        if key in self._warning_keys:
            return
        self._warning_keys.add(key)
        self.warnings.append(ParseWarning(code=code, message=message))

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
        creator_entity_id = _tag(entity, GameTag.CREATOR)
        reference = self.resolver.reference(
            card_id,
            entity_id=int(entity.id),
            visibility=visibility,
            created_by_entity_id=creator_entity_id,
            source=InformationSource.GAMESTATE_RECONSTRUCTED,
        )
        if creator_entity_id is None:
            return reference
        creator = next(
            (
                candidate
                for candidate in self.game.entities
                if int(candidate.id) == creator_entity_id
            ),
            None,
        )
        return replace(
            reference,
            provenance=Provenance(
                creator_entity_id=creator_entity_id,
                creator_card_id=(getattr(creator, "card_id", None) or None)
                if creator is not None
                else None,
            ),
        )

    def _minion_state(self, entity: object) -> MinionState:
        dormant = bool(_tag(entity, GameTag.DORMANT))
        cached = self._dormant_cache.get(int(entity.id), {}) if dormant else {}
        maximum_health = cached.get(GameTag.HEALTH, _tag(entity, GameTag.HEALTH))
        damage = cached.get(GameTag.DAMAGE, _tag(entity, GameTag.DAMAGE) or 0)
        return MinionState(
            card=self._entity_ref(entity),
            attack=cached.get(GameTag.ATK, _tag(entity, GameTag.ATK)),
            health=maximum_health - damage if maximum_health is not None else None,
            max_health=maximum_health,
            taunt=bool(_tag(entity, GameTag.TAUNT)),
            divine_shield=bool(_tag(entity, GameTag.DIVINE_SHIELD)),
            stealth=bool(_tag(entity, GameTag.STEALTH)),
            frozen=bool(_tag(entity, GameTag.FROZEN)),
            silenced=bool(_tag(entity, GameTag.SILENCED)),
            dormant=dormant,
        )


def capture_turn_snapshots(
    context: ReplayContext,
    resolver: CardReferenceResolver,
    *,
    friendly_player_id: int,
) -> SnapshotResult:
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


def _packet_entity_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value or None
    if isinstance(value, dict):
        return _packet_entity_id(value.get("id") or value.get("entity"))
    if value is None:
        return None
    return _packet_entity_id(getattr(value, "id", None))


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
