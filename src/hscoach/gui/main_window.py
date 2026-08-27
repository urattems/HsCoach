"""Fenêtre desktop simple de Hearthstone Replay Analyzer."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hscoach.application import (
    AnalysisProgress,
    AnalysisRequest,
    AnalysisResult,
    AnalysisService,
    AnalysisStatus,
    BatchAnalysisResult,
    CancellationToken,
    ProgressStage,
)
from hscoach.config import AppConfig
from hscoach.exceptions import ReplayInputError
from hscoach.gui.controller import QueueStatus, ReplayQueue
from hscoach.gui.settings import (
    GuiPreferences,
    SettingsStore,
    default_cache_directory,
)
from hscoach.gui.worker import AnalysisWorker
from hscoach.input.sources import RawXmlSource


class DropZone(QFrame):
    """Zone de dépôt qui n'accepte que des fichiers locaux."""

    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAccessibleName("Zone de dépôt des replays")
        self.setMinimumHeight(135)
        layout = QVBoxLayout(self)
        title = QLabel("Glissez vos replays Hearthstone ici")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        formats = QLabel("Formats : .hsreplay, .xml, .txt")
        formats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(formats)
        layout.addStretch()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls() and any(url.isLocalFile() for url in mime.urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class MainWindow(QMainWindow):
    """Frontend Qt sans logique Hearthstone dupliquée."""

    def __init__(
        self,
        *,
        service_factory: Callable[[AppConfig], AnalysisService] = AnalysisService,
        settings_store: SettingsStore | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Hearthstone Replay Analyzer")
        self.setMinimumSize(700, 560)
        self.resize(900, 720)
        self._settings = settings_store or SettingsStore()
        self._preferences = self._settings.load()
        self._service_factory = service_factory
        self._queue = ReplayQueue()
        self._thread: QThread | None = None
        self._worker: AnalysisWorker | None = None
        self._cancellation: CancellationToken | None = None
        self._batch_result: BatchAnalysisResult | None = None
        self._running = False
        self._build_ui()
        self._load_preferences()
        geometry = self._settings.load_geometry()
        if geometry is not None:
            self.restoreGeometry(geometry)
        self._update_analyse_enabled()

    def _build_ui(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)

        heading = QLabel("Hearthstone Replay Analyzer")
        heading.setStyleSheet("font-size: 24px; font-weight: 650;")
        outer.addWidget(heading)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._add_sources)
        outer.addWidget(self.drop_zone)

        self.browse_button = QPushButton("Parcourir…")
        self.browse_button.setToolTip("Sélectionner un ou plusieurs fichiers de replay")
        self.browse_button.clicked.connect(self._browse_replays)
        outer.addWidget(self.browse_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        url_layout = QHBoxLayout()
        url_label = QLabel("Ou collez une URL")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("URL XML ou HSReplay…")
        self.url_input.setAccessibleName("URL XML ou page HSReplay")
        url_label.setBuddy(self.url_input)
        self.url_add_button = QPushButton("Ajouter")
        self.url_add_button.clicked.connect(self._add_url)
        self.url_input.returnPressed.connect(self._add_url)
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input, 1)
        url_layout.addWidget(self.url_add_button)
        outer.addLayout(url_layout)

        raw_xml_group = QGroupBox("Ou collez le contenu XML brut")
        raw_xml_layout = QVBoxLayout(raw_xml_group)
        self.raw_xml_input = QPlainTextEdit()
        self.raw_xml_input.setPlaceholderText("Collez ici le contenu XML brut du replay…")
        self.raw_xml_input.setAccessibleName("Contenu XML brut du replay")
        self.raw_xml_input.setMaximumHeight(130)
        self.raw_xml_analyse_button = QPushButton("Analyser ce texte")
        self.raw_xml_analyse_button.setEnabled(False)
        self.raw_xml_input.textChanged.connect(self._update_raw_xml_enabled)
        self.raw_xml_analyse_button.clicked.connect(self._analyse_raw_xml)
        raw_xml_layout.addWidget(self.raw_xml_input)
        raw_xml_layout.addWidget(self.raw_xml_analyse_button, alignment=Qt.AlignmentFlag.AlignRight)
        outer.addWidget(raw_xml_group)

        self.source_table = QTableWidget(0, 3)
        self.source_table.setHorizontalHeaderLabels(["Source", "État", "Action"])
        self.source_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.source_table.verticalHeader().setVisible(False)
        header = self.source_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.source_table.setAccessibleName("Replays à analyser")
        outer.addWidget(self.source_table, 1)

        output_group = QGroupBox("Dossier de sortie")
        output_layout = QHBoxLayout(output_group)
        self.output_input = QLineEdit()
        self.output_input.setReadOnly(True)
        self.output_input.setAccessibleName("Dossier de sortie")
        self.output_input.textChanged.connect(self._update_analyse_enabled)
        self.output_button = QPushButton("Choisir…")
        self.output_button.clicked.connect(self._choose_output_directory)
        output_layout.addWidget(self.output_input, 1)
        output_layout.addWidget(self.output_button)
        outer.addWidget(output_group)

        options_group = QGroupBox("Formats de sortie")
        options_layout = QGridLayout(options_group)
        self.markdown_checkbox = QCheckBox("Résumé Markdown")
        self.llm_checkbox = QCheckBox("JSON pour IA")
        self.full_json_checkbox = QCheckBox("JSON complet")
        self.full_json_checkbox.setToolTip("Rapport exhaustif surtout utile au diagnostic")
        self.open_after_checkbox = QCheckBox("Ouvrir le dossier après l'analyse")
        for checkbox in (
            self.markdown_checkbox,
            self.llm_checkbox,
            self.full_json_checkbox,
            self.open_after_checkbox,
        ):
            checkbox.toggled.connect(self._preferences_changed)
        options_layout.addWidget(self.markdown_checkbox, 0, 0)
        options_layout.addWidget(self.llm_checkbox, 0, 1)
        options_layout.addWidget(self.full_json_checkbox, 0, 2)
        options_layout.addWidget(self.open_after_checkbox, 1, 0, 1, 3)
        outer.addWidget(options_group)

        action_layout = QHBoxLayout()
        self.analyse_button = QPushButton("ANALYSER")
        self.analyse_button.setMinimumHeight(44)
        self.analyse_button.setStyleSheet("font-size: 16px; font-weight: 650;")
        self.analyse_button.clicked.connect(self._start_analysis)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_analysis)
        action_layout.addWidget(self.analyse_button, 1)
        action_layout.addWidget(self.cancel_button)
        outer.addLayout(action_layout)

        self.progress_label = QLabel("Prêt.")
        self.progress_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        outer.addWidget(self.progress_label)
        outer.addWidget(self.progress_bar)

        result_group = QGroupBox("Résultats")
        result_layout = QVBoxLayout(result_group)
        self.result_list = QListWidget()
        self.result_list.currentRowChanged.connect(self._update_result_actions)
        result_layout.addWidget(self.result_list)
        result_actions = QHBoxLayout()
        self.view_summary_button = QPushButton("Voir le résumé")
        self.open_folder_button = QPushButton("Ouvrir le dossier")
        self.copy_llm_button = QPushButton("Copier le chemin du JSON IA")
        self.view_summary_button.clicked.connect(self._view_summary)
        self.open_folder_button.clicked.connect(self._open_result_folder)
        self.copy_llm_button.clicked.connect(self._copy_llm_path)
        result_actions.addWidget(self.view_summary_button)
        result_actions.addWidget(self.open_folder_button)
        result_actions.addWidget(self.copy_llm_button)
        result_layout.addLayout(result_actions)
        outer.addWidget(result_group)
        self._update_result_actions()

    def _load_preferences(self) -> None:
        preferences = self._preferences
        checkboxes = (
            self.markdown_checkbox,
            self.llm_checkbox,
            self.full_json_checkbox,
            self.open_after_checkbox,
        )
        blockers = [QSignalBlocker(checkbox) for checkbox in checkboxes]
        self.output_input.setText(str(preferences.output_directory))
        self.markdown_checkbox.setChecked(preferences.export_markdown)
        self.llm_checkbox.setChecked(preferences.export_llm_json)
        self.full_json_checkbox.setChecked(preferences.export_full_json)
        self.open_after_checkbox.setChecked(preferences.open_after_analysis)
        del blockers

    def _browse_replays(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Sélectionner des replays Hearthstone",
            "",
            "Replays Hearthstone (*.hsreplay *.xml *.txt);;Tous les fichiers (*)",
        )
        self._add_sources(paths)

    def _add_url(self) -> None:
        candidate = self.url_input.text().strip()
        if not candidate:
            return
        if self._add_sources([candidate]):
            # setText purge aussi l'historique undo/redo : Ctrl+Z ne doit jamais
            # pouvoir restaurer une URL signée après son ajout.
            self.url_input.setText("")

    def _analyse_raw_xml(self) -> None:
        content = self.raw_xml_input.toPlainText()
        if not content.strip() or self._running:
            return
        if len(content.encode("utf-8")) > self._queue.max_size_bytes:
            QMessageBox.warning(
                self,
                "XML brut trop volumineux",
                "Le contenu XML dépasse la limite autorisée de 50 Mio.",
            )
            return
        try:
            source = RawXmlSource(content)
            source.load(max_size_bytes=self._queue.max_size_bytes)
        except ReplayInputError as exc:
            QMessageBox.warning(self, "XML brut invalide", str(exc))
            return
        if self._add_sources([source]):
            self.raw_xml_input.clear()
            if self.analyse_button.isEnabled():
                self._start_analysis()

    def _update_raw_xml_enabled(self) -> None:
        content = self.raw_xml_input.toPlainText()
        self.raw_xml_analyse_button.setEnabled(
            not self._running
            and bool(content.strip())
            and len(content.encode("utf-8")) <= self._queue.max_size_bytes
        )

    def _add_sources(self, sources: Iterable[str | Path | RawXmlSource]) -> bool:
        added = False
        errors: list[str] = []
        for source in sources:
            try:
                self._queue.add(source)
                added = True
            except ReplayInputError as exc:
                errors.append(str(exc))
        self._rebuild_source_table()
        self._update_analyse_enabled()
        if errors:
            QMessageBox.warning(self, "Replay non ajouté", "\n".join(errors))
        return added

    def _remove_source(self, identifier: str) -> None:
        if self._running:
            return
        self._queue.remove(identifier)
        self._rebuild_source_table()
        self._update_analyse_enabled()

    def _rebuild_source_table(self) -> None:
        self.source_table.setRowCount(len(self._queue.items))
        for row, item in enumerate(self._queue.items):
            source_cell = QTableWidgetItem(item.label)
            source_cell.setToolTip(item.label)
            status_text = item.status.value
            if item.detail:
                status_text = f"{status_text} — {item.detail}"
            status_cell = QTableWidgetItem(status_text)
            remove_button = QPushButton("Supprimer")
            remove_button.setToolTip(f"Retirer {item.label} de la liste")
            remove_button.setEnabled(not self._running)
            remove_button.clicked.connect(
                lambda checked=False, identifier=item.identifier: self._remove_source(identifier)
            )
            self.source_table.setItem(row, 0, source_cell)
            self.source_table.setItem(row, 1, status_cell)
            self.source_table.setCellWidget(row, 2, remove_button)

    def _choose_output_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choisir le dossier de sortie",
            self.output_input.text(),
        )
        if selected:
            self.output_input.setText(selected)
            self._preferences_changed()

    def _preferences_changed(self) -> None:
        raw_output = self.output_input.text().strip()
        self._preferences = GuiPreferences(
            output_directory=(
                Path(raw_output) if raw_output else self._preferences.output_directory
            ),
            export_markdown=self.markdown_checkbox.isChecked(),
            export_llm_json=self.llm_checkbox.isChecked(),
            export_full_json=self.full_json_checkbox.isChecked(),
            open_after_analysis=self.open_after_checkbox.isChecked(),
        )
        self._settings.save(self._preferences)
        self._update_analyse_enabled()

    def _update_analyse_enabled(self) -> None:
        has_export = (
            self.markdown_checkbox.isChecked()
            or self.llm_checkbox.isChecked()
            or self.full_json_checkbox.isChecked()
        )
        enabled = (
            not self._running
            and bool(self._queue.items)
            and has_export
            and _output_path_can_be_created(self.output_input.text())
        )
        self.analyse_button.setEnabled(enabled)

    def _start_analysis(self) -> None:
        if not self.analyse_button.isEnabled() or self._running:
            return
        self._preferences_changed()
        self._queue.reset_statuses()
        self._rebuild_source_table()
        self.result_list.clear()
        self._batch_result = None
        self._cancellation = CancellationToken()
        request = AnalysisRequest(
            sources=tuple(item.source for item in self._queue.items),
            output_directory=self._preferences.output_directory,
            export_markdown=self._preferences.export_markdown,
            export_full_json=self._preferences.export_full_json,
            export_llm_json=self._preferences.export_llm_json,
        )
        config = AppConfig(
            output_directory=self._preferences.output_directory,
            cache_directory=default_cache_directory(),
        )
        service = self._service_factory(config)
        self._thread = QThread(self)
        self._worker = AnalysisWorker(service, request, self._cancellation)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_batch_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._set_running(True)
        self.progress_bar.setRange(0, len(self._queue.items))
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%v / %m parties analysées")
        self.progress_bar.setVisible(True)
        self.progress_label.setText("Analyse en cours…")
        self._thread.start()

    def _cancel_analysis(self) -> None:
        if self._cancellation is None:
            return
        self._cancellation.cancel()
        self.cancel_button.setEnabled(False)
        self.progress_label.setText(
            "Annulation demandée. Le replay en cours se terminera proprement."
        )

    def _on_progress(self, progress: AnalysisProgress) -> None:
        if 1 <= progress.source_index <= len(self._queue.items):
            item = self._queue.items[progress.source_index - 1]
            if progress.stage is ProgressStage.STARTED:
                item.status = QueueStatus.RUNNING
            elif progress.stage is ProgressStage.FAILED:
                item.status = QueueStatus.ERROR
                item.detail = progress.message
            elif progress.stage is ProgressStage.CANCELLED:
                item.status = QueueStatus.CANCELLED
                item.detail = progress.message
            elif progress.stage is ProgressStage.REPORTS_GENERATED:
                item.status = QueueStatus.SUCCESS
            self._rebuild_source_table()
        self.progress_bar.setValue(progress.completed_sources)
        self.progress_label.setText(
            f"{progress.source_label} — {progress.message}"
            if progress.source_label
            else progress.message
        )

    def _on_batch_finished(self, batch: BatchAnalysisResult) -> None:
        self._batch_result = batch
        self.result_list.clear()
        for index, result in enumerate(batch.results):
            if index < len(self._queue.items):
                queue_item = self._queue.items[index]
                queue_item.status = {
                    AnalysisStatus.SUCCESS: QueueStatus.SUCCESS,
                    AnalysisStatus.ERROR: QueueStatus.ERROR,
                    AnalysisStatus.CANCELLED: QueueStatus.CANCELLED,
                }[result.status]
                queue_item.detail = result.error_message
            self.result_list.addItem(_result_label(result))
        self._rebuild_source_table()
        self.progress_bar.setValue(len(batch.results))
        self.progress_label.setText(
            f"{batch.success_count} réussie(s), {batch.error_count} erreur(s), "
            f"{batch.cancelled_count} annulée(s)."
        )
        if self.result_list.count():
            self.result_list.setCurrentRow(0)
        if self._preferences.open_after_analysis and batch.success_count:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._preferences.output_directory)))

    def _on_worker_failed(self, message: str) -> None:
        for item in self._queue.items:
            if item.status in {QueueStatus.PENDING, QueueStatus.RUNNING}:
                item.status = QueueStatus.ERROR
                item.detail = message
        self._rebuild_source_table()
        self.progress_label.setText(message)

    def _on_thread_finished(self) -> None:
        self._worker = None
        self._thread = None
        self._cancellation = None
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.browse_button.setEnabled(not running)
        self.url_input.setEnabled(not running)
        self.url_add_button.setEnabled(not running)
        self.raw_xml_input.setEnabled(not running)
        self._update_raw_xml_enabled()
        self.output_input.setEnabled(not running)
        self.output_button.setEnabled(not running)
        self.markdown_checkbox.setEnabled(not running)
        self.llm_checkbox.setEnabled(not running)
        self.full_json_checkbox.setEnabled(not running)
        self.open_after_checkbox.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self._rebuild_source_table()
        self._update_analyse_enabled()

    def _selected_result(self) -> AnalysisResult | None:
        if self._batch_result is None:
            return None
        row = self.result_list.currentRow()
        if 0 <= row < len(self._batch_result.results):
            return self._batch_result.results[row]
        return None

    def _update_result_actions(self, row: int = -1) -> None:
        del row
        result = self._selected_result()
        reports = result.reports if result is not None else None
        self.view_summary_button.setEnabled(bool(reports and reports.markdown))
        self.open_folder_button.setEnabled(bool(reports and reports.directory))
        self.copy_llm_button.setEnabled(bool(reports and reports.llm))

    def _view_summary(self) -> None:
        result = self._selected_result()
        if result and result.reports and result.reports.markdown:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.reports.markdown)))

    def _open_result_folder(self) -> None:
        result = self._selected_result()
        if result and result.reports and result.reports.directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.reports.directory)))

    def _copy_llm_path(self) -> None:
        result = self._selected_result()
        if result and result.reports and result.reports.llm:
            QApplication.clipboard().setText(str(result.reports.llm))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._settings.save_geometry(self.saveGeometry())
        if self._running:
            if self._cancellation is not None:
                self._cancellation.cancel()
            QMessageBox.information(
                self,
                "Analyse en cours",
                "L'analyse en cours doit se terminer proprement avant de fermer l'application.",
            )
            event.ignore()
            return
        event.accept()


def _result_label(result: AnalysisResult) -> str:
    if result.status is AnalysisStatus.SUCCESS and result.analysis is not None:
        analysis = result.analysis
        return (
            f"✓ {analysis.player.card_class} vs {analysis.opponent.card_class} — "
            f"{analysis.metadata.result}"
        )
    if result.status is AnalysisStatus.CANCELLED:
        return f"– {result.source_label} — Annulé"
    return f"✕ {result.source_label} — {result.error_message or 'Erreur'}"


def _output_path_can_be_created(raw_path: str) -> bool:
    candidate = raw_path.strip()
    if not candidate:
        return False
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        return False
    if path.exists():
        return path.is_dir() and os.access(path, os.W_OK)
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


__all__ = ["DropZone", "MainWindow"]
