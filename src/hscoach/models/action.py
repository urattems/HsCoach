"""Modèles des événements, choix et options du protocole."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hscoach.models.card import CardRef, InformationSource


class PlayerSide(StrEnum):
    """Côté interne, indépendant de tout identifiant Battle.net."""

    PLAYER = "PLAYER"
    OPPONENT = "OPPONENT"
    SYSTEM = "SYSTEM"


class TurnPhase(StrEnum):
    """Frontière temporelle explicite d'un demi-tour."""

    TURN_START = "turn_start"
    ACTION_PHASE_START = "action_phase_start"
    ACTION_PHASE_END = "action_phase_end"
    TURN_END = "turn_end"
    UNKNOWN = "unknown"


class ActionType(StrEnum):
    """Types factuels traduits pour les sorties utilisateur."""

    DRAW = "Pioche"
    PLAY_CARD = "Carte jouée"
    SUMMON = "Serviteur invoqué"
    CAST_SPELL = "Sort lancé"
    EQUIP_WEAPON = "Arme équipée"
    HERO_POWER = "Pouvoir héroïque utilisé"
    ATTACK = "Attaque"
    DAMAGE = "Dégâts"
    HEAL = "Soins"
    DEATH = "Mort"
    TRANSFORM = "Transformation"
    BUFF = "Amélioration"
    DEBUFF = "Affaiblissement"
    SILENCE = "Silence"
    DISCOVER = "Découverte"
    CHOICE = "Choix"
    CREATE_CARD = "Carte créée"
    ADD_TO_HAND = "Carte ajoutée en main"
    SHUFFLE_INTO_DECK = "Carte mélangée dans le deck"
    PLAY_SECRET = "Secret joué"
    REVEAL_SECRET = "Secret révélé"
    START_GAME = "Début de partie"
    START_GAME_EFFECT = "Effet au début de la partie"
    START_TURN = "Début du tour"
    END_TURN = "Fin du tour"
    FATIGUE = "Fatigue"
    VICTORY = "Victoire"
    DEFEAT = "Défaite"
    CONCEDE = "Concession"
    UNCLASSIFIED = "Événement non classifié"


@dataclass(slots=True)
class GameAction:
    """Événement ordonné extrait du replay."""

    sequence: int
    action_type: ActionType
    player: PlayerSide
    description: str
    timestamp: str | None = None
    source_card: CardRef | None = None
    target_card: CardRef | None = None
    information_source: InformationSource = InformationSource.REPLAY_EXPLICIT
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RecordedOption:
    """Option enregistrée par le client, sans prétention stratégique."""

    index: int
    option_type: str
    description: str
    entity: CardRef | None = None
    targets: list[CardRef] = field(default_factory=list)
    error: str | None = None
    available: bool = True
    selected: bool = False


@dataclass(slots=True)
class RecordedChoice:
    """Choix protocolaire offert puis, si disponible, réponse enregistrée."""

    sequence: int
    timestamp: str | None
    choice_type: str
    player: PlayerSide
    offered: list[CardRef] = field(default_factory=list)
    chosen: list[CardRef] = field(default_factory=list)
    source_card: CardRef | None = None
    completed: bool = False


@dataclass(slots=True)
class Decision:
    """Ensemble d'options et sélection envoyée par le client."""

    sequence: int
    timestamp: str | None
    options: list[RecordedOption] = field(default_factory=list)
    selected_option_index: int | None = None
    selected_suboption_index: int | None = None
    selected_target_entity_id: int | None = None
    selected_position: int | None = None
