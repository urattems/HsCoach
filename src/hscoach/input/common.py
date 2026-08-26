"""Types et validation communs aux sources de replay non fiables."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from hscoach.exceptions import ReplayInputError
from hscoach.privacy import SENSITIVE_FIELD_NAMES, redact_sensitive_text

DEFAULT_MAX_SIZE_BYTES = 50 * 1024 * 1024
_FORBIDDEN_XML_DECLARATIONS = (b"<!doctype", b"<!entity")


@dataclass(frozen=True, slots=True)
class LoadedReplay:
    """Contenu validé d'un replay et libellé de sa source sans secret."""

    data: bytes
    source_label: str


def safe_local_label(path: str | Path) -> str:
    """Produire un nom de fichier affichable sans identifiant sensible."""

    name = Path(path).name or "Replay local"
    redacted = redact_sensitive_text(name)
    if any(marker.casefold() in redacted.casefold() for marker in SENSITIVE_FIELD_NAMES):
        return "Replay local [nom masqué]"
    return redacted


def validate_replay_xml(
    data: bytes,
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
) -> ElementTree.Element:
    """Valider un document XML HSReplay sans résoudre de ressource externe.

    La prévalidation interdit toute DTD et déclaration d'entité avant de confier
    les octets à :mod:`xml.etree.ElementTree`. Seule la racine officielle
    ``HSReplay``, sans espace de noms, est acceptée afin de rester compatible avec
    ``python-hsreplay``.
    """

    validate_size_limit(max_size_bytes)
    if not isinstance(data, bytes):
        raise ReplayInputError("Le contenu du replay doit être fourni sous forme d'octets.")
    if not data:
        raise ReplayInputError("Le replay est vide.")
    if len(data) > max_size_bytes:
        raise ReplayInputError(
            f"Le replay dépasse la taille maximale autorisée ({max_size_bytes} octets)."
        )

    # Retirer les octets NUL permet aussi de détecter ces déclarations dans un
    # document UTF-16, avant que le parseur XML ne voie le contenu non fiable.
    declaration_scan = data.lower().replace(b"\x00", b"")
    if any(marker in declaration_scan for marker in _FORBIDDEN_XML_DECLARATIONS):
        raise ReplayInputError("Le replay contient une DTD ou une déclaration d'entité interdite.")

    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, ValueError) as exc:
        raise ReplayInputError("Le replay ne contient pas un document XML valide.") from exc

    if root.tag != "HSReplay":
        raise ReplayInputError(
            "Le document XML n'est pas un replay HSReplay : la racine attendue est HSReplay."
        )
    return root


def validate_size_limit(max_size_bytes: int) -> None:
    """Refuser une limite de taille incohérente avant toute entrée/sortie."""

    if isinstance(max_size_bytes, bool) or not isinstance(max_size_bytes, int):
        raise ReplayInputError("La taille maximale doit être un nombre entier d'octets.")
    if max_size_bytes <= 0:
        raise ReplayInputError("La taille maximale autorisée doit être strictement positive.")
