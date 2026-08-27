from __future__ import annotations

import httpx
import pytest

from hscoach.exceptions import ReplayInputError
from hscoach.input.sources import (
    DirectXmlUrlSource,
    HsReplayPageSource,
    LocalReplaySource,
    RawXmlSource,
    classify_replay_source,
    safe_source_label,
)

VALID_REPLAY = b'<HSReplay build="1"><Game id="42" /></HSReplay>'


def test_windows_path_is_not_mistaken_for_url_scheme() -> None:
    source = classify_replay_source(r"C:\Replays\partie.xml")

    assert isinstance(source, LocalReplaySource)
    assert source.display_label == "partie.xml"


def test_local_battletag_is_redacted_from_all_display_labels() -> None:
    source = LocalReplaySource(r"C:\Replays\Alice#1234.hsreplay")

    assert source.display_label == "JOUEUR.hsreplay"
    assert "Alice#1234" not in source.display_label
    assert "Alice#1234" not in safe_source_label(source.path)


def test_direct_signed_xml_url_has_safe_label_and_repr() -> None:
    secret = "SUPER_SECRET"
    source = classify_replay_source(
        f"https://replays.example/private/game.xml?X-Amz-Signature={secret}"
    )

    assert isinstance(source, DirectXmlUrlSource)
    assert source.display_label == "replays.example/…/game.xml"
    assert secret not in source.display_label
    assert secret not in repr(source)


def test_direct_xml_url_resolves_with_injected_http_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "replays.example"
        return httpx.Response(200, content=VALID_REPLAY)

    source = DirectXmlUrlSource("https://replays.example/game.xml?token=SECRET")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        loaded = source.load(client=client)

    assert loaded.data == VALID_REPLAY
    assert loaded.source_label == "replays.example"


def test_hsreplay_page_is_recognized_but_never_requested() -> None:
    requested = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200, content=VALID_REPLAY)

    source = classify_replay_source("https://hsreplay.net/replay/AbC_123?utm_source=test")
    assert isinstance(source, HsReplayPageSource)
    assert source.display_label == "hsreplay.net/replay/AbC_123"

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ReplayInputError) as captured,
    ):
        source.load(client=client)

    assert requested is False
    assert str(captured.value) == (
        "Les liens de page HSReplay ne sont pas encore pris en charge.\n"
        "Utilisez le lien XML direct ou un fichier local."
    )


def test_near_miss_hsreplay_domain_is_not_treated_as_official_page() -> None:
    source = classify_replay_source("https://hsreplay.net.example/replay/ABC")

    assert isinstance(source, DirectXmlUrlSource)


def test_html_response_is_rejected_without_leaking_query() -> None:
    secret = "NE_PAS_AFFICHER"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html></html>")

    source = DirectXmlUrlSource(f"https://example.test/page?token={secret}")
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ReplayInputError) as captured,
    ):
        source.load(client=client)

    assert "HSReplay" in str(captured.value)
    assert secret not in str(captured.value)


def test_non_http_url_is_rejected() -> None:
    with pytest.raises(ReplayInputError, match="HTTP"):
        classify_replay_source("ftp://example.test/replay.xml")


def test_raw_xml_source_loads_valid_replay_without_exposing_content() -> None:
    source = RawXmlSource(VALID_REPLAY.decode("utf-8"))

    loaded = source.load()

    assert loaded.data == VALID_REPLAY
    assert loaded.source_label == "XML brut collé"
    assert VALID_REPLAY.decode("utf-8") not in repr(source)
    assert source.fallback_game_id.startswith("xml-colle-")
    assert source.fallback_game_id.replace("-", "").isalnum()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("pas du XML", "XML valide"),
        ('<!DOCTYPE HSReplay [<!ENTITY x "AAAA">]><HSReplay />', "DTD|entité"),
        ("<html />", "racine attendue"),
    ],
)
def test_raw_xml_source_rejects_invalid_or_unsafe_content(content: str, message: str) -> None:
    with pytest.raises(ReplayInputError, match=message):
        RawXmlSource(content).load()


def test_raw_xml_source_applies_size_limit() -> None:
    with pytest.raises(ReplayInputError, match="taille maximale"):
        RawXmlSource(VALID_REPLAY).load(max_size_bytes=10)
