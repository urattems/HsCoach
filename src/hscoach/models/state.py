"""Modèles des états reconstruits de la partie."""

from __future__ import annotations

from dataclasses import dataclass, field

from hscoach.models.action import (
    Decision,
    GameAction,
    PlayerSide,
    RecordedChoice,
    TurnPhase,
)
from hscoach.models.card import CardRef, InformationSource


@dataclass(slots=True)
class MinionState:
    """Statistiques courantes connues d'un serviteur sur le plateau."""

    card: CardRef
    attack: int | None = None
    health: int | None = None
    max_health: int | None = None
    taunt: bool = False
    divine_shield: bool = False
    stealth: bool = False
    frozen: bool = False
    silenced: bool = False
    source: InformationSource = InformationSource.GAMESTATE_RECONSTRUCTED


@dataclass(slots=True)
class HeroState:
    """État courant connu du héros d'un côté."""

    card: CardRef | None = None
    health: int | None = None
    armor: int | None = None
    attack: int | None = None
    weapon: CardRef | None = None
    hero_power: CardRef | None = None


@dataclass(slots=True)
class SideState:
    """Main, plateau et ressources connus d'un côté."""

    side: PlayerSide
    mana_available: int | None = None
    mana_used: int | None = None
    hero: HeroState = field(default_factory=HeroState)
    hand: list[CardRef] = field(default_factory=list)
    hidden_hand_count: int = 0
    board: list[MinionState] = field(default_factory=list)


@dataclass(slots=True)
class BoardState:
    """Vue bilatérale à un instant déterminé."""

    player: SideState
    opponent: SideState
    source: InformationSource = InformationSource.GAMESTATE_RECONSTRUCTED


@dataclass(slots=True)
class ValueDelta:
    """Valeur objective avant/après et différence numérique si calculable."""

    before: int | str | bool | None
    after: int | str | bool | None
    delta: int | None = None


@dataclass(slots=True)
class EntityDelta:
    """Modification atomique observée sur une entité."""

    sequence: int
    entity_id: int
    side: PlayerSide
    phase: TurnPhase
    attribute: str
    value: ValueDelta
    card: CardRef | None = None
    source_card: CardRef | None = None
    information_source: InformationSource = InformationSource.REPLAY_EXPLICIT
    metadata: dict[str, int | str | bool | None] = field(default_factory=dict)


@dataclass(slots=True)
class HeroDelta:
    """Différences agrégées de santé, armure ou attaque d'un héros."""

    side: PlayerSide
    health: ValueDelta | None = None
    armor: ValueDelta | None = None
    attack: ValueDelta | None = None


@dataclass(slots=True)
class ManaDelta:
    """Différences de ressources d'un côté entre deux frontières."""

    side: PlayerSide
    available: ValueDelta | None = None
    used: ValueDelta | None = None


@dataclass(slots=True)
class ZoneDelta:
    """Déplacement objectif d'une entité entre deux zones visibles."""

    entity_id: int
    side: PlayerSide
    from_zone: str
    to_zone: str
    card: CardRef | None = None


@dataclass(slots=True)
class StateDelta:
    """Différence structurée entre deux snapshots temporels."""

    from_phase: TurnPhase
    to_phase: TurnPhase
    entities: list[EntityDelta] = field(default_factory=list)
    heroes: list[HeroDelta] = field(default_factory=list)
    mana: list[ManaDelta] = field(default_factory=list)
    zones: list[ZoneDelta] = field(default_factory=list)
    complete: bool = True


@dataclass(slots=True)
class TurnState:
    """État et événements d'un demi-tour joueur."""

    turn_number: int
    round_number: int
    active_player: PlayerSide
    turn_start_state: BoardState | None = None
    action_phase_start_state: BoardState | None = None
    actions: list[GameAction] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    choices: list[RecordedChoice] = field(default_factory=list)
    entity_deltas: list[EntityDelta] = field(default_factory=list)
    state_deltas: list[StateDelta] = field(default_factory=list)
    action_phase_end_state: BoardState | None = None
    turn_end_state: BoardState | None = None

    @property
    def start_state(self) -> BoardState | None:
        """Alias Python V1 : l'état de décision, anciennement nommé `start_state`."""

        return self.action_phase_start_state

    @property
    def end_state(self) -> BoardState | None:
        """Alias Python V1 : la fin de phase d'action, anciennement `end_state`."""

        return self.action_phase_end_state
