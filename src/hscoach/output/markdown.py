"""Rendu Markdown compact, factuel et intégralement partageable."""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from hscoach.exceptions import ExportError
from hscoach.models import (
    ActionType,
    BoardState,
    CardRef,
    Decision,
    EntityDelta,
    GameAnalysis,
    KnowledgeStatus,
    MinionState,
    PlayerSide,
    SideState,
    TurnPhase,
    Visibility,
)
from hscoach.privacy import assert_shareable_text
from hscoach.replay.timeline import gameplay_start_event_groups

MARKDOWN_FILENAME = "game_summary.md"


def export_markdown(
    analysis: GameAnalysis,
    output_directory: str | Path = Path("output"),
) -> Path:
    """Écrire atomiquement ``output/<game-id>/game_summary.md``."""

    from hscoach.output.json_export import report_directory_name

    report = render_markdown(analysis)
    root = Path(output_directory).expanduser().resolve()
    game_directory = (root / report_directory_name(analysis)).resolve()
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
            prefix=f".{MARKDOWN_FILENAME}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(report)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        destination = game_directory / MARKDOWN_FILENAME
        temporary_path.replace(destination)
        temporary_path = None
        return destination
    except ExportError:
        raise
    except OSError as exc:
        raise ExportError("Le rapport Markdown n’a pas pu être écrit.") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def render_markdown(analysis: GameAnalysis) -> str:
    """Transformer une analyse en rapport français lisible par un humain ou un LLM."""

    lines = [
        "# Replay Hearthstone",
        "",
        "## Résumé",
        "",
        f"- Joueur : {analysis.player.card_class}",
        f"- Adversaire : {analysis.opponent.card_class}",
        f"- Résultat : {analysis.metadata.result}",
        f"- Tours : {analysis.metadata.turn_count}",
        f"- Demi-tours enregistrés : {len(analysis.turns)}",
        f"- Durée : {_duration(analysis.metadata.duration_seconds)}",
        f"- Format : {analysis.metadata.game_format or 'Inconnu'}",
        f"- Type de partie : {analysis.metadata.game_type or 'Inconnu'}",
    ]
    if analysis.metadata.started_at:
        lines.append(f"- Début : {analysis.metadata.started_at}")

    _append_deck(lines, analysis)
    _append_mulligan(lines, analysis)
    _append_start_events(lines, analysis)
    _append_turns(lines, analysis)
    _append_important_events(lines, analysis)
    _append_unknowns(lines, analysis)
    _append_warnings(lines, analysis)

    report = "\n".join(lines).rstrip() + "\n"
    assert_shareable_text(report)
    return report


def _append_deck(lines: list[str], analysis: GameAnalysis) -> None:
    lines.extend(["", "## Deck du joueur", ""])
    if not analysis.player.deck:
        lines.append("Deck du joueur : non disponible dans le replay.")
    else:
        grouped: dict[int | None, list[tuple[str, int]]] = {}
        for deck_card in analysis.player.deck:
            grouped.setdefault(deck_card.card.cost, []).append(
                (deck_card.card.name, deck_card.count)
            )
        for cost in sorted(grouped, key=lambda value: (value is None, value or 0)):
            title = "Coût inconnu" if cost is None else f"{cost} mana"
            lines.extend([f"### {title}", ""])
            for name, count in sorted(grouped[cost], key=lambda item: item[0].casefold()):
                lines.append(f"- {count}× {name}")
            lines.append("")
        lines.pop()
    lines.extend(["", "Deck adverse complet : inconnu."])


def _append_mulligan(lines: list[str], analysis: GameAnalysis) -> None:
    mulligan = analysis.mulligan
    lines.extend(["", "## Mulligan", ""])
    if mulligan.status is KnowledgeStatus.PARTIAL:
        lines.extend(
            [
                "> Mulligan partiellement reconstruit : les catégories ambiguës restent signalées.",
                "",
            ]
        )
    elif mulligan.status is KnowledgeStatus.UNKNOWN:
        lines.extend(
            [
                "> Mulligan non déterminé : le replay ne contient pas assez de faits.",
                "",
            ]
        )
    for title, cards in (
        ("Main proposée", mulligan.offered),
        ("Cartes conservées", mulligan.kept),
        ("Cartes renvoyées", mulligan.returned),
        ("Cartes reçues", mulligan.received),
    ):
        lines.extend([f"### {title}", ""])
        lines.extend(_card_bullets(cards))
        lines.append("")
    lines.pop()


def _append_start_events(lines: list[str], analysis: GameAnalysis) -> None:
    lines.extend(["", "## Démarrage de la partie", ""])
    if not analysis.start_of_game_events:
        lines.append("Aucun effet de début de partie distinct n’a été observé.")
        return
    protocol = [
        action
        for action in analysis.start_of_game_events
        if action.action_type is ActionType.START_GAME
    ]
    gameplay = [
        (action, protocol_occurrences)
        for action, protocol_occurrences in gameplay_start_event_groups(
            analysis.start_of_game_events
        )
        if action.action_type is not ActionType.START_GAME and not _is_technical_action(action)
    ]
    lines.extend(["### Protocole", ""])
    lines.extend(f"- {action.description}" for action in protocol[:1])
    if not protocol:
        lines.append("- Début protocolaire non déterminé.")
    lines.extend(["", "### Effets de gameplay", ""])
    if not gameplay:
        lines.append("- Aucun effet de gameplay distinct observé.")
        return
    lines.extend(f"- {action.description}" for action, _ in gameplay)


def _append_turns(lines: list[str], analysis: GameAnalysis) -> None:
    lines.extend(["", "# Partie détaillée"])
    if not analysis.turns:
        lines.extend(["", "Aucun tour exploitable n’a été reconstruit."])
        return
    for turn in analysis.turns:
        side = _side_label(turn.active_player)
        lines.extend(
            [
                "",
                f"## Tour {turn.round_number} — {side}",
                "",
                "### Début du demi-tour",
                "",
            ]
        )
        lines.extend(_board_lines(turn.turn_start_state))
        lines.extend(["", "### Au moment de décider", ""])
        lines.extend(_board_lines(turn.action_phase_start_state))
        if turn.decisions:
            lines.extend(["", "### Décisions enregistrées", ""])
            lines.append(
                "> Ces options sont uniquement celles enregistrées par le client à cet instant ; "
                "elles ne représentent pas toutes les lignes stratégiques possibles."
            )
            for decision in turn.decisions:
                lines.extend(["", f"Décision {decision.sequence} :"])
                lines.extend(_decision_lines(decision))
        lines.extend(["", "### Actions effectuées", ""])
        actions = [
            action
            for action in turn.actions
            if action.action_type not in {ActionType.START_TURN, ActionType.END_TURN}
            and action.action_type
            not in {
                ActionType.DAMAGE,
                ActionType.HEAL,
                ActionType.BUFF,
                ActionType.DEBUFF,
                ActionType.SILENCE,
            }
            and not _is_technical_action(action)
        ]
        if actions:
            lines.extend(
                f"{index}. {action.description}" for index, action in enumerate(actions, 1)
            )
        else:
            lines.append("Aucune action classifiée entre le début et la fin de ce demi-tour.")
        lines.extend(["", "### Changements observés", ""])
        lines.extend(_observed_delta_lines(turn.entity_deltas))
        lines.extend(["", "### Fin de la phase d’action", ""])
        lines.extend(_board_lines(turn.action_phase_end_state))
        lines.extend(["", "### Après les déclenchements de fin de tour", ""])
        lines.extend(_board_lines(turn.turn_end_state))


def _append_important_events(lines: list[str], analysis: GameAnalysis) -> None:
    lines.extend(["", "# Événements importants", ""])
    if not analysis.important_events:
        lines.append("Aucun événement important supplémentaire n’a été isolé.")
        return
    descriptions = Counter(
        action.description
        for action in analysis.important_events
        if not _is_technical_action(action)
    )
    if not descriptions:
        lines.append("Aucun événement important supplémentaire n’a été isolé.")
        return
    for description, count in descriptions.items():
        prefix = f"{count}× " if count > 1 else ""
        lines.append(f"- {prefix}{description}")


def _append_unknowns(lines: list[str], analysis: GameAnalysis) -> None:
    lines.extend(["", "# Informations inconnues", ""])
    lines.append("- Le deck adverse complet est inconnu et n’est pas extrapolé.")
    if analysis.unresolved_cards:
        for card_id in sorted(analysis.unresolved_cards):
            lines.append(f"- Carte inconnue [{card_id}]")
    else:
        lines.append("- Aucun Card ID rencontré n’est resté irrésolu.")
    if any(
        state is not None and state.opponent.hidden_hand_count
        for turn in analysis.turns
        for state in (
            turn.turn_start_state,
            turn.action_phase_start_state,
            turn.action_phase_end_state,
            turn.turn_end_state,
        )
    ):
        lines.append(
            "- Les identités cachées de la main adverse restent inconnues au moment observé."
        )


def _append_warnings(lines: list[str], analysis: GameAnalysis) -> None:
    lines.extend(["", "# Avertissements du parseur", ""])
    if not analysis.warnings:
        lines.append("Aucun avertissement.")
        return
    lines.extend(f"- [{warning.code}] {warning.message}" for warning in analysis.warnings)


def _card_bullets(cards: Iterable[CardRef] | None) -> list[str]:
    if cards is None:
        return ["- Non déterminé."]
    values = [card.name for card in cards]
    return [f"- {value}" for value in values] if values else ["- Aucune."]


def _board_lines(state: BoardState | None) -> list[str]:
    if state is None:
        return ["État du board non disponible pour cet événement."]
    lines: list[str] = []
    for side in (state.player, state.opponent):
        lines.extend(_side_state_lines(side))
    return lines


def _side_state_lines(side: SideState) -> list[str]:
    hero = side.hero
    hero_parts: list[str] = []
    if hero.card:
        hero_parts.append(hero.card.name)
    if hero.health is not None:
        hero_parts.append(f"{hero.health} PV")
    if hero.armor:
        hero_parts.append(f"{hero.armor} armure")
    if hero.attack:
        hero_parts.append(f"{hero.attack} ATQ")
    if hero.weapon:
        weapon = hero.weapon.name
        if hero.weapon.durability is not None:
            weapon += f" ({hero.weapon.durability} durabilité)"
        hero_parts.append(f"arme : {weapon}")
    if hero.hero_power:
        hero_parts.append(f"pouvoir : {hero.hero_power.name}")

    return [
        f"- {_side_label(side.side)}",
        f"  - Mana : {_mana(side)}",
        f"  - Héros : {', '.join(hero_parts) if hero_parts else 'état inconnu'}",
        f"  - Main : {_hand(side)}",
        f"  - Board : {_board(side.board)}",
    ]


def _mana(side: SideState) -> str:
    if side.mana_available is None and side.mana_used is None:
        return "inconnu"
    available = "?" if side.mana_available is None else str(side.mana_available)
    used = "?" if side.mana_used is None else str(side.mana_used)
    return f"{available} disponible, {used} utilisé"


def _hand(side: SideState) -> str:
    known = [card.name for card in side.hand if card.visibility is Visibility.KNOWN]
    hidden_in_list = sum(card.visibility is Visibility.HIDDEN for card in side.hand)
    hidden = max(side.hidden_hand_count, hidden_in_list)
    parts = [*known]
    if hidden:
        parts.append(f"{hidden} {'carte inconnue' if hidden == 1 else 'cartes inconnues'}")
    return ", ".join(parts) if parts else "vide"


def _board(minions: Iterable[MinionState]) -> str:
    values = [_minion(minion) for minion in minions]
    return "; ".join(values) if values else "vide"


def _minion(minion: MinionState) -> str:
    attack = "?" if minion.attack is None else str(minion.attack)
    health = "?" if minion.health is None else str(minion.health)
    mechanics = [
        label
        for enabled, label in (
            (minion.taunt, "Provocation"),
            (minion.divine_shield, "Bouclier divin"),
            (minion.stealth, "Camouflage"),
            (minion.frozen, "Gelé"),
            (minion.silenced, "Réduit au silence"),
            (minion.dormant, "Dormant"),
        )
        if enabled
    ]
    suffix = f" ({', '.join(mechanics)})" if mechanics else ""
    return f"{minion.card.name} — {attack}/{health}{suffix}"


def _decision_lines(decision: Decision) -> list[str]:
    chosen = [
        option
        for option in decision.options
        if option.selected and not _is_invalid_end_turn_marker(option)
    ]
    available = [
        option
        for option in decision.options
        if option.available and not option.selected and not _is_invalid_end_turn_marker(option)
    ]
    unavailable = [
        option
        for option in decision.options
        if not option.available and not option.selected and not _is_invalid_end_turn_marker(option)
    ]
    if not chosen and not available and not unavailable:
        return ["- Aucune option exploitable enregistrée."]
    lines: list[str] = []
    for option in chosen:
        lines.append(f"- Action choisie : {option.description}")
    for option in available:
        lines.append(f"- Option disponible : {option.description}")
    if unavailable:
        count = len(unavailable)
        lines.append(
            f"- {count} option{'s' if count != 1 else ''} indisponible"
            f"{'s' if count != 1 else ''} enregistrée{'s' if count != 1 else ''} "
            "(détails bruts dans le JSON)."
        )
    if (
        decision.selected_option_index is not None
        and decision.selected_option_index != 0
        and not any(option.selected for option in chosen)
    ):
        lines.append(f"- Action choisie : option {decision.selected_option_index}")
    return lines


def _is_invalid_end_turn_marker(option: object) -> bool:
    return (
        getattr(option, "option_type", None) == "Fin du tour"
        and getattr(option, "error", None) == "INVALID"
    )


def _observed_delta_lines(deltas: list[EntityDelta]) -> list[str]:
    visible_deltas = [
        delta
        for delta in deltas
        if not delta.technical
        and not delta.metadata.get("technical_lethal_damage_reset")
        and delta.attribute != "dormant"
    ]
    if not visible_deltas:
        return ["Aucun changement atomique supplémentaire classifié."]
    lines: list[str] = []
    for delta in visible_deltas:
        before = delta.value.before
        after = delta.value.after
        difference = delta.value.delta
        suffix = f" ({difference:+d})" if difference is not None else ""
        phase = {
            TurnPhase.ACTION_PHASE_START: "avant les actions",
            TurnPhase.ACTION_PHASE_END: "pendant les actions",
            TurnPhase.TURN_END: "après la fin des actions",
            TurnPhase.TURN_START: "au début du demi-tour",
            TurnPhase.UNKNOWN: "phase non déterminée",
        }[delta.phase]
        card = delta.card.name if delta.card is not None else f"Entité {delta.entity_id}"
        attribute = {
            "attack": "attaque",
            "health": "points de vie",
            "max_health": "points de vie maximum",
            "damage_tag": "marqueur de dégâts technique",
            "silenced": "silence",
        }.get(delta.attribute, delta.attribute)
        source = (
            f", source explicite : {delta.source_card.name}"
            if delta.source_card is not None and not delta.source_card.technical
            else ""
        )
        lines.append(f"- [{phase}] {card} — {attribute} : {before} → {after}{suffix}{source}.")
    return lines


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "Inconnue"
    rounded = max(0, round(seconds))
    minutes, remaining = divmod(rounded, 60)
    return f"{minutes} min {remaining:02d} s" if minutes else f"{remaining} s"


def _is_technical_action(action: object) -> bool:
    target = getattr(action, "target_card", None)
    return bool(getattr(action, "technical", False)) or (
        getattr(action, "action_type", None) is ActionType.CARD_CREATED
        and target is not None
        and (target.technical or target.card_type == "ENCHANTMENT")
    )


def _side_label(side: PlayerSide) -> str:
    return {
        PlayerSide.PLAYER: "JOUEUR",
        PlayerSide.OPPONENT: "ADVERSAIRE",
        PlayerSide.SYSTEM: "SYSTÈME",
    }[side]
