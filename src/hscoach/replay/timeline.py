"""Extraction chronologique factuelle des paquets HSReplay.

Le ``PacketTree`` officiel reste la source de vérité.  Les références de cartes
sont figées au moment de chaque événement : révéler plus tard une entité adverse
ne modifie donc jamais une pioche auparavant cachée.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from hearthstone.enums import BlockType, CardType, GameTag, PlayState, Step, Zone
from hslog.packets import (
    Block,
    ChangeEntity,
    CreateGame,
    FullEntity,
    HideEntity,
    ShowEntity,
    TagChange,
)

from hscoach.models.action import ActionType, GameAction, PlayerSide, TurnPhase
from hscoach.models.card import CardRef, InformationSource, Visibility
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


@dataclass(slots=True)
class _EntityState:
    """État temporel minimal d'une entité du protocole."""

    entity_id: int
    raw_card_id: str | None = None
    observable_card_id: str | None = None
    tags: dict[GameTag, int] = field(default_factory=dict)
    creation_emitted: bool = False

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
    ActionType.CREATE_CARD,
    ActionType.DEATH,
    ActionType.PLAY_SECRET,
    ActionType.REVEAL_SECRET,
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
        self._remember_timestamp(packet)
        if isinstance(packet, Block):
            self._visit_block(packet)
        elif isinstance(packet, ShowEntity | FullEntity | ChangeEntity):
            self._visit_entity_packet(packet)
        elif isinstance(packet, HideEntity):
            self._visit_hide_entity(packet)
        elif isinstance(packet, TagChange):
            self._visit_tag_change(packet)
        elif hasattr(packet, "packets"):
            for child in packet.packets:
                self._visit(child)

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
                    f"{source.name} déclenche un effet de début de partie.",
                    timestamp=timestamp,
                    source_card=source,
                    metadata={"block_type": block_type.name},
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
        self._emit(
            action_type,
            side,
            description,
            timestamp=timestamp,
            source_card=source,
            target_card=target,
            metadata={
                "block_type": BlockType.PLAY.name,
                "entity_id": entity_id,
                "target_entity_id": target_id,
            },
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
        )

    def _visit_entity_packet(self, packet: ShowEntity | FullEntity | ChangeEntity) -> None:
        entity_id = _entity_id(packet.entity)
        if entity_id is None:
            return
        state = self.entities.setdefault(entity_id, _EntityState(entity_id))
        previous_zone = state.zone
        previous_ref = self._reference(entity_id)
        previous_id = state.raw_card_id

        card_id = (packet.card_id or "").strip() or None
        if isinstance(packet, ChangeEntity) or card_id:
            state.raw_card_id = card_id
            state.observable_card_id = card_id
        self._apply_tags(state, packet.tags)
        self._emit_creation_if_needed(state)
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

    def _visit_tag_change(self, packet: TagChange) -> None:
        entity_id = _entity_id(packet.entity)
        if entity_id is None:
            return
        try:
            tag = GameTag(packet.tag)
        except ValueError:
            return
        state = self.entities.setdefault(entity_id, _EntityState(entity_id))
        previous_zone = state.zone
        previous_value = state.tags.get(tag)
        state.tags[tag] = int(packet.value)

        if tag is GameTag.CURRENT_PLAYER:
            if packet.value:
                self.active_player_entity_id = entity_id
            elif self.active_player_entity_id == entity_id:
                self.active_player_entity_id = None
        elif tag is GameTag.TURN and entity_id == self.game_entity_id and packet.value > 0:
            if not self.turns:
                # TURN=1 précède le mulligan dans les replays réels. Attendre
                # MAIN_READY évite de présenter la main initiale comme des pioches.
                self.pending_first_turn = int(packet.value)
            else:
                self._start_turn(int(packet.value))
        elif tag is GameTag.STEP:
            if self.pending_first_turn is not None and packet.value in {
                Step.MAIN_READY,
                Step.MAIN_RESOURCE,
                Step.MAIN_DRAW,
                Step.MAIN_START,
                Step.MAIN_START_TRIGGERS,
                Step.MAIN_ACTION,
            }:
                self._start_turn(self.pending_first_turn)
                self.pending_first_turn = None
            self._update_phase(packet.value)
            if packet.value == Step.MAIN_END:
                self._emit_explicit_end_turn()
        elif tag is GameTag.ZONE:
            self._handle_zone_change(state, previous_zone, state.zone)
        elif tag is GameTag.CREATOR:
            self._emit_creation_if_needed(state)
        elif tag is GameTag.PLAYSTATE:
            self._emit_playstate(entity_id, int(packet.value))

        if self.current_turn is not None and tag in {
            GameTag.DAMAGE,
            GameTag.ATK,
            GameTag.HEALTH,
            GameTag.SILENCED,
        }:
            self._record_stat_change(state, tag, previous_value, int(packet.value))

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
        if source is not None:
            metadata["source_explicit"] = True

        if tag is GameTag.DAMAGE:
            maximum = state.tags.get(GameTag.HEALTH)
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
        elif tag in {GameTag.ATK, GameTag.HEALTH}:
            if state.card_type not in {CardType.MINION, CardType.HERO}:
                return
            attack = state.tags.get(GameTag.ATK)
            damage = state.tags.get(GameTag.DAMAGE, 0)
            if tag is GameTag.ATK:
                before_stats = (previous_value, (state.tags.get(GameTag.HEALTH) or 0) - damage)
                after_stats = (current_value, (state.tags.get(GameTag.HEALTH) or 0) - damage)
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

    def _emit_creation_if_needed(self, state: _EntityState) -> None:
        if state.creation_emitted or state.creator is None:
            return
        state.creation_emitted = True
        target = self._reference(state.entity_id)
        source = self._reference(state.creator)
        side = self._side_for_entity(state.entity_id)
        self._emit(
            ActionType.CREATE_CARD,
            side,
            f"{target.name} est créée par {source.name}.",
            source_card=source,
            target_card=target,
            metadata={
                "entity_id": state.entity_id,
                "created_by_entity_id": state.creator,
            },
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
            return self.resolver.reference(
                None,
                entity_id=entity_id,
                visibility=Visibility.HIDDEN,
                created_by_entity_id=state.creator if state else None,
            )
        return self.resolver.reference(
            state.observable_card_id,
            entity_id=entity_id,
            visibility=Visibility.KNOWN,
            created_by_entity_id=state.creator,
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
        )
        target_turn = destination if destination is not None else self.current_turn
        if target_turn is None:
            self.start_events.append(action)
        else:
            target_turn.actions.append(action)
        if action_type in _IMPORTANT_ACTIONS:
            self.important_events.append(action)
        return action

    def _reserve_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def _remember_timestamp(self, packet: object) -> None:
        timestamp = _timestamp(packet)
        if timestamp:
            self.last_timestamp = timestamp


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
