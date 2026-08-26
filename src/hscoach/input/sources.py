"""Détection explicite des différentes sources de replay prises en charge."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

from hscoach.exceptions import ReplayInputError
from hscoach.input.common import DEFAULT_MAX_SIZE_BYTES, LoadedReplay, safe_local_label
from hscoach.input.local import SUPPORTED_REPLAY_EXTENSIONS, load_local_replay
from hscoach.input.remote import DEFAULT_HTTP_TIMEOUT_SECONDS, load_remote_replay, safe_remote_label

_HSREPLAY_PAGE_HOSTS = frozenset({"hsreplay.net", "www.hsreplay.net"})
_HSREPLAY_PAGE_PATH = re.compile(r"/replay/([A-Za-z0-9_-]+)/?", re.IGNORECASE)
_SAFE_LAST_PATH_COMPONENT = re.compile(r"[^A-Za-z0-9._%+-]+")
_SENSITIVE_PATH_MARKERS = (
    "x-amz-credential",
    "x-amz-security-token",
    "x-amz-signature",
)


class ReplaySourceKind(StrEnum):
    """Catégorie factuelle d'une source avant son chargement."""

    LOCAL = "local"
    DIRECT_XML_URL = "direct_xml_url"
    HSREPLAY_PAGE = "hsreplay_page"


@runtime_checkable
class ReplaySource(Protocol):
    """Contrat minimal consommé par la couche applicative."""

    @property
    def display_label(self) -> str:
        """Retourner un libellé sûr pour l'interface et les journaux."""

    @property
    def kind(self) -> ReplaySourceKind:
        """Retourner la catégorie de source."""

    def load(
        self,
        *,
        max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> LoadedReplay:
        """Charger et valider le replay."""


@dataclass(frozen=True, slots=True)
class LocalReplaySource:
    """Replay explicitement sélectionné sur le disque local."""

    path: Path = field(repr=False)
    kind: ReplaySourceKind = field(default=ReplaySourceKind.LOCAL, init=False)

    def __init__(self, path: str | Path) -> None:
        object.__setattr__(self, "path", Path(path))
        object.__setattr__(self, "kind", ReplaySourceKind.LOCAL)

    @property
    def display_label(self) -> str:
        return safe_local_label(self.path)

    def load(
        self,
        *,
        max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> LoadedReplay:
        del timeout_seconds, client
        return load_local_replay(self.path, max_size_bytes=max_size_bytes)


@dataclass(frozen=True, slots=True)
class DirectXmlUrlSource:
    """URL HTTP(S) candidate pointant directement vers un XML HSReplay."""

    url: str = field(repr=False)
    kind: ReplaySourceKind = field(default=ReplaySourceKind.DIRECT_XML_URL, init=False)

    def __post_init__(self) -> None:
        # Valide également les identifiants intégrés, le port et le nom d'hôte.
        safe_remote_label(self.url)

    @property
    def display_label(self) -> str:
        return safe_url_display_label(self.url)

    def load(
        self,
        *,
        max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> LoadedReplay:
        return load_remote_replay(
            self.url,
            max_size_bytes=max_size_bytes,
            timeout_seconds=timeout_seconds,
            client=client,
        )


@dataclass(frozen=True, slots=True)
class HsReplayPageSource:
    """Page publique HSReplay reconnue mais volontairement non scrapée."""

    url: str = field(repr=False)
    replay_id: str = field(init=False)
    kind: ReplaySourceKind = field(default=ReplaySourceKind.HSREPLAY_PAGE, init=False)

    def __post_init__(self) -> None:
        parsed = _validated_http_url(self.url)
        match = _HSREPLAY_PAGE_PATH.fullmatch(parsed.path)
        if (
            parsed.hostname is None
            or parsed.hostname.casefold().rstrip(".") not in _HSREPLAY_PAGE_HOSTS
        ):
            raise ReplayInputError("L'URL n'est pas une page publique HSReplay reconnue.")
        if match is None:
            raise ReplayInputError("L'URL n'est pas une page publique HSReplay reconnue.")
        object.__setattr__(self, "replay_id", match.group(1))

    @property
    def display_label(self) -> str:
        return f"hsreplay.net/replay/{self.replay_id}"

    def load(
        self,
        *,
        max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> LoadedReplay:
        del max_size_bytes, timeout_seconds, client
        raise ReplayInputError(
            "Les liens de page HSReplay ne sont pas encore pris en charge.\n"
            "Utilisez le lien XML direct ou un fichier local."
        )


def classify_replay_source(value: str | Path | ReplaySource) -> ReplaySource:
    """Détecter une source locale, XML directe ou page publique HSReplay."""

    if isinstance(value, ReplaySource):
        return value
    if isinstance(value, Path):
        return LocalReplaySource(value)
    if not isinstance(value, str):
        raise ReplayInputError("La source du replay doit être un chemin ou une URL.")

    candidate = value.strip()
    if not candidate:
        raise ReplayInputError("La source du replay est vide.")

    # ``urlsplit('C:\\...')`` interprète sinon la lettre du lecteur comme un schéma.
    if PureWindowsPath(candidate).drive:
        return LocalReplaySource(candidate)

    try:
        parsed = urlsplit(candidate)
    except (TypeError, ValueError) as exc:
        raise ReplayInputError("La source du replay est invalide.") from exc

    scheme = parsed.scheme.casefold()
    if scheme in {"http", "https"}:
        normalized_host = (parsed.hostname or "").casefold().rstrip(".")
        if normalized_host in _HSREPLAY_PAGE_HOSTS and _HSREPLAY_PAGE_PATH.fullmatch(parsed.path):
            return HsReplayPageSource(candidate)
        return DirectXmlUrlSource(candidate)
    if scheme or "://" in candidate:
        raise ReplayInputError("Seules les URL HTTP et HTTPS sont autorisées.")
    return LocalReplaySource(candidate)


# Alias descriptif conservé pour les appelants qui parlent de détection plutôt que classification.
detect_replay_source = classify_replay_source


def validate_local_source_candidate(source: LocalReplaySource) -> None:
    """Valider rapidement l'extension sans lire le replay sur le thread UI."""

    suffix = source.path.suffix.casefold()
    if suffix and suffix not in SUPPORTED_REPLAY_EXTENSIONS:
        extensions = ", ".join(sorted(SUPPORTED_REPLAY_EXTENSIONS))
        raise ReplayInputError(f"Extension de replay non prise en charge (attendu : {extensions}).")


def safe_url_display_label(url: str) -> str:
    """Afficher une URL sans query string, fragment, identifiants ni chemin sensible."""

    host = safe_remote_label(url)
    parsed = _validated_http_url(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        return host

    last_part = path_parts[-1]
    if any(marker in last_part.casefold() for marker in _SENSITIVE_PATH_MARKERS):
        return host
    safe_last_part = _SAFE_LAST_PATH_COMPONENT.sub("_", last_part).strip("._")[:80]
    if not safe_last_part:
        return host
    separator = "/…/" if len(path_parts) > 1 else "/"
    return f"{host}{separator}{safe_last_part}"


def safe_source_label(value: object) -> str:
    """Produire un libellé de repli sans jamais recopier une URL brute."""

    if isinstance(value, ReplaySource):
        return value.display_label
    if isinstance(value, Path):
        return safe_local_label(value)
    if isinstance(value, str):
        candidate = value.strip()
        if PureWindowsPath(candidate).drive or "://" not in candidate:
            return safe_local_label(candidate)
        try:
            return safe_url_display_label(candidate)
        except ReplayInputError:
            return "URL de replay invalide"
    return "Source de replay invalide"


def _validated_http_url(url: str):
    safe_remote_label(url)
    try:
        return urlsplit(url)
    except (TypeError, ValueError) as exc:
        raise ReplayInputError("L'URL du replay est invalide.") from exc


__all__ = [
    "DirectXmlUrlSource",
    "HsReplayPageSource",
    "LocalReplaySource",
    "ReplaySource",
    "ReplaySourceKind",
    "classify_replay_source",
    "detect_replay_source",
    "safe_source_label",
    "safe_url_display_label",
    "validate_local_source_candidate",
]
