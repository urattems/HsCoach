"""Résolution prudente des Card IDs en références partageables."""

from __future__ import annotations

from collections.abc import Mapping

from hscoach.models import Card, CardRef, InformationSource, Visibility
from hscoach.models.card import UnresolvedCards


class CardResolver:
    """Résoudre les cartes et enregistrer les identifiants absents de la base."""

    def __init__(
        self,
        cards_by_id: Mapping[str, Card],
        *,
        english_cards_by_id: Mapping[str, Card] | None = None,
        allow_en_fallback: bool = False,
        unresolved: UnresolvedCards | None = None,
    ) -> None:
        self.cards_by_id = dict(cards_by_id)
        self.english_cards_by_id = dict(english_cards_by_id or {})
        self.allow_en_fallback = allow_en_fallback
        self.unresolved = unresolved or UnresolvedCards()

    @property
    def unresolved_ids(self) -> tuple[str, ...]:
        """Identifiants non résolus dans un ordre déterministe."""

        return tuple(sorted(self.unresolved.ids))

    def resolve(self, card_id: str | None) -> Card:
        """Retourner une carte connue ou un substitut français explicite."""

        normalized_id = card_id.strip() if isinstance(card_id, str) else ""
        card = self.cards_by_id.get(normalized_id)
        if card is not None:
            if not card.unresolved:
                return card
            fallback = self._english_fallback(normalized_id)
            if fallback is not None:
                return fallback
            self.unresolved.add(normalized_id)
            return card

        fallback = self._english_fallback(normalized_id)
        if fallback is not None:
            return fallback

        self.unresolved.add(normalized_id)
        name = f"Carte inconnue [{normalized_id}]" if normalized_id else "Carte inconnue"
        return Card(id=normalized_id, name=name, unresolved=True)

    def _english_fallback(self, card_id: str) -> Card | None:
        if not self.allow_en_fallback:
            return None
        card = self.english_cards_by_id.get(card_id)
        if card is None or card.unresolved:
            return None
        return card

    def reference(
        self,
        card_id: str | None,
        *,
        entity_id: int | None = None,
        visibility: Visibility = Visibility.KNOWN,
        created_by_entity_id: int | None = None,
        source: InformationSource = InformationSource.REPLAY_EXPLICIT,
    ) -> CardRef:
        """Créer une référence sans jamais exposer l'identité d'une entité cachée."""

        if visibility == Visibility.HIDDEN:
            return CardRef(
                entity_id=entity_id,
                card_id=None,
                name="Carte inconnue",
                visibility=Visibility.HIDDEN,
                source=source,
                created_by_entity_id=created_by_entity_id,
            )

        card = self.resolve(card_id)
        return CardRef(
            entity_id=entity_id,
            card_id=card.id or None,
            name=card.name,
            visibility=Visibility.KNOWN,
            source=source,
            text=card.text,
            cost=card.cost,
            attack=card.attack,
            health=card.health,
            durability=card.durability,
            created_by_entity_id=created_by_entity_id,
        )
