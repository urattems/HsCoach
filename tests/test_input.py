from __future__ import annotations

import logging

import httpx
import pytest

from hscoach.exceptions import ReplayInputError
from hscoach.input import LoadedReplay, load_source, validate_replay_xml
from hscoach.input.local import load_local_replay
from hscoach.input.remote import load_remote_replay, safe_remote_label

VALID_REPLAY = b'<HSReplay build="1"><Game id="42" /></HSReplay>'


@pytest.mark.parametrize("extension", [".hsreplay", ".xml", ".txt", ".XML", ""])
def test_load_local_replay_accepts_supported_extensions(tmp_path, extension: str) -> None:
    replay_path = tmp_path / f"partie{extension}"
    replay_path.write_bytes(VALID_REPLAY)

    loaded = load_local_replay(replay_path)

    assert loaded == LoadedReplay(data=VALID_REPLAY, source_label=replay_path.name)


def test_load_local_replay_rejects_unsupported_extension(tmp_path) -> None:
    replay_path = tmp_path / "partie.json"
    replay_path.write_bytes(VALID_REPLAY)

    with pytest.raises(ReplayInputError, match="Extension"):
        load_local_replay(replay_path)


def test_validate_replay_xml_accepts_official_hsreplay_root() -> None:
    root = validate_replay_xml(VALID_REPLAY)

    assert root.tag == "HSReplay"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"pas du XML",
        b"<html></html>",
        b'<Game id="42" />',
        b'<HSReplay xmlns="urn:hsreplay"><Game /></HSReplay>',
        b'<!DOCTYPE HSReplay SYSTEM "file:///etc/passwd"><HSReplay />',
        b'<!DOCTYPE HSReplay [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><HSReplay>&xxe;</HSReplay>',
        "<!DOCTYPE HSReplay><HSReplay />".encode("utf-16"),
    ],
)
def test_validate_replay_xml_rejects_unsafe_or_invalid_documents(payload: bytes) -> None:
    with pytest.raises(ReplayInputError):
        validate_replay_xml(payload)


def test_load_local_replay_enforces_size_limit_while_reading(tmp_path) -> None:
    replay_path = tmp_path / "partie.xml"
    replay_path.write_bytes(VALID_REPLAY)

    with pytest.raises(ReplayInputError, match="taille maximale"):
        load_local_replay(replay_path, max_size_bytes=8)


def test_load_source_dispatches_local_replay(tmp_path) -> None:
    replay_path = tmp_path / "partie.hsreplay"
    replay_path.write_bytes(VALID_REPLAY)

    assert load_source(str(replay_path)).data == VALID_REPLAY


def test_remote_replay_uses_injected_client_and_streams_response(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "replays.example"
        return httpx.Response(200, content=VALID_REPLAY)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        caplog.at_level(logging.DEBUG),
    ):
        loaded = load_remote_replay(
            "https://replays.example/game.xml?X-Amz-Signature=SECRET",
            client=client,
        )

        assert loaded == LoadedReplay(data=VALID_REPLAY, source_label="replays.example")
        assert not client.is_closed
    assert "SECRET" not in caplog.text
    assert "replays.example" in caplog.text


def test_remote_replay_rejects_non_http_scheme_without_leaking_url() -> None:
    secret_url = "file:///tmp/replay.xml?X-Amz-Signature=SECRET"

    with pytest.raises(ReplayInputError) as captured:
        load_source(secret_url)

    assert "SECRET" not in str(captured.value)


def test_remote_replay_reports_timeout_without_leaking_signed_query(caplog) -> None:
    secret_url = "https://replays.example/game.xml?X-Amz-Signature=SUPER_SECRET"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("échec pour une URL signée", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        caplog.at_level(logging.DEBUG),
        pytest.raises(ReplayInputError) as captured,
    ):
        load_remote_replay(secret_url, client=client)

    combined_output = str(captured.value) + caplog.text
    assert "SUPER_SECRET" not in combined_output
    assert secret_url not in combined_output
    assert "replays.example" in combined_output


@pytest.mark.parametrize("status_code", [403, 404, 500])
def test_remote_replay_handles_http_errors_without_query(status_code: int) -> None:
    secret_url = "https://replays.example/game.xml?token=NE_PAS_AFFICHER"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ReplayInputError) as captured,
    ):
        load_remote_replay(secret_url, client=client)

    assert str(status_code) in str(captured.value)
    assert "NE_PAS_AFFICHER" not in str(captured.value)


def test_remote_replay_enforces_content_length_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": "999"}, content=VALID_REPLAY)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ReplayInputError, match="taille maximale"),
    ):
        load_remote_replay("https://replays.example/game.xml", max_size_bytes=100, client=client)


def test_remote_replay_enforces_streamed_size_limit_without_content_length() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=VALID_REPLAY)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ReplayInputError, match="taille maximale"),
    ):
        load_remote_replay("https://replays.example/game.xml", max_size_bytes=8, client=client)


def test_safe_remote_label_never_contains_path_or_query() -> None:
    label = safe_remote_label("https://replays.example/game.xml?X-Amz-Credential=SECRET")

    assert label == "replays.example"


def test_safe_remote_label_rejects_embedded_credentials_without_leaking_them() -> None:
    secret_url = "https://user:password@replays.example/game.xml?token=SECRET"

    with pytest.raises(ReplayInputError) as captured:
        safe_remote_label(secret_url)

    assert "password" not in str(captured.value)
    assert "SECRET" not in str(captured.value)
