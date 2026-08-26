"""Préférences desktop limitées aux choix non sensibles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, QStandardPaths


@dataclass(frozen=True, slots=True)
class GuiPreferences:
    output_directory: Path
    export_markdown: bool = True
    export_llm_json: bool = True
    export_full_json: bool = False
    open_after_analysis: bool = False


class SettingsStore:
    """Adaptateur injectable autour de QSettings ; aucune source n'y est écrite."""

    _ALLOWED_KEYS = frozenset(
        {
            "output_directory",
            "export_markdown",
            "export_llm_json",
            "export_full_json",
            "open_after_analysis",
            "window_geometry",
        }
    )

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings()

    def load(self) -> GuiPreferences:
        raw_output = self.settings.value("output_directory", str(default_output_directory()))
        output = Path(str(raw_output)) if raw_output else default_output_directory()
        return GuiPreferences(
            output_directory=output,
            export_markdown=self._boolean("export_markdown", True),
            export_llm_json=self._boolean("export_llm_json", True),
            export_full_json=self._boolean("export_full_json", False),
            open_after_analysis=self._boolean("open_after_analysis", False),
        )

    def save(self, preferences: GuiPreferences) -> None:
        self.settings.setValue("output_directory", str(preferences.output_directory))
        self.settings.setValue("export_markdown", preferences.export_markdown)
        self.settings.setValue("export_llm_json", preferences.export_llm_json)
        self.settings.setValue("export_full_json", preferences.export_full_json)
        self.settings.setValue("open_after_analysis", preferences.open_after_analysis)
        self.settings.sync()

    def load_geometry(self) -> QByteArray | None:
        value = self.settings.value("window_geometry")
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    def save_geometry(self, geometry: QByteArray) -> None:
        self.settings.setValue("window_geometry", geometry)
        self.settings.sync()

    def _boolean(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.casefold() in {"1", "true", "yes", "oui", "on"}
        return bool(value)


def default_output_directory() -> Path:
    raw_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(raw_path) / "HSCoach"


def default_cache_directory() -> Path:
    raw_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    if raw_path:
        return Path(raw_path)
    fallback = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(fallback) / "cache"


__all__ = [
    "GuiPreferences",
    "SettingsStore",
    "default_cache_directory",
    "default_output_directory",
]
