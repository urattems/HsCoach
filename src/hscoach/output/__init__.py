"""Exports partageables d'une analyse de replay."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from hscoach.exceptions import ExportError
from hscoach.models import GameAnalysis
from hscoach.output.json_export import (
    JSON_FILENAME,
    export_json,
    render_json,
    report_directory_name,
)
from hscoach.output.llm_json import LLM_JSON_FILENAME, render_llm_json
from hscoach.output.llm_json import export_llm_json as _export_llm_json
from hscoach.output.markdown import MARKDOWN_FILENAME, render_markdown
from hscoach.output.markdown import export_markdown as _export_markdown

export_markdown = _export_markdown
export_llm_json = _export_llm_json


@dataclass(slots=True, frozen=True)
class ExportedReports:
    """Chemins absolus des rapports demandés pour une partie."""

    markdown: Path | None = None
    json: Path | None = None
    llm: Path | None = None

    @property
    def full_json(self) -> Path | None:
        """Alias explicite du champ historique ``json``."""

        return self.json

    @property
    def directory(self) -> Path | None:
        """Retourner le dossier commun du premier rapport disponible."""

        for report in (self.markdown, self.llm, self.json):
            if report is not None:
                return report.parent
        return None


def export_analysis(
    analysis: GameAnalysis,
    output_directory: str | Path = Path("output"),
    *,
    export_markdown: bool = True,
    export_full_json: bool = True,
    export_llm_json: bool = True,
) -> ExportedReports:
    """Valider puis écrire uniquement les rapports demandés.

    Les trois options restent actives par défaut afin de préserver le contrat CLI V2.
    """

    rendered = {}
    if export_markdown:
        rendered[MARKDOWN_FILENAME] = render_markdown(analysis)
    if export_full_json:
        rendered[JSON_FILENAME] = render_json(analysis)
    if export_llm_json:
        rendered[LLM_JSON_FILENAME] = render_llm_json(analysis)

    if not rendered:
        return ExportedReports()

    root = Path(output_directory).expanduser().resolve()
    directory = (root / report_directory_name(analysis)).resolve()
    if not directory.is_relative_to(root):
        raise ExportError("Le dossier de sortie calculé est hors du dossier autorisé.")
    temporary: dict[str, Path] = {}
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    committed_all = False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.resolve().is_relative_to(root):
            raise ExportError("Le dossier de sortie pointe hors du dossier autorisé.")
        for name, content in rendered.items():
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=directory,
                prefix=f".{name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                temporary[name] = Path(stream.name)
        # Aucun rapport final n'existe encore lors d'une première exportation.
        # Les rapports existants sont sauvegardés afin qu'un échec conserve l'ancien lot complet.
        for name in temporary:
            destination = directory / name
            if destination.exists():
                backup = directory / f".{name}.rollback.tmp"
                destination.replace(backup)
                backups[destination] = backup
        for name, source in temporary.items():
            destination = directory / name
            source.replace(destination)
            committed.append(destination)
        committed_all = True
        return ExportedReports(
            markdown=directory / MARKDOWN_FILENAME if export_markdown else None,
            json=directory / JSON_FILENAME if export_full_json else None,
            llm=directory / LLM_JSON_FILENAME if export_llm_json else None,
        )
    except OSError as exc:
        rollback_failed = False
        for path in committed:
            if path in backups:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                rollback_failed = True
        for destination, backup in backups.items():
            if backup.exists():
                try:
                    backup.replace(destination)
                except OSError:
                    # Conserver la sauvegarde si le système refuse aussi le rollback.
                    rollback_failed = True
        message = "Le lot de rapports n'a pas pu être écrit intégralement."
        if rollback_failed:
            message += " Le lot précédent n'a pas pu être restauré intégralement."
        raise ExportError(message) from exc
    finally:
        for path in temporary.values():
            with suppress(OSError):
                path.unlink(missing_ok=True)
        if committed_all:
            for path in backups.values():
                with suppress(OSError):
                    path.unlink(missing_ok=True)


__all__ = [
    "ExportedReports",
    "export_analysis",
    "export_json",
    "export_llm_json",
    "export_markdown",
    "render_json",
    "render_llm_json",
    "render_markdown",
]
