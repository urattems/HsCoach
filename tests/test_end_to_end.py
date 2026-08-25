import hashlib
import json
from pathlib import Path

from hscoach.cards import parse_cards
from hscoach.models import Card
from hscoach.output import export_analysis
from hscoach.replay.parser import analyze_replay_data

PROJECT_ROOT = Path(__file__).parents[1]
SAMPLE = PROJECT_ROOT / "samples" / "sample_replay.hsreplay"
CARDS_CACHE = PROJECT_ROOT / ".cache" / "hearthstonejson" / "frFR" / "cards.json"


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

    assert document["schema_version"] == "1.0"
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
