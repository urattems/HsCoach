"""Orchestration unique du chargement, de l'analyse et des exports."""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx

from hscoach.application.cancellation import CancellationToken
from hscoach.application.requests import AnalysisRequest
from hscoach.application.results import (
    AnalysisProgress,
    AnalysisResult,
    AnalysisStatus,
    BatchAnalysisResult,
    ProgressStage,
)
from hscoach.cards import HearthstoneJSON
from hscoach.config import AppConfig
from hscoach.exceptions import CardDataError, ExportError, HSCoachError
from hscoach.input.sources import (
    RawXmlSource,
    ReplaySource,
    classify_replay_source,
    safe_source_label,
)
from hscoach.models import Card, GameAnalysis
from hscoach.models.game import ParseWarning
from hscoach.output import ExportedReports, export_analysis
from hscoach.replay.parser import analyze_replay_data

LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[AnalysisProgress], None]


class AnalysisService:
    """Service synchrone, indépendant de Qt, partagé par tous les frontends."""

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        http_client: httpx.Client | None = None,
        card_provider_factory: Callable[..., Any] = HearthstoneJSON,
        analyzer: Callable[..., GameAnalysis] = analyze_replay_data,
        exporter: Callable[..., ExportedReports] = export_analysis,
    ) -> None:
        self.config = config or AppConfig()
        self.http_client = http_client
        self._card_provider_factory = card_provider_factory
        self._analyzer = analyzer
        self._exporter = exporter

    def analyze_batch(
        self,
        request: AnalysisRequest,
        *,
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken | None = None,
    ) -> BatchAnalysisResult:
        """Analyser séquentiellement toutes les sources sans abandonner après une erreur."""

        token = cancellation or CancellationToken()
        total = len(request.sources)
        batch = BatchAnalysisResult()
        output_checked = False
        output_error: ExportError | None = None
        cards_by_build: dict[
            str | None, tuple[Mapping[str, Card], Mapping[str, Card] | None, str, str | None]
        ] = {}

        for index, raw_source in enumerate(request.sources, start=1):
            label = safe_source_label(raw_source)
            if token.is_cancelled:
                result = AnalysisResult(
                    source_label=label,
                    status=AnalysisStatus.CANCELLED,
                    error_message="Analyse annulée avant le lancement de ce replay.",
                )
                batch.results.append(result)
                self._emit(
                    progress,
                    ProgressStage.CANCELLED,
                    index,
                    total,
                    len(batch.results),
                    label,
                    result.error_message,
                )
                continue

            self._emit(
                progress,
                ProgressStage.STARTED,
                index,
                total,
                len(batch.results),
                label,
                "Analyse en cours…",
            )
            try:
                if not output_checked:
                    output_checked = True
                    output_error = self._output_error(request)
                if output_error is not None:
                    raise output_error
                source = classify_replay_source(raw_source)
                label = source.display_label
                loaded = source.load(
                    max_size_bytes=self.config.max_download_size_bytes,
                    timeout_seconds=self.config.http_timeout_seconds,
                    client=self.http_client,
                )
                self._emit(
                    progress,
                    ProgressStage.REPLAY_READY,
                    index,
                    total,
                    len(batch.results),
                    label,
                    "Replay chargé et validé.",
                )

                replay_build = self._replay_build(loaded.data)
                if replay_build not in cards_by_build:
                    cards_by_build[replay_build] = self._load_cards(
                        request.allow_en_fallback, build=replay_build
                    )
                cards, english_cards, card_status, card_build = cards_by_build[replay_build]
                self._emit(
                    progress,
                    ProgressStage.CARDS_READY,
                    index,
                    total,
                    len(batch.results),
                    label,
                    "Données françaises des cartes chargées.",
                )

                analysis = self._analyzer(
                    loaded.data,
                    cards,
                    english_cards_by_id=english_cards,
                    allow_en_fallback=request.allow_en_fallback,
                    source_label=loaded.source_label,
                    max_size_bytes=self.config.max_download_size_bytes,
                )
                analysis.metadata.card_data_status = card_status
                analysis.metadata.card_data_build = card_build
                if card_status == "fallback":
                    analysis.warnings.append(
                        ParseWarning(
                            code="hearthstonejson_build_fallback",
                            message=(
                                f"Les données exactes du build {replay_build} sont indisponibles ; "
                                "les définitions HearthstoneJSON courantes ont été utilisées."
                            ),
                        )
                    )
                if (
                    isinstance(source, RawXmlSource)
                    and analysis.metadata.game_id == "partie-inconnue"
                ):
                    analysis.metadata.game_id = source.fallback_game_id
                self._emit(
                    progress,
                    ProgressStage.GAME_RECONSTRUCTED,
                    index,
                    total,
                    len(batch.results),
                    label,
                    "Partie reconstruite.",
                )

                reports = self._exporter(
                    analysis,
                    request.output_directory,
                    export_markdown=request.export_markdown,
                    export_full_json=request.export_full_json,
                    export_llm_json=request.export_llm_json,
                )
                result = AnalysisResult(
                    source_label=label,
                    status=AnalysisStatus.SUCCESS,
                    analysis=analysis,
                    reports=reports,
                )
                batch.results.append(result)
                self._emit(
                    progress,
                    ProgressStage.REPORTS_GENERATED,
                    index,
                    total,
                    len(batch.results),
                    label,
                    "Rapports générés." if request.writes_reports else "Analyse terminée.",
                )
            except HSCoachError as exc:
                result = AnalysisResult(
                    source_label=label,
                    status=AnalysisStatus.ERROR,
                    error_message=str(exc),
                )
                batch.results.append(result)
                self._emit(
                    progress,
                    ProgressStage.FAILED,
                    index,
                    total,
                    len(batch.results),
                    label,
                    str(exc),
                )
            except Exception:
                LOGGER.debug("Erreur interne pendant l'analyse de %s.", label, exc_info=True)
                message = "Une erreur interne inattendue a interrompu l'analyse de ce replay."
                batch.results.append(
                    AnalysisResult(
                        source_label=label,
                        status=AnalysisStatus.ERROR,
                        error_message=message,
                    )
                )
                self._emit(
                    progress,
                    ProgressStage.FAILED,
                    index,
                    total,
                    len(batch.results),
                    label,
                    message,
                )

        self._emit(
            progress,
            ProgressStage.BATCH_COMPLETE,
            total,
            total,
            len(batch.results),
            "Batch",
            (
                f"{batch.success_count} partie(s) analysée(s), "
                f"{batch.error_count} erreur(s), {batch.cancelled_count} annulée(s)."
            ),
        )
        return batch

    def inspect(
        self,
        source: str | Path | ReplaySource,
        *,
        allow_en_fallback: bool = False,
    ) -> GameAnalysis:
        """Analyser une source sans produire de rapport, pour la CLI d'inspection."""

        resolved = classify_replay_source(source)
        loaded = resolved.load(
            max_size_bytes=self.config.max_download_size_bytes,
            timeout_seconds=self.config.http_timeout_seconds,
            client=self.http_client,
        )
        replay_build = self._replay_build(loaded.data)
        cards, english_cards, status, card_build = self._load_cards(
            allow_en_fallback, build=replay_build
        )
        analysis = self._analyzer(
            loaded.data,
            cards,
            english_cards_by_id=english_cards,
            allow_en_fallback=allow_en_fallback,
            source_label=loaded.source_label,
            max_size_bytes=self.config.max_download_size_bytes,
        )
        analysis.metadata.card_data_status = status
        analysis.metadata.card_data_build = card_build
        return analysis

    def refresh_cards(self, *, locale: str | None = None) -> Mapping[str, Card]:
        """Actualiser les cartes via la même configuration applicative."""

        selected_locale = locale or self.config.locale
        try:
            provider = self._card_provider_factory(
                self.config.cache_directory,
                locale=selected_locale,
                timeout=self.config.http_timeout_seconds,
                client=self.http_client,
            )
        except (TypeError, ValueError) as exc:
            raise CardDataError("La locale HearthstoneJSON demandée n'est pas valide.") from exc
        return provider.refresh()

    def _load_cards(
        self, allow_en_fallback: bool, *, build: str | None = None
    ) -> tuple[Mapping[str, Card], Mapping[str, Card] | None, str, str | None]:
        provider = self._card_provider_factory(
            self.config.cache_directory,
            locale=self.config.locale,
            build=build,
            timeout=self.config.http_timeout_seconds,
            client=self.http_client,
        )
        cards = provider.load()
        english_cards = None
        if allow_en_fallback:
            english_cards = self._card_provider_factory(
                self.config.cache_directory,
                locale="enUS",
                build=build,
                timeout=self.config.http_timeout_seconds,
                client=self.http_client,
            ).load()
        status = getattr(provider, "resolution", "exact-build" if build else "latest")
        resolved_build = getattr(provider, "resolved_build", build)
        return cards, english_cards, status, resolved_build

    @staticmethod
    def _replay_build(data: bytes) -> str | None:
        try:
            build = ElementTree.fromstring(data).get("build")
        except (ElementTree.ParseError, ValueError, TypeError):
            return None
        return build if build and build.isdigit() else None

    @staticmethod
    def _emit(
        callback: ProgressCallback | None,
        stage: ProgressStage,
        source_index: int,
        total_sources: int,
        completed_sources: int,
        source_label: str,
        message: str,
    ) -> None:
        if callback is not None:
            callback(
                AnalysisProgress(
                    stage=stage,
                    source_index=source_index,
                    total_sources=total_sources,
                    completed_sources=completed_sources,
                    source_label=source_label,
                    message=message,
                )
            )

    @staticmethod
    def _output_error(request: AnalysisRequest) -> ExportError | None:
        if not request.writes_reports:
            return None
        root = request.output_directory.expanduser().resolve()
        probe_path: Path | None = None
        try:
            root.mkdir(parents=True, exist_ok=True)
            if not root.is_dir():
                raise OSError
            descriptor, raw_path = tempfile.mkstemp(dir=root, prefix=".hscoach-write-test-")
            os.close(descriptor)
            probe_path = Path(raw_path)
        except OSError:
            return ExportError("Le dossier de sortie n'est pas accessible en écriture.")
        finally:
            if probe_path is not None:
                probe_path.unlink(missing_ok=True)
        return None


__all__ = ["AnalysisService", "ProgressCallback"]
