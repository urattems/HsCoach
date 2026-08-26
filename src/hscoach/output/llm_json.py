"""Export JSON compact, factuel et déterministe destiné aux LLM."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from hscoach.exceptions import ExportError
from hscoach.models import CardRef, GameAnalysis, InformationSource
from hscoach.output.json_export import safe_game_id
from hscoach.privacy import assert_shareable_text
from hscoach.replay.timeline import gameplay_start_event_groups

LLM_JSON_FILENAME = "game_llm.json"
LLM_SCHEMA = "hscoach-llm/1.0"

__all__ = [
    "LLM_JSON_FILENAME",
    "LLM_SCHEMA",
    "analysis_to_llm_dict",
    "export_llm_json",
    "render_llm_json",
]


class _CompactDocument:
    """Construire le document en dédupliquant les données statiques des cartes."""

    def __init__(self) -> None:
        self.card_definitions: dict[str, dict[str, Any]] = {}
        self.entities: dict[str, dict[str, Any]] = {}
        self.exported_sequences: set[int] = set()

    def build(self, analysis: GameAnalysis) -> dict[str, Any]:
        game = {
            "id": analysis.metadata.game_id,
            "build": analysis.metadata.build,
            "replay_version": analysis.metadata.replay_version,
            "started_at": analysis.metadata.started_at,
            "format": analysis.metadata.game_format,
            "type": analysis.metadata.game_type,
            "result": analysis.metadata.result,
            "full_turns": analysis.metadata.turn_count,
            "duration_seconds": analysis.metadata.duration_seconds,
            "player": self._player(analysis.player),
            "opponent": self._player(analysis.opponent),
        }
        deck = [
            {"card": self._ref(item.card), "count": item.count} for item in analysis.player.deck
        ]
        mulligan = {
            "status": analysis.mulligan.status.value,
            "offered": self._refs_or_none(analysis.mulligan.offered),
            "kept": self._refs_or_none(analysis.mulligan.kept),
            "returned": self._refs_or_none(analysis.mulligan.returned),
            "received": self._refs_or_none(analysis.mulligan.received),
            "source": analysis.mulligan.source.value,
        }
        start_events = []
        for action, protocol_occurrences in gameplay_start_event_groups(
            analysis.start_of_game_events
        ):
            if action.technical:
                continue
            rendered_action = self._action(action)
            if protocol_occurrences > 1:
                details = dict(rendered_action.get("details", {}))
                details["protocol_occurrences"] = protocol_occurrences
                rendered_action["details"] = details
            start_events.append(rendered_action)
        turns = [self._turn(turn) for turn in analysis.turns]
        # Les événements importants sont déjà présents dans les événements de début
        # ou les tours. Leurs séquences évitent une deuxième copie intégrale.
        important = [
            action.sequence
            for action in analysis.important_events
            if not action.technical and action.sequence in self.exported_sequences
        ]
        warnings = [
            {
                "code": warning.code,
                "message": warning.message,
                "source": warning.source.value,
            }
            for warning in analysis.warnings
        ]

        return {
            "schema_version": LLM_SCHEMA,
            "game": _drop_none(game),
            "cards": {
                "definitions": dict(sorted(self.card_definitions.items())),
                "entities": dict(sorted(self.entities.items())),
            },
            "player_deck": deck,
            "mulligan": mulligan,
            "start_of_game_events": start_events,
            "turns": turns,
            "important_events": important,
            "warnings": warnings,
        }

    def _player(self, player: Any) -> dict[str, Any]:
        known_cards = [self._ref(card) for card in player.known_cards if not card.technical]
        return _drop_none(
            {
                "side": player.side.value,
                "class": player.card_class,
                "hero": self._ref(player.hero),
                "known_cards": known_cards,
            }
        )

    def _turn(self, turn: Any) -> dict[str, Any]:
        phase_changes = [self._state_delta(delta) for delta in turn.state_deltas]
        result = {
            "half_turn": turn.turn_number,
            "round": turn.round_number,
            "active_player": turn.active_player.value,
            "action_phase_start": self._state(turn.action_phase_start_state),
            "actions": [self._action(action) for action in turn.actions if not action.technical],
            "state_changes": {
                "entity_changes": [
                    self._entity_delta(delta) for delta in turn.entity_deltas if not delta.technical
                ],
                "phase_changes": phase_changes,
            },
            "action_phase_end": self._boundary(
                turn.action_phase_end_state,
                base="action_phase_start",
                phase="action_phase_end",
                phase_changes=phase_changes,
            ),
            "turn_end": self._boundary(
                turn.turn_end_state,
                base="action_phase_end",
                phase="turn_end",
                phase_changes=phase_changes,
            ),
            "decisions": [self._decision(decision) for decision in turn.decisions],
        }
        if turn.choices:
            result["choices"] = [self._choice(choice) for choice in turn.choices]
        return result

    def _boundary(
        self,
        state: Any,
        *,
        base: str,
        phase: str,
        phase_changes: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if state is None:
            return None
        if any(change["from"] == base and change["to"] == phase for change in phase_changes):
            return {"base": base, "apply_phase_change_to": phase}
        # Un snapshot disponible sans delta correspondant reste préférable à une
        # relation inventée. Ce repli ne duplique que les replays incomplets.
        return self._state(state)

    def _state(self, state: Any) -> dict[str, Any] | None:
        if state is None:
            return None
        return {
            "player": self._side_state(state.player),
            "opponent": self._side_state(state.opponent),
            **(
                {"source": state.source.value}
                if state.source is not InformationSource.GAMESTATE_RECONSTRUCTED
                else {}
            ),
        }

    def _side_state(self, state: Any) -> dict[str, Any]:
        hero = _drop_none(
            {
                "card": self._ref(state.hero.card),
                "health": state.hero.health,
                "armor": state.hero.armor,
                "attack": state.hero.attack,
                "weapon": self._ref(state.hero.weapon),
                "hero_power": self._ref(state.hero.hero_power),
            }
        )
        board = []
        for minion in state.board:
            flags = [
                name
                for name in (
                    "taunt",
                    "divine_shield",
                    "stealth",
                    "frozen",
                    "silenced",
                    "dormant",
                )
                if getattr(minion, name)
            ]
            board.append(
                _drop_none(
                    {
                        "card": self._ref(minion.card),
                        "attack": minion.attack,
                        "health": minion.health,
                        "max_health": minion.max_health,
                        "flags": flags or None,
                        "source": (
                            minion.source.value
                            if minion.source is not InformationSource.GAMESTATE_RECONSTRUCTED
                            else None
                        ),
                    }
                )
            )
        return _drop_none(
            {
                "mana": [state.mana_available, state.mana_used],
                "hero": hero,
                "hand": [self._ref(card) for card in state.hand],
                "hidden_hand": state.hidden_hand_count,
                "board": board,
            }
        )

    def _action(self, action: Any) -> dict[str, Any]:
        self.exported_sequences.add(action.sequence)
        description = None
        if action.source_card is None and action.target_card is None:
            description = action.description
        return _drop_none(
            {
                "seq": action.sequence,
                "type": action.action_type.value,
                "player": action.player.value,
                "description": description,
                "source_card": self._ref(action.source_card),
                "target_card": self._ref(action.target_card),
                "information_source": (
                    action.information_source.value
                    if action.information_source is not InformationSource.REPLAY_EXPLICIT
                    else None
                ),
                "details": _compact_action_metadata(action.metadata) or None,
            }
        )

    def _decision(self, decision: Any) -> dict[str, Any]:
        options: dict[str, list[list[Any]]] = {
            "available": [],
            "unavailable": [],
            "chosen": [],
        }
        for option in decision.options:
            status = "chosen" if option.selected else "available"
            if not option.available:
                status = "unavailable"
            # Chaque ligne vaut [index, type, entity, targets, raw_error].
            options[status].append(
                [
                    option.index,
                    option.option_type,
                    self._ref(option.entity),
                    [self._ref(target) for target in option.targets],
                    option.error,
                ]
            )
        return _drop_none(
            {
                "seq": decision.sequence,
                "options": {status: rows for status, rows in options.items() if rows},
                "selected_option": decision.selected_option_index,
                "selected_suboption": decision.selected_suboption_index,
                "selected_target_entity": decision.selected_target_entity_id,
                "selected_position": decision.selected_position,
            }
        )

    def _choice(self, choice: Any) -> dict[str, Any]:
        return _drop_none(
            {
                "seq": choice.sequence,
                "type": choice.choice_type,
                "player": choice.player.value,
                "offered": [self._ref(card) for card in choice.offered],
                "chosen": [self._ref(card) for card in choice.chosen],
                "source_card": self._ref(choice.source_card),
                "completed": choice.completed,
            }
        )

    def _entity_delta(self, delta: Any) -> list[Any]:
        # [seq, entity, side, phase, attribute, before, after, delta,
        #  card, explicit_source_card, information_source, metadata]
        card_reference = self._ref(delta.card)
        if delta.card is not None and delta.card.entity_id == delta.entity_id:
            card_reference = None
        information_source = (
            delta.information_source.value
            if delta.information_source is not InformationSource.REPLAY_EXPLICIT
            else None
        )
        result = [
            delta.sequence,
            delta.entity_id,
            delta.side.value,
            delta.phase.value,
            delta.attribute,
            delta.value.before,
            delta.value.after,
            delta.value.delta,
            card_reference,
            self._ref(delta.source_card),
            information_source,
            _compact_entity_metadata(delta.metadata) or None,
        ]
        while result and result[-1] is None:
            result.pop()
        return result

    def _state_delta(self, delta: Any) -> dict[str, Any]:
        return _drop_none(
            {
                "from": delta.from_phase.value,
                "to": delta.to_phase.value,
                "complete": delta.complete,
                "entities": [
                    self._entity_delta(item) for item in delta.entities if not item.technical
                ]
                or None,
                "heroes": [self._hero_delta(item) for item in delta.heroes] or None,
                "mana": [self._mana_delta(item) for item in delta.mana] or None,
                "zones": [self._zone_delta(item) for item in delta.zones] or None,
            }
        )

    def _hero_delta(self, delta: Any) -> dict[str, Any]:
        return _drop_none(
            {
                "side": delta.side.value,
                "health": self._value_delta(delta.health),
                "armor": self._value_delta(delta.armor),
                "attack": self._value_delta(delta.attack),
            }
        )

    def _mana_delta(self, delta: Any) -> dict[str, Any]:
        return _drop_none(
            {
                "side": delta.side.value,
                "available": self._value_delta(delta.available),
                "used": self._value_delta(delta.used),
            }
        )

    def _zone_delta(self, delta: Any) -> dict[str, Any]:
        return _drop_none(
            {
                "entity": delta.entity_id,
                "side": delta.side.value,
                "from": delta.from_zone,
                "to": delta.to_zone,
                "card": self._ref(delta.card),
            }
        )

    @staticmethod
    def _value_delta(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        return _drop_none({"before": value.before, "after": value.after, "delta": value.delta})

    def _refs_or_none(self, cards: list[CardRef] | None) -> list[Any] | None:
        if cards is None:
            return None
        return [self._ref(card) for card in cards]

    def _ref(self, card: CardRef | None) -> int | str | None:
        if card is None or card.technical:
            return None
        key = self._card_key(card)
        entity_details = _drop_none(
            {
                "card": key,
                "source": card.source.value
                if card.source is not InformationSource.REPLAY_EXPLICIT
                else None,
                "provenance": self._provenance(card),
            }
        )
        if card.visibility.value == "hidden":
            self.card_definitions.setdefault(
                key, {"name": "Carte inconnue", "visibility": "hidden"}
            )
            reference = f"hidden:{card.entity_id}" if card.entity_id is not None else "hidden"
            self.entities.setdefault(reference, {"card": key, "visibility": "hidden"})
            return reference

        self.card_definitions.setdefault(key, self._card_definition(card))
        if card.entity_id is not None:
            entity_key = str(card.entity_id)
            existing = self.entities.get(entity_key)
            if existing is None:
                self.entities[entity_key] = entity_details
            else:
                existing_card = str(existing.get("card", ""))
                if existing_card.startswith(("entity:", "unknown:")) and not key.startswith(
                    ("entity:", "unknown:")
                ):
                    existing["card"] = key
                for field in ("source", "provenance"):
                    if field not in existing and field in entity_details:
                        existing[field] = entity_details[field]
            return card.entity_id
        return key

    @staticmethod
    def _provenance(card: CardRef) -> dict[str, Any] | None:
        provenance = card.provenance
        if provenance is None:
            return None
        return _drop_none(
            {
                "creator_entity_id": provenance.creator_entity_id,
                "creator_card_id": provenance.creator_card_id,
                "confidence": provenance.confidence.value,
            }
        )

    @staticmethod
    def _card_key(card: CardRef) -> str:
        if card.visibility.value == "hidden":
            return "hidden"
        if card.card_id:
            return card.card_id
        if card.entity_id is not None:
            return f"entity:{card.entity_id}"
        digest = hashlib.sha256(card.name.encode("utf-8")).hexdigest()[:12]
        return f"unknown:{digest}"

    @staticmethod
    def _card_definition(card: CardRef) -> dict[str, Any]:
        return _drop_none(
            {
                "name": card.name,
                "text": card.text,
                "type": card.card_type,
                "cost": card.cost,
                "attack": card.attack,
                "health": card.health,
                "durability": card.durability,
                "mechanics": list(card.mechanics) or None,
            }
        )


def analysis_to_llm_dict(analysis: GameAnalysis) -> dict[str, Any]:
    """Construire le document public conforme au schéma compact LLM 1.0."""

    return _CompactDocument().build(analysis)


def render_llm_json(analysis: GameAnalysis) -> str:
    """Sérialiser le document compact en JSON UTF-8 stable."""

    try:
        rendered = json.dumps(
            analysis_to_llm_dict(analysis),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ExportError("L'analyse LLM contient une valeur non sérialisable en JSON.") from exc
    rendered += "\n"
    assert_shareable_text(rendered)
    return rendered


def export_llm_json(
    analysis: GameAnalysis,
    output_directory: str | Path = Path("output"),
) -> Path:
    """Écrire atomiquement ``output/<game-id>/game_llm.json``."""

    rendered = render_llm_json(analysis)
    root = Path(output_directory).expanduser().resolve()
    game_directory = (root / safe_game_id(analysis.metadata.game_id)).resolve()
    if not game_directory.is_relative_to(root):
        raise ExportError("Le dossier de sortie calculé est situé hors du dossier autorisé.")

    temporary_path: Path | None = None
    try:
        game_directory.mkdir(parents=True, exist_ok=True)
        if not game_directory.resolve().is_relative_to(root):
            raise ExportError("Le dossier de sortie pointe hors du dossier autorisé.")
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=game_directory,
            prefix=f".{LLM_JSON_FILENAME}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(rendered)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        destination = game_directory / LLM_JSON_FILENAME
        temporary_path.replace(destination)
        temporary_path = None
        return destination
    except ExportError:
        raise
    except OSError as exc:
        raise ExportError("Le rapport JSON compact n'a pas pu être écrit.") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _drop_none(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


def _plain(value: Any) -> Any:
    """Convertir uniquement les métadonnées techniques en valeurs JSON stables."""

    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [_plain(item) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Type JSON non pris en charge : {type(value).__name__}")


def _compact_action_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Retirer les répétitions déjà portées par l'action et ses références."""

    redundant = {
        "after",
        "amount",
        "before",
        "delta",
        "entity_id",
        "phase",
        "source_explicit",
        "damage_tag_before",
        "damage_tag_after",
        "created_by_entity_id",
        "effect_index",
        "protocol_only_reveal",
        "trigger_keyword",
        "stats_after",
        "stats_before",
        "tag",
    }
    compact_keys = {
        "after": "a",
        "amount": "n",
        "before": "b",
        "block_type": "block",
        "created_by_entity_id": "created_by",
        "delta": "d",
        "from_zone": "from",
        "phase": "phase",
        "playstate": "result",
        "stats_after": "stats_after",
        "stats_before": "stats_before",
        "tag": "tag",
        "target_entity_id": "target",
        "to_zone": "to",
    }
    return {
        compact_keys.get(key, key): _plain(value)
        for key, value in metadata.items()
        if key not in redundant and value is not None
    }


def _compact_entity_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Conserver seulement les précisions absentes des colonnes du delta."""

    represented_by_delta = {
        "after",
        "amount",
        "before",
        "damage_tag_after",
        "damage_tag_before",
        "delta",
        "entity_id",
        "phase",
        "source_explicit",
        "stats_after",
        "stats_before",
        "tag",
    }
    return {
        key: _plain(value)
        for key, value in metadata.items()
        if key not in represented_by_delta and value is not None
    }
