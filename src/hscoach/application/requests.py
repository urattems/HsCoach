"""Requêtes immuables acceptées par :class:`AnalysisService`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from hscoach.input.sources import ReplaySource


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    """Décrire un batch et les rapports réellement demandés."""

    sources: tuple[str | Path | ReplaySource, ...] = field(repr=False)
    output_directory: Path = Path("output")
    export_markdown: bool = True
    export_full_json: bool = True
    export_llm_json: bool = True
    allow_en_fallback: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "output_directory", Path(self.output_directory))

    @property
    def writes_reports(self) -> bool:
        """Indiquer si au moins un rendu doit être écrit."""

        return self.export_markdown or self.export_full_json or self.export_llm_json
