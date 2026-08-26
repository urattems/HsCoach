from __future__ import annotations

import os
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QTimer

from hscoach.application import (
    AnalysisProgress,
    AnalysisResult,
    AnalysisStatus,
    BatchAnalysisResult,
    ProgressStage,
)
from hscoach.gui.controller import ReplayQueue
from hscoach.gui.main_window import MainWindow
from hscoach.gui.settings import GuiPreferences, SettingsStore
from hscoach.models import GameAnalysis, Player, PlayerSide, ReplayMetadata
from hscoach.output import ExportedReports


def _analysis() -> GameAnalysis:
    return GameAnalysis(
        metadata=ReplayMetadata(game_id="42", result="Victoire"),
        player=Player(
            side=PlayerSide.PLAYER,
            entity_id=2,
            player_id=1,
            card_class="Chaman",
        ),
        opponent=Player(
            side=PlayerSide.OPPONENT,
            entity_id=3,
            player_id=2,
            card_class="Paladin",
        ),
    )


def _settings_store(tmp_path: Path) -> SettingsStore:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.clear()
    return SettingsStore(settings)


def _replay(tmp_path: Path, name: str = "game.xml") -> Path:
    path = tmp_path / name
    path.write_text('<HSReplay build="1"><Game id="42" /></HSReplay>', encoding="utf-8")
    return path


def test_default_gui_exports_markdown_and_llm_only(qtbot, tmp_path: Path) -> None:
    window = MainWindow(settings_store=_settings_store(tmp_path))
    qtbot.addWidget(window)

    assert window.markdown_checkbox.isChecked() is True
    assert window.llm_checkbox.isChecked() is True
    assert window.full_json_checkbox.isChecked() is False


def test_settings_persist_only_non_sensitive_preferences(tmp_path: Path) -> None:
    store = _settings_store(tmp_path)
    store.save(
        GuiPreferences(
            output_directory=tmp_path / "rapports",
            export_markdown=False,
            export_llm_json=True,
            export_full_json=True,
            open_after_analysis=True,
        )
    )

    loaded = store.load()

    assert loaded.output_directory == tmp_path / "rapports"
    assert loaded.export_markdown is False
    assert loaded.export_llm_json is True
    assert loaded.export_full_json is True
    keys = set(store.settings.allKeys())
    assert keys == {
        "export_full_json",
        "export_llm_json",
        "export_markdown",
        "open_after_analysis",
        "output_directory",
    }
    assert all("url" not in key.casefold() for key in keys)


def test_queue_accepts_multiple_replays_and_rejects_json(tmp_path: Path) -> None:
    queue = ReplayQueue()
    first = _replay(tmp_path, "one.hsreplay")
    second = _replay(tmp_path, "two.txt")
    bad = tmp_path / "game_llm.json"
    bad.write_text("{}", encoding="utf-8")

    queue.add(first)
    queue.add(second)

    assert [item.label for item in queue.items] == ["one.hsreplay", "two.txt"]
    try:
        queue.add(bad)
    except Exception as exc:
        assert "Extension" in str(exc)
    else:
        raise AssertionError("Le JSON IA ne doit jamais être accepté comme replay.")


def test_url_is_cleared_and_only_safe_label_is_displayed(qtbot, tmp_path: Path) -> None:
    window = MainWindow(settings_store=_settings_store(tmp_path))
    qtbot.addWidget(window)
    secret = "SUPER_SECRET"
    window.url_input.setText(f"https://replays.example/private/game.xml?X-Amz-Signature={secret}")

    qtbot.mouseClick(window.url_add_button, Qt.MouseButton.LeftButton)

    assert window.url_input.text() == ""
    assert window.url_input.isUndoAvailable() is False
    window.url_input.undo()
    assert secret not in window.url_input.text()
    displayed = window.source_table.item(0, 0).text()
    assert displayed == "replays.example/…/game.xml"
    assert secret not in displayed


def test_queue_redacts_battletag_in_local_filename(tmp_path: Path) -> None:
    queue = ReplayQueue()
    replay = _replay(tmp_path, "Alice#1234.hsreplay")

    item = queue.add(replay)

    assert item.label == "JOUEUR.hsreplay"
    assert "Alice#1234" not in item.label


def test_worker_keeps_ui_responsive_and_passes_gui_export_flags(qtbot, tmp_path: Path) -> None:
    started = Event()
    release = Event()
    captured_requests = []
    summary = tmp_path / "out" / "42" / "game_summary.md"
    llm = tmp_path / "out" / "42" / "game_llm.json"
    summary.parent.mkdir(parents=True)
    summary.write_text("# Résumé", encoding="utf-8")
    llm.write_text("{}", encoding="utf-8")

    class BlockingService:
        def analyze_batch(self, request, *, progress, cancellation):
            del cancellation
            captured_requests.append(request)
            progress(
                AnalysisProgress(
                    stage=ProgressStage.STARTED,
                    source_index=1,
                    total_sources=1,
                    completed_sources=0,
                    source_label="game.xml",
                    message="Analyse en cours…",
                )
            )
            started.set()
            assert release.wait(5)
            result = AnalysisResult(
                source_label="game.xml",
                status=AnalysisStatus.SUCCESS,
                analysis=_analysis(),
                reports=ExportedReports(markdown=summary, llm=llm),
            )
            return BatchAnalysisResult([result])

    window = MainWindow(
        settings_store=_settings_store(tmp_path),
        service_factory=lambda config: BlockingService(),
    )
    qtbot.addWidget(window)
    window.output_input.setText(str(tmp_path / "out"))
    window.drop_zone.files_dropped.emit([str(_replay(tmp_path))])
    assert window.analyse_button.isEnabled()

    qtbot.mouseClick(window.analyse_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(started.is_set, timeout=2_000)
    ui_tick = []
    QTimer.singleShot(0, lambda: ui_tick.append(True))
    qtbot.waitUntil(lambda: bool(ui_tick), timeout=2_000)
    release.set()
    qtbot.waitUntil(lambda: window._thread is None, timeout=5_000)

    request = captured_requests[0]
    assert request.export_markdown is True
    assert request.export_llm_json is True
    assert request.export_full_json is False
    assert window.result_list.item(0).text() == "✓ Chaman vs Paladin — Victoire"


def test_cancel_button_sets_cooperative_token(qtbot, tmp_path: Path) -> None:
    started = Event()
    release = Event()
    tokens = []

    class BlockingService:
        def analyze_batch(self, request, *, progress, cancellation):
            del request, progress
            tokens.append(cancellation)
            started.set()
            assert release.wait(5)
            return BatchAnalysisResult(
                [
                    AnalysisResult(
                        source_label="game.xml",
                        status=AnalysisStatus.CANCELLED,
                        error_message="Analyse annulée.",
                    )
                ]
            )

    window = MainWindow(
        settings_store=_settings_store(tmp_path),
        service_factory=lambda config: BlockingService(),
    )
    qtbot.addWidget(window)
    window.output_input.setText(str(tmp_path / "out"))
    window.drop_zone.files_dropped.emit([str(_replay(tmp_path))])
    qtbot.mouseClick(window.analyse_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(started.is_set, timeout=2_000)

    qtbot.mouseClick(window.cancel_button, Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: tokens[0].is_cancelled, timeout=2_000)
    assert "Annulation demandée" in window.progress_label.text()
    release.set()
    qtbot.waitUntil(lambda: window._thread is None, timeout=5_000)
    assert "Annulé" in window.result_list.item(0).text()
