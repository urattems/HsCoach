"""Modèles des cartes Hearthstone et de leurs références dans un replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Visibility(StrEnum):
    """Visibilité de l'identité d'une entité au moment observé."""

    KNOWN = "known"
    HIDDEN = "hidden"


class InformationSource(StrEnum):
    """Origine factuelle d'une information exportée."""

    REPLAY_EXPLICIT = "replay_explicit"
    GAMESTATE_RECONSTRUCTED = "gamestate_reconstructed"
    UNCERTAIN = "uncertain"


@dataclass(slots=True, frozen=True)
class Provenance:
    """Provenance historique explicitement portée par le replay.

    Une provenance n'est pas, à elle seule, la preuve qu'une carte vient d'être
    créée à l'instant où elle est observée.
    """

    creator_entity_id: int
    creator_card_id: str | None = None
    confidence: InformationSource = InformationSource.REPLAY_EXPLICIT


@dataclass(slots=True, frozen=True)
class Card:
    """Carte issue du fichier complet HearthstoneJSON d'une locale."""

    id: str
    name: str
    text: str | None = None
    cost: int | None = None
    attack: int | None = None
    health: int | None = None
    durability: int | None = None
    type: str | None = None
    card_class: str | None = None
    rarity: str | None = None
    mechanics: tuple[str, ...] = ()
    collectible: bool = False
    unresolved: bool = False


@dataclass(slots=True, frozen=True)
class CardRef:
    """Identité connue, cachée ou irrésolue d'une entité du replay."""

    entity_id: int | None
    card_id: str | None
    name: str
    visibility: Visibility = Visibility.KNOWN
    source: InformationSource = InformationSource.REPLAY_EXPLICIT
    text: str | None = None
    cost: int | None = None
    attack: int | None = None
    health: int | None = None
    durability: int | None = None
    card_type: str | None = None
    mechanics: tuple[str, ...] = ()
    created_by_entity_id: int | None = None
    provenance: Provenance | None = None
    technical: bool = False

    def __post_init__(self) -> None:
        """Conserver l'ancien identifiant tout en exposant la provenance V3."""

        if self.provenance is None and self.created_by_entity_id is not None:
            object.__setattr__(
                self,
                "provenance",
                Provenance(creator_entity_id=self.created_by_entity_id),
            )
        elif self.created_by_entity_id is None and self.provenance is not None:
            object.__setattr__(self, "created_by_entity_id", self.provenance.creator_entity_id)
        if self.card_type == "ENCHANTMENT" and not self.technical:
            object.__setattr__(self, "technical", True)


@dataclass(slots=True, frozen=True)
class DeckCard:
    """Carte et quantité dans une liste de deck explicite."""

    card: CardRef
    count: int = 1


@dataclass(slots=True)
class UnresolvedCards:
    """Registre déterministe des Card IDs non résolus."""

    ids: set[str] = field(default_factory=set)

    def add(self, card_id: str | None) -> None:
        """Ajouter un identifiant non vide."""

        if card_id:
            self.ids.add(card_id)
