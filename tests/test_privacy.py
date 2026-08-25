from pathlib import Path
from xml.etree import ElementTree

import pytest

from hscoach.exceptions import ExportError
from hscoach.privacy import assert_shareable_text, redact_sensitive_text
from hscoach.replay.parser import analyze_replay_data

SAMPLE = Path(__file__).parents[1] / "samples" / "sample_replay.hsreplay"


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
        "Urattems596#2531 "
        "https://bucket.example/replay.xml?X-Amz-Credential=ABC&X-Amz-Signature=SECRET"
    )

    redacted = redact_sensitive_text(raw)

    assert "Urattems" not in redacted
    assert "SECRET" not in redacted
    assert "bucket.example" in redacted


def test_export_guard_rejects_sensitive_markers() -> None:
    with pytest.raises(ExportError, match="donnée sensible"):
        assert_shareable_text('{"accountHi": "123"}')
