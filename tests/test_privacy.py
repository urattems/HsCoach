from pathlib import Path
from xml.etree import ElementTree

import pytest

from hscoach.exceptions import ExportError
from hscoach.privacy import assert_shareable_text, redact_sensitive_text
from hscoach.replay.parser import analyze_replay_data

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"
requires_user_sample = pytest.mark.skipif(
    not SAMPLE.is_file(), reason="Replay utilisateur local non disponible."
)


@requires_user_sample
def test_real_player_names_and_accounts_never_enter_analysis_model() -> None:
    root = ElementTree.fromstring(SAMPLE.read_bytes())
    private_values = {
        value
        for player in root.findall("./Game/Player")
        for key in ("name", "accountHi", "accountLo")
        if (value := player.attrib.get(key))
    }
    analysis = analyze_replay_data(SAMPLE.read_bytes(), {})
    model_text = repr(analysis)

    assert private_values
    assert all(value not in model_text for value in private_values)
    assert "accountHi" not in model_text
    assert "accountLo" not in model_text


def test_signed_url_and_battletag_are_redacted() -> None:
    raw = (
        "JoueurTest#1234 "
        "https://bucket.example/replay.xml?X-Amz-Credential=ABC&X-Amz-Signature=SECRET"
    )

    redacted = redact_sensitive_text(raw)

    assert "JoueurTest" not in redacted
    assert "SECRET" not in redacted
    assert "bucket.example" in redacted


@pytest.mark.parametrize(
    "signed_url",
    [
        "https://bucket.example/replay?X%2DAmz%2DSignature=SECRET",
        "https://bucket.example/replay?redirect=X%252DAmz%252DCredential%3DSECRET",
        "https://bucket.example/replay?x-amz-algorithm=AWS4-HMAC-SHA256",
    ],
)
def test_encoded_signed_urls_are_redacted_and_rejected(signed_url: str) -> None:
    redacted = redact_sensitive_text(signed_url)

    assert "SECRET" not in redacted
    assert "URL SIGNÉE MASQUÉE" in redacted
    with pytest.raises(ExportError, match="donnée sensible"):
        assert_shareable_text(signed_url)


def test_ordinary_url_is_not_redacted_or_rejected() -> None:
    ordinary_url = "https://example.com/replay.xml?page=2"

    assert redact_sensitive_text(ordinary_url) == ordinary_url
    assert_shareable_text(ordinary_url)


@pytest.mark.parametrize(
    "marker",
    [
        "accountHi",
        "accountLo",
        "BattleTag",
        "X-Amz-Credential",
        "X-Amz-Security-Token",
        "X-Amz-Signature",
    ],
)
def test_export_guard_rejects_sensitive_markers(marker: str) -> None:
    with pytest.raises(ExportError, match="donnée sensible"):
        assert_shareable_text(f'{{"secret": "{marker}"}}')
