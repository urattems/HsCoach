"""Traductions françaises des enums techniques Hearthstone."""

from __future__ import annotations

from hearthstone.enums import CardClass, FormatType, GameType

CARD_CLASS_FR: dict[CardClass, str] = {
    CardClass.DEATHKNIGHT: "Chevalier de la mort",
    CardClass.DEMONHUNTER: "Chasseur de démons",
    CardClass.DRUID: "Druide",
    CardClass.HUNTER: "Chasseur",
    CardClass.MAGE: "Mage",
    CardClass.PALADIN: "Paladin",
    CardClass.PRIEST: "Prêtre",
    CardClass.ROGUE: "Voleur",
    CardClass.SHAMAN: "Chaman",
    CardClass.WARLOCK: "Démoniste",
    CardClass.WARRIOR: "Guerrier",
    CardClass.NEUTRAL: "Neutre",
}

FORMAT_FR: dict[FormatType, str] = {
    FormatType.FT_WILD: "Libre",
    FormatType.FT_STANDARD: "Standard",
    FormatType.FT_CLASSIC: "Classique",
    FormatType.FT_TWIST: "Imprévu",
}

GAME_TYPE_FR: dict[GameType, str] = {
    GameType.GT_VS_AI: "Contre l’IA",
    GameType.GT_VS_FRIEND: "Partie amicale",
    GameType.GT_TUTORIAL: "Tutoriel",
    GameType.GT_ARENA: "Arène",
    GameType.GT_RANKED: "Partie classée",
    GameType.GT_CASUAL: "Partie libre",
    GameType.GT_TAVERNBRAWL: "Bras de fer",
    GameType.GT_BATTLEGROUNDS: "Champs de bataille",
}


def card_class_fr(value: int | None) -> str:
    """Traduire une valeur CardClass sans inventer une classe inconnue."""

    if value is None or value not in CardClass._value2member_map_:
        return "Classe inconnue"
    return CARD_CLASS_FR.get(CardClass(value), "Classe inconnue")


def format_fr(value: str | None) -> str:
    """Traduire le format numérique d'une partie."""

    try:
        enum_value = FormatType(int(value)) if value is not None else FormatType.FT_UNKNOWN
    except ValueError:
        return "Format inconnu"
    return FORMAT_FR.get(enum_value, "Format inconnu")


def game_type_fr(value: str | None) -> str:
    """Traduire le type numérique d'une partie."""

    try:
        enum_value = GameType(int(value)) if value is not None else GameType.GT_UNKNOWN
    except ValueError:
        return "Type de partie inconnu"
    return GAME_TYPE_FR.get(enum_value, "Type de partie inconnu")
