import hashlib
import json
from pathlib import Path

import pytest

from hscoach.cards import parse_cards
from hscoach.models import Card
from hscoach.output import export_analysis
from hscoach.replay.parser import analyze_replay_data

PROJECT_ROOT = Path(__file__).parents[1]
SAMPLE = PROJECT_ROOT / "samples" / "sample_replay.hsreplay"
MINIMAL_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "minimal_replay.hsreplay"
CARDS_CACHE = PROJECT_ROOT / ".cache" / "hearthstonejson" / "frFR" / "cards.json"
requires_user_sample = pytest.mark.skipif(
    not SAMPLE.is_file(), reason="Replay utilisateur local non disponible."
)


def test_anonymous_versioned_fixture_runs_the_full_pipeline(tmp_path: Path) -> None:
    cards = {
        "TEST_DRAW": Card(id="TEST_DRAW", name="Carte de test", cost=1, type="SPELL"),
        "TEST_HERO_PLAYER": Card(id="TEST_HERO_PLAYER", name="Héros test", type="HERO"),
        "TEST_HERO_OPPONENT": Card(id="TEST_HERO_OPPONENT", name="Héros adverse test", type="HERO"),
    }

    analysis = analyze_replay_data(MINIMAL_FIXTURE.read_bytes(), cards)
    reports = export_analysis(analysis, tmp_path)

    assert analysis.schema_version == "2.0"
    assert analysis.metadata.game_id == "fixture-anonyme"
    assert analysis.metadata.result == "Victoire"
    assert len(analysis.turns) == 1
    assert analysis.turns[0].turn_start_state is not None
    assert analysis.turns[0].action_phase_start_state is not None
    assert analysis.turns[0].action_phase_end_state is not None
    assert analysis.turns[0].turn_end_state is not None
    assert reports.markdown.is_file()
    assert reports.json.is_file()
    assert reports.llm is not None and reports.llm.is_file()
    report_paths = (reports.markdown, reports.json, reports.llm)
    combined = "".join(
        path.read_text(encoding="utf-8") for path in report_paths if path is not None
    )
    assert "accountHi" not in combined
    assert "accountLo" not in combined


@requires_user_sample
def test_real_sample_generates_private_french_reports(tmp_path: Path) -> None:
    replay_bytes = SAMPLE.read_bytes()
    hash_before = hashlib.sha256(replay_bytes).hexdigest()
    cards = (
        parse_cards(CARDS_CACHE.read_bytes())
        if CARDS_CACHE.exists()
        else {
            "JAIL_850": Card(
                id="JAIL_850",
                name="Maiev la Gardienne",
                cost=4,
                attack=1,
                health=3,
            )
        }
    )

    analysis = analyze_replay_data(replay_bytes, cards)
    reports = export_analysis(analysis, tmp_path)

    assert hashlib.sha256(SAMPLE.read_bytes()).hexdigest() == hash_before
    assert reports.markdown.is_file() and reports.markdown.stat().st_size > 1_000
    assert reports.json.is_file() and reports.json.stat().st_size > 10_000

    markdown = reports.markdown.read_text(encoding="utf-8")
    document = json.loads(reports.json.read_text(encoding="utf-8"))
    combined = markdown + reports.json.read_text(encoding="utf-8")

    assert document["schema_version"] == "2.0"
    assert document["game"]["game_id"] == "151665"
    assert document["game"]["result"] == "Victoire"
    assert len(document["turns"]) == 12
    assert "Joueur : Chaman" in markdown
    assert "Adversaire : Paladin" in markdown
    assert "Maiev la Gardienne — 1/7" in markdown
    assert "cartes inconnues" in markdown
    assert "elles ne représentent pas toutes les lignes stratégiques possibles" in markdown
    assert "Warden Maiev" not in combined
    assert "accountHi" not in combined
    assert "accountLo" not in combined
