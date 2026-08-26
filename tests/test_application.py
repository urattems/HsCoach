from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hscoach.application import (
    AnalysisRequest,
    AnalysisService,
    AnalysisStatus,
    CancellationToken,
    ProgressStage,
)
from hscoach.config import AppConfig
from hscoach.exceptions import ReplayInputError
from hscoach.input import LoadedReplay
from hscoach.input.sources import ReplaySourceKind
from hscoach.models import GameAnalysis, Player, PlayerSide, ReplayMetadata
from hscoach.output import ExportedReports, export_analysis


def _analysis(game_id: str = "42") -> GameAnalysis:
    return GameAnalysis(
        metadata=ReplayMetadata(game_id=game_id, result="Victoire"),
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
            card_class="Mage",
        ),
    )


@dataclass
class FakeSource:
    display_label: str
    data: bytes = b"replay"
    error: ReplayInputError | None = None
    kind: ReplaySourceKind = ReplaySourceKind.LOCAL
    load_count: int = 0

    def load(self, **kwargs) -> LoadedReplay:
        del kwargs
        self.load_count += 1
        if self.error is not None:
            raise self.error
        return LoadedReplay(self.data, self.display_label)


class FakeCards:
    load_count = 0

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def load(self):
        type(self).load_count += 1
        return {}


def test_batch_continues_after_error_and_loads_cards_once(tmp_path: Path) -> None:
    FakeCards.load_count = 0
    first = FakeSource("premier.xml", b"first")
    broken = FakeSource("cassé.xml", error=ReplayInputError("Replay invalide."))
    last = FakeSource("dernier.xml", b"last")
    analyzed: list[bytes] = []
    exported: list[str] = []
    progress = []

    def analyzer(data, cards, **kwargs):
        del cards, kwargs
        analyzed.append(data)
        return _analysis(str(len(analyzed)))

    def exporter(analysis, output_directory, **kwargs):
        del output_directory
        exported.append(analysis.metadata.game_id)
        assert kwargs == {
            "export_markdown": True,
            "export_full_json": True,
            "export_llm_json": True,
        }
        return ExportedReports()

    service = AnalysisService(
        AppConfig(cache_directory=tmp_path / "cache"),
        card_provider_factory=FakeCards,
        analyzer=analyzer,
        exporter=exporter,
    )
    result = service.analyze_batch(
        AnalysisRequest(sources=(first, broken, last), output_directory=tmp_path / "out"),
        progress=progress.append,
    )

    assert [item.status for item in result.results] == [
        AnalysisStatus.SUCCESS,
        AnalysisStatus.ERROR,
        AnalysisStatus.SUCCESS,
    ]
    assert result.success_count == 2
    assert result.error_count == 1
    assert analyzed == [b"first", b"last"]
    assert exported == ["1", "2"]
    assert FakeCards.load_count == 1
    assert progress[-1].stage is ProgressStage.BATCH_COMPLETE


def test_cancellation_finishes_current_source_and_skips_pending_sources(tmp_path: Path) -> None:
    FakeCards.load_count = 0
    token = CancellationToken()
    first = FakeSource("premier.xml")
    second = FakeSource("second.xml")

    def analyzer(data, cards, **kwargs):
        del data, cards, kwargs
        token.cancel()
        return _analysis()

    service = AnalysisService(
        AppConfig(cache_directory=tmp_path / "cache"),
        card_provider_factory=FakeCards,
        analyzer=analyzer,
        exporter=lambda *args, **kwargs: ExportedReports(),
    )
    result = service.analyze_batch(
        AnalysisRequest(sources=(first, second), output_directory=tmp_path / "out"),
        cancellation=token,
    )

    assert [item.status for item in result.results] == [
        AnalysisStatus.SUCCESS,
        AnalysisStatus.CANCELLED,
    ]
    assert first.load_count == 1
    assert second.load_count == 0


def test_pre_cancelled_batch_does_not_touch_sources_or_output(tmp_path: Path) -> None:
    source = FakeSource("jamais-lancé.xml")
    token = CancellationToken()
    token.cancel()
    output_directory = tmp_path / "non-cree"
    service = AnalysisService(
        AppConfig(cache_directory=tmp_path / "cache"),
        card_provider_factory=FakeCards,
        analyzer=lambda *args, **kwargs: _analysis(),
        exporter=lambda *args, **kwargs: ExportedReports(),
    )

    result = service.analyze_batch(
        AnalysisRequest(sources=(source,), output_directory=output_directory),
        cancellation=token,
    )

    assert result.results[0].status is AnalysisStatus.CANCELLED
    assert source.load_count == 0
    assert not output_directory.exists()


def test_inspect_uses_same_loading_and_analysis_without_export(tmp_path: Path) -> None:
    source = FakeSource("inspect.xml")
    export_called = False

    def exporter(*args, **kwargs):
        nonlocal export_called
        del args, kwargs
        export_called = True
        return ExportedReports()

    service = AnalysisService(
        AppConfig(cache_directory=tmp_path / "cache"),
        card_provider_factory=FakeCards,
        analyzer=lambda *args, **kwargs: _analysis("inspect"),
        exporter=exporter,
    )

    analysis = service.inspect(source)

    assert analysis.metadata.game_id == "inspect"
    assert source.load_count == 1
    assert export_called is False


def test_selective_exports_keep_historical_defaults(tmp_path: Path) -> None:
    analysis = _analysis()

    selective = export_analysis(
        analysis,
        tmp_path / "selective",
        export_markdown=True,
        export_llm_json=True,
        export_full_json=False,
    )
    historical = export_analysis(analysis, tmp_path / "historical")

    assert selective.markdown is not None and selective.markdown.is_file()
    assert selective.llm is not None and selective.llm.is_file()
    assert selective.json is None
    assert not list((tmp_path / "selective").rglob("game_analysis.json"))
    assert historical.markdown is not None and historical.markdown.is_file()
    assert historical.llm is not None and historical.llm.is_file()
    assert historical.json is not None and historical.json.is_file()
