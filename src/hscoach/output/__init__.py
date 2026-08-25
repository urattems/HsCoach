"""Exports partageables d'une analyse de replay."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hscoach.models import GameAnalysis
from hscoach.output.json_export import export_json, render_json
from hscoach.output.llm_json import export_llm_json, render_llm_json
from hscoach.output.markdown import export_markdown, render_markdown


@dataclass(slots=True, frozen=True)
class ExportedReports:
    """Chemins absolus des rapports d'une partie."""

    markdown: Path
    json: Path
    llm: Path | None = None


def export_analysis(
    analysis: GameAnalysis,
    output_directory: str | Path = Path("output"),
) -> ExportedReports:
    """Valider puis écrire les trois rapports partageables."""

    # Valider les trois représentations avant de créer le premier fichier.
    render_markdown(analysis)
    render_json(analysis)
    render_llm_json(analysis)
    return ExportedReports(
        markdown=export_markdown(analysis, output_directory),
        json=export_json(analysis, output_directory),
        llm=export_llm_json(analysis, output_directory),
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
