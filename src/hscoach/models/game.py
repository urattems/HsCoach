"""Modèles racine d'une analyse de partie."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

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


class KnowledgeStatus(StrEnum):
    """Niveau de connaissance factuelle d'une section reconstruite."""

    KNOWN = "known"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class Mulligan:
    """Mulligan du joueur, avec distinction explicite des incertitudes."""

    offered: list[CardRef] | None = None
    kept: list[CardRef] | None = None
    returned: list[CardRef] | None = None
    received: list[CardRef] | None = None
    status: KnowledgeStatus = KnowledgeStatus.UNKNOWN
    source: InformationSource = InformationSource.REPLAY_EXPLICIT

    def __post_init__(self) -> None:
        if self.status is KnowledgeStatus.UNKNOWN and any(
            value is not None for value in (self.offered, self.kept, self.returned, self.received)
        ):
            self.status = KnowledgeStatus.KNOWN

    @property
    def partially_reconstructed(self) -> bool:
        """Alias de lecture conservé pour les consommateurs Python V1."""

        return self.status is KnowledgeStatus.PARTIAL


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
    player_class: str = "Classe inconnue"
    opponent_class: str = "Classe inconnue"
    action_count: int = 0
    state_delta_count: int = 0
    buff_count: int = 0
    damage_count: int = 0
    heal_count: int = 0
    created_card_count: int = 0
    option_count: int = 0
    unknown_action_count: int = 0
    mulligan_status: KnowledgeStatus = KnowledgeStatus.UNKNOWN
    game_state_completeness: str = "unknown"


@dataclass(slots=True)
class GameAnalysis:
    """Agrégat complet exporté avec le schéma JSON 2.0."""

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
    schema_version: str = "2.0"
