"""Chargement et cache local des données complètes HearthstoneJSON."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from hscoach.exceptions import CardDataError
from hscoach.models import Card

LOGGER = logging.getLogger(__name__)

CARDS_URL_TEMPLATE = "https://api.hearthstonejson.com/v1/{build}/{locale}/cards.json"
DEFAULT_CACHE_DIRECTORY = Path(".cache")
DEFAULT_LOCALE = "frFR"
DEFAULT_MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0
_SAFE_LOCALE = re.compile(r"[a-z]{2}[A-Z]{2}")


class _ReadableTextParser(HTMLParser):
    """Convertir le petit sous-ensemble HTML des textes de cartes en texte brut."""

    _SEPARATORS = frozenset({"br", "div", "li", "p", "tr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() in self._SEPARATORS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self._SEPARATORS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_card_text(value: object) -> str | None:
    """Nettoyer le HTML Hearthstone sans ajouter de contenu absent de la source."""

    if not isinstance(value, str) or not value.strip():
        return None

    parser = _ReadableTextParser()
    try:
        parser.feed(value)
        parser.close()
    except (AssertionError, ValueError):
        # HTMLParser est permissif, mais une donnée externe mal formée ne doit pas
        # empêcher le chargement de toute la base.
        return " ".join(value.split()) or None

    cleaned = " ".join("".join(parser.parts).replace("[x]", " ").split())
    return cleaned or None


def _optional_int(value: object) -> int | None:
    """Conserver uniquement les nombres entiers attendus dans HearthstoneJSON."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _mechanics(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def parse_cards(payload: bytes | str | Sequence[object]) -> dict[str, Card]:
    """Parser le ``cards.json`` complet et l'indexer par Card ID.

    Les entrées sans identifiant sont ignorées. Une entrée qui existe mais ne
    contient aucun nom localisé reste indexée avec un libellé explicite, sans
    chercher silencieusement une traduction anglaise.
    """

    try:
        decoded: object = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CardDataError("Les données de cartes en cache ne sont pas un JSON valide.") from exc

    if not isinstance(decoded, list):
        raise CardDataError("Les données HearthstoneJSON doivent contenir une liste de cartes.")

    cards: dict[str, Card] = {}
    for raw_card in decoded:
        if not isinstance(raw_card, Mapping):
            continue
        card_id = _optional_string(raw_card.get("id"))
        if card_id is None:
            continue

        localized_name = _optional_string(raw_card.get("name"))
        unresolved = localized_name is None
        cards[card_id] = Card(
            id=card_id,
            name=localized_name or f"Carte inconnue [{card_id}]",
            text=clean_card_text(raw_card.get("text")),
            cost=_optional_int(raw_card.get("cost")),
            attack=_optional_int(raw_card.get("attack")),
            health=_optional_int(raw_card.get("health")),
            durability=_optional_int(raw_card.get("durability")),
            type=_optional_string(raw_card.get("type")),
            card_class=_optional_string(raw_card.get("cardClass")),
            rarity=_optional_string(raw_card.get("rarity")),
            mechanics=_mechanics(raw_card.get("mechanics")),
            collectible=raw_card.get("collectible") is True,
            unresolved=unresolved,
        )

    if not cards:
        raise CardDataError("Les données HearthstoneJSON ne contiennent aucune carte valide.")
    return cards


class HearthstoneJSON:
    """Service de cache pour le fichier complet ``cards.json`` d'une locale."""

    def __init__(
        self,
        cache_directory: str | Path = DEFAULT_CACHE_DIRECTORY,
        *,
        locale: str = DEFAULT_LOCALE,
        build: str | int | None = None,
        client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_download_size: int = DEFAULT_MAX_DOWNLOAD_SIZE,
    ) -> None:
        if not isinstance(locale, str) or _SAFE_LOCALE.fullmatch(locale) is None:
            raise ValueError("La locale HearthstoneJSON n'est pas valide.")
        if build is not None and (isinstance(build, bool) or not str(build).isdigit()):
            raise ValueError("Le build HearthstoneJSON n'est pas valide.")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Le délai HTTP doit être positif.")
        if (
            isinstance(max_download_size, bool)
            or not isinstance(max_download_size, int)
            or max_download_size <= 0
        ):
            raise ValueError("La taille maximale doit être positive.")

        self.cache_directory = Path(cache_directory)
        self.locale = locale
        self.build = str(build) if build is not None else None
        self.resolution = "latest" if self.build is None else "exact-build"
        self.resolved_build = self.build
        self.client = client
        self.timeout = timeout
        self.max_download_size = max_download_size

    @property
    def cache_dir(self) -> Path:
        """Dossier propre à la locale, sous la racine de cache configurée."""

        build_key = self.build or "latest"
        return self.cache_directory / "hearthstonejson" / build_key / self.locale

    @property
    def cards_path(self) -> Path:
        return self.cache_dir / "cards.json"

    @property
    def metadata_path(self) -> Path:
        return self.cache_dir / "metadata.json"

    @property
    def source_url(self) -> str:
        return CARDS_URL_TEMPLATE.format(build=self.build or "latest", locale=self.locale)

    def load(self) -> dict[str, Card]:
        """Utiliser immédiatement le cache valide, sinon le télécharger."""

        try:
            return self._load_cache()
        except CardDataError as cache_error:
            LOGGER.info("Aucun cache HearthstoneJSON %s valide; téléchargement.", self.locale)
            try:
                return self._download_and_cache(cache_error=cache_error)
            except CardDataError:
                if self.build is None:
                    raise
                fallback = HearthstoneJSON(
                    self.cache_directory,
                    locale=self.locale,
                    client=self.client,
                    timeout=self.timeout,
                    max_download_size=self.max_download_size,
                )
                cards = fallback.load()
                self.resolution = "fallback"
                self.resolved_build = fallback.resolved_build
                return cards

    def refresh(self) -> dict[str, Card]:
        """Actualiser atomiquement le cache, avec repli sur sa version valide."""

        cached_cards: dict[str, Card] | None = None
        cache_error: CardDataError | None = None
        try:
            cached_cards = self._load_cache()
        except CardDataError as exc:
            cache_error = exc

        try:
            return self._download_and_cache(cache_error=cache_error)
        except CardDataError:
            if cached_cards is None:
                raise
            LOGGER.warning(
                "Actualisation des cartes impossible; "
                "le cache HearthstoneJSON existant est conservé."
            )
            return cached_cards

    def _load_cache(self) -> dict[str, Card]:
        try:
            size = self.cards_path.stat().st_size
            if size > self.max_download_size:
                raise CardDataError("Le cache des cartes dépasse la taille maximale autorisée.")
            payload = self.cards_path.read_bytes()
        except FileNotFoundError as exc:
            raise CardDataError(
                "Aucun cache local valide des cartes HearthstoneJSON n'est disponible."
            ) from exc
        except OSError as exc:
            raise CardDataError("Le cache HearthstoneJSON local ne peut pas être lu.") from exc

        expected_sha256 = self._load_expected_sha256()
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != expected_sha256:
            raise CardDataError(
                "Cache HearthstoneJSON corrompu : l'empreinte SHA-256 ne correspond pas."
            )
        return parse_cards(payload)

    def _load_expected_sha256(self) -> str:
        try:
            raw_metadata = self.metadata_path.read_bytes()
            metadata = json.loads(raw_metadata)
        except FileNotFoundError as exc:
            raise CardDataError(
                "Cache HearthstoneJSON corrompu : les métadonnées sont absentes."
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CardDataError(
                "Cache HearthstoneJSON corrompu : les métadonnées sont invalides."
            ) from exc

        if not isinstance(metadata, Mapping):
            raise CardDataError("Cache HearthstoneJSON corrompu : les métadonnées sont invalides.")
        expected_sha256 = metadata.get("sha256")
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in expected_sha256)
        ):
            raise CardDataError(
                "Cache HearthstoneJSON corrompu : l'empreinte SHA-256 est invalide."
            )
        return expected_sha256.casefold()

    def _download_and_cache(self, *, cache_error: CardDataError | None = None) -> dict[str, Card]:
        try:
            payload = self._download()
            cards = parse_cards(payload)
            self._write_cache(payload, card_count=len(cards))
            return cards
        except CardDataError as exc:
            if cache_error is None:
                raise
            raise CardDataError(f"{cache_error} Actualisation impossible : {exc}") from exc
        except (httpx.HTTPError, OSError) as exc:
            message = "Impossible de télécharger les données de cartes HearthstoneJSON."
            if cache_error is not None:
                message += " Aucun cache local valide n'est disponible."
            raise CardDataError(message) from exc

    def _download(self) -> bytes:
        owns_client = self.client is None
        client = self.client or httpx.Client(follow_redirects=True)
        chunks: list[bytes] = []
        total_size = 0
        try:
            with client.stream(
                "GET", self.source_url, timeout=self.timeout, follow_redirects=True
            ) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise CardDataError(
                        "Le serveur HearthstoneJSON a refusé l'actualisation "
                        f"(HTTP {response.status_code})."
                    ) from exc

                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        advertised_size = int(content_length)
                    except ValueError:
                        advertised_size = 0
                    if advertised_size > self.max_download_size:
                        raise CardDataError(
                            "Le fichier de cartes dépasse la taille maximale autorisée."
                        )

                for chunk in response.iter_bytes():
                    total_size += len(chunk)
                    if total_size > self.max_download_size:
                        raise CardDataError(
                            "Le fichier de cartes dépasse la taille maximale autorisée."
                        )
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise CardDataError(
                "Impossible de contacter HearthstoneJSON pour actualiser les cartes."
            ) from exc
        finally:
            if owns_client:
                client.close()

        return b"".join(chunks)

    def _write_cache(self, payload: bytes, *, card_count: int) -> None:
        temporary: dict[Path, Path] = {}
        backups: dict[Path, Path] = {}
        committed: list[Path] = []
        committed_all = False
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            metadata: dict[str, Any] = {
                "requested_build": self.build,
                "resolution": self.resolution,
                "card_count": card_count,
                "downloaded_at": datetime.now(UTC).isoformat(),
                "locale": self.locale,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "source": self.source_url,
            }
            metadata_payload = (
                json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")

            temporary[self.cards_path] = self._write_temporary(payload, suffix=".cards.tmp")
            temporary[self.metadata_path] = self._write_temporary(
                metadata_payload, suffix=".metadata.tmp"
            )
            for destination in temporary:
                if destination.exists():
                    backup = self._reserve_temporary_path(suffix=".rollback.tmp")
                    backups[destination] = backup
                    os.replace(destination, backup)
            for destination, source in temporary.items():
                os.replace(source, destination)
                committed.append(destination)
            committed_all = True
        except OSError as exc:
            rollback_failed = self._rollback_cache(committed, backups)
            message = "Le cache HearthstoneJSON ne peut pas être mis à jour."
            if rollback_failed:
                message += " Son état précédent n'a pas pu être restauré intégralement."
            raise CardDataError(message) from exc
        finally:
            for path in temporary.values():
                with suppress(OSError):
                    path.unlink(missing_ok=True)
            if committed_all:
                for path in backups.values():
                    with suppress(OSError):
                        path.unlink(missing_ok=True)

    def _write_temporary(self, payload: bytes, *, suffix: str) -> Path:
        descriptor, raw_path = tempfile.mkstemp(dir=self.cache_dir, suffix=suffix)
        temporary_path = Path(raw_path)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return temporary_path

    def _reserve_temporary_path(self, *, suffix: str) -> Path:
        descriptor, raw_path = tempfile.mkstemp(dir=self.cache_dir, suffix=suffix)
        os.close(descriptor)
        temporary_path = Path(raw_path)
        temporary_path.unlink()
        return temporary_path

    @staticmethod
    def _rollback_cache(committed: list[Path], backups: dict[Path, Path]) -> bool:
        rollback_failed = False
        for destination in committed:
            if destination in backups:
                continue
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                rollback_failed = True
        for destination, backup in backups.items():
            if not backup.exists():
                continue
            try:
                os.replace(backup, destination)
            except OSError:
                # Garder le backup plutôt que supprimer la dernière copie récupérable.
                rollback_failed = True
        return rollback_failed
