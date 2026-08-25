"""Erreurs métier affichables proprement par la CLI."""


class HSCoachError(Exception):
    """Classe de base des erreurs attendues de l'application."""


class ReplayInputError(HSCoachError):
    """Le replay local ou distant ne peut pas être chargé en sécurité."""


class ReplayParseError(HSCoachError):
    """Le contenu n'est pas un document HSReplay exploitable."""


class CardDataError(HSCoachError):
    """Les données HearthstoneJSON ne peuvent pas être chargées."""


class ExportError(HSCoachError):
    """Les rapports ne peuvent pas être écrits."""
