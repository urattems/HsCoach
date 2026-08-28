"""Extraction chronologique factuelle des paquets HSReplay.

Le ``PacketTree`` officiel reste la source de vérité.  Les références de cartes
sont figées au moment de chaque événement : révéler plus tard une entité adverse
ne modifie donc jamais une pioche auparavant cachée.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol

from hearthstone.enums import BlockType, CardType, GameTag, PlayState, Step, Zone
from hslog.packets import (
    Block,
    CachedTagForDormantChange,
    ChangeEntity,
    CreateGame,
    FullEntity,
    HideEntity,
    ShowEntity,
    TagChange,
)

from hscoach.models.action import ActionType, GameAction, PlayerSide, TurnPhase
from hscoach.models.card import CardRef, InformationSource, Provenance, Visibility
from hscoach.models.game import ParseWarning
from hscoach.models.state import EntityDelta, TurnState, ValueDelta
from hscoach.replay.parser import ReplayContext


class CardReferenceResolver(Protocol):
    """Petite surface stable requise du résolveur de cartes."""

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
class TimelineResult:
    """Chronologie et registre d'identités issus d'un replay."""

    turns: list[TurnState] = field(default_factory=list)
    start_of_game_events: list[GameAction] = field(default_factory=list)
    important_events: list[GameAction] = field(default_factory=list)
    entity_card_ids: dict[int, str] = field(default_factory=dict)
    warnings: list[ParseWarning] = field(default_factory=list)
    action_protocol_orders: dict[int, int] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class _EntityState:
    """État temporel minimal d'une entité du protocole."""

    entity_id: int
    raw_card_id: str | None = None
    observable_card_id: str | None = None
    tags: dict[GameTag, int] = field(default_factory=dict)
    creation_emitted: bool = False
    dormant_cached_tags: dict[GameTag, int] = field(default_factory=dict)
    dormant_projection_tags: set[GameTag] = field(default_factory=set)
    dormant_restore_tags: set[GameTag] = field(default_factory=set)

    @property
    def controller(self) -> int | None:
        return self.tags.get(GameTag.CONTROLLER)

    @property
    def zone(self) -> Zone:
        return _enum_or_default(Zone, self.tags.get(GameTag.ZONE), Zone.INVALID)

    @property
    def card_type(self) -> CardType:
        return _enum_or_default(CardType, self.tags.get(GameTag.CARDTYPE), CardType.INVALID)

    @property
    def creator(self) -> int | None:
        value = self.tags.get(GameTag.CREATOR)
        return value if value else None


@dataclass(slots=True, frozen=True)
class _BlockContext:
    """Contexte causal uniquement quand le protocole porte une cible explicite."""

    block_type: BlockType
    entity_id: int | None
    target_id: int | None
    sequence: int | None


_IMPORTANT_ACTIONS = {
    ActionType.ADD_TO_HAND,
    ActionType.DISCOVER,
    ActionType.CHOICE,
    ActionType.TRANSFORM,
    ActionType.PLAY_SECRET,
    ActionType.REVEAL_SECRET,
    ActionType.FATIGUE,
    ActionType.BECOMES_DORMANT,
    ActionType.AWAKENS,
    ActionType.VICTORY,
    ActionType.DEFEAT,
    ActionType.CONCEDE,
}


class _TimelineBuilder:
    def __init__(
        self,
        context: ReplayContext,
        resolver: CardReferenceResolver,
        player_entity_id: int,
        opponent_entity_id: int,
    ) -> None:
        self.context = context
        self.resolver = resolver
        self.player_entity_id = player_entity_id
        self.opponent_entity_id = opponent_entity_id
        self.entities: dict[int, _EntityState] = {}
        self.player_ids: dict[int, int] = {}
        self.game_entity_id: int | None = None
        self.active_player_entity_id: int | None = None
        self.turns: list[TurnState] = []
        self.start_events: list[GameAction] = []
        self.important_events: list[GameAction] = []
        self.warnings: list[ParseWarning] = []
        self.current_turn: TurnState | None = None
        self.pending_first_turn: int | None = None
        self.sequence = 0
        self.deaths_emitted = 0
        self.death_depth = 0
        self.last_timestamp = context.game_xml.attrib.get("ts")
        self.current_phase = TurnPhase.UNKNOWN
        self.block_stack: list[_BlockContext] = []
        self.pending_creation_entity_id: int | None = None
        self.nested_power_events: dict[int, list[dict[str, object]]] = {}
        self.action_protocol_orders: dict[int, int] = {}
        self.packet_protocol_orders = _packet_protocol_orders(context)
        self.current_protocol_order: int | None = None
        if not self.packet_protocol_orders:
            self.warnings.append(
                ParseWarning(
                    code="ordre_protocole_indisponible",
                    message=(
                        "L'ordre protocolaire commun n'a pas pu être établi ; "
                        "les choix sans horodatage conservent un placement prudent."
                    ),
                )
            )

    def build(self) -> TimelineResult:
        packets = list(self.context.packet_tree)
        start_index = self._ingest_initial_packets(packets)
        self._emit(
            ActionType.START_GAME,
            PlayerSide.SYSTEM,
            "Début de la partie.",
            timestamp=self.last_timestamp,
        )
        for packet in packets[start_index:]:
            self._visit(packet)
        self._finish_current_turn()

        for turn in self.turns:
            turn.actions.sort(key=lambda action: action.sequence)
            turn.entity_deltas.sort(key=lambda delta: delta.sequence)
        self.start_events.sort(key=lambda action: action.sequence)
        self.important_events.sort(key=lambda action: action.sequence)
        card_ids = {
            entity_id: state.raw_card_id
            for entity_id, state in sorted(self.entities.items())
            if state.raw_card_id
        }
        return TimelineResult(
            turns=self.turns,
            start_of_game_events=self.start_events,
            important_events=self.important_events,
            entity_card_ids=card_ids,
            warnings=self.warnings,
            action_protocol_orders=self.action_protocol_orders,
        )

    def _ingest_initial_packets(self, packets: list[object]) -> int:
        """Charger CreateGame et les FullEntity initiaux sans créer de faux événements."""

        index = 0
        if packets and isinstance(packets[0], CreateGame):
            create_game = packets[0]
            self.game_entity_id = _entity_id(create_game.entity)
            if self.game_entity_id is not None:
                self._ingest_entity(
                    self.game_entity_id,
                    None,
                    create_game.tags,
                    initial=True,
                )
            for player in create_game.players:
                entity_id = _entity_id(player.entity)
                if entity_id is None:
                    continue
                self.player_ids[entity_id] = int(player.player_id)
                self._ingest_entity(entity_id, None, player.tags, initial=True)
            index = 1

        while index < len(packets) and isinstance(packets[index], FullEntity):
            packet = packets[index]
            entity_id = _entity_id(packet.entity)
            if entity_id is not None:
                self._ingest_entity(entity_id, packet.card_id, packet.tags, initial=True)
            index += 1

        if self.player_entity_id not in self.player_ids:
            self.warnings.append(
                ParseWarning(
                    code="joueur_timeline_inconnu",
                    message="Le contrôleur du joueur local est ambigu dans la chronologie.",
                )
            )
        return index

    def _visit(self, packet: object) -> None:
        previous_protocol_order = self.current_protocol_order
        self.current_protocol_order = self.packet_protocol_orders.get(id(packet))
        try:
            self._remember_timestamp(packet)
            if self.pending_creation_entity_id is not None and not (
                isinstance(packet, ShowEntity)
                and _entity_id(packet.entity) == self.pending_creation_entity_id
            ):
                # Seul le ShowEntity immédiatement associé au FullEntity constitue
                # encore la même introduction. Une révélation tardive ne doit pas
                # fabriquer une création à cet instant.
                self.pending_creation_entity_id = None
            if isinstance(packet, Block):
                self._visit_block(packet)
            elif isinstance(packet, ShowEntity | FullEntity | ChangeEntity):
                self._visit_entity_packet(packet)
            elif isinstance(packet, HideEntity):
                self._visit_hide_entity(packet)
            elif isinstance(packet, CachedTagForDormantChange):
                self._visit_cached_dormant_change(packet)
            elif isinstance(packet, TagChange):
                self._visit_tag_change(packet)
            elif hasattr(packet, "packets"):
                for child in packet.packets:
                    self._visit(child)
        finally:
            self.current_protocol_order = previous_protocol_order

    def _visit_block(self, block: Block) -> None:
        block_type = _enum_or_default(BlockType, block.type, BlockType.INVALID)
        timestamp = _timestamp(block) or self.last_timestamp
        destination = self.current_turn
        entity_id = _entity_id(block.entity)
        target_id = _entity_id(block.target)

        if block_type is BlockType.DEATHS:
            previous_count = self.deaths_emitted
            self.death_depth += 1
            self.block_stack.append(_BlockContext(block_type, entity_id, target_id, sequence=None))
            try:
                for child in block.packets:
                    self._visit(child)
            finally:
                self.block_stack.pop()
            self.death_depth -= 1
            if self.deaths_emitted == previous_count:
                self._emit(
                    ActionType.UNCLASSIFIED,
                    PlayerSide.SYSTEM,
                    "Bloc de résolution des morts sans entité explicitement identifiée.",
                    timestamp=timestamp,
                    metadata={"block_type": block_type.name},
                    destination=destination,
                )
            return

        sequence = (
            self._reserve_sequence()
            if block_type
            in {
                BlockType.PLAY,
                BlockType.POWER,
                BlockType.ATTACK,
                BlockType.TRIGGER,
            }
            else None
        )
        self.block_stack.append(_BlockContext(block_type, entity_id, target_id, sequence))
        try:
            for child in block.packets:
                self._visit(child)
        finally:
            self.block_stack.pop()

        state = self.entities.get(entity_id) if entity_id is not None else None
        side = self._side_for_entity(entity_id)

        if block_type is BlockType.TRIGGER:
            if (
                self.current_turn is None
                and state
                and state.raw_card_id
                and _block_reveals_entity(block, entity_id)
            ):
                # ShowEntity a rendu l'identité publique dans ce bloc, même si un
                # HideEntity ultérieur la remet ensuite dans le deck.
                source = self.resolver.reference(
                    state.raw_card_id,
                    entity_id=entity_id,
                    visibility=Visibility.KNOWN,
                    created_by_entity_id=state.creator,
                )
                self._emit(
                    ActionType.START_GAME_EFFECT,
                    side,
                    f"{source.name} déclenche son effet de début de partie.",
                    timestamp=timestamp,
                    source_card=source,
                    metadata={
                        "block_type": block_type.name,
                        "effect_index": getattr(block, "effectindex", None),
                        "trigger_keyword": getattr(block, "trigger_keyword", None),
                        "protocol_only_reveal": _is_reveal_only_trigger(block, entity_id),
                    },
                    destination=destination,
                    sequence=sequence,
                )
            return
        if block_type is BlockType.PLAY:
            self._emit_play(
                entity_id,
                side,
                state,
                timestamp,
                destination,
                sequence,
                target_id,
            )
        elif block_type is BlockType.POWER:
            self._emit_power(entity_id, side, state, timestamp, destination, sequence)
        elif block_type is BlockType.ATTACK:
            source = self._reference(entity_id)
            target = self._reference(target_id)
            self._emit(
                ActionType.ATTACK,
                side,
                f"{source.name} attaque {target.name}.",
                timestamp=timestamp,
                source_card=source,
                target_card=target,
                metadata={
                    "block_type": block_type.name,
                    "entity_id": entity_id,
                    "target_entity_id": target_id,
                },
                destination=destination,
                sequence=sequence,
            )
        elif block_type is BlockType.FATIGUE:
            target = self._reference(target_id or entity_id)
            self._emit(
                ActionType.FATIGUE,
                self._side_for_entity(target_id or entity_id),
                f"{target.name} subit la fatigue.",
                timestamp=timestamp,
                target_card=target,
                metadata={
                    "block_type": block_type.name,
                    "entity_id": entity_id,
                    "target_entity_id": target_id,
                },
                destination=destination,
                sequence=sequence,
            )

    def _emit_play(
        self,
        entity_id: int | None,
        side: PlayerSide,
        state: _EntityState | None,
        timestamp: str | None,
        destination: TurnState | None,
        sequence: int | None,
        target_id: int | None,
    ) -> None:
        source = self._reference(entity_id)
        target = self._reference(target_id) if target_id else None
        card_type = state.card_type if state else CardType.INVALID
        if state and state.tags.get(GameTag.SECRET):
            action_type = ActionType.PLAY_SECRET
            description = f"{_side_label(side)} joue un secret."
        elif card_type is CardType.SPELL:
            action_type = ActionType.CAST_SPELL
            description = f"{_side_label(side)} lance {source.name}."
        elif card_type is CardType.WEAPON:
            action_type = ActionType.EQUIP_WEAPON
            description = f"{_side_label(side)} équipe {source.name}."
        elif card_type is CardType.HERO_POWER:
            action_type = ActionType.HERO_POWER
            description = f"{_side_label(side)} utilise {source.name}."
        else:
            action_type = ActionType.PLAY_CARD
            description = f"{_side_label(side)} joue {source.name}."
        metadata: dict[str, object] = {
            "block_type": BlockType.PLAY.name,
            "entity_id": entity_id,
            "target_entity_id": target_id,
        }
        protocol_events = self.nested_power_events.pop(sequence, [])
        if protocol_events:
            metadata["protocol_events"] = protocol_events
        self._emit(
            action_type,
            side,
            description,
            timestamp=timestamp,
            source_card=source,
            target_card=target,
            metadata=metadata,
            destination=destination,
            sequence=sequence,
        )

    def _emit_power(
        self,
        entity_id: int | None,
        side: PlayerSide,
        state: _EntityState | None,
        timestamp: str | None,
        destination: TurnState | None,
        sequence: int | None,
    ) -> None:
        parent_play = next(
            (
                context
                for context in reversed(self.block_stack)
                if context.block_type is BlockType.PLAY
            ),
            None,
        )
        if (
            state
            and state.card_type is CardType.HERO_POWER
            and parent_play is not None
            and parent_play.entity_id == entity_id
            and parent_play.sequence is not None
        ):
            self.nested_power_events.setdefault(parent_play.sequence, []).append(
                {
                    "block_type": BlockType.POWER.name,
                    "entity_id": entity_id,
                    "sequence": sequence,
                }
            )
            return
        source = self._reference(entity_id)
        if state and state.card_type is CardType.HERO_POWER:
            action_type = ActionType.HERO_POWER
            description = f"{_side_label(side)} utilise {source.name}."
        else:
            action_type = ActionType.UNCLASSIFIED
            description = f"Effet de {source.name} résolu."
        self._emit(
            action_type,
            side,
            description,
            timestamp=timestamp,
            source_card=source,
            metadata={"block_type": BlockType.POWER.name, "entity_id": entity_id},
            destination=destination,
            sequence=sequence,
            technical=source.technical,
        )

    def _visit_entity_packet(self, packet: ShowEntity | FullEntity | ChangeEntity) -> None:
        entity_id = _entity_id(packet.entity)
        if entity_id is None:
            return
        introduced = isinstance(packet, FullEntity) and entity_id not in self.entities
        state = self.entities.setdefault(entity_id, _EntityState(entity_id))
        previous_zone = state.zone
        previous_ref = self._reference(entity_id)
        previous_id = state.raw_card_id

        if introduced:
            self.pending_creation_entity_id = entity_id

        card_id = (packet.card_id or "").strip() or None
        if isinstance(packet, ChangeEntity) or card_id:
            state.raw_card_id = card_id
            state.observable_card_id = card_id
        self._apply_tags(state, packet.tags)
        causally_introduced = introduced and bool(self.block_stack)
        if (
            causally_introduced and (state.raw_card_id is not None or state.creator is not None)
        ) or (
            isinstance(packet, ShowEntity)
            and self.pending_creation_entity_id == entity_id
            and bool(self.block_stack)
        ):
            self._emit_creation_if_needed(state, observed_introduction=True)
            self.pending_creation_entity_id = None
        self._handle_zone_change(state, previous_zone, state.zone)

        if (
            isinstance(packet, ChangeEntity)
            and previous_id
            and card_id
            and previous_id != card_id
            and self.current_turn is not None
        ):
            target = self._reference(entity_id)
            self._emit(
                ActionType.TRANSFORM,
                self._side_for_entity(entity_id),
                f"{previous_ref.name} devient {target.name}.",
                source_card=previous_ref,
                target_card=target,
                metadata={"entity_id": entity_id},
            )

    def _visit_hide_entity(self, packet: HideEntity) -> None:
        entity_id = _entity_id(packet.entity)
        if entity_id is None:
            return
        state = self.entities.setdefault(entity_id, _EntityState(entity_id))
        previous_zone = state.zone
        zone = _enum_or_default(Zone, packet.zone, Zone.INVALID)
        state.tags[GameTag.ZONE] = int(zone)
        if self._side_for_entity(entity_id) is PlayerSide.OPPONENT and zone in {
            Zone.DECK,
            Zone.HAND,
            Zone.SETASIDE,
        }:
            state.observable_card_id = None
        self._handle_zone_change(state, previous_zone, zone)

    def _visit_cached_dormant_change(self, packet: CachedTagForDormantChange) -> None:
        """Conserver les statistiques effectives masquées par la projection Dormant."""

        entity_id = _entity_id(packet.entity)
        if entity_id is None:
            return
        tag_value = _optional_protocol_int(getattr(packet, "tag", None))
        current_value = _optional_protocol_int(getattr(packet, "value", None))
        if tag_value is None or current_value is None:
            return
        try:
            tag = GameTag(tag_value)
        except ValueError:
            return
        if tag not in {GameTag.ATK, GameTag.HEALTH, GameTag.DAMAGE}:
            return

        state = self.entities.setdefault(entity_id, _EntityState(entity_id))
        if (
            not state.tags.get(GameTag.DORMANT)
            and tag in state.dormant_restore_tags
            and current_value == 0
        ):
            # Au réveil, le protocole efface son cache avec des zéros avant de
            # restaurer les vraies valeurs. Ces zéros ne sont pas des stats.
            return

        previous_value = state.dormant_cached_tags.get(tag)
        state.dormant_cached_tags[tag] = current_value
        if state.tags.get(GameTag.DORMANT) and previous_value is not None:
            # Une vraie modification reçue pendant Dormant touche le cache
            # effectif. Elle doit rester visible, contrairement à la projection.
            self._record_stat_change(
                state,
                tag,
                previous_value,
                current_value,
                use_dormant_cache=True,
            )

    def _visit_tag_change(self, packet: TagChange) -> None:
        entity_id = _entity_id(packet.entity)
        if entity_id is None:
            return
        tag_value = _optional_protocol_int(getattr(packet, "tag", None))
        current_value = _optional_protocol_int(getattr(packet, "value", None))
        if tag_value is None or current_value is None:
            return
        try:
            tag = GameTag(tag_value)
        except ValueError:
            return
        state = self.entities.setdefault(entity_id, _EntityState(entity_id))
        previous_zone = state.zone
        previous_value = state.tags.get(tag)
        was_dormant = bool(state.tags.get(GameTag.DORMANT))
        state.tags[tag] = current_value

        if tag is GameTag.DORMANT and bool(previous_value) != bool(current_value):
            if current_value:
                state.dormant_projection_tags = {
                    cached_tag
                    for cached_tag in state.dormant_cached_tags
                    if cached_tag in {GameTag.ATK, GameTag.HEALTH, GameTag.DAMAGE}
                }
                state.dormant_restore_tags.clear()
            else:
                state.dormant_restore_tags = {
                    cached_tag
                    for cached_tag in state.dormant_cached_tags
                    if cached_tag in {GameTag.ATK, GameTag.HEALTH, GameTag.DAMAGE}
                }
                state.dormant_projection_tags.clear()
            self._record_dormant_change(state, was_dormant, bool(current_value))

        if tag is GameTag.CURRENT_PLAYER:
            if current_value:
                self.active_player_entity_id = entity_id
            elif self.active_player_entity_id == entity_id:
                self.active_player_entity_id = None
        elif tag is GameTag.TURN and entity_id == self.game_entity_id and current_value > 0:
            if not self.turns:
                # TURN=1 précède le mulligan dans les replays réels. Attendre
                # MAIN_READY évite de présenter la main initiale comme des pioches.
                self.pending_first_turn = current_value
            else:
                self._start_turn(current_value)
        elif tag is GameTag.STEP:
            if self.pending_first_turn is not None and current_value in {
                Step.MAIN_READY,
                Step.MAIN_RESOURCE,
                Step.MAIN_DRAW,
                Step.MAIN_START,
                Step.MAIN_START_TRIGGERS,
                Step.MAIN_ACTION,
            }:
                self._start_turn(self.pending_first_turn)
                self.pending_first_turn = None
            self._update_phase(current_value)
            if current_value == Step.MAIN_END:
                self._emit_explicit_end_turn()
        elif tag is GameTag.ZONE:
            self._handle_zone_change(state, previous_zone, state.zone)
        elif tag is GameTag.PLAYSTATE:
            self._emit_playstate(entity_id, current_value)

        if self.current_turn is not None and tag in {
            GameTag.DAMAGE,
            GameTag.ATK,
            GameTag.HEALTH,
            GameTag.SILENCED,
        }:
            technical_reason = self._technical_stat_transition(
                state,
                tag,
                previous_value,
                current_value,
            )
            self._record_stat_change(
                state,
                tag,
                previous_value,
                current_value,
                technical_reason=technical_reason,
            )

    def _technical_stat_transition(
        self,
        state: _EntityState,
        tag: GameTag,
        previous_value: int | None,
        current_value: int,
    ) -> str | None:
        if tag not in {GameTag.ATK, GameTag.HEALTH, GameTag.DAMAGE}:
            return None

        cached_value = state.dormant_cached_tags.get(tag)
        if state.tags.get(GameTag.DORMANT) and tag in state.dormant_projection_tags:
            state.dormant_projection_tags.discard(tag)
            if cached_value is not None and previous_value == cached_value:
                return "dormant_projection"
        elif not state.tags.get(GameTag.DORMANT) and tag in state.dormant_restore_tags:
            state.dormant_restore_tags.discard(tag)
            if cached_value is not None and current_value == cached_value:
                return "dormant_restore"

        if state.card_type is CardType.MINION and state.zone in {
            Zone.GRAVEYARD,
            Zone.REMOVEDFROMGAME,
        }:
            return "entity_left_play"
        return None

    def _record_dormant_change(
        self,
        state: _EntityState,
        previous_value: bool,
        current_value: bool,
    ) -> None:
        if self.current_turn is None:
            return
        side = self._side_for_entity(state.entity_id)
        target = self._reference(state.entity_id)
        source = self._explicit_source_for_target(state.entity_id)
        sequence = self._reserve_sequence()
        metadata: dict[str, object] = {
            "entity_id": state.entity_id,
            "tag": GameTag.DORMANT.name,
            "before": previous_value,
            "after": current_value,
            "phase": self.current_phase.value,
        }
        if source is not None:
            metadata["source_explicit"] = True
        self.current_turn.entity_deltas.append(
            EntityDelta(
                sequence=sequence,
                entity_id=state.entity_id,
                side=side,
                phase=self.current_phase,
                attribute="dormant",
                value=ValueDelta(before=previous_value, after=current_value),
                card=target,
                source_card=source,
                metadata={
                    key: value
                    for key, value in metadata.items()
                    if isinstance(value, (str, int, bool)) or value is None
                },
            )
        )
        action_type = ActionType.BECOMES_DORMANT if current_value else ActionType.AWAKENS
        description = (
            f"{target.name} passe à l’état Dormant."
            if current_value
            else f"{target.name} se réveille."
        )
        self._emit(
            action_type,
            side,
            description,
            source_card=source,
            target_card=target,
            metadata=metadata,
            sequence=sequence,
        )

    def _update_phase(self, raw_step: object) -> None:
        step = _enum_or_default(Step, raw_step, Step.INVALID)
        if step is Step.MAIN_READY:
            # Les changements suivants seront visibles à l'ouverture des actions.
            self.current_phase = TurnPhase.ACTION_PHASE_START
        elif step is Step.MAIN_ACTION:
            self.current_phase = TurnPhase.ACTION_PHASE_END
        elif step is Step.MAIN_END:
            self.current_phase = TurnPhase.TURN_END
        elif step in {Step.MAIN_CLEANUP, Step.MAIN_NEXT, Step.FINAL_WRAPUP}:
            self.current_phase = TurnPhase.UNKNOWN

    def _start_turn(self, turn_number: int) -> None:
        self._finish_current_turn()
        side = self._side_for_entity(self.active_player_entity_id)
        if side is PlayerSide.SYSTEM:
            self.warnings.append(
                ParseWarning(
                    code="joueur_actif_inconnu",
                    message=f"Le joueur actif du demi-tour {turn_number} est inconnu.",
                )
            )
        turn = TurnState(
            turn_number=turn_number,
            round_number=(turn_number + 1) // 2,
            active_player=side,
        )
        self.turns.append(turn)
        self.current_turn = turn
        self._emit(
            ActionType.START_TURN,
            side,
            f"Début du tour de {_side_label(side)}.",
            destination=turn,
        )

    def _finish_current_turn(self) -> None:
        """Fermer le conteneur sans inventer un événement de fin de tour."""

        self.current_turn = None
        self.current_phase = TurnPhase.UNKNOWN

    def _emit_explicit_end_turn(self) -> None:
        if self.current_turn is None:
            return
        turn = self.current_turn
        if not any(action.action_type is ActionType.END_TURN for action in turn.actions):
            self._emit(
                ActionType.END_TURN,
                turn.active_player,
                f"Fin du tour de {_side_label(turn.active_player)}.",
                destination=turn,
            )

    def _handle_zone_change(
        self,
        state: _EntityState,
        previous_zone: Zone,
        current_zone: Zone,
    ) -> None:
        if previous_zone is current_zone:
            return
        side = self._side_for_entity(state.entity_id)
        card = self._reference(state.entity_id)
        if current_zone is Zone.HAND and self.current_turn is not None:
            if previous_zone is Zone.DECK:
                description = (
                    f"{_side_label(side)} pioche {card.name}."
                    if card.visibility is Visibility.KNOWN
                    else f"{_side_label(side)} pioche une carte inconnue."
                )
                self._emit(
                    ActionType.DRAW,
                    side,
                    description,
                    target_card=card,
                    metadata={
                        "entity_id": state.entity_id,
                        "from_zone": previous_zone.name,
                        "to_zone": current_zone.name,
                    },
                )
            else:
                description = (
                    f"{card.name} est ajoutée à la main de {_side_label(side)}."
                    if card.visibility is Visibility.KNOWN
                    else f"Une carte inconnue est ajoutée à la main de {_side_label(side)}."
                )
                self._emit(
                    ActionType.ADD_TO_HAND,
                    side,
                    description,
                    target_card=card,
                    metadata={
                        "entity_id": state.entity_id,
                        "from_zone": previous_zone.name,
                        "to_zone": current_zone.name,
                    },
                )
        elif (
            current_zone is Zone.PLAY
            and previous_zone is not Zone.PLAY
            and state.card_type is CardType.MINION
            and self.current_turn is not None
        ):
            source = self._explicit_source_for_target(state.entity_id)
            metadata: dict[str, object] = {
                "entity_id": state.entity_id,
                "from_zone": previous_zone.name,
                "to_zone": current_zone.name,
            }
            if source is not None:
                metadata["source_explicit"] = True
            self._emit(
                ActionType.SUMMON,
                side,
                f"{card.name} est invoqué.",
                source_card=source,
                target_card=card,
                metadata=metadata,
            )
        elif (
            current_zone is Zone.DECK
            and previous_zone not in {Zone.INVALID, Zone.DECK}
            and self.current_turn is not None
        ):
            self._emit(
                ActionType.SHUFFLE_INTO_DECK,
                side,
                f"{card.name} est mélangée dans le deck {_possessive_side(side)}.",
                target_card=card,
                metadata={
                    "entity_id": state.entity_id,
                    "from_zone": previous_zone.name,
                    "to_zone": current_zone.name,
                },
            )
        elif (
            current_zone is Zone.GRAVEYARD
            and previous_zone is Zone.PLAY
            and state.tags.get(GameTag.SECRET)
            and self.current_turn is not None
        ):
            self._emit(
                ActionType.REVEAL_SECRET,
                side,
                f"Le secret {card.name} est révélé.",
                target_card=card,
                metadata={
                    "entity_id": state.entity_id,
                    "from_zone": previous_zone.name,
                    "to_zone": current_zone.name,
                },
            )
        elif (
            current_zone is Zone.GRAVEYARD
            and previous_zone is Zone.PLAY
            and (self.death_depth or state.card_type is CardType.MINION)
        ):
            self.deaths_emitted += 1
            self._emit(
                ActionType.DEATH,
                side,
                f"{card.name} meurt.",
                target_card=card,
                metadata={
                    "block_type": BlockType.DEATHS.name,
                    "entity_id": state.entity_id,
                    "from_zone": previous_zone.name,
                    "to_zone": current_zone.name,
                },
            )

    def _record_stat_change(
        self,
        state: _EntityState,
        tag: GameTag,
        previous_value: int | None,
        current_value: int,
        *,
        use_dormant_cache: bool = False,
        technical_reason: str | None = None,
    ) -> None:
        if self.current_turn is None:
            return
        if tag is GameTag.DAMAGE and previous_value is None:
            previous_value = 0
        if previous_value is None or previous_value == current_value:
            return

        side = self._side_for_entity(state.entity_id)
        target = self._reference(state.entity_id)
        source = self._explicit_source_for_target(state.entity_id)
        sequence = self._reserve_sequence()
        attribute = tag.name.lower()
        before: int | bool = previous_value
        after: int | bool = current_value
        action_type: ActionType | None = None
        description: str | None = None
        metadata: dict[str, object] = {
            "entity_id": state.entity_id,
            "tag": tag.name,
            "before": previous_value,
            "after": current_value,
            "delta": current_value - previous_value,
            "phase": self.current_phase.value,
        }
        technical = technical_reason is not None
        if technical_reason is not None:
            metadata["technical_reason"] = technical_reason
        if source is not None:
            metadata["source_explicit"] = True

        if tag is GameTag.DAMAGE:
            maximum = (
                state.dormant_cached_tags.get(
                    GameTag.HEALTH,
                    state.tags.get(GameTag.HEALTH),
                )
                if use_dormant_cache
                else state.tags.get(GameTag.HEALTH)
            )
            is_lethal_reset = (
                current_value < previous_value
                and maximum is not None
                and previous_value >= maximum
                and state.card_type is CardType.MINION
            )
            if maximum is not None:
                if is_lethal_reset:
                    before = previous_value
                    after = current_value
                    attribute = "damage_tag"
                else:
                    before = maximum - previous_value
                    after = maximum - current_value
                    attribute = "health"
                metadata["damage_tag_before"] = previous_value
                metadata["damage_tag_after"] = current_value
            amount = abs(current_value - previous_value)
            if current_value > previous_value:
                action_type = ActionType.DAMAGE
                description = (
                    f"{target.name} subit {amount} point{'s' if amount != 1 else ''} de dégâts."
                )
            elif not is_lethal_reset:
                action_type = ActionType.HEAL
                description = (
                    f"{target.name} récupère {amount} point{'s' if amount != 1 else ''} de vie."
                )
            else:
                metadata["technical_lethal_damage_reset"] = True
                technical = True
        elif tag in {GameTag.ATK, GameTag.HEALTH}:
            if state.card_type not in {CardType.MINION, CardType.HERO}:
                return
            effective_tags = state.dormant_cached_tags if use_dormant_cache else state.tags
            attack = effective_tags.get(GameTag.ATK)
            damage = effective_tags.get(GameTag.DAMAGE, 0)
            if tag is GameTag.ATK:
                before_stats = (
                    previous_value,
                    (effective_tags.get(GameTag.HEALTH) or 0) - damage,
                )
                after_stats = (
                    current_value,
                    (effective_tags.get(GameTag.HEALTH) or 0) - damage,
                )
                attribute = "attack"
            else:
                before_stats = (attack, previous_value - damage)
                after_stats = (attack, current_value - damage)
                attribute = "max_health"
            metadata["stats_before"] = _statline(before_stats)
            metadata["stats_after"] = _statline(after_stats)
            amount = abs(current_value - previous_value)
            if current_value > previous_value:
                action_type = ActionType.BUFF
                description = (
                    f"{target.name} passe de {metadata['stats_before']} à "
                    f"{metadata['stats_after']}."
                )
            else:
                action_type = ActionType.DEBUFF
                description = (
                    f"{target.name} passe de {metadata['stats_before']} à "
                    f"{metadata['stats_after']}."
                )
            metadata["amount"] = amount
        elif tag is GameTag.SILENCED and current_value:
            before = bool(previous_value)
            after = True
            attribute = "silenced"
            action_type = ActionType.SILENCE
            description = f"{target.name} est réduit au silence."

        self.current_turn.entity_deltas.append(
            EntityDelta(
                sequence=sequence,
                entity_id=state.entity_id,
                side=side,
                phase=self.current_phase,
                attribute=attribute,
                value=ValueDelta(
                    before=before,
                    after=after,
                    delta=(after - before)
                    if isinstance(before, int)
                    and not isinstance(before, bool)
                    and isinstance(after, int)
                    and not isinstance(after, bool)
                    else None,
                ),
                card=target,
                source_card=source,
                metadata={
                    key: value
                    for key, value in metadata.items()
                    if isinstance(value, (str, int, bool)) or value is None
                },
                technical=technical,
            )
        )
        if action_type is not None and description is not None:
            self._emit(
                action_type,
                side,
                description,
                source_card=source,
                target_card=target,
                metadata=metadata,
                sequence=sequence,
                technical=technical,
            )

    def _explicit_source_for_target(self, target_id: int) -> CardRef | None:
        for context in reversed(self.block_stack):
            if (
                context.target_id == target_id
                and context.entity_id is not None
                and context.entity_id != target_id
            ):
                return self._reference(context.entity_id)
        return None

    def _emit_creation_if_needed(
        self,
        state: _EntityState,
        *,
        observed_introduction: bool,
    ) -> None:
        if not observed_introduction or state.creation_emitted:
            return
        state.creation_emitted = True
        target = self._reference(state.entity_id)
        source = self._reference(state.creator) if state.creator is not None else None
        side = self._side_for_entity(state.entity_id)
        if source is not None and source.visibility is Visibility.KNOWN:
            description = f"{target.name} est créée par {source.name}."
        elif target.visibility is Visibility.KNOWN:
            description = f"{target.name} entre dans la partie."
        else:
            description = "Une carte inconnue entre dans la partie."
        self._emit(
            ActionType.CARD_CREATED,
            side,
            description,
            source_card=source,
            target_card=target,
            metadata={
                "entity_id": state.entity_id,
                "created_by_entity_id": state.creator,
                "event_type": "CARD_CREATED",
            },
            technical=target.technical,
        )

    def _emit_playstate(self, entity_id: int, value: int) -> None:
        try:
            playstate = PlayState(value)
        except ValueError:
            return
        side = self._side_for_entity(entity_id)
        if playstate is PlayState.WON:
            action_type = ActionType.VICTORY
            description = f"Victoire {_possessive_side(side)}."
        elif playstate is PlayState.LOST:
            action_type = ActionType.DEFEAT
            description = f"Défaite {_possessive_side(side)}."
        elif playstate is PlayState.CONCEDED:
            action_type = ActionType.CONCEDE
            description = f"{_side_label(side)} concède."
        else:
            return
        self._emit(action_type, side, description, metadata={"playstate": playstate.name})

    def _ingest_entity(
        self,
        entity_id: int,
        card_id: str | None,
        tags: list[tuple[int, int]],
        *,
        initial: bool,
    ) -> None:
        state = self.entities.setdefault(entity_id, _EntityState(entity_id))
        if card_id:
            state.raw_card_id = card_id
            state.observable_card_id = card_id
        self._apply_tags(state, tags)
        if initial and state.creator is not None:
            state.creation_emitted = True

    @staticmethod
    def _apply_tags(state: _EntityState, tags: list[tuple[int, int]]) -> None:
        for raw_tag, raw_value in tags:
            try:
                state.tags[GameTag(raw_tag)] = int(raw_value)
            except ValueError:
                continue

    def _reference(self, entity_id: int | None) -> CardRef:
        state = self.entities.get(entity_id) if entity_id is not None else None
        if state is None or not state.observable_card_id:
            reference = self.resolver.reference(
                None,
                entity_id=entity_id,
                visibility=Visibility.HIDDEN,
                created_by_entity_id=state.creator if state else None,
            )
        else:
            reference = self.resolver.reference(
                state.observable_card_id,
                entity_id=entity_id,
                visibility=Visibility.KNOWN,
                created_by_entity_id=state.creator,
            )
        if state is None or state.creator is None:
            return reference
        creator = self.entities.get(state.creator)
        return replace(
            reference,
            provenance=Provenance(
                creator_entity_id=state.creator,
                creator_card_id=creator.observable_card_id if creator is not None else None,
            ),
        )

    def _side_for_entity(self, entity_id: int | None) -> PlayerSide:
        if entity_id is None:
            return PlayerSide.SYSTEM
        if entity_id == self.player_entity_id:
            return PlayerSide.PLAYER
        if entity_id == self.opponent_entity_id:
            return PlayerSide.OPPONENT
        state = self.entities.get(entity_id)
        controller = state.controller if state else None
        player_id = self.player_ids.get(self.player_entity_id)
        opponent_id = self.player_ids.get(self.opponent_entity_id)
        if controller == player_id:
            return PlayerSide.PLAYER
        if controller == opponent_id:
            return PlayerSide.OPPONENT
        return PlayerSide.SYSTEM

    def _emit(
        self,
        action_type: ActionType,
        side: PlayerSide,
        description: str,
        *,
        timestamp: str | None = None,
        source_card: CardRef | None = None,
        target_card: CardRef | None = None,
        metadata: dict[str, object] | None = None,
        destination: TurnState | None = None,
        sequence: int | None = None,
        technical: bool = False,
    ) -> GameAction:
        if sequence is None:
            sequence = self._reserve_sequence()
        action = GameAction(
            sequence=sequence,
            action_type=action_type,
            player=side,
            description=description,
            timestamp=timestamp or self.last_timestamp,
            source_card=source_card,
            target_card=target_card,
            metadata=dict(metadata or {}),
            technical=technical,
        )
        target_turn = destination if destination is not None else self.current_turn
        if target_turn is None:
            self.start_events.append(action)
        else:
            target_turn.actions.append(action)
        if _is_important_action(action):
            self.important_events.append(action)
        if self.current_protocol_order is not None:
            self.action_protocol_orders[id(action)] = self.current_protocol_order
        return action

    def _reserve_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def _remember_timestamp(self, packet: object) -> None:
        timestamp = _timestamp(packet)
        if timestamp:
            self.last_timestamp = timestamp


_ORDERED_PACKET_TYPES = (
    Block,
    FullEntity,
    ShowEntity,
    ChangeEntity,
    HideEntity,
    TagChange,
    CachedTagForDormantChange,
)


def _packet_protocol_orders(context: ReplayContext) -> dict[int, int]:
    """Relier les paquets HearthSim à leur position dans la traversée XML canonique."""

    packets: list[object] = []

    def visit(items: object) -> None:
        for packet in items:
            if isinstance(packet, _ORDERED_PACKET_TYPES):
                packets.append(packet)
            children = getattr(packet, "packets", None)
            if children is not None:
                visit(children)

    visit(context.packet_tree)
    packet_signatures = [_packet_protocol_signature(packet) for packet in packets]
    xml_items = [
        (protocol_order, _xml_protocol_signature(element))
        for protocol_order, element in enumerate(context.game_xml.iter())
        if element.tag in {packet_type.__name__ for packet_type in _ORDERED_PACKET_TYPES}
    ]
    if packet_signatures != [signature for _, signature in xml_items]:
        return {}
    return {
        id(packet): protocol_order
        for packet, (protocol_order, _) in zip(packets, xml_items, strict=True)
    }


def _packet_protocol_signature(packet: object) -> tuple[object, ...]:
    packet_type = type(packet).__name__
    entity_id = _entity_id(getattr(packet, "entity", None))
    if isinstance(packet, Block):
        return (
            packet_type,
            entity_id,
            _optional_protocol_int(getattr(packet, "type", None)),
            _entity_id(getattr(packet, "target", None)) or 0,
        )
    if isinstance(packet, FullEntity):
        return (packet_type, entity_id, getattr(packet, "card_id", None) or None)
    if isinstance(packet, ShowEntity | ChangeEntity):
        return (packet_type, entity_id, getattr(packet, "card_id", None) or None)
    if isinstance(packet, HideEntity):
        return (packet_type, entity_id, _optional_protocol_int(getattr(packet, "zone", None)))
    if isinstance(packet, TagChange | CachedTagForDormantChange):
        return (
            packet_type,
            entity_id,
            _optional_protocol_int(getattr(packet, "tag", None)),
            _optional_protocol_int(getattr(packet, "value", None)),
        )
    return (packet_type,)


def _optional_protocol_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _xml_protocol_signature(element: object) -> tuple[object, ...]:
    packet_type = element.tag
    entity_attribute = "id" if packet_type == "FullEntity" else "entity"
    entity_id = _entity_id(element.attrib.get(entity_attribute))
    if packet_type == "Block":
        return (
            packet_type,
            entity_id,
            _integer_attribute(element, "type"),
            _integer_attribute(element, "target") or 0,
        )
    if packet_type in {"FullEntity", "ShowEntity", "ChangeEntity"}:
        return (packet_type, entity_id, element.attrib.get("cardID") or None)
    if packet_type == "HideEntity":
        return (packet_type, entity_id, _integer_attribute(element, "zone"))
    if packet_type in {"TagChange", "CachedTagForDormantChange"}:
        return (
            packet_type,
            entity_id,
            _integer_attribute(element, "tag"),
            _integer_attribute(element, "value"),
        )
    return (packet_type,)


def _integer_attribute(element: object, name: str) -> int | None:
    try:
        return int(element.attrib[name])
    except (KeyError, TypeError, ValueError):
        return None


def extract_timeline(
    context: ReplayContext,
    resolver: CardReferenceResolver,
    *,
    player_entity_id: int,
    opponent_entity_id: int,
) -> TimelineResult:
    """Extraire les demi-tours et actions sans rétro-révéler les entités cachées."""

    return _TimelineBuilder(
        context,
        resolver,
        player_entity_id,
        opponent_entity_id,
    ).build()


def _is_important_action(action: GameAction) -> bool:
    if action.technical or action.action_type not in _IMPORTANT_ACTIONS:
        return False
    if action.action_type is ActionType.ADD_TO_HAND:
        return (
            action.player is PlayerSide.PLAYER
            and action.target_card is not None
            and action.target_card.provenance is not None
        )
    return True


def gameplay_start_event_groups(
    events: list[GameAction],
) -> list[tuple[GameAction, int]]:
    """Construire la vue gameplay sans perdre les occurrences protocolaires brutes.

    Deux vraies résolutions restent deux lignes. Une occurrence qui ne fait que
    révéler la même source est rattachée à la résolution substantielle et compte
    dans ``protocol_occurrences``. Ce signal structurel est volontairement plus
    strict qu'une fenêtre temporelle heuristique : deux triggers substantiels ne
    sont jamais fusionnés, même s'ils sont simultanés.
    """

    ordinary: list[tuple[GameAction, int]] = []
    grouped: dict[tuple[object, ...], list[GameAction]] = {}
    for action in events:
        if action.action_type is not ActionType.START_GAME_EFFECT:
            ordinary.append((action, 1))
            continue
        key = (
            action.action_type,
            action.player,
            action.source_card.entity_id if action.source_card else None,
            action.source_card.card_id if action.source_card else None,
            action.target_card.entity_id if action.target_card else None,
            action.target_card.card_id if action.target_card else None,
            action.description,
        )
        grouped.setdefault(key, []).append(action)

    result = ordinary
    for actions in grouped.values():
        substantive = [
            action for action in actions if not action.metadata.get("protocol_only_reveal")
        ]
        reveal_only_count = len(actions) - len(substantive)
        if substantive:
            result.append((substantive[0], 1 + reveal_only_count))
            result.extend((action, 1) for action in substantive[1:])
        else:
            result.append((actions[0], len(actions)))
    return sorted(result, key=lambda item: item[0].sequence)


def _timestamp(packet: object) -> str | None:
    value = getattr(packet, "ts", None)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _entity_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value or None
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed or None
    if isinstance(value, dict):
        return _entity_id(value.get("id") or value.get("entity"))
    return _entity_id(getattr(value, "id", None)) if value is not None else None


def _block_reveals_entity(block: Block, entity_id: int | None) -> bool:
    if entity_id is None:
        return False
    for packet in block.packets:
        if (
            isinstance(packet, (ShowEntity, ChangeEntity))
            and _entity_id(packet.entity) == entity_id
        ):
            return bool(packet.card_id)
        if hasattr(packet, "packets") and _block_reveals_entity(packet, entity_id):
            return True
    return False


def _is_reveal_only_trigger(block: Block, entity_id: int | None) -> bool:
    if entity_id is None or len(block.packets) != 1:
        return False
    packet = block.packets[0]
    return isinstance(packet, ShowEntity) and _entity_id(packet.entity) == entity_id


def _enum_or_default(enum: type, value: object, default: object):
    try:
        return enum(value)
    except (TypeError, ValueError):
        return default


def _side_label(side: PlayerSide) -> str:
    if side is PlayerSide.PLAYER:
        return "JOUEUR"
    if side is PlayerSide.OPPONENT:
        return "ADVERSAIRE"
    return "SYSTÈME"


def _possessive_side(side: PlayerSide) -> str:
    if side is PlayerSide.PLAYER:
        return "du JOUEUR"
    if side is PlayerSide.OPPONENT:
        return "de l’ADVERSAIRE"
    return "du SYSTÈME"


def _statline(values: tuple[int | None, int | None]) -> str:
    attack, health = values
    return f"{'?' if attack is None else attack}/{'?' if health is None else health}"
