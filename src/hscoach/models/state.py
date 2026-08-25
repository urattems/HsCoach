"""Modèles des états reconstruits de la partie."""

from __future__ import annotations

from dataclasses import dataclass, field

from hscoach.models.action import Decision, GameAction, PlayerSide
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
class TurnState:
    """État et événements d'un demi-tour joueur."""

    turn_number: int
    round_number: int
    active_player: PlayerSide
    start_state: BoardState | None = None
    actions: list[GameAction] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    end_state: BoardState | None = None
