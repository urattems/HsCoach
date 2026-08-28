"""Configuration centralisée de l'application."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class AppConfig:
    """Valeurs de configuration sûres utilisées par défaut."""

    locale: str = "frFR"
    anonymize: bool = True
    allow_en_fallback: bool = False
    max_download_size_mb: int = 50
    http_timeout_seconds: float = 20.0
    output_directory: Path = Path("output")
    cache_directory: Path = Path(".cache")

    @property
    def max_download_size_bytes(self) -> int:
        """Retourner la limite de téléchargement exprimée en octets."""

        return self.max_download_size_mb * 1024 * 1024

    @property
    def card_cache_directory(self) -> Path:
        """Retourner le dossier de cache HearthstoneJSON pour la locale active."""

        return self.cache_directory / "hearthstonejson" / "latest" / self.locale
