"""Modèles racine d'une analyse de partie."""

from __future__ import annotations

from dataclasses import dataclass, field

from hscoach.models.action import GameAction, PlayerSide
from hscoach.models.card import CardRef, DeckCard, InformationSource
from hscoach.models.state import TurnState


@dataclass(slots=True)
class ParseWarning:
    """Limite ou ambiguïté rendue explicitement visible."""

    code: str
    message: str
    source: InformationSource = InformationSource.UNCERTAIN


@dataclass(slots=True)
class ReplayMetadata:
    """Métadonnées non sensibles du document et de la partie."""

    game_id: str
    build: str | None = None
    replay_version: str | None = None
    started_at: str | None = None
    game_format: str | None = None
    game_type: str | None = None
    scenario_id: str | None = None
    result: str = "Inconnu"
    turn_count: int = 0
    duration_seconds: float | None = None


@dataclass(slots=True)
class Player:
    """Joueur sans nom ni identifiant de compte partageable."""

    side: PlayerSide
    entity_id: int
    player_id: int
    card_class: str = "Classe inconnue"
    hero: CardRef | None = None
    deck: list[DeckCard] = field(default_factory=list)
    known_cards: list[CardRef] = field(default_factory=list)


@dataclass(slots=True)
class Mulligan:
    """Mulligan du joueur, avec distinction explicite des incertitudes."""

    offered: list[CardRef] = field(default_factory=list)
    kept: list[CardRef] = field(default_factory=list)
    returned: list[CardRef] = field(default_factory=list)
    received: list[CardRef] = field(default_factory=list)
    partially_reconstructed: bool = False
    source: InformationSource = InformationSource.REPLAY_EXPLICIT


@dataclass(slots=True)
class ReplayDiagnostics:
    """Compteurs factuels de la commande d'inspection."""

    valid: bool = True
    entity_count: int = 0
    event_count: int = 0
    turn_count: int = 0
    resolved_card_count: int = 0
    unresolved_card_count: int = 0
    has_player_deck: bool = False
    has_mulligan: bool = False
    has_options: bool = False


@dataclass(slots=True)
class GameAnalysis:
    """Agrégat complet exporté avec le schéma JSON 1.0."""

    metadata: ReplayMetadata
    player: Player
    opponent: Player
    mulligan: Mulligan = field(default_factory=Mulligan)
    start_of_game_events: list[GameAction] = field(default_factory=list)
    turns: list[TurnState] = field(default_factory=list)
    important_events: list[GameAction] = field(default_factory=list)
    unresolved_cards: list[str] = field(default_factory=list)
    warnings: list[ParseWarning] = field(default_factory=list)
    diagnostics: ReplayDiagnostics = field(default_factory=ReplayDiagnostics)
    schema_version: str = "1.0"
