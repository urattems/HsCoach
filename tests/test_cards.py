from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest

from hscoach.cards import CardResolver, HearthstoneJSON, clean_card_text, parse_cards
from hscoach.exceptions import CardDataError
from hscoach.models import Visibility

CARDS_PAYLOAD = [
    {
        "id": "FR_001",
        "name": "Garde français",
        "text": "<b>Cri de guerre :</b><br>Donne +2 PV.",
        "cost": 3,
        "attack": 2,
        "health": 4,
        "durability": 1,
        "type": "MINION",
        "cardClass": "PALADIN",
        "rarity": "COMMON",
        "mechanics": ["BATTLECRY", "TAUNT"],
        "collectible": True,
    },
    {
        "id": "TOKEN_001",
        "name": "Jeune recrue",
        "text": "<i>Invoqué par un effet.</i>",
        "type": "MINION",
    },
]


def cards_json() -> bytes:
    return json.dumps(CARDS_PAYLOAD, ensure_ascii=False).encode()


def write_cache(service: HearthstoneJSON, payload: bytes) -> None:
    service.cache_dir.mkdir(parents=True, exist_ok=True)
    service.cards_path.write_bytes(payload)
    service.metadata_path.write_text(
        json.dumps({"sha256": hashlib.sha256(payload).hexdigest()}),
        encoding="utf-8",
    )


def test_parse_cards_keeps_french_text_and_all_fields() -> None:
    cards = parse_cards(cards_json())

    card = cards["FR_001"]
    assert card.name == "Garde français"
    assert card.text == "Cri de guerre : Donne +2 PV."
    assert card.cost == 3
    assert card.attack == 2
    assert card.health == 4
    assert card.durability == 1
    assert card.card_class == "PALADIN"
    assert card.mechanics == ("BATTLECRY", "TAUNT")
    assert card.collectible is True


def test_parse_cards_includes_non_collectible_cards() -> None:
    card = parse_cards(cards_json())["TOKEN_001"]

    assert card.name == "Jeune recrue"
    assert card.collectible is False


def test_clean_card_text_handles_breaks_entities_and_empty_values() -> None:
    assert clean_card_text("<b>Dégâts&nbsp;:</b><br/>3") == "Dégâts : 3"
    assert clean_card_text("") is None
    assert clean_card_text(None) is None


def test_unknown_card_is_french_and_registered() -> None:
    resolver = CardResolver({})

    card = resolver.resolve("INCONNUE_42")
    reference = resolver.reference("INCONNUE_42", entity_id=7)

    assert card.name == "Carte inconnue [INCONNUE_42]"
    assert card.unresolved is True
    assert reference.name == "Carte inconnue [INCONNUE_42]"
    assert resolver.unresolved_ids == ("INCONNUE_42",)


def test_english_fallback_is_disabled_unless_explicitly_requested() -> None:
    english = parse_cards('[{"id":"ONLY_EN","name":"English name"}]')

    default_resolver = CardResolver({}, english_cards_by_id=english)
    explicit_resolver = CardResolver(
        {},
        english_cards_by_id=english,
        allow_en_fallback=True,
    )

    assert default_resolver.resolve("ONLY_EN").name == "Carte inconnue [ONLY_EN]"
    assert explicit_resolver.resolve("ONLY_EN").name == "English name"


def test_hidden_reference_never_reveals_card_identity() -> None:
    resolver = CardResolver(parse_cards(cards_json()))

    reference = resolver.reference(
        "FR_001", entity_id=9, visibility=Visibility.HIDDEN, created_by_entity_id=3
    )

    assert reference.card_id is None
    assert reference.name == "Carte inconnue"
    assert reference.text is None
    assert reference.cost is None
    assert reference.durability is None
    assert reference.visibility is Visibility.HIDDEN
    assert resolver.unresolved_ids == ()


def test_known_reference_copies_card_durability() -> None:
    resolver = CardResolver(parse_cards(cards_json()))

    assert resolver.reference("FR_001").durability == 1


def test_load_downloads_full_cards_file_and_creates_metadata(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ("https://api.hearthstonejson.com/v1/latest/frFR/cards.json")
        return httpx.Response(200, content=cards_json())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = HearthstoneJSON(tmp_path, client=client)
        cards = service.load()

    assert cards["FR_001"].name == "Garde français"
    assert service.cards_path.read_bytes() == cards_json()
    metadata = json.loads(service.metadata_path.read_text(encoding="utf-8"))
    assert metadata["locale"] == "frFR"
    assert metadata["card_count"] == 2
    assert metadata["source"].endswith("/frFR/cards.json")
    assert metadata["sha256"] == hashlib.sha256(cards_json()).hexdigest()


def test_exact_build_uses_isolated_url_and_cache(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.hearthstonejson.com/v1/248348/frFR/cards.json"
        return httpx.Response(200, content=cards_json())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = HearthstoneJSON(tmp_path, build="248348", client=client)
        cards = service.load()

    assert cards["FR_001"].name == "Garde français"
    assert service.cache_dir == tmp_path / "hearthstonejson" / "248348" / "frFR"
    assert service.resolution == "exact-build"


def test_missing_exact_build_falls_back_explicitly_to_latest(tmp_path) -> None:
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if "/248348/" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=cards_json())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        service = HearthstoneJSON(tmp_path, build="248348", client=client)
        service.load()

    assert requested == [
        "https://api.hearthstonejson.com/v1/248348/frFR/cards.json",
        "https://api.hearthstonejson.com/v1/latest/frFR/cards.json",
    ]
    assert service.resolution == "fallback"


@pytest.mark.parametrize("build", ["../latest", "1/2", "abc", True])
def test_invalid_build_is_rejected_before_path_construction(tmp_path, build) -> None:
    with pytest.raises(ValueError, match="build HearthstoneJSON"):
        HearthstoneJSON(tmp_path, build=build)


def test_load_uses_cache_without_network(tmp_path) -> None:
    service = HearthstoneJSON(tmp_path)
    write_cache(service, cards_json())

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Accès réseau inattendu: {request.url.host}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        offline_service = HearthstoneJSON(tmp_path, client=client)
        cards = offline_service.load()

    assert cards["TOKEN_001"].collectible is False


def test_refresh_falls_back_to_valid_cache_without_modifying_it(tmp_path) -> None:
    service = HearthstoneJSON(tmp_path)
    write_cache(service, cards_json())
    original_payload = service.cards_path.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("hors ligne", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        offline_service = HearthstoneJSON(tmp_path, client=client)
        cards = offline_service.refresh()

    assert cards["FR_001"].name == "Garde français"
    assert service.cards_path.read_bytes() == original_payload


def test_refresh_atomically_replaces_existing_cache(tmp_path) -> None:
    old_payload = json.dumps([{"id": "OLD", "name": "Ancienne carte"}]).encode()
    service = HearthstoneJSON(tmp_path)
    write_cache(service, old_payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=cards_json())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        refreshed_service = HearthstoneJSON(tmp_path, client=client)
        cards = refreshed_service.refresh()

    assert "OLD" not in cards
    assert refreshed_service.cards_path.read_bytes() == cards_json()
    assert not list(refreshed_service.cache_dir.glob("*.tmp"))


def test_refresh_restores_complete_cache_if_metadata_commit_fails(tmp_path, monkeypatch) -> None:
    old_payload = json.dumps([{"id": "OLD", "name": "Ancienne carte"}]).encode()
    service = HearthstoneJSON(tmp_path)
    write_cache(service, old_payload)
    old_metadata = service.metadata_path.read_bytes()
    original_replace = os.replace

    def failing_replace(source, destination):
        if (
            Path(source).name.endswith(".metadata.tmp")
            and Path(destination) == service.metadata_path
        ):
            raise OSError("échec simulé")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", failing_replace)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=cards_json())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cards = HearthstoneJSON(tmp_path, client=client).refresh()

    assert set(cards) == {"OLD"}
    assert service.cards_path.read_bytes() == old_payload
    assert service.metadata_path.read_bytes() == old_metadata
    assert not list(service.cache_dir.glob("*.tmp"))


def test_cache_update_cleans_first_temporary_if_second_creation_fails(
    tmp_path, monkeypatch
) -> None:
    service = HearthstoneJSON(tmp_path)
    original_write_temporary = service._write_temporary
    call_count = 0

    def failing_write_temporary(payload, *, suffix):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("échec simulé")
        return original_write_temporary(payload, suffix=suffix)

    monkeypatch.setattr(service, "_write_temporary", failing_write_temporary)

    with pytest.raises(CardDataError, match="ne peut pas être mis à jour"):
        service._write_cache(cards_json(), card_count=2)

    assert not list(service.cache_dir.glob("*.tmp"))


@pytest.mark.parametrize("locale", ["", "../enUS", "frFR?x=1", "frFR:alt", True])
def test_invalid_locale_is_rejected_before_url_or_path_construction(tmp_path, locale) -> None:
    with pytest.raises(ValueError, match="locale HearthstoneJSON"):
        HearthstoneJSON(tmp_path, locale=locale)


@pytest.mark.parametrize("timeout", [True, "20", 0, float("nan"), float("inf")])
def test_invalid_card_download_timeout_is_rejected(tmp_path, timeout) -> None:
    with pytest.raises(ValueError, match="délai HTTP"):
        HearthstoneJSON(tmp_path, timeout=timeout)


@pytest.mark.parametrize("max_download_size", [True, 1.5, "100", 0])
def test_invalid_card_download_size_is_rejected(tmp_path, max_download_size) -> None:
    with pytest.raises(ValueError, match="taille maximale"):
        HearthstoneJSON(tmp_path, max_download_size=max_download_size)


def test_offline_without_cache_reports_clear_french_error(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("hors ligne", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(CardDataError, match="Aucun cache local valide"),
    ):
        HearthstoneJSON(tmp_path, client=client).load()


def test_hash_mismatch_triggers_refresh_and_replaces_corrupted_cache(tmp_path) -> None:
    service = HearthstoneJSON(tmp_path)
    altered_payload = '[{"id":"ALTERED","name":"Cache altéré"}]'.encode()
    write_cache(service, altered_payload)
    service.metadata_path.write_text(json.dumps({"sha256": "0" * 64}), encoding="utf-8")

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, content=cards_json())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        refreshed_service = HearthstoneJSON(tmp_path, client=client)
        cards = refreshed_service.load()

    assert request_count == 1
    assert "ALTERED" not in cards
    assert refreshed_service.cards_path.read_bytes() == cards_json()
    metadata = json.loads(refreshed_service.metadata_path.read_text(encoding="utf-8"))
    assert metadata["sha256"] == hashlib.sha256(cards_json()).hexdigest()


@pytest.mark.parametrize("metadata", [None, "pas du JSON", json.dumps({"sha256": "0" * 64})])
def test_invalid_cache_is_never_used_offline(tmp_path, metadata: str | None) -> None:
    service = HearthstoneJSON(tmp_path)
    service.cache_dir.mkdir(parents=True)
    service.cards_path.write_bytes(cards_json())
    if metadata is not None:
        service.metadata_path.write_text(metadata, encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("hors ligne", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(CardDataError, match="Cache HearthstoneJSON corrompu.*Actualisation"),
    ):
        HearthstoneJSON(tmp_path, client=client).load()
