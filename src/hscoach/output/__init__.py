"""Exports partageables d'une analyse de replay."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hscoach.models import GameAnalysis
from hscoach.output.json_export import export_json, render_json
from hscoach.output.llm_json import export_llm_json as _export_llm_json
from hscoach.output.llm_json import render_llm_json
from hscoach.output.markdown import export_markdown as _export_markdown
from hscoach.output.markdown import render_markdown

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

    # Valider toutes les représentations demandées avant de créer le premier fichier.
    if export_markdown:
        render_markdown(analysis)
    if export_full_json:
        render_json(analysis)
    if export_llm_json:
        render_llm_json(analysis)
    return ExportedReports(
        markdown=(_export_markdown(analysis, output_directory) if export_markdown else None),
        json=export_json(analysis, output_directory) if export_full_json else None,
        llm=_export_llm_json(analysis, output_directory) if export_llm_json else None,
    )


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
