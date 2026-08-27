"""Export JSON déterministe et partageable d'une analyse de replay."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from hscoach.exceptions import ExportError
from hscoach.models.game import GameAnalysis
from hscoach.privacy import assert_shareable_text

JSON_FILENAME = "game_analysis.json"
_SAFE_GAME_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?")
_WINDOWS_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)

__all__ = [
    "analysis_to_dict",
    "export_json",
    "render_json",
    "report_directory_name",
    "safe_game_id",
]


def analysis_to_dict(analysis: GameAnalysis) -> dict[str, Any]:
    """Construire le document public conforme au schéma JSON 2.0."""

    if analysis.schema_version != "2.0":
        raise ExportError(
            f"La version du schéma JSON n'est pas prise en charge : {analysis.schema_version!r}."
        )

    return {
        "schema_version": analysis.schema_version,
        "game": _to_json_value(analysis.metadata),
        "player": _to_json_value(analysis.player),
        "opponent": _to_json_value(analysis.opponent),
        "mulligan": _to_json_value(analysis.mulligan),
        "start_of_game_events": _to_json_value(analysis.start_of_game_events),
        "turns": _to_json_value(analysis.turns),
        "important_events": _to_json_value(analysis.important_events),
        "unresolved_cards": _to_json_value(analysis.unresolved_cards),
        "warnings": _to_json_value(analysis.warnings),
        "diagnostics": _to_json_value(analysis.diagnostics),
    }


def render_json(analysis: GameAnalysis) -> str:
    """Sérialiser une analyse en JSON UTF-8 stable, terminé par une nouvelle ligne."""

    try:
        rendered = json.dumps(
            analysis_to_dict(analysis),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ExportError("L'analyse contient une valeur non sérialisable en JSON.") from exc

    rendered += "\n"
    assert_shareable_text(rendered)
    return rendered


def export_json(
    analysis: GameAnalysis,
    output_directory: str | Path = Path("output"),
) -> Path:
    """Écrire atomiquement ``output/<game-id>/game_analysis.json``.

    Le chemin retourné est absolu. Un identifiant de partie inhabituel est remplacé
    par un identifiant opaque et déterministe afin qu'il ne puisse jamais influencer
    l'arborescence de sortie.
    """

    rendered = render_json(analysis)
    root = Path(output_directory).expanduser().resolve()
    game_directory = root / report_directory_name(analysis)
    resolved_game_directory = game_directory.resolve()

    if not resolved_game_directory.is_relative_to(root):
        raise ExportError("Le dossier de sortie calculé est situé hors du dossier autorisé.")

    temporary_path: Path | None = None
    try:
        resolved_game_directory.mkdir(parents=True, exist_ok=True)
        # Une cible existante qui est un lien peut sinon contourner la vérification
        # faite avant la création du dossier.
        if not resolved_game_directory.resolve().is_relative_to(root):
            raise ExportError("Le dossier de sortie pointe hors du dossier autorisé.")

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=resolved_game_directory,
            prefix=f".{JSON_FILENAME}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(rendered)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        destination = resolved_game_directory / JSON_FILENAME
        temporary_path.replace(destination)
        temporary_path = None
        return destination
    except ExportError:
        raise
    except OSError as exc:
        raise ExportError("Le rapport JSON n'a pas pu être écrit.") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def safe_game_id(game_id: str) -> str:
    """Retourner un nom de dossier déterministe sans séparateur ni traversal."""

    candidate = game_id.strip()
    windows_stem = candidate.split(".", maxsplit=1)[0].upper()
    if (
        _SAFE_GAME_ID.fullmatch(candidate)
        and candidate not in {".", ".."}
        and windows_stem not in _WINDOWS_RESERVED_STEMS
    ):
        return candidate

    digest = hashlib.sha256(game_id.encode("utf-8")).hexdigest()[:16]
    return f"partie-{digest}"


def report_directory_name(analysis: GameAnalysis) -> str:
    """Nommer un rapport par date et matchup, sans identité de compte."""

    date = "date-inconnue"
    if analysis.metadata.started_at:
        with suppress(ValueError):
            date = (
                datetime.fromisoformat(analysis.metadata.started_at.replace("Z", "+00:00"))
                .date()
                .isoformat()
            )
    matchup = _ascii_slug(f"{analysis.player.card_class}-vs-{analysis.opponent.card_class}")
    return f"{date}-{matchup}-{safe_game_id(analysis.metadata.game_id)}"


def _ascii_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-") or "match-inconnu"


def _to_json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _to_json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return _to_json_value(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Les clés des objets JSON doivent être des chaînes.")
            converted[key] = _to_json_value(item)
        return converted
    if isinstance(value, (list, tuple)):
        return [_to_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted_items = [_to_json_value(item) for item in value]
        return sorted(converted_items, key=_stable_sort_key)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Type JSON non pris en charge : {type(value).__name__}")


def _stable_sort_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
