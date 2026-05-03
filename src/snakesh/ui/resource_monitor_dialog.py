from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import cmp_to_key
import ipaddress
import logging
import os
import platform
from threading import Lock
import time

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QSplineSeries, QValueAxis
from PySide6.QtCore import QEvent, QObject, QMetaObject, QSettings, QSize, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QCloseEvent, QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from snakesh.services.resource_monitor import (
    DiskDeviceSample,
    FilesystemEntry,
    GpuAdapterSample,
    GpuSample,
    InterfaceBandwidthEntry,
    ProcessActionResult,
    ProcessCountsSnapshot,
    ProcessEntry,
    ProcessInventorySnapshot,
    ResourceMonitorOverviewCollector,
    ResourceMonitorCancelledError,
    ResourceMonitorSnapshot,
    ResourceProcessCollector,
    perform_process_action,
)
from snakesh.services.diagnostics_service import TRACE_LEVEL, log_worker_failed, log_worker_finished, log_worker_started
from snakesh.services.settings_service import AppSettings
from snakesh.ui.theme import blend_colors, readable_foreground_color


_CARD_COLUMNS = 4
_SORT_ROLE = Qt.UserRole + 200
_SEARCH_ROLE = Qt.UserRole + 201
_TREE_KEY_ROLE = Qt.UserRole + 202
_DEFAULT_PROCESS_SORT_COLUMN = 2
_DEFAULT_PROCESS_SORT_ORDER = Qt.DescendingOrder
_METRIC_CARD_VALUE_MIN_WIDTH = 280
_PROCESS_REFRESH_INTERVAL_VISIBLE_MS = 4000
_PROCESS_REFRESH_INTERVAL_HIDDEN_MS = 5000
_PROCESS_COLUMN_BASE_WIDTHS = (220, 80, 82, 110, 78, 140, 110, 168)
_SLOW_DETAIL_REFRESH_INTERVAL_MS = 5000
_CLOSE_POLL_INTERVAL_MS = 1800
_FAST_OVERVIEW_WARNING_SECONDS = 1.0
_SLOW_OVERVIEW_WARNING_SECONDS = 3.0
_UI_APPLY_STAGE_WARNING_SECONDS = 0.250
_RESOURCE_MONITOR_ZOOM_MIN = 75
_RESOURCE_MONITOR_ZOOM_MAX = 150
_RESOURCE_MONITOR_ZOOM_STEP = 5
_METRIC_CARD_MIN_HEIGHT = 128
_METRIC_CARD_LAYOUT_MARGINS = (16, 14, 16, 14)
_METRIC_CARD_LAYOUT_SPACING = 6
_METRIC_CARD_BASE_WIDTH = (
    _METRIC_CARD_VALUE_MIN_WIDTH + _METRIC_CARD_LAYOUT_MARGINS[0] + _METRIC_CARD_LAYOUT_MARGINS[2]
)
_QT_WIDGET_MAX_SIZE = 16777215


_LOGGER = logging.getLogger(__name__)


def _scaled_dimension(value: int | float, scale: float, *, minimum: int = 1) -> int:
    normalized_scale = max(
        _RESOURCE_MONITOR_ZOOM_MIN / 100.0,
        min(_RESOURCE_MONITOR_ZOOM_MAX / 100.0, float(scale)),
    )
    return max(minimum, int(round(float(value) * normalized_scale)))


class _SortableTreeWidgetItem(QTreeWidgetItem):
    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        column = tree.sortColumn() if tree is not None else 0
        left = self.data(column, _SORT_ROLE)
        right = other.data(column, _SORT_ROLE)
        if left is not None and right is not None:
            try:
                return left < right
            except TypeError:
                pass
        return self.text(column).lower() < other.text(column).lower()


class _TaskWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        task_name: str,
        task: Callable[[Callable[[], bool]], object],
        *,
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__()
        self._task_name = task_name
        self._task = task
        self._context = context or {}

    def _stop_requested(self) -> bool:
        thread = self.thread()
        if not isinstance(thread, QThread):
            return False
        try:
            return bool(thread.isInterruptionRequested())
        except RuntimeError:
            return True

    @staticmethod
    def _safe_emit(signal, *args: object) -> bool:  # noqa: ANN001
        try:
            signal.emit(*args)
        except RuntimeError:
            return False
        return True

    @Slot()
    def run(self) -> None:
        started_at = time.monotonic()
        log_worker_started(self._task_name, context=self._context)
        try:
            if not self._safe_emit(self.succeeded, self._task(self._stop_requested)):
                return
        except ResourceMonitorCancelledError as exc:
            log_worker_finished(
                self._task_name,
                time.monotonic() - started_at,
                cancelled=True,
                context=self._context,
            )
            self._safe_emit(self.failed, str(exc))
        except Exception as exc:  # noqa: BLE001
            log_worker_failed(
                self._task_name,
                time.monotonic() - started_at,
                context=self._context,
            )
            self._safe_emit(self.failed, str(exc))
        else:
            log_worker_finished(
                self._task_name,
                time.monotonic() - started_at,
                context=self._context,
            )
        finally:
            self._safe_emit(self.finished)


@dataclass(slots=True)
class _MetricRowSection:
    group: QGroupBox
    cards: dict[str, _MetricCard]
    charts: dict[str, QChartView] = field(default_factory=dict)
    status_label: QLabel | None = None


@dataclass(slots=True)
class _GpuAdapterSection:
    group: QGroupBox
    cards: dict[str, _MetricCard]
    activity_chart: QChartView
    temperature_chart: QChartView
    status_label: QLabel


class _MetricCard(QFrame):
    def __init__(self, title: str, *, theme: dict[str, QColor | list[QColor]], parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._zoom_scale = 1.0
        self._value_base_point_size = 21.0
        self._value_min_point_size = 13.0
        self._value_fit_tolerance = 0.0
        self._value_font_refresh_pending = False
        self._value_applied_point_size = self._value_base_point_size
        self._applying_value_font = False
        self.setObjectName("metricCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("metricTitle")
        self.value_label = QLabel("--", self)
        self.value_label.setObjectName("metricValue")
        self.value_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.value_label.setMinimumWidth(_METRIC_CARD_VALUE_MIN_WIDTH)
        self.value_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.value_label.installEventFilter(self)
        self.detail_label = QLabel("", self)
        self.detail_label.setObjectName("metricDetail")
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.detail_label.setMinimumWidth(_METRIC_CARD_VALUE_MIN_WIDTH)
        self.detail_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label, 1)
        self._apply_zoom_geometry()
        self._apply_style()

    def _theme_color(self, key: str, fallback: str) -> QColor:
        value = self._theme.get(key)
        if isinstance(value, QColor) and value.isValid():
            return value
        return QColor(fallback)

    def _apply_style(self) -> None:
        background_color = self._theme_color("background", "#1a222d")
        plot_background_color = self._theme_color("plot_background", background_color.name())
        border_color = self._theme_color("border", "#3b4d66")
        accent_color = self._theme_color("accent", "#2d6cdf")
        text_color = self._theme_color("text", "#e7edf5")
        background = blend_colors(background_color.name(), plot_background_color.name(), 0.45)
        card_border = blend_colors(border_color.name(), accent_color.name(), 0.22)
        title_color = readable_foreground_color(text_color.name(), background, minimum_ratio=4.5)
        muted_color = blend_colors(title_color, background, 0.35)
        self._value_color = title_color
        self.setStyleSheet(
            f"""
            QFrame#metricCard {{
                background-color: {background};
                border: 1px solid {card_border};
                border-radius: 16px;
            }}
            QLabel#metricTitle {{
                background-color: transparent;
                border: none;
                color: {muted_color};
                font-size: {10.0 * self._zoom_scale:.1f}pt;
                font-weight: 700;
                letter-spacing: 0.5px;
            }}
            QLabel#metricValue {{
                background-color: transparent;
                border: none;
                color: {title_color};
                font-weight: 700;
            }}
            QLabel#metricDetail {{
                background-color: transparent;
                border: none;
                color: {title_color};
                font-size: {9.5 * self._zoom_scale:.1f}pt;
            }}
            """
        )

    def set_theme(self, theme: dict[str, QColor | list[QColor]]) -> None:
        self._theme = theme
        self._apply_style()
        self._schedule_value_font_refresh(allow_growth=True)

    def set_zoom_scale(self, scale: float) -> None:
        self._zoom_scale = max(0.75, min(1.5, float(scale)))
        self._apply_zoom_geometry()
        self._apply_style()
        self._schedule_value_font_refresh(allow_growth=True)

    def set_content(self, value: str, detail: str) -> None:
        self.value_label.setText(value)
        self.detail_label.setText(detail)
        self._update_value_font(allow_growth=False)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_value_font_refresh(allow_growth=True)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.value_label and event is not None and not self._applying_value_font:
            if event.type() in {
                QEvent.Type.FontChange,
                QEvent.Type.Polish,
                QEvent.Type.PolishRequest,
                QEvent.Type.StyleChange,
            }:
                self._schedule_value_font_refresh(allow_growth=True)
        return super().eventFilter(watched, event)

    def _schedule_value_font_refresh(self, *, allow_growth: bool) -> None:
        self.setProperty("_metric_card_allow_growth", allow_growth)
        if self._value_font_refresh_pending:
            return
        self._value_font_refresh_pending = True
        QMetaObject.invokeMethod(self, "_run_scheduled_value_font_refresh", Qt.QueuedConnection)

    @Slot()
    def _run_scheduled_value_font_refresh(self) -> None:
        self._value_font_refresh_pending = False
        allow_growth = bool(self.property("_metric_card_allow_growth"))
        self._update_value_font(allow_growth=allow_growth)

    def _update_value_font(self, *, allow_growth: bool) -> None:
        text = self.value_label.text()
        lines = [line for line in text.splitlines() if line] or [text]
        layout = self.layout()
        if layout is not None:
            margins = layout.contentsMargins()
            available_width = max(1, self.width() - margins.left() - margins.right())
        else:
            available_width = self.value_label.contentsRect().width()
        available_width = max(available_width, self.value_label.contentsRect().width()) + self._value_fit_tolerance

        font = self.value_label.font()
        font.setWeight(QFont.Weight.DemiBold)
        point_size = self._value_base_point_size * self._zoom_scale
        min_point_size = self._value_min_point_size * self._zoom_scale
        while point_size > min_point_size:
            font.setPointSizeF(point_size)
            metrics = QFontMetricsF(font)
            if max(metrics.horizontalAdvance(line) for line in lines) <= available_width:
                break
            point_size -= 1.0
        fitted_point_size = max(min_point_size, point_size)
        if allow_growth:
            applied_point_size = fitted_point_size
        else:
            applied_point_size = min(self._value_applied_point_size, fitted_point_size)
        self._value_applied_point_size = applied_point_size
        font.setPointSizeF(applied_point_size)
        self._applying_value_font = True
        try:
            self.value_label.setFont(font)
            self._apply_value_label_style(applied_point_size)
        finally:
            self._applying_value_font = False

    def refresh_value_font(self) -> None:
        self._update_value_font(allow_growth=True)

    def _apply_zoom_geometry(self) -> None:
        self.setMaximumHeight(_QT_WIDGET_MAX_SIZE)
        if self._zoom_scale >= 1.0:
            self.setMaximumWidth(_QT_WIDGET_MAX_SIZE)
        card_height = _scaled_dimension(_METRIC_CARD_MIN_HEIGHT, self._zoom_scale)
        self.setMinimumHeight(card_height)
        label_min_width = _scaled_dimension(_METRIC_CARD_VALUE_MIN_WIDTH, self._zoom_scale)
        self.value_label.setMinimumWidth(label_min_width)
        self.detail_label.setMinimumWidth(label_min_width)
        layout = self.layout()
        if layout is not None:
            left, top, right, bottom = _METRIC_CARD_LAYOUT_MARGINS
            layout.setContentsMargins(
                _scaled_dimension(left, self._zoom_scale),
                _scaled_dimension(top, self._zoom_scale),
                _scaled_dimension(right, self._zoom_scale),
                _scaled_dimension(bottom, self._zoom_scale),
            )
            layout.setSpacing(_scaled_dimension(_METRIC_CARD_LAYOUT_SPACING, self._zoom_scale))
        if self._zoom_scale < 1.0:
            self.setMaximumWidth(_scaled_dimension(_METRIC_CARD_BASE_WIDTH, self._zoom_scale))
        self.updateGeometry()

    def _apply_value_label_style(self, point_size: float) -> None:
        color = getattr(self, "_value_color", "#e7edf5")
        self.value_label.setStyleSheet(
            f"""
            background-color: transparent;
            border: none;
            color: {color};
            font-weight: 600;
            font-size: {point_size:.0f}pt;
            """
        )


class _StableChartView(QChartView):
    def __init__(self, chart: QChart, *, preferred_height: int, parent=None) -> None:
        super().__init__(chart, parent)
        size_hint = super().sizeHint()
        minimum_hint = super().minimumSizeHint()
        self._zoom_scale = 1.0
        self._base_minimum_width = max(1, minimum_hint.width())
        self._base_minimum_height = max(1, preferred_height)
        self._base_preferred_height = max(160, preferred_height + 20)
        if parent is not None:
            parent.installEventFilter(self)
        self._apply_zoom_geometry()

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        parent_width = self._parent_content_width()
        if parent_width > 0:
            hint.setWidth(max(parent_width, self.minimumWidth()))
        hint.setHeight(max(_scaled_dimension(self._base_preferred_height, self._zoom_scale), self.minimumHeight()))
        return hint

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(max(_scaled_dimension(self._base_minimum_width, self._zoom_scale), self.minimumWidth()))
        hint.setHeight(max(_scaled_dimension(self._base_minimum_height, self._zoom_scale), self.minimumHeight()))
        return hint

    def set_zoom_scale(self, scale: float) -> None:
        self._zoom_scale = max(0.75, min(1.5, float(scale)))
        self._apply_zoom_geometry()

    def _apply_zoom_geometry(self) -> None:
        self.setMaximumWidth(_QT_WIDGET_MAX_SIZE)
        self.setMinimumSize(
            QSize(
                _scaled_dimension(self._base_minimum_width, self._zoom_scale),
                _scaled_dimension(self._base_minimum_height, self._zoom_scale),
            )
        )
        if self._zoom_scale < 1.0:
            self.setMaximumHeight(_scaled_dimension(self._base_preferred_height, self._zoom_scale))
        else:
            self.setMaximumHeight(_QT_WIDGET_MAX_SIZE)
        self.updateGeometry()

    def _parent_content_width(self) -> int:
        parent = self.parentWidget()
        if parent is None:
            return 0
        try:
            return max(0, int(parent.contentsRect().width()))
        except RuntimeError:
            return 0

    def eventFilter(self, watched, event) -> bool:
        if watched is self.parentWidget() and event is not None:
            if event.type() in {QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest}:
                self._apply_zoom_geometry()
        return super().eventFilter(watched, event)


class ResourceMonitorDialog(QDialog):
    _GEOMETRY_KEY = "resource_monitor/geometry"

    def __init__(
        self,
        parent=None,
        *,
        settings: AppSettings | None = None,
        on_settings_changed: Callable[[AppSettings], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings_explicit = isinstance(settings, AppSettings)
        self._settings = AppSettings.from_dict(settings.to_dict()) if isinstance(settings, AppSettings) else AppSettings.defaults()
        self._on_settings_changed = on_settings_changed
        self.setObjectName("resourceMonitorDialog")
        self.setWindowTitle("Resource Monitor")
        self.resize(1420, 940)

        self._overview_collector = ResourceMonitorOverviewCollector()
        self._process_collector = ResourceProcessCollector()
        self._slow_details_enabled = True
        self._history: list[ResourceMonitorSnapshot] = []
        self._max_history = self._history_capacity_from_settings()
        self._zoom_percent = self._normalized_zoom_percent(self._settings.resource_monitor_zoom_percent)
        self._applying_resource_settings = False
        self._last_process_count = 0
        self._last_thread_count = 0
        self._current_processes: dict[int, ProcessEntry] = {}
        self._latest_overview_snapshot: ResourceMonitorSnapshot | None = None
        self._latest_process_snapshot: ProcessInventorySnapshot | None = None
        self._overview_dirty = False
        self._processes_dirty = False
        self._close_pending = False
        self._is_closing = False
        self._app_wheel_event_filter_installed = False

        self._sample_status_labels: list[QLabel] = []
        self._sample_page_updaters: dict[QWidget, Callable[[ResourceMonitorSnapshot], None]] = {}
        self._dirty_sample_pages: set[QWidget] = set()
        self._chart_states: dict[QChartView, dict[str, object]] = {}
        self._tree_signatures: dict[QTreeWidget, tuple[object, ...]] = {}
        self._detail_trees: list[QTreeWidget] = []
        self._metric_cards: list[_MetricCard] = []
        self._disk_device_sections: dict[str, _MetricRowSection] = {}
        self._disk_device_section_order: list[str] = []
        self._network_adapter_sections: dict[str, _MetricRowSection] = {}
        self._network_adapter_section_order: list[str] = []
        self._gpu_adapter_sections: dict[str, _GpuAdapterSection] = {}
        self._gpu_adapter_section_order: list[str] = []

        self._sample_thread: QThread | None = None
        self._sample_worker: _TaskWorker | None = None
        self._slow_detail_thread: QThread | None = None
        self._slow_detail_worker: _TaskWorker | None = None
        self._overview_refresh_pending = False
        self._overview_slow_refresh_pending = False
        self._active_overview_refresh_kind = ""
        self._process_thread: QThread | None = None
        self._process_worker: _TaskWorker | None = None
        self._process_refresh_is_full = False
        self._action_thread: QThread | None = None
        self._action_worker: _TaskWorker | None = None
        self._pending_elevated_action: tuple[int, bool] | None = None
        self._active_action_pid: int | None = None
        self._active_action_force = False
        self._main_thread_call_lock = Lock()
        self._main_thread_calls: deque[tuple[str, tuple[object, ...]]] = deque()

        self._sample_timer = QTimer(self)
        self._sample_timer.setInterval(self._settings.resource_monitor_sample_refresh_ms)
        self._sample_timer.timeout.connect(self._trigger_sample_refresh)
        self._slow_detail_timer = QTimer(self)
        self._slow_detail_timer.setInterval(_SLOW_DETAIL_REFRESH_INTERVAL_MS)
        self._slow_detail_timer.timeout.connect(self._trigger_slow_detail_refresh)
        self._process_timer = QTimer(self)
        self._process_timer.setInterval(max(self._settings.resource_monitor_process_refresh_ms, 5000))
        self._process_timer.timeout.connect(self._trigger_process_refresh)
        self._main_thread_dispatch_timer = QTimer(self)
        self._main_thread_dispatch_timer.setInterval(50)
        self._main_thread_dispatch_timer.timeout.connect(self._drain_main_thread_calls)
        self._main_thread_dispatch_timer.start()
        self._close_force_timer = QTimer(self)
        self._close_force_timer.setSingleShot(True)
        self._close_force_timer.setInterval(_CLOSE_POLL_INTERVAL_MS)
        self._close_force_timer.timeout.connect(self._force_close_threads)

        theme = self._chart_theme(series_count=8)
        self._cards: dict[str, _MetricCard] = {}

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        self.tabs = QTabWidget(self)
        root_layout.addWidget(self.tabs, 1)

        self.overview_page = QWidget(self)
        self.cpu_page = QWidget(self)
        self.ram_page = QWidget(self)
        self.disks_page = QWidget(self)
        self.network_page = QWidget(self)
        self.gpu_page = QWidget(self)
        self.processes_page = QWidget(self)
        self.settings_page = QWidget(self)
        self.tabs.addTab(self.overview_page, "Overview")
        self.tabs.addTab(self.cpu_page, "CPU")
        self.tabs.addTab(self.ram_page, "RAM")
        self.tabs.addTab(self.disks_page, "Disks")
        self.tabs.addTab(self.network_page, "Network")
        self.tabs.addTab(self.gpu_page, "GPU")
        self.tabs.addTab(self.processes_page, "Processes")
        self.tabs.addTab(self.settings_page, "Settings")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self._build_overview_page(theme)
        self._build_cpu_page(theme)
        self._build_ram_page(theme)
        self._build_disks_page(theme)
        self._build_network_page(theme)
        self._build_gpu_page(theme)
        self._build_processes_page()
        self._build_settings_page()
        self._sample_page_updaters = {
            self.overview_page: self._apply_overview_snapshot,
            self.cpu_page: self._apply_cpu_snapshot,
            self.ram_page: self._apply_ram_snapshot,
            self.disks_page: self._apply_disks_snapshot,
            self.network_page: self._apply_network_snapshot,
            self.gpu_page: self._apply_gpu_snapshot,
        }
        self._dirty_sample_pages = set(self._sample_page_updaters)
        self._apply_dialog_theme()
        self._apply_resource_monitor_zoom()
        self._restore_geometry()
        self._install_zoom_wheel_event_filter()
        self.destroyed.connect(lambda *_args: self._remove_zoom_wheel_event_filter())

        QTimer.singleShot(0, self._start_initial_refreshes)

    def _build_overview_page(self, theme: dict[str, QColor | list[QColor]]) -> None:
        page_layout = QVBoxLayout(self.overview_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(10)
        self.overview_intro_label = self._create_page_intro(
            "Scan live CPU, memory, disk, network, GPU, and process telemetry for this machine at a glance.",
            self.overview_page,
        )
        page_layout.addWidget(self.overview_intro_label)

        scroll_area = QScrollArea(self.overview_page)
        scroll_area.setObjectName("resourceScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        page_layout.addWidget(scroll_area, 1)

        self.overview_content = QWidget(scroll_area)
        self.overview_content.setObjectName("resourcePageContent")
        scroll_area.setWidget(self.overview_content)
        content_layout = QVBoxLayout(self.overview_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)
        content_layout.setAlignment(Qt.AlignTop)

        self._cards = self._add_metric_cards(
            content_layout,
            parent=self.overview_content,
            theme=theme,
            items=[
                ("cpu", "CPU"),
                ("memory", "Memory"),
                ("disk", "Disk"),
                ("disk_io", "Disk I/O"),
                ("network", "Network"),
                ("processes", "Processes"),
                ("gpu", "GPU"),
            ],
            columns=_CARD_COLUMNS,
        )

        charts_grid = QGridLayout()
        charts_grid.setContentsMargins(0, 0, 0, 0)
        charts_grid.setHorizontalSpacing(12)
        charts_grid.setVerticalSpacing(12)
        self.cpu_chart = self._create_chart_view("CPU Usage", parent=self.overview_content)
        self.memory_chart = self._create_chart_view("Memory Usage", parent=self.overview_content)
        self.disk_chart = self._create_chart_view("Disk Throughput", parent=self.overview_content)
        self.network_chart = self._create_chart_view("Network Bandwidth", parent=self.overview_content)
        self.gpu_chart = self._create_chart_view("GPU", parent=self.overview_content)
        charts_grid.addWidget(self.cpu_chart, 0, 0)
        charts_grid.addWidget(self.memory_chart, 0, 1)
        charts_grid.addWidget(self.disk_chart, 1, 0)
        charts_grid.addWidget(self.network_chart, 1, 1)
        charts_grid.addWidget(self.gpu_chart, 2, 0, 1, 2)
        charts_grid.setColumnStretch(0, 1)
        charts_grid.setColumnStretch(1, 1)
        content_layout.addLayout(charts_grid)

        details_grid = QGridLayout()
        details_grid.setContentsMargins(0, 0, 0, 0)
        details_grid.setHorizontalSpacing(12)
        details_grid.setVerticalSpacing(12)
        self.filesystems_group, self.filesystems_tree = self._create_detail_tree(
            "Filesystems",
            ["Mount", "Device", "Type", "Used", "Total", "Free", "Read", "Write", "Use %"],
            parent=self.overview_content,
        )
        self.interfaces_group, self.interfaces_tree = self._create_detail_tree(
            "Interfaces",
            ["Interface", "IPv4", "IPv6", "State", "Speed", "Receive", "Send", "Total RX", "Total TX"],
            parent=self.overview_content,
        )
        details_grid.addWidget(self.filesystems_group, 0, 0)
        details_grid.addWidget(self.interfaces_group, 0, 1)
        details_grid.setColumnStretch(0, 1)
        details_grid.setColumnStretch(1, 1)
        content_layout.addLayout(details_grid)

        self.overview_status_label = self._create_status_label("Collecting resource telemetry...", self.overview_page)
        content_layout.addWidget(self.overview_status_label)
        self._sample_status_labels.append(self.overview_status_label)

        self._set_empty_chart(self.cpu_chart, "CPU Usage", "Collecting live data...")
        self._set_empty_chart(self.memory_chart, "Memory Usage", "Collecting live data...")
        self._set_empty_chart(self.disk_chart, "Disk Throughput", "Collecting live data...")
        self._set_empty_chart(self.network_chart, "Network Bandwidth", "Collecting live data...")
        self._set_empty_chart(self.gpu_chart, "GPU", "Collecting live data...")

    def _build_cpu_page(self, theme: dict[str, QColor | list[QColor]]) -> None:
        page_layout = QVBoxLayout(self.cpu_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(10)
        page_layout.addWidget(
            self._create_page_intro(
                "Inspect overall CPU load, logical core activity, and temperature sensors when available.",
                self.cpu_page,
            )
        )
        scroll_area, content, content_layout = self._create_scrolled_content(self.cpu_page)
        page_layout.addWidget(scroll_area, 1)

        self.cpu_device_section = self._create_metric_row_section(
            "CPU",
            parent=content,
            theme=theme,
            items=[
                ("usage", "CPU Usage"),
                ("cores", "Logical Cores"),
                ("temperature", "Temperature"),
            ],
            columns=3,
            chart_specs=[
                ("usage_history", "CPU Usage History"),
                ("temperature_history", "CPU Temperature"),
            ],
            chart_columns=2,
        )
        self.cpu_detail_cards = self.cpu_device_section.cards
        self.cpu_detail_chart = self.cpu_device_section.charts["usage_history"]
        self.cpu_temp_chart = self.cpu_device_section.charts["temperature_history"]
        content_layout.addWidget(self.cpu_device_section.group)

        self.cpu_cores_group, self.cpu_cores_tree = self._create_detail_tree(
            "Logical Core Usage",
            ["Core", "Usage"],
            parent=content,
        )
        cpu_header = self.cpu_cores_tree.header()
        if cpu_header is not None:
            cpu_header.setSortIndicator(0, Qt.AscendingOrder)
        self.cpu_cores_tree.sortItems(0, Qt.AscendingOrder)
        content_layout.addWidget(self.cpu_cores_group)

        self.cpu_status_label = self._create_status_label("Collecting CPU telemetry...", self.cpu_page)
        page_layout.addWidget(self.cpu_status_label)
        self._sample_status_labels.append(self.cpu_status_label)

        self._set_empty_chart(self.cpu_detail_chart, "CPU Usage History", "Collecting live data...")
        self._set_empty_chart(self.cpu_temp_chart, "CPU Temperature", _cpu_temperature_unavailable_text())

    def _build_ram_page(self, theme: dict[str, QColor | list[QColor]]) -> None:
        page_layout = QVBoxLayout(self.ram_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(10)
        page_layout.addWidget(
            self._create_page_intro(
                "Track physical memory, swap usage, and memory pressure since this monitor opened.",
                self.ram_page,
            )
        )
        scroll_area, content, content_layout = self._create_scrolled_content(self.ram_page)
        page_layout.addWidget(scroll_area, 1)

        self.ram_section = self._create_metric_row_section(
            "Memory",
            parent=content,
            theme=theme,
            items=[
                ("memory", "Physical Memory"),
                ("available", "Available"),
                ("swap", "Swap"),
            ],
            columns=3,
            chart_specs=[
                ("usage", "Memory Pressure"),
                ("bytes", "Used Memory"),
            ],
            chart_columns=2,
            chart_min_height=260,
        )
        self.ram_cards = self.ram_section.cards
        self.ram_usage_chart = self.ram_section.charts["usage"]
        self.ram_bytes_chart = self.ram_section.charts["bytes"]
        content_layout.addWidget(self.ram_section.group)

        self.ram_status_label = self._create_status_label("Collecting memory telemetry...", self.ram_page)
        page_layout.addWidget(self.ram_status_label)
        self._sample_status_labels.append(self.ram_status_label)

        self._set_empty_chart(self.ram_usage_chart, "Memory Pressure", "Collecting live data...")
        self._set_empty_chart(self.ram_bytes_chart, "Used Memory", "Collecting live data...")

    def _build_disks_page(self, theme: dict[str, QColor | list[QColor]]) -> None:
        page_layout = QVBoxLayout(self.disks_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(10)
        page_layout.addWidget(
            self._create_page_intro(
                "Watch the home volume, mounted filesystems, and per-device disk throughput for the local machine.",
                self.disks_page,
            )
        )
        self.disks_scroll_area, content, content_layout = self._create_scrolled_content(self.disks_page)
        page_layout.addWidget(self.disks_scroll_area, 1)

        self.disk_detail_cards: dict[str, _MetricCard] = {}
        self.disk_device_sections_container = QWidget(content)
        self.disk_device_sections_container.setObjectName("resourcePageContent")
        self.disk_device_sections_layout = QVBoxLayout(self.disk_device_sections_container)
        self.disk_device_sections_layout.setContentsMargins(0, 0, 0, 0)
        self.disk_device_sections_layout.setSpacing(12)
        self.disk_device_sections_container.setVisible(False)
        content_layout.addWidget(self.disk_device_sections_container)

        charts_grid = QGridLayout()
        charts_grid.setContentsMargins(0, 0, 0, 0)
        charts_grid.setHorizontalSpacing(12)
        charts_grid.setVerticalSpacing(12)
        self.disk_home_usage_group, self.disk_usage_history_chart = self._create_chart_section(
            "Home Volume Usage",
            parent=content,
        )
        self.disk_throughput_group, self.disk_io_history_chart = self._create_chart_section(
            "Disk Throughput",
            parent=content,
        )
        charts_grid.addWidget(self.disk_home_usage_group, 0, 0)
        charts_grid.addWidget(self.disk_throughput_group, 0, 1)
        charts_grid.setColumnStretch(0, 1)
        charts_grid.setColumnStretch(1, 1)
        content_layout.addLayout(charts_grid)

        self.disk_filesystems_group, self.disk_filesystems_tree = self._create_detail_tree(
            "Mounted Filesystems",
            ["Mount", "Device", "Type", "Used", "Total", "Free", "Read", "Write", "Use %"],
            parent=content,
        )
        content_layout.addWidget(self.disk_filesystems_group)

        self.disks_status_label = self._create_status_label("Collecting disk telemetry...", self.disks_page)
        page_layout.addWidget(self.disks_status_label)
        self._sample_status_labels.append(self.disks_status_label)

        self._set_empty_chart(self.disk_usage_history_chart, "Home Volume Usage", "Collecting live data...")
        self._set_empty_chart(self.disk_io_history_chart, "Disk Throughput", "Collecting live data...")

    def _build_network_page(self, theme: dict[str, QColor | list[QColor]]) -> None:
        page_layout = QVBoxLayout(self.network_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(10)
        page_layout.addWidget(
            self._create_page_intro(
                "Monitor receive and send bandwidth, cumulative transfer totals, and all detected interfaces, including offline adapters.",
                self.network_page,
            )
        )
        scroll_area, content, content_layout = self._create_scrolled_content(self.network_page)
        page_layout.addWidget(scroll_area, 1)

        self.network_detail_cards: dict[str, _MetricCard] = {}
        self.network_adapter_sections_container = QWidget(content)
        self.network_adapter_sections_container.setObjectName("resourcePageContent")
        self.network_adapter_sections_layout = QVBoxLayout(self.network_adapter_sections_container)
        self.network_adapter_sections_layout.setContentsMargins(0, 0, 0, 0)
        self.network_adapter_sections_layout.setSpacing(12)
        self.network_adapter_sections_container.setVisible(False)
        content_layout.addWidget(self.network_adapter_sections_container)

        charts_grid = QGridLayout()
        charts_grid.setContentsMargins(0, 0, 0, 0)
        charts_grid.setHorizontalSpacing(12)
        charts_grid.setVerticalSpacing(12)
        self.network_bandwidth_group, self.network_detail_chart = self._create_chart_section(
            "Network Bandwidth",
            parent=content,
        )
        charts_grid.addWidget(self.network_bandwidth_group, 0, 0)
        charts_grid.setColumnStretch(0, 1)
        content_layout.addLayout(charts_grid)

        self.network_interfaces_group, self.network_interfaces_tree = self._create_detail_tree(
            "Interfaces",
            ["Interface", "IPv4", "IPv6", "State", "Speed", "Receive", "Send", "Total RX", "Total TX"],
            parent=content,
        )
        network_header = self.network_interfaces_tree.header()
        if network_header is not None:
            network_header.setSortIndicator(0, Qt.AscendingOrder)
        content_layout.addWidget(self.network_interfaces_group)

        self.network_status_label = self._create_status_label("Collecting network telemetry...", self.network_page)
        page_layout.addWidget(self.network_status_label)
        self._sample_status_labels.append(self.network_status_label)

        self._set_empty_chart(self.network_detail_chart, "Network Bandwidth", "Collecting live data...")

    def _build_gpu_page(self, theme: dict[str, QColor | list[QColor]]) -> None:
        page_layout = QVBoxLayout(self.gpu_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(10)
        page_layout.addWidget(
            self._create_page_intro(
                "View GPU utilization, VRAM, and temperature telemetry when supported on this system.",
                self.gpu_page,
            )
        )
        scroll_area, content, content_layout = self._create_scrolled_content(self.gpu_page)
        page_layout.addWidget(scroll_area, 1)

        self.gpu_detail_cards: dict[str, _MetricCard] = {}

        self.gpu_adapter_sections_container = QWidget(content)
        self.gpu_adapter_sections_container.setObjectName("resourcePageContent")
        self.gpu_adapter_sections_layout = QVBoxLayout(self.gpu_adapter_sections_container)
        self.gpu_adapter_sections_layout.setContentsMargins(0, 0, 0, 0)
        self.gpu_adapter_sections_layout.setSpacing(12)
        self.gpu_adapter_sections_container.setVisible(False)
        content_layout.addWidget(self.gpu_adapter_sections_container)

        self.gpu_status_label = self._create_status_label("Collecting GPU telemetry...", self.gpu_page)
        page_layout.addWidget(self.gpu_status_label)
        self._sample_status_labels.append(self.gpu_status_label)

    def _build_processes_page(self) -> None:
        layout = QVBoxLayout(self.processes_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        intro = self._create_page_intro(
            "Inspect the current process list, sort by live resource usage, and end tasks when needed.",
            self.processes_page,
        )
        layout.addWidget(intro)

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(8)
        controls_row.addWidget(QLabel("Search"))
        self.process_search_input = QLineEdit(self.processes_page)
        self.process_search_input.setObjectName("processSearch")
        self.process_search_input.setPlaceholderText("Filter by name, PID, user, or command")
        controls_row.addWidget(self.process_search_input, 1)
        self.process_refresh_btn = QPushButton("Refresh")
        self.end_task_btn = QPushButton("End Task")
        self.force_kill_btn = QPushButton("Force Kill")
        controls_row.addWidget(self.process_refresh_btn)
        controls_row.addWidget(self.end_task_btn)
        controls_row.addWidget(self.force_kill_btn)
        layout.addLayout(controls_row)

        self.process_tree = QTreeWidget(self.processes_page)
        self.process_tree.setObjectName("resourceDetailTree")
        self.process_tree.setColumnCount(9)
        self.process_tree.setHeaderLabels(
            ["Name", "PID", "CPU %", "Memory", "Threads", "User", "Status", "Started", "Command"]
        )
        self.process_tree.setAlternatingRowColors(True)
        self.process_tree.setRootIsDecorated(False)
        self.process_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.process_tree.setSortingEnabled(True)
        self.process_tree.setUniformRowHeights(True)
        self.process_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.process_tree.setTextElideMode(Qt.ElideRight)
        self._detail_trees.append(self.process_tree)
        header = self.process_tree.header()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.Interactive)
            header.setSectionResizeMode(1, QHeaderView.Fixed)
            header.setSectionResizeMode(2, QHeaderView.Fixed)
            header.setSectionResizeMode(3, QHeaderView.Fixed)
            header.setSectionResizeMode(4, QHeaderView.Fixed)
            header.setSectionResizeMode(5, QHeaderView.Interactive)
            header.setSectionResizeMode(6, QHeaderView.Fixed)
            header.setSectionResizeMode(7, QHeaderView.Fixed)
            header.setSectionResizeMode(8, QHeaderView.Stretch)
            header.setStretchLastSection(False)
            header.setSortIndicator(_DEFAULT_PROCESS_SORT_COLUMN, _DEFAULT_PROCESS_SORT_ORDER)
            self._resize_process_tree_columns()
        self.process_tree.sortItems(_DEFAULT_PROCESS_SORT_COLUMN, _DEFAULT_PROCESS_SORT_ORDER)
        layout.addWidget(self.process_tree, 1)

        self.process_status_label = self._create_status_label("Collecting process list...", self.processes_page)
        layout.addWidget(self.process_status_label)

        self.process_search_input.textChanged.connect(self._apply_process_filter)
        self.process_refresh_btn.clicked.connect(self._refresh_processes_clicked)
        self.end_task_btn.clicked.connect(lambda: self._start_process_action(force=False))
        self.force_kill_btn.clicked.connect(lambda: self._start_process_action(force=True))
        self.process_tree.itemSelectionChanged.connect(self._update_process_action_state)
        self.process_tree.customContextMenuRequested.connect(self._show_process_context_menu)
        self._update_process_action_state()

    def _build_settings_page(self) -> None:
        layout = QVBoxLayout(self.settings_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(
            self._create_page_intro(
                "Adjust Resource Monitor display and refresh behavior.",
                self.settings_page,
            )
        )

        scroll_area, content, content_layout = self._create_scrolled_content(self.settings_page)
        layout.addWidget(scroll_area, 1)

        group = QGroupBox("Resource Monitor Settings", content)
        group.setObjectName("resourceSection")
        form = QFormLayout(group)
        form.setContentsMargins(12, 16, 12, 12)
        form.setSpacing(10)

        self.show_offline_adapters_check = QCheckBox("Show offline adapters")
        self.show_offline_adapters_check.setChecked(self._settings.resource_monitor_show_offline_adapters)
        form.addRow(QLabel("Offline Adapters"), self.show_offline_adapters_check)

        zoom_widget = QWidget(group)
        zoom_layout = QHBoxLayout(zoom_widget)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_layout.setSpacing(8)
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(_RESOURCE_MONITOR_ZOOM_MIN, _RESOURCE_MONITOR_ZOOM_MAX)
        self.zoom_slider.setSingleStep(_RESOURCE_MONITOR_ZOOM_STEP)
        self.zoom_slider.setPageStep(_RESOURCE_MONITOR_ZOOM_STEP)
        self.zoom_slider.setValue(self._zoom_percent)
        self.zoom_spin = QSpinBox()
        self.zoom_spin.setRange(_RESOURCE_MONITOR_ZOOM_MIN, _RESOURCE_MONITOR_ZOOM_MAX)
        self.zoom_spin.setSingleStep(_RESOURCE_MONITOR_ZOOM_STEP)
        self.zoom_spin.setSuffix("%")
        self.zoom_spin.setValue(self._zoom_percent)
        zoom_layout.addWidget(self.zoom_slider, 1)
        zoom_layout.addWidget(self.zoom_spin, 0)
        form.addRow(QLabel("Zoom"), zoom_widget)

        self.sample_refresh_spin = QSpinBox()
        self.sample_refresh_spin.setRange(500, 10000)
        self.sample_refresh_spin.setSingleStep(250)
        self.sample_refresh_spin.setSuffix(" ms")
        self.sample_refresh_spin.setValue(self._settings.resource_monitor_sample_refresh_ms)
        form.addRow(QLabel("Telemetry Refresh"), self.sample_refresh_spin)

        self.process_refresh_spin = QSpinBox()
        self.process_refresh_spin.setRange(2000, 30000)
        self.process_refresh_spin.setSingleStep(500)
        self.process_refresh_spin.setSuffix(" ms")
        self.process_refresh_spin.setValue(self._settings.resource_monitor_process_refresh_ms)
        form.addRow(QLabel("Process Refresh"), self.process_refresh_spin)

        self.history_minutes_spin = QSpinBox()
        self.history_minutes_spin.setRange(2, 60)
        self.history_minutes_spin.setSingleStep(1)
        self.history_minutes_spin.setSuffix(" min")
        self.history_minutes_spin.setValue(self._settings.resource_monitor_history_minutes)
        form.addRow(QLabel("Chart History"), self.history_minutes_spin)

        self.settings_status_label = self._create_status_label("", group)
        form.addRow(QLabel("Status"), self.settings_status_label)
        content_layout.addWidget(group)
        content_layout.addStretch(1)

        self.show_offline_adapters_check.toggled.connect(self._resource_settings_controls_changed)
        self.zoom_slider.valueChanged.connect(self._zoom_slider_changed)
        self.zoom_spin.valueChanged.connect(self._zoom_spin_changed)
        self.sample_refresh_spin.valueChanged.connect(self._resource_settings_controls_changed)
        self.process_refresh_spin.valueChanged.connect(self._resource_settings_controls_changed)
        self.history_minutes_spin.valueChanged.connect(self._resource_settings_controls_changed)
        self._update_resource_settings_status()

    @staticmethod
    def _normalized_zoom_percent(value: int) -> int:
        clamped = max(_RESOURCE_MONITOR_ZOOM_MIN, min(_RESOURCE_MONITOR_ZOOM_MAX, int(value)))
        return int(round(clamped / _RESOURCE_MONITOR_ZOOM_STEP) * _RESOURCE_MONITOR_ZOOM_STEP)

    def _history_capacity_from_settings(self) -> int:
        sample_ms = max(500, min(10000, int(self._settings.resource_monitor_sample_refresh_ms)))
        history_minutes = max(2, min(60, int(self._settings.resource_monitor_history_minutes)))
        return max(1, int((history_minutes * 60_000) / sample_ms))

    def _zoom_scale(self) -> float:
        return max(0.75, min(1.5, self._zoom_percent / 100.0))

    def _scaled_point_size(self, point_size: float) -> float:
        return max(1.0, point_size * self._zoom_scale())

    @Slot(int)
    def _zoom_slider_changed(self, value: int) -> None:
        if self._applying_resource_settings:
            return
        normalized = self._normalized_zoom_percent(value)
        if self.zoom_spin.value() != normalized:
            self.zoom_spin.setValue(normalized)
            return
        self._resource_settings_controls_changed()

    @Slot(int)
    def _zoom_spin_changed(self, value: int) -> None:
        if self._applying_resource_settings:
            return
        normalized = self._normalized_zoom_percent(value)
        if self.zoom_slider.value() != normalized:
            self.zoom_slider.setValue(normalized)
            return
        self._resource_settings_controls_changed()

    @Slot()
    def _resource_settings_controls_changed(self) -> None:
        if self._applying_resource_settings:
            return
        self._settings.resource_monitor_show_offline_adapters = self.show_offline_adapters_check.isChecked()
        self._settings.resource_monitor_zoom_percent = self._normalized_zoom_percent(self.zoom_spin.value())
        self._settings.resource_monitor_sample_refresh_ms = self.sample_refresh_spin.value()
        self._settings.resource_monitor_process_refresh_ms = self.process_refresh_spin.value()
        self._settings.resource_monitor_history_minutes = self.history_minutes_spin.value()
        self._apply_resource_monitor_preferences(persist=True)

    def _apply_resource_monitor_preferences(self, *, persist: bool) -> None:
        self._zoom_percent = self._normalized_zoom_percent(self._settings.resource_monitor_zoom_percent)
        self._settings.resource_monitor_zoom_percent = self._zoom_percent
        self._sample_timer.setInterval(max(500, min(10000, int(self._settings.resource_monitor_sample_refresh_ms))))
        self._update_process_refresh_interval(current_page=self.tabs.currentWidget())
        self._max_history = self._history_capacity_from_settings()
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        self._sync_resource_settings_controls()
        self._apply_resource_monitor_zoom()
        self._reapply_latest_sample_snapshot()
        self._update_resource_settings_status()
        if persist:
            self._persist_resource_monitor_settings()

    def _sync_resource_settings_controls(self) -> None:
        if not hasattr(self, "zoom_spin"):
            return
        self._applying_resource_settings = True
        try:
            self.show_offline_adapters_check.setChecked(self._settings.resource_monitor_show_offline_adapters)
            self.zoom_slider.setValue(self._zoom_percent)
            self.zoom_spin.setValue(self._zoom_percent)
            self.sample_refresh_spin.setValue(max(500, min(10000, int(self._settings.resource_monitor_sample_refresh_ms))))
            self.process_refresh_spin.setValue(max(2000, min(30000, int(self._settings.resource_monitor_process_refresh_ms))))
            self.history_minutes_spin.setValue(max(2, min(60, int(self._settings.resource_monitor_history_minutes))))
        finally:
            self._applying_resource_settings = False

    def _update_resource_settings_status(self) -> None:
        if hasattr(self, "settings_status_label"):
            self.settings_status_label.setText(
                f"Keeping up to {self._max_history} samples at {self._sample_timer.interval()} ms intervals."
            )

    def _persist_resource_monitor_settings(self) -> None:
        if self._on_settings_changed is None:
            return
        self._on_settings_changed(AppSettings.from_dict(self._settings.to_dict()))

    def _reapply_latest_sample_snapshot(self) -> None:
        snapshot = self._latest_overview_snapshot
        if snapshot is None:
            return
        self._update_cards(snapshot)
        for page in self._sample_page_updaters:
            self._set_sample_page_dirty(page, True)
        current_page = self.tabs.currentWidget()
        updater = self._sample_page_updaters.get(current_page)
        if updater is not None:
            updater(snapshot)
            self._set_sample_page_dirty(current_page, False)

    @Slot()
    def _start_initial_refreshes(self) -> None:
        self._update_process_refresh_interval(current_page=self.tabs.currentWidget())
        self._sample_timer.start()
        self._process_timer.start()
        self._start_sample_refresh()
        if self.tabs.currentWidget() is self.processes_page:
            self._start_process_refresh()
        else:
            self._start_process_count_refresh()
        if self._slow_details_enabled:
            self._slow_detail_timer.start()
            QTimer.singleShot(250, self._trigger_slow_detail_refresh)

    def _update_process_refresh_interval(self, *, current_page: QWidget | None) -> None:
        configured = max(2000, min(30000, int(self._settings.resource_monitor_process_refresh_ms)))
        target_interval = configured if current_page is self.processes_page else max(configured, 5000)
        if self._process_timer.interval() != target_interval:
            self._process_timer.setInterval(target_interval)

    def _add_metric_cards(
        self,
        layout: QVBoxLayout,
        *,
        parent: QWidget,
        theme: dict[str, QColor | list[QColor]],
        items: list[tuple[str, str]],
        columns: int,
    ) -> dict[str, _MetricCard]:
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        cards: dict[str, _MetricCard] = {}
        for index, (key, title) in enumerate(items):
            card = _MetricCard(title, theme=theme, parent=parent)
            card.set_zoom_scale(self._zoom_scale())
            cards[key] = card
            self._metric_cards.append(card)
            grid.addWidget(card, index // max(1, columns), index % max(1, columns))
        for column in range(max(1, columns)):
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)
        return cards

    def _create_metric_row_section(
        self,
        title: str,
        *,
        parent: QWidget,
        theme: dict[str, QColor | list[QColor]],
        items: list[tuple[str, str]],
        columns: int,
        chart_specs: list[tuple[str, str]] | None = None,
        chart_columns: int = 2,
        chart_min_height: int = 240,
        include_status_label: bool = False,
    ) -> _MetricRowSection:
        group = QGroupBox(title, parent)
        group.setObjectName("resourceSection")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)
        cards = self._add_metric_cards(
            layout,
            parent=group,
            theme=theme,
            items=items,
            columns=columns,
        )
        charts: dict[str, QChartView] = {}
        if chart_specs:
            charts_grid = QGridLayout()
            charts_grid.setContentsMargins(0, 0, 0, 0)
            charts_grid.setHorizontalSpacing(12)
            charts_grid.setVerticalSpacing(12)
            total_chart_columns = max(1, chart_columns)
            for index, (key, chart_title) in enumerate(chart_specs):
                view = self._create_chart_view(chart_title, parent=group, min_height=chart_min_height)
                charts[key] = view
                charts_grid.addWidget(view, index // total_chart_columns, index % total_chart_columns)
            for column in range(total_chart_columns):
                charts_grid.setColumnStretch(column, 1)
            layout.addLayout(charts_grid)
        status_label = self._create_status_label("", group) if include_status_label else None
        if status_label is not None:
            layout.addWidget(status_label)
        return _MetricRowSection(group=group, cards=cards, charts=charts, status_label=status_label)

    def _create_page_intro(self, text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("pageIntroLabel")
        label.setWordWrap(True)
        return label

    def _create_chart_section(self, title: str, *, parent: QWidget) -> tuple[QGroupBox, QChartView]:
        group = QGroupBox(title, parent)
        group.setObjectName("resourceSection")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)
        chart = self._create_chart_view(title, parent=group)
        layout.addWidget(chart)
        return group, chart

    def _create_status_label(self, text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("statusLabel")
        label.setWordWrap(True)
        return label

    def _create_scrolled_content(self, parent: QWidget) -> tuple[QScrollArea, QWidget, QVBoxLayout]:
        scroll_area = QScrollArea(parent)
        scroll_area.setObjectName("resourceScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        content = QWidget(scroll_area)
        content.setObjectName("resourcePageContent")
        scroll_area.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignTop)
        return scroll_area, content, layout

    def _create_detail_tree(self, title: str, headers: list[str], *, parent: QWidget) -> tuple[QGroupBox, QTreeWidget]:
        group = QGroupBox(title, parent)
        group.setObjectName("resourceSection")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)
        tree = QTreeWidget(group)
        tree.setObjectName("resourceDetailTree")
        tree.setColumnCount(len(headers))
        tree.setHeaderLabels(headers)
        tree.setAlternatingRowColors(True)
        tree.setRootIsDecorated(False)
        tree.setSelectionMode(QAbstractItemView.NoSelection)
        tree.setSortingEnabled(True)
        tree.setUniformRowHeights(True)
        tree.setMinimumHeight(220)
        tree.setProperty("_resource_monitor_base_min_height", 220)
        self._detail_trees.append(tree)
        header = tree.header()
        if header is not None:
            for column in range(max(0, len(headers) - 1)):
                header.setSectionResizeMode(column, QHeaderView.Interactive)
                base_width = max(96, min(220, len(headers[column]) * 14 + 44))
                header.resizeSection(column, _scaled_dimension(base_width, self._zoom_scale(), minimum=72))
            header.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
        layout.addWidget(tree)
        return group, tree

    def _create_chart_view(self, title: str, *, parent: QWidget, min_height: int = 260) -> QChartView:
        chart = QChart()
        chart.setTitle(title)
        chart.legend().setVisible(True)
        chart.setAnimationOptions(QChart.NoAnimation)
        self._apply_chart_theme(chart)
        view = _StableChartView(chart, preferred_height=min_height, parent=parent)
        view.setObjectName("resourceChartView")
        view.setRenderHint(QPainter.Antialiasing, True)
        view.set_zoom_scale(self._zoom_scale())
        self._chart_states[view] = {"kind": "empty", "title": title}
        return view

    def _apply_dialog_theme(self) -> None:
        settings = self._theme_settings()
        dialog_background = self._safe_qcolor(settings.app_bg_start, "#0e1116")
        dialog_background_end = self._safe_qcolor(settings.app_bg_end, dialog_background.name())
        section_background = self._safe_qcolor(
            blend_colors(settings.field_bg, settings.app_bg_start, 0.12),
            settings.field_bg,
        )
        header_background = self._safe_qcolor(
            blend_colors(settings.field_bg, settings.text_color, 0.08),
            settings.field_bg,
        )
        border = self._safe_qcolor(settings.field_border, "#324050")
        accent = self._safe_qcolor(settings.accent_color, "#2d6cdf")
        tab_active_bg = self._safe_qcolor(settings.tab_active_bg, accent.name())
        tab_active_fg = self._safe_qcolor(settings.tab_active_fg, "#f8fbff")
        tab_inactive_bg = self._safe_qcolor(settings.tab_inactive_bg, settings.field_bg)
        tab_inactive_fg = self._safe_qcolor(settings.tab_inactive_fg, settings.text_color)
        text_color = self._safe_qcolor(
            readable_foreground_color(settings.text_color, settings.field_bg, minimum_ratio=4.5),
            "#e7edf5",
        )
        muted_text = self._safe_qcolor(
            blend_colors(text_color.name(), settings.app_bg_start, 0.38),
            text_color.name(),
        )
        alternate_row = self._safe_qcolor(
            blend_colors(settings.field_bg, text_color.name(), 0.05),
            settings.field_bg,
        )
        chart_border = self._safe_qcolor(
            blend_colors(border.name(), accent.name(), 0.18),
            border.name(),
        )
        selected_background = tab_active_bg
        selected_text = self._safe_qcolor(
            readable_foreground_color(tab_active_fg.name(), selected_background.name(), minimum_ratio=4.5),
            tab_active_fg.name(),
        )
        hover_background = self._safe_qcolor(
            blend_colors(settings.field_bg, accent.name(), 0.14),
            settings.field_bg,
        )
        hover_text = self._safe_qcolor(
            readable_foreground_color(text_color.name(), hover_background.name(), minimum_ratio=4.5),
            text_color.name(),
        )
        field_focus_background = self._safe_qcolor(
            blend_colors(settings.field_bg, settings.app_bg_end, 0.16),
            settings.field_bg,
        )
        placeholder_text = self._safe_qcolor(
            blend_colors(text_color.name(), settings.field_bg, 0.46),
            text_color.name(),
        )
        scrollbar_track = self._safe_qcolor(
            blend_colors(section_background.name(), dialog_background.name(), 0.28),
            section_background.name(),
        )
        scrollbar_handle = self._safe_qcolor(
            blend_colors(border.name(), accent.name(), 0.35),
            border.name(),
        )
        scrollbar_handle_hover = self._safe_qcolor(
            blend_colors(scrollbar_handle.name(), accent.name(), 0.25),
            accent.name(),
        )
        self.setStyleSheet(
            f"""
            QDialog#resourceMonitorDialog {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                            stop:0 {dialog_background.name()},
                                            stop:1 {dialog_background_end.name()});
                border: 1px solid {border.name()};
            }}
            QDialog#resourceMonitorDialog QLabel {{
                background-color: transparent;
                border: none;
                font-size: {self._scaled_point_size(9.5):.1f}pt;
            }}
            QDialog#resourceMonitorDialog QLabel#pageIntroLabel {{
                color: {text_color.name()};
                font-size: {self._scaled_point_size(10.0):.1f}pt;
            }}
            QDialog#resourceMonitorDialog QLabel#statusLabel {{
                color: {muted_text.name()};
                font-size: {self._scaled_point_size(9.5):.1f}pt;
            }}
            QDialog#resourceMonitorDialog QTabWidget::pane {{
                border: 1px solid {border.name()};
                border-radius: 12px;
                background-color: {section_background.name()};
                top: -1px;
            }}
            QDialog#resourceMonitorDialog QTabBar::tab {{
                background-color: {tab_inactive_bg.name()};
                color: {tab_inactive_fg.name()};
                border: 1px solid {border.name()};
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 8px 14px;
                margin-right: 4px;
                font-weight: 600;
                font-size: {self._scaled_point_size(9.5):.1f}pt;
            }}
            QDialog#resourceMonitorDialog QTabBar::tab:selected {{
                background-color: {tab_active_bg.name()};
                color: {tab_active_fg.name()};
                border-color: {tab_active_bg.name()};
            }}
            QDialog#resourceMonitorDialog QTabBar::tab:hover:!selected {{
                background-color: {header_background.name()};
                color: {text_color.name()};
            }}
            QDialog#resourceMonitorDialog QScrollArea#resourceScrollArea {{
                background: transparent;
                border: none;
            }}
            QDialog#resourceMonitorDialog QWidget#resourcePageContent {{
                background: transparent;
            }}
            QDialog#resourceMonitorDialog QGroupBox#resourceSection {{
                background-color: {section_background.name()};
                border: 1px solid {border.name()};
                border-radius: 16px;
                margin-top: 12px;
                padding-top: 8px;
            }}
            QDialog#resourceMonitorDialog QGroupBox#resourceSection::title {{
                subcontrol-origin: margin;
                left: {_scaled_dimension(12, self._zoom_scale())}px;
                padding: 0 {_scaled_dimension(6, self._zoom_scale())}px;
                color: {muted_text.name()};
                font-weight: 700;
                font-size: {self._scaled_point_size(9.5):.1f}pt;
            }}
            QDialog#resourceMonitorDialog QTreeWidget#resourceDetailTree {{
                background-color: {settings.field_bg};
                color: {text_color.name()};
                border: 1px solid {border.name()};
                border-radius: 10px;
                padding: {_scaled_dimension(4, self._zoom_scale())}px;
                alternate-background-color: {alternate_row.name()};
                selection-background-color: {selected_background.name()};
                selection-color: {selected_text.name()};
                outline: none;
                font-size: {self._scaled_point_size(9.5):.1f}pt;
            }}
            QDialog#resourceMonitorDialog QTreeWidget#resourceDetailTree::item {{
                border-radius: 6px;
                padding: {_scaled_dimension(2, self._zoom_scale())}px {_scaled_dimension(4, self._zoom_scale())}px;
                font-size: {self._scaled_point_size(9.5):.1f}pt;
            }}
            QDialog#resourceMonitorDialog QTreeWidget#resourceDetailTree::item:selected {{
                background-color: {selected_background.name()};
                color: {selected_text.name()};
            }}
            QDialog#resourceMonitorDialog QTreeWidget#resourceDetailTree::item:hover {{
                background-color: {hover_background.name()};
                color: {hover_text.name()};
            }}
            QDialog#resourceMonitorDialog QHeaderView::section {{
                background-color: {header_background.name()};
                color: {text_color.name()};
                border: 1px solid {border.name()};
                padding: {_scaled_dimension(6, self._zoom_scale())}px {_scaled_dimension(8, self._zoom_scale())}px;
                font-weight: 600;
                font-size: {self._scaled_point_size(9.0):.1f}pt;
            }}
            QDialog#resourceMonitorDialog QChartView#resourceChartView {{
                background: transparent;
                border: 1px solid {chart_border.name()};
                border-radius: 16px;
                padding: 4px;
            }}
            QDialog#resourceMonitorDialog QLineEdit#processSearch {{
                background-color: {settings.field_bg};
                color: {text_color.name()};
                border: 1px solid {border.name()};
                border-radius: 8px;
                padding: {_scaled_dimension(6, self._zoom_scale())}px {_scaled_dimension(8, self._zoom_scale())}px;
                selection-background-color: {selected_background.name()};
                selection-color: {selected_text.name()};
                placeholder-text-color: {placeholder_text.name()};
                font-size: {self._scaled_point_size(9.5):.1f}pt;
            }}
            QDialog#resourceMonitorDialog QPushButton {{
                font-size: {self._scaled_point_size(9.5):.1f}pt;
                padding: {_scaled_dimension(6, self._zoom_scale())}px {_scaled_dimension(12, self._zoom_scale())}px;
            }}
            QDialog#resourceMonitorDialog QCheckBox,
            QDialog#resourceMonitorDialog QSpinBox,
            QDialog#resourceMonitorDialog QSlider {{
                font-size: {self._scaled_point_size(9.5):.1f}pt;
            }}
            QDialog#resourceMonitorDialog QLineEdit#processSearch:focus {{
                background-color: {field_focus_background.name()};
                border: 1px solid {accent.name()};
            }}
            QDialog#resourceMonitorDialog QMenu {{
                background-color: {section_background.name()};
                color: {text_color.name()};
                border: 1px solid {border.name()};
            }}
            QDialog#resourceMonitorDialog QMenu::item {{
                padding: 6px 22px 6px 12px;
                background: transparent;
            }}
            QDialog#resourceMonitorDialog QMenu::item:selected {{
                background-color: {selected_background.name()};
                color: {selected_text.name()};
            }}
            QDialog#resourceMonitorDialog QMenu::separator {{
                height: 1px;
                background: {border.name()};
                margin: 4px 8px;
            }}
            QDialog#resourceMonitorDialog QScrollBar:vertical {{
                background: {scrollbar_track.name()};
                width: 12px;
                margin: 4px 2px 4px 2px;
                border-radius: 6px;
            }}
            QDialog#resourceMonitorDialog QScrollBar::handle:vertical {{
                background: {scrollbar_handle.name()};
                min-height: 24px;
                border-radius: 6px;
            }}
            QDialog#resourceMonitorDialog QScrollBar::handle:vertical:hover {{
                background: {scrollbar_handle_hover.name()};
            }}
            QDialog#resourceMonitorDialog QScrollBar:horizontal {{
                background: {scrollbar_track.name()};
                height: 12px;
                margin: 2px 4px 2px 4px;
                border-radius: 6px;
            }}
            QDialog#resourceMonitorDialog QScrollBar::handle:horizontal {{
                background: {scrollbar_handle.name()};
                min-width: 24px;
                border-radius: 6px;
            }}
            QDialog#resourceMonitorDialog QScrollBar::handle:horizontal:hover {{
                background: {scrollbar_handle_hover.name()};
            }}
            QDialog#resourceMonitorDialog QScrollBar::add-line:vertical,
            QDialog#resourceMonitorDialog QScrollBar::sub-line:vertical,
            QDialog#resourceMonitorDialog QScrollBar::add-line:horizontal,
            QDialog#resourceMonitorDialog QScrollBar::sub-line:horizontal {{
                background: transparent;
                border: none;
                width: 0px;
                height: 0px;
            }}
            QDialog#resourceMonitorDialog QScrollBar::add-page:vertical,
            QDialog#resourceMonitorDialog QScrollBar::sub-page:vertical,
            QDialog#resourceMonitorDialog QScrollBar::add-page:horizontal,
            QDialog#resourceMonitorDialog QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}
            """
        )

    def refresh_theme(self) -> None:
        self._apply_dialog_theme()
        theme = self._chart_theme(series_count=8)
        for card in self._metric_cards:
            card.set_theme(theme)
            card.set_zoom_scale(self._zoom_scale())
        for view in list(self._chart_states):
            if isinstance(view, _StableChartView):
                view.set_zoom_scale(self._zoom_scale())
            self._refresh_chart_view_theme(view)
        QMetaObject.invokeMethod(self, "_refresh_metric_card_fonts", Qt.QueuedConnection)

    def _apply_resource_monitor_zoom(self) -> None:
        app_font = QApplication.font()
        font = QFont(app_font)
        if app_font.pointSizeF() > 0:
            font.setPointSizeF(self._scaled_point_size(app_font.pointSizeF()))
        self.setFont(font)
        self._apply_detail_tree_zoom(font)
        self.refresh_theme()
        self._apply_detail_tree_zoom(font)

    def _apply_detail_tree_zoom(self, font: QFont) -> None:
        for tree in list(self._detail_trees):
            try:
                tree.setFont(QFont(font))
                base_height = int(tree.property("_resource_monitor_base_min_height") or 0)
                if base_height > 0:
                    tree.setMinimumHeight(_scaled_dimension(base_height, self._zoom_scale(), minimum=120))
                header = tree.header()
                if header is not None:
                    header.setFont(QFont(font))
                    if font.pointSizeF() > 0:
                        header.setStyleSheet(f"QHeaderView {{ font-size: {font.pointSizeF():.1f}pt; }}")
                    header.setMinimumSectionSize(_scaled_dimension(36, self._zoom_scale(), minimum=24))
            except RuntimeError:
                continue
        self._resize_process_tree_columns()

    def _resize_process_tree_columns(self) -> None:
        if not hasattr(self, "process_tree"):
            return
        header = self.process_tree.header()
        if header is None:
            return
        for column, base_width in enumerate(_PROCESS_COLUMN_BASE_WIDTHS):
            header.resizeSection(column, _scaled_dimension(base_width, self._zoom_scale(), minimum=48))

    @Slot()
    def _refresh_metric_card_fonts(self) -> None:
        for card in self._metric_cards:
            card.refresh_value_font()

    def apply_runtime_settings(self, settings: AppSettings) -> None:
        try:
            incoming = AppSettings.from_dict(settings.to_dict())
            self._settings_explicit = True
            if incoming.to_dict() == self._settings.to_dict():
                return
            self._settings = incoming
            self._apply_resource_monitor_preferences(persist=False)
        except Exception as exc:  # noqa: BLE001
            self._handle_ui_exception("apply-runtime-settings", exc)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._handle_zoom_wheel_event(event):
            return
        super().wheelEvent(event)

    def eventFilter(self, watched, event) -> bool:
        try:
            is_wheel = event is not None and event.type() == QEvent.Type.Wheel
        except Exception:
            is_wheel = False
        if is_wheel and self._is_resource_monitor_event_target(watched):
            if self._handle_zoom_wheel_event(event):
                return True
        return super().eventFilter(watched, event)

    def _install_zoom_wheel_event_filter(self) -> None:
        if self._app_wheel_event_filter_installed:
            return
        app = QApplication.instance()
        if app is None:
            return
        try:
            app.installEventFilter(self)
        except RuntimeError:
            return
        self._app_wheel_event_filter_installed = True

    def _remove_zoom_wheel_event_filter(self) -> None:
        if not self._app_wheel_event_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except RuntimeError:
                pass
        self._app_wheel_event_filter_installed = False

    def _is_resource_monitor_event_target(self, watched: object) -> bool:
        if watched is self:
            return True
        if not isinstance(watched, QWidget):
            return False
        try:
            return self.isAncestorOf(watched) or watched.window() is self
        except RuntimeError:
            return False

    def _handle_zoom_wheel_event(self, event) -> bool:  # noqa: ANN001
        try:
            modifiers = event.modifiers()
        except Exception:
            return False
        if not (modifiers & Qt.ControlModifier):
            return False
        try:
            delta = event.angleDelta().y()
        except Exception:
            delta = 0
        if delta:
            direction = 1 if delta > 0 else -1
            self._settings.resource_monitor_zoom_percent = self._normalized_zoom_percent(
                self._zoom_percent + (direction * _RESOURCE_MONITOR_ZOOM_STEP)
            )
            self._apply_resource_monitor_preferences(persist=True)
        try:
            event.accept()
        except Exception:
            pass
        return True

    def _refresh_chart_view_theme(self, view: QChartView) -> None:
        state = self._chart_states.get(view)
        if state is None:
            return
        chart = view.chart()
        if chart is None:
            return
        kind = str(state.get("kind") or "")
        if kind == "line":
            series_objects = state.get("series")
            if not isinstance(series_objects, list):
                return
            theme = self._apply_chart_theme(chart, series_count=max(1, len(series_objects)))
            axis_x = state.get("axis_x")
            axis_y = state.get("axis_y")
            if axis_x is not None:
                self._style_chart_axis(axis_x, theme)
            if axis_y is not None:
                self._style_chart_axis(axis_y, theme)
            series_colors = theme["series_colors"] if isinstance(theme["series_colors"], list) else [theme["accent"]]
            for index, series in enumerate(series_objects):
                if not isinstance(series, QLineSeries):
                    continue
                pen = QPen(series_colors[index % len(series_colors)] if series_colors else theme["accent"])
                pen.setWidthF(max(1.0, series.pen().widthF()))
                series.setPen(pen)
            chart.setTitle(str(state.get("title") or chart.title()))
            chart.legend().setVisible(len(series_objects) > 1)
            return

        title = str(state.get("title") or chart.title())
        message = str(state.get("message") or "")
        chart.setTitle(f"{title}\n{message}" if message else title)
        chart.legend().setVisible(False)
        self._apply_chart_theme(chart)

    def _restore_geometry(self) -> None:
        geometry = QSettings("SnakeSh", "SnakeSh").value(self._GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)
        self._clamp_to_screen()

    def restore_saved_geometry(self) -> None:
        self._restore_geometry()

    def _clamp_to_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = min(self.width(), available.width())
        height = min(self.height(), available.height())
        if width != self.width() or height != self.height():
            self.resize(width, height)
        x = max(available.left(), min(self.x(), available.right() - self.width()))
        y = max(available.top(), min(self.y(), available.bottom() - self.height()))
        if x != self.x() or y != self.y():
            self.move(x, y)

    @Slot()
    def _trigger_sample_refresh(self) -> None:
        self._request_overview_refresh(include_slow_details=False)

    @Slot()
    def _trigger_slow_detail_refresh(self) -> None:
        self._request_overview_refresh(include_slow_details=True)

    @Slot()
    def _trigger_process_refresh(self) -> None:
        if self.tabs.currentWidget() is self.processes_page:
            self._start_process_refresh()
        else:
            self._start_process_count_refresh()

    def _trace_overview_refresh(self, message: str, *args: object) -> None:
        if _LOGGER.isEnabledFor(TRACE_LEVEL):
            _LOGGER.log(TRACE_LEVEL, message, *args)

    def _request_overview_refresh(self, *, include_slow_details: bool) -> bool:
        if self._is_closing:
            return False
        if self._sample_thread is not None:
            if include_slow_details:
                self._overview_slow_refresh_pending = True
                self._overview_refresh_pending = False
            elif not self._overview_slow_refresh_pending:
                self._overview_refresh_pending = True
            self._trace_overview_refresh(
                "Queued overview refresh active_kind=%s requested_kind=%s pending_fast=%s pending_slow=%s",
                self._active_overview_refresh_kind or "idle",
                "slow" if include_slow_details else "fast",
                self._overview_refresh_pending,
                self._overview_slow_refresh_pending,
            )
            return True
        return self._start_overview_refresh(include_slow_details=include_slow_details)

    def _start_sample_refresh(self) -> bool:
        return self._request_overview_refresh(include_slow_details=False)

    def _start_slow_detail_refresh(self) -> bool:
        if not self._slow_details_enabled:
            return False
        return self._request_overview_refresh(include_slow_details=True)

    def _start_overview_refresh(self, *, include_slow_details: bool) -> bool:
        if self._sample_thread is not None or self._is_closing:
            return False
        kind = "slow" if include_slow_details else "fast"
        self._active_overview_refresh_kind = kind
        if include_slow_details:
            self._overview_slow_refresh_pending = False
            self._overview_refresh_pending = False
        else:
            self._overview_refresh_pending = False
        self._trace_overview_refresh("Starting %s overview refresh.", kind)
        self._sample_thread = QThread(self)
        self._sample_worker = _TaskWorker(
            f"resource-monitor-overview-{kind}",
            lambda stop_callback: self._run_overview_refresh(
                include_slow_details=include_slow_details,
                stop_callback=stop_callback,
            ),
            context={"kind": kind},
        )
        self._sample_worker.moveToThread(self._sample_thread)
        self._sample_thread.started.connect(self._sample_worker.run)
        self._sample_worker.succeeded.connect(lambda payload: self._enqueue_main_thread_call("_on_sample_ready", payload))
        self._sample_worker.failed.connect(lambda message: self._enqueue_main_thread_call("_on_sample_failed", message))
        self._sample_worker.finished.connect(self._sample_thread.quit)
        self._sample_worker.finished.connect(self._sample_worker.deleteLater)
        self._sample_thread.finished.connect(lambda: self._enqueue_main_thread_call("_on_sample_finished"))
        self._sample_thread.finished.connect(self._sample_thread.deleteLater)
        self._sample_thread.start()
        return True

    def _run_overview_refresh(
        self,
        *,
        include_slow_details: bool,
        stop_callback: Callable[[], bool],
    ) -> ResourceMonitorSnapshot:
        started_at = time.monotonic()
        kind = "slow" if include_slow_details else "fast"
        if include_slow_details:
            snapshot = self._collect_slow_details_snapshot(stop_callback)
        else:
            snapshot = self._collect_fast_snapshot()
        duration = time.monotonic() - started_at
        _LOGGER.debug(
            "Resource Monitor %s overview refresh completed duration=%.3fs errors=%s",
            kind,
            duration,
            len(snapshot.errors),
        )
        warning_threshold = (
            _SLOW_OVERVIEW_WARNING_SECONDS
            if include_slow_details
            else _FAST_OVERVIEW_WARNING_SECONDS
        )
        if duration > warning_threshold:
            _LOGGER.warning(
                "Resource Monitor %s overview refresh was slow duration=%.3fs",
                kind,
                duration,
            )
        return snapshot

    def _collect_slow_details_snapshot(self, stop_callback: Callable[[], bool]) -> ResourceMonitorSnapshot:
        try:
            return self._refresh_slow_details_snapshot(stop_callback=stop_callback)
        except TypeError as exc:
            if "stop_callback" not in str(exc):
                raise
            return self._refresh_slow_details_snapshot()  # type: ignore[call-arg]

    def _collect_fast_snapshot(self) -> ResourceMonitorSnapshot:
        return self._overview_collector.collect_fast(
            process_count=self._last_process_count,
            thread_count=self._last_thread_count,
        )

    def _refresh_slow_details_snapshot(
        self,
        *,
        stop_callback: Callable[[], bool],
    ) -> ResourceMonitorSnapshot:
        self._overview_collector.refresh_slow_details(stop_callback=stop_callback)
        if stop_callback():
            raise ResourceMonitorCancelledError("Resource Monitor slow overview refresh cancelled.")
        return self._collect_fast_snapshot()

    def _collect_process_snapshot(self, stop_callback: Callable[[], bool]) -> ProcessInventorySnapshot:
        try:
            return self._process_collector.collect(stop_callback=stop_callback)
        except TypeError as exc:
            if "stop_callback" not in str(exc):
                raise
            return self._process_collector.collect()  # type: ignore[call-arg]

    def _collect_process_counts_snapshot(self, stop_callback: Callable[[], bool]) -> ProcessCountsSnapshot:
        try:
            return self._process_collector.collect_counts(stop_callback=stop_callback)
        except TypeError as exc:
            if "stop_callback" not in str(exc):
                raise
            return self._process_collector.collect_counts()  # type: ignore[call-arg]

    def _start_process_refresh(self) -> bool:
        if self._process_thread is not None or self._is_closing:
            return False
        self._process_refresh_is_full = True
        self.process_refresh_btn.setEnabled(False)
        self._update_process_action_state()
        self._process_thread = QThread(self)
        self._process_worker = _TaskWorker(
            "resource-monitor-processes-full",
            self._collect_process_snapshot,
            context={"kind": "full"},
        )
        self._process_worker.moveToThread(self._process_thread)
        self._process_thread.started.connect(self._process_worker.run)
        self._process_worker.succeeded.connect(
            lambda payload: self._enqueue_main_thread_call("_on_process_snapshot_ready", payload)
        )
        self._process_worker.failed.connect(
            lambda message: self._enqueue_main_thread_call("_on_process_snapshot_failed", message)
        )
        self._process_worker.finished.connect(self._process_thread.quit)
        self._process_worker.finished.connect(self._process_worker.deleteLater)
        self._process_thread.finished.connect(lambda: self._enqueue_main_thread_call("_on_process_finished"))
        self._process_thread.finished.connect(self._process_thread.deleteLater)
        self._process_thread.start()
        return True

    def _start_process_count_refresh(self) -> bool:
        if self._process_thread is not None or self._is_closing:
            return False
        self._process_refresh_is_full = False
        self._process_thread = QThread(self)
        self._process_worker = _TaskWorker(
            "resource-monitor-processes-counts",
            self._collect_process_counts_snapshot,
            context={"kind": "counts"},
        )
        self._process_worker.moveToThread(self._process_thread)
        self._process_thread.started.connect(self._process_worker.run)
        self._process_worker.succeeded.connect(
            lambda payload: self._enqueue_main_thread_call("_on_process_counts_ready", payload)
        )
        self._process_worker.failed.connect(
            lambda message: self._enqueue_main_thread_call("_on_process_snapshot_failed", message)
        )
        self._process_worker.finished.connect(self._process_thread.quit)
        self._process_worker.finished.connect(self._process_worker.deleteLater)
        self._process_thread.finished.connect(lambda: self._enqueue_main_thread_call("_on_process_finished"))
        self._process_thread.finished.connect(self._process_thread.deleteLater)
        self._process_thread.start()
        return True

    @Slot()
    def _refresh_processes_clicked(self) -> None:
        self._start_process_refresh()

    @Slot(object)
    def _on_sample_ready(self, payload: object) -> None:
        if self._is_closing:
            return
        if not isinstance(payload, ResourceMonitorSnapshot):
            self._on_sample_failed("Unexpected resource snapshot payload.")
            return

        def _apply_payload() -> None:
            self._latest_overview_snapshot = payload
            self._history.append(payload)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

            self._run_timed_ui_stage(
                "update-cards",
                lambda: self._update_cards(payload),
                history_size=len(self._history),
                snapshot_errors=len(payload.errors),
            )

            current_page = self.tabs.currentWidget()
            current_tab = self._current_tab_label(current_page)
            for page in self._sample_page_updaters:
                self._set_sample_page_dirty(page, True)
            if current_page in self._sample_page_updaters:
                self._run_timed_ui_stage(
                    "apply-sample-page",
                    lambda: self._sample_page_updaters[current_page](payload),
                    history_size=len(self._history),
                    page=current_tab,
                    snapshot_errors=len(payload.errors),
                )
                self._set_sample_page_dirty(current_page, False)

            if payload.errors:
                status_message = "Live data updated with warnings: " + " | ".join(payload.errors)
            else:
                status_message = f"Live data updated at {datetime.now().strftime('%H:%M:%S')}."
            self._set_sample_status_text(status_message)

        self._run_timed_ui_stage(
            "on-sample-ready",
            _apply_payload,
            history_size=len(self._history) + 1,
            snapshot_errors=len(payload.errors),
        )

    @Slot(str)
    def _on_sample_failed(self, message: str) -> None:
        if self._is_closing:
            return
        self._set_sample_status_text(message)

    @Slot()
    def _on_sample_finished(self) -> None:
        completed_kind = self._active_overview_refresh_kind or "fast"
        self._sample_thread = None
        self._sample_worker = None
        self._active_overview_refresh_kind = ""
        if not self._is_closing:
            if self._overview_slow_refresh_pending:
                self._trace_overview_refresh(
                    "Running queued slow overview refresh after completed_kind=%s.",
                    completed_kind,
                )
                self._overview_slow_refresh_pending = False
                self._overview_refresh_pending = False
                self._start_overview_refresh(include_slow_details=True)
                return
            if self._overview_refresh_pending:
                self._trace_overview_refresh(
                    "Running queued fast overview refresh after completed_kind=%s.",
                    completed_kind,
                )
                self._overview_refresh_pending = False
                self._start_overview_refresh(include_slow_details=False)
                return
        self._maybe_finish_close()

    @Slot()
    def _on_slow_detail_finished(self) -> None:
        self._slow_detail_thread = None
        self._slow_detail_worker = None
        self._maybe_finish_close()

    @Slot(object)
    def _on_process_snapshot_ready(self, payload: object) -> None:
        if self._is_closing:
            return
        if not isinstance(payload, ProcessInventorySnapshot):
            self._on_process_snapshot_failed("Unexpected process snapshot payload.")
            return

        def _apply_payload() -> None:
            self._latest_process_snapshot = payload
            self._last_process_count = len(payload.entries)
            self._last_thread_count = payload.total_threads
            self._update_process_card()
            if self.tabs.currentWidget() is self.processes_page:
                self._populate_process_tree(payload)
                self._processes_dirty = False
            else:
                self._processes_dirty = True
            if payload.errors:
                self.process_status_label.setText("Process list updated with warnings: " + " | ".join(payload.errors[:5]))
            else:
                self.process_status_label.setText(
                    f"Process list updated at {datetime.now().strftime('%H:%M:%S')}."
                )

        self._run_timed_ui_stage(
            "on-process-snapshot-ready",
            _apply_payload,
            rows=len(payload.entries),
            snapshot_errors=len(payload.errors),
        )

    @Slot(object)
    def _on_process_counts_ready(self, payload: object) -> None:
        if self._is_closing:
            return
        if not isinstance(payload, ProcessCountsSnapshot):
            self._on_process_snapshot_failed("Unexpected process count payload.")
            return
        self._last_process_count = payload.process_count
        self._last_thread_count = payload.thread_count
        self._update_process_card()
        if payload.errors:
            self.process_status_label.setText("Process counts updated with warnings: " + " | ".join(payload.errors[:5]))

    @Slot(str)
    def _on_process_snapshot_failed(self, message: str) -> None:
        if self._is_closing:
            return
        self.process_status_label.setText(message)

    @Slot()
    def _on_process_finished(self) -> None:
        was_full_refresh = self._process_refresh_is_full
        self._process_thread = None
        self._process_worker = None
        self._process_refresh_is_full = False
        self.process_refresh_btn.setEnabled(True)
        self._update_process_action_state()
        if not was_full_refresh and not self._is_closing and self.tabs.currentWidget() is self.processes_page:
            self._start_process_refresh()
            return
        self._maybe_finish_close()

    @Slot(int)
    def _on_tab_changed(self, _index: int) -> None:
        if self._is_closing:
            return

        def _apply_tab_change() -> None:
            current_page = self.tabs.currentWidget()
            self._update_process_refresh_interval(current_page=current_page)
            updater = self._sample_page_updaters.get(current_page)
            if (
                updater is not None
                and current_page in self._dirty_sample_pages
                and self._latest_overview_snapshot is not None
            ):
                updater(self._latest_overview_snapshot)
                self._set_sample_page_dirty(current_page, False)
            if current_page is self.processes_page and self._processes_dirty and self._latest_process_snapshot is not None:
                self._populate_process_tree(self._latest_process_snapshot)
                self._processes_dirty = False
            if current_page is self.processes_page and self._process_thread is None:
                self._start_process_refresh()

        self._run_timed_ui_stage("tab-changed", _apply_tab_change, index=_index)

    def _apply_overview_snapshot(self, snapshot: ResourceMonitorSnapshot) -> None:
        self._populate_filesystems(self.filesystems_tree, snapshot.filesystems, snapshot.disk_devices)
        self._populate_interfaces(self.interfaces_tree, self._visible_ordered_interfaces(snapshot.interfaces))
        self._refresh_overview_charts()

    def _apply_cpu_snapshot(self, snapshot: ResourceMonitorSnapshot) -> None:
        sample = snapshot.sample
        core_count = len(sample.cpu_per_core_percentages) or sample.logical_cpu_count
        self.cpu_detail_cards["usage"].set_content(f"{sample.cpu_percent:.1f}%", "Average across logical CPU cores")
        self.cpu_detail_cards["cores"].set_content(str(core_count), "Logical cores currently visible")
        if sample.cpu_temperature_c is None:
            self.cpu_detail_cards["temperature"].set_content("--", _cpu_temperature_unavailable_text())
        else:
            self.cpu_detail_cards["temperature"].set_content(
                _format_temperature(sample.cpu_temperature_c),
                _cpu_temperature_detail_text(),
            )
        core_percentages = sample.cpu_per_core_percentages
        if not core_percentages and sample.logical_cpu_count > 0:
            core_percentages = tuple(0.0 for _ in range(sample.logical_cpu_count))
        self._populate_cpu_cores(core_percentages)
        self._refresh_cpu_charts()

    def _apply_ram_snapshot(self, snapshot: ResourceMonitorSnapshot) -> None:
        sample = snapshot.sample
        memory_free_bytes = max(0, sample.memory_total_bytes - sample.memory_used_bytes)
        self.ram_cards["memory"].set_content(
            f"{_format_bytes(sample.memory_used_bytes)} / {_format_bytes(sample.memory_total_bytes)}",
            f"{sample.memory_percent:.1f}% used",
        )
        self.ram_cards["available"].set_content(
            _format_bytes(memory_free_bytes),
            "Currently available physical memory",
        )
        self.ram_cards["swap"].set_content(
            f"{_format_bytes(sample.swap_used_bytes)} / {_format_bytes(sample.swap_total_bytes)}",
            f"{sample.swap_percent:.1f}% used",
        )
        self._refresh_ram_charts()

    def _apply_disks_snapshot(self, snapshot: ResourceMonitorSnapshot) -> None:
        scroll_position = self._scroll_area_position(self.disks_scroll_area)
        previous_updates_enabled = self.disk_device_sections_container.updatesEnabled()
        self.disk_device_sections_container.setUpdatesEnabled(False)
        try:
            filesystems_by_key = self._preferred_filesystems_by_disk_device_key(snapshot.filesystems)
            for disk_device, section in self._sync_disk_device_sections(snapshot.disk_devices):
                filesystem = filesystems_by_key.get(disk_device.key)
                if filesystem is None:
                    section.cards["volume"].set_content("--", "Mounted volume details unavailable.")
                    section.cards["free"].set_content("--", "Mounted volume details unavailable.")
                else:
                    mount_label = self._filesystem_mount_label(filesystem)
                    section.cards["volume"].set_content(
                        f"{_format_bytes(filesystem.used_bytes)} / {_format_bytes(filesystem.total_bytes)}",
                        f"{filesystem.usage_percent:.1f}% used on {mount_label}",
                    )
                    section.cards["free"].set_content(
                        _format_bytes(filesystem.free_bytes),
                        f"Free on {mount_label}",
                    )
                section.cards["read"].set_content(
                    _format_rate(disk_device.read_bytes_per_sec),
                    f"Since open {_format_bytes(disk_device.read_bytes_since_open)}",
                )
                section.cards["write"].set_content(
                    _format_rate(disk_device.write_bytes_per_sec),
                    f"Since open {_format_bytes(disk_device.write_bytes_since_open)}",
                )

                usage_points = self._history_disk_usage_points(disk_device.key)
                if all(point is None for point in usage_points):
                    self._set_empty_chart(
                        section.charts["usage_history"],
                        "Volume Usage",
                        "Mounted volume history is unavailable for this device.",
                    )
                else:
                    self._set_line_chart(
                        section.charts["usage_history"],
                        "Volume Usage",
                        [("Usage", usage_points)],
                        fixed_max=100.0,
                        fixed_point_count=self._max_history,
                    )

                throughput_series_specs, throughput_axis_title = self._history_disk_series_for_device(disk_device)
                if not throughput_series_specs:
                    self._set_empty_chart(
                        section.charts["throughput_history"],
                        "Disk Throughput",
                        "Per-device disk throughput is unavailable.",
                    )
                else:
                    self._set_line_chart(
                        section.charts["throughput_history"],
                        "Disk Throughput",
                        throughput_series_specs,
                        axis_title=throughput_axis_title,
                        fixed_point_count=self._max_history,
                    )

            self._populate_filesystems(self.disk_filesystems_tree, snapshot.filesystems, snapshot.disk_devices)
            self._refresh_disk_charts()
        finally:
            self.disk_device_sections_container.setUpdatesEnabled(previous_updates_enabled)
            self._restore_scroll_area_position_later(self.disks_scroll_area, scroll_position)

    def _apply_network_snapshot(self, snapshot: ResourceMonitorSnapshot) -> None:
        ordered_interfaces = self._visible_ordered_interfaces(snapshot.interfaces)
        previous_updates_enabled = self.network_adapter_sections_container.updatesEnabled()
        self.network_adapter_sections_container.setUpdatesEnabled(False)
        try:
            for entry, section in self._sync_network_adapter_sections(ordered_interfaces):
                receive_detail = "Current receive bandwidth" if entry.is_up else "Adapter is currently offline"
                send_detail = "Current send bandwidth" if entry.is_up else "Adapter is currently offline"
                section.cards["receive"].set_content(
                    _format_network_rate(entry.recv_bytes_per_sec),
                    receive_detail,
                )
                section.cards["send"].set_content(
                    _format_network_rate(entry.sent_bytes_per_sec),
                    send_detail,
                )
                section.cards["total_receive"].set_content(
                    _format_bytes(entry.recv_bytes_total),
                    "Received since this monitor opened",
                )
                section.cards["total_send"].set_content(
                    _format_bytes(entry.sent_bytes_total),
                    "Sent since this monitor opened",
                )
                bandwidth_series_specs, bandwidth_axis_title = self._history_interface_series(entry.name)
                if not bandwidth_series_specs:
                    self._set_empty_chart(
                        section.charts["bandwidth_history"],
                        "Network Bandwidth",
                        "Interface history is unavailable.",
                    )
                else:
                    self._set_line_chart(
                        section.charts["bandwidth_history"],
                        "Network Bandwidth",
                        bandwidth_series_specs,
                        axis_title=bandwidth_axis_title,
                        fixed_point_count=self._max_history,
                    )
        finally:
            self.network_adapter_sections_container.setUpdatesEnabled(previous_updates_enabled)
        self._populate_interfaces(self.network_interfaces_tree, ordered_interfaces)
        self._refresh_network_charts()

    def _apply_gpu_snapshot(self, snapshot: ResourceMonitorSnapshot) -> None:
        self._refresh_gpu_charts()

    def _set_sample_status_text(self, message: str) -> None:
        for label in self._sample_status_labels:
            label.setText(message)

    def _set_sample_page_dirty(self, page: QWidget, dirty: bool) -> None:
        if dirty:
            self._dirty_sample_pages.add(page)
        else:
            self._dirty_sample_pages.discard(page)
        if page is self.overview_page:
            self._overview_dirty = dirty

    @staticmethod
    def _scroll_area_position(scroll_area: QScrollArea) -> tuple[int, int]:
        return (
            scroll_area.verticalScrollBar().value(),
            scroll_area.horizontalScrollBar().value(),
        )

    def _restore_scroll_area_position_later(self, scroll_area: QScrollArea, position: tuple[int, int]) -> None:
        def _restore() -> None:
            try:
                vertical = scroll_area.verticalScrollBar()
                horizontal = scroll_area.horizontalScrollBar()
                vertical.setValue(max(vertical.minimum(), min(position[0], vertical.maximum())))
                horizontal.setValue(max(horizontal.minimum(), min(position[1], horizontal.maximum())))
            except RuntimeError:
                return

        _restore()
        QTimer.singleShot(0, _restore)

    @staticmethod
    def _sort_order_label(sort_order: Qt.SortOrder) -> str:
        return "desc" if sort_order == Qt.DescendingOrder else "asc"

    @staticmethod
    def _format_log_context(context: dict[str, object]) -> str:
        if not context:
            return ""
        parts = [f"{key}={value}" for key, value in sorted(context.items())]
        return " " + " ".join(parts)

    def _ui_log_debug(self, message: str, **context: object) -> None:
        _LOGGER.debug(f"{message}{self._format_log_context(context)}")

    def _ui_log_warning(self, message: str, **context: object) -> None:
        _LOGGER.warning(f"{message}{self._format_log_context(context)}")

    def _current_tab_label(self, page: QWidget | None = None) -> str:
        widget = self.tabs.currentWidget() if page is None else page
        if widget is not None:
            index = self.tabs.indexOf(widget)
            if index >= 0:
                label = str(self.tabs.tabText(index) or "").strip()
                if label:
                    return label
        return "Unknown"

    def _tree_label(self, tree: QTreeWidget) -> str:
        if tree is self.filesystems_tree:
            return "overview-filesystems"
        if tree is self.disk_filesystems_tree:
            return "disk-filesystems"
        if tree is self.interfaces_tree:
            return "overview-interfaces"
        if tree is self.network_interfaces_tree:
            return "network-interfaces"
        if tree is self.cpu_cores_tree:
            return "cpu-cores"
        if tree is self.process_tree:
            return "processes"
        return tree.objectName() or "tree"

    def _tree_sort_state(
        self,
        tree: QTreeWidget,
        *,
        default_column: int = 0,
        default_order: Qt.SortOrder = Qt.AscendingOrder,
    ) -> tuple[int, Qt.SortOrder]:
        sort_column = tree.sortColumn()
        if sort_column < 0:
            sort_column = default_column
        header = tree.header()
        sort_order = header.sortIndicatorOrder() if header is not None else default_order
        return sort_column, sort_order

    def _tree_log_context(
        self,
        tree: QTreeWidget,
        *,
        row_count: int,
        default_column: int = 0,
        default_order: Qt.SortOrder = Qt.AscendingOrder,
        **extra: object,
    ) -> dict[str, object]:
        sort_column, sort_order = self._tree_sort_state(
            tree,
            default_column=default_column,
            default_order=default_order,
        )
        context: dict[str, object] = {
            "current_rows": tree.topLevelItemCount(),
            "rows": row_count,
            "sort_column": sort_column,
            "sort_order": self._sort_order_label(sort_order),
            "tab": self._current_tab_label(),
            "tree": self._tree_label(tree),
        }
        context.update(extra)
        return context

    def _run_timed_ui_stage(
        self,
        stage: str,
        callback: Callable[[], object],
        *,
        warning_threshold: float = _UI_APPLY_STAGE_WARNING_SECONDS,
        **context: object,
    ) -> object | None:
        stage_context: dict[str, object] = {"tab": self._current_tab_label()}
        stage_context.update(context)
        self._ui_log_debug(f"Resource Monitor UI stage start stage={stage}", **stage_context)
        started_at = time.monotonic()
        try:
            return callback()
        except Exception as exc:  # noqa: BLE001
            self._handle_ui_exception(stage, exc, **stage_context)
            return None
        finally:
            duration = time.monotonic() - started_at
            message = f"Resource Monitor UI stage done stage={stage} duration={duration:.3f}s"
            if duration > warning_threshold:
                self._ui_log_warning(message, **stage_context)
            else:
                self._ui_log_debug(message, **stage_context)

    def _handle_ui_exception(self, stage: str, exc: Exception, **context: object) -> None:
        context_text = " ".join(f"{key}={value!r}" for key, value in sorted(context.items()) if value is not None)
        _LOGGER.exception("Resource Monitor UI stage failed stage=%s %s", stage, context_text)
        message = f"Resource Monitor UI error while updating {stage}: {exc}"
        if hasattr(self, "process_status_label"):
            self.process_status_label.setText(message)
        if hasattr(self, "settings_status_label"):
            self.settings_status_label.setText(message)
        if hasattr(self, "_set_sample_status_text"):
            self._set_sample_status_text(message)

    def _tree_item_key(self, item: QTreeWidgetItem) -> str:
        return str(item.data(0, _TREE_KEY_ROLE) or "")

    def _top_level_keys(self, tree: QTreeWidget) -> list[str]:
        return [self._tree_item_key(tree.topLevelItem(index)) for index in range(tree.topLevelItemCount())]

    def _compare_tree_items(self, left: QTreeWidgetItem, right: QTreeWidgetItem, *, sort_column: int) -> int:
        left_value = left.data(sort_column, _SORT_ROLE)
        right_value = right.data(sort_column, _SORT_ROLE)
        if left_value is not None and right_value is not None:
            try:
                if left_value < right_value:
                    return -1
                if left_value > right_value:
                    return 1
            except TypeError:
                pass
        left_text = left.text(sort_column).lower()
        right_text = right.text(sort_column).lower()
        if left_text < right_text:
            return -1
        if left_text > right_text:
            return 1
        return 0

    def _sorted_tree_items(
        self,
        items: list[QTreeWidgetItem],
        *,
        sort_column: int,
        sort_order: Qt.SortOrder,
    ) -> list[QTreeWidgetItem]:
        sorted_items = sorted(
            items,
            key=cmp_to_key(lambda left, right: self._compare_tree_items(left, right, sort_column=sort_column)),
        )
        if sort_order == Qt.DescendingOrder:
            sorted_items.reverse()
        return sorted_items

    def _reorder_top_level_items(self, tree: QTreeWidget, desired_items: list[QTreeWidgetItem]) -> None:
        for index, item in enumerate(desired_items):
            if tree.topLevelItem(index) is item:
                continue
            current_index = tree.indexOfTopLevelItem(item)
            if current_index < 0:
                continue
            moved_item = tree.takeTopLevelItem(current_index)
            tree.insertTopLevelItem(index, moved_item)

    @staticmethod
    def _set_item_texts(item: QTreeWidgetItem, values: list[str]) -> None:
        for column, value in enumerate(values):
            if item.text(column) != value:
                item.setText(column, value)

    def _build_filesystem_item(
        self,
        entry: FilesystemEntry,
        disk_devices_by_key: dict[str, DiskDeviceSample],
    ) -> _SortableTreeWidgetItem:
        item = _SortableTreeWidgetItem([""] * 9)
        self._update_filesystem_item(item, entry, disk_devices_by_key)
        return item

    def _update_filesystem_item(
        self,
        item: QTreeWidgetItem,
        entry: FilesystemEntry,
        disk_devices_by_key: dict[str, DiskDeviceSample],
    ) -> None:
        disk_device = disk_devices_by_key.get(entry.disk_device_key)
        mount_label = entry.mountpoint
        if entry.is_home:
            mount_label += " (Home)"
        self._set_item_texts(
            item,
            [
                mount_label,
                entry.device,
                entry.filesystem_type,
                _format_bytes(entry.used_bytes),
                _format_bytes(entry.total_bytes),
                _format_bytes(entry.free_bytes),
                _format_rate(disk_device.read_bytes_per_sec) if disk_device is not None else "--",
                _format_rate(disk_device.write_bytes_per_sec) if disk_device is not None else "--",
                f"{entry.usage_percent:.1f}%",
            ],
        )
        item.setData(0, _TREE_KEY_ROLE, entry.mountpoint)
        item.setData(3, _SORT_ROLE, entry.used_bytes)
        item.setData(4, _SORT_ROLE, entry.total_bytes)
        item.setData(5, _SORT_ROLE, entry.free_bytes)
        item.setData(6, _SORT_ROLE, disk_device.read_bytes_per_sec if disk_device is not None else -1.0)
        item.setData(7, _SORT_ROLE, disk_device.write_bytes_per_sec if disk_device is not None else -1.0)
        item.setData(8, _SORT_ROLE, entry.usage_percent)

    def _build_interface_item(
        self,
        entry: InterfaceBandwidthEntry,
        *,
        name_sort_role: object | None = None,
    ) -> _SortableTreeWidgetItem:
        item = _SortableTreeWidgetItem([""] * 9)
        self._update_interface_item(item, entry, name_sort_role=name_sort_role)
        return item

    def _update_interface_item(
        self,
        item: QTreeWidgetItem,
        entry: InterfaceBandwidthEntry,
        *,
        name_sort_role: object | None = None,
    ) -> None:
        self._set_item_texts(
            item,
            [
                entry.name,
                entry.ipv4_address or "Unassigned",
                entry.ipv6_address or "Unassigned",
                "Up" if entry.is_up else "Offline",
                f"{entry.speed_mbps} Mbps" if entry.speed_mbps > 0 else "Unknown",
                _format_network_rate(entry.recv_bytes_per_sec),
                _format_network_rate(entry.sent_bytes_per_sec),
                _format_bytes(entry.recv_bytes_total),
                _format_bytes(entry.sent_bytes_total),
            ],
        )
        item.setData(0, _TREE_KEY_ROLE, entry.name)
        item.setData(0, _SORT_ROLE, name_sort_role)
        item.setData(5, _SORT_ROLE, entry.recv_bytes_per_sec)
        item.setData(6, _SORT_ROLE, entry.sent_bytes_per_sec)
        item.setData(7, _SORT_ROLE, entry.recv_bytes_total)
        item.setData(8, _SORT_ROLE, entry.sent_bytes_total)

    def _reconcile_keyed_tree(
        self,
        tree: QTreeWidget,
        *,
        stage: str,
        signature: tuple[object, ...],
        row_count: int,
        entry_key: Callable[[object], str],
        entries: list[object],
        update_item: Callable[[QTreeWidgetItem, object], None],
        create_item: Callable[[object], _SortableTreeWidgetItem],
        default_column: int = 0,
        default_order: Qt.SortOrder = Qt.AscendingOrder,
    ) -> None:
        if self._tree_signatures.get(tree) == signature:
            return

        context = self._tree_log_context(
            tree,
            row_count=row_count,
            default_column=default_column,
            default_order=default_order,
        )

        def _apply_reconcile() -> None:
            sort_column, sort_order = self._tree_sort_state(
                tree,
                default_column=default_column,
                default_order=default_order,
            )
            previous_signal_state = tree.blockSignals(True)
            tree.setUpdatesEnabled(False)
            created = 0
            removed = 0
            desired_items: list[QTreeWidgetItem] = []
            try:
                existing_items = {
                    self._tree_item_key(tree.topLevelItem(index)): tree.topLevelItem(index)
                    for index in range(tree.topLevelItemCount())
                }
                stale_keys = set(existing_items)
                self._ui_log_debug(
                    f"Resource Monitor UI breadcrumb phase={stage}-row-mutation-start",
                    **context,
                )
                for entry in entries:
                    key = entry_key(entry)
                    stale_keys.discard(key)
                    item = existing_items.get(key)
                    if item is None:
                        item = create_item(entry)
                        tree.addTopLevelItem(item)
                        created += 1
                    update_item(item, entry)
                    desired_items.append(item)
                self._ui_log_debug(
                    f"Resource Monitor UI breadcrumb phase={stage}-row-mutation-done",
                    **context,
                    created=created,
                    touched=len(desired_items),
                )

                stale_items = [existing_items[key] for key in stale_keys]
                self._ui_log_debug(
                    f"Resource Monitor UI breadcrumb phase={stage}-remove-stale-start",
                    **context,
                    stale_rows=len(stale_items),
                )
                for item in stale_items:
                    current_index = tree.indexOfTopLevelItem(item)
                    if current_index < 0:
                        continue
                    tree.takeTopLevelItem(current_index)
                    removed += 1
                self._ui_log_debug(
                    f"Resource Monitor UI breadcrumb phase={stage}-remove-stale-done",
                    **context,
                    removed=removed,
                )

                desired_keys = [self._tree_item_key(item) for item in desired_items]
                sorted_keys = [
                    self._tree_item_key(item)
                    for item in self._sorted_tree_items(
                        desired_items,
                        sort_column=sort_column,
                        sort_order=sort_order,
                    )
                ]
                if desired_keys == sorted_keys:
                    if self._top_level_keys(tree) != desired_keys:
                        self._reorder_top_level_items(tree, desired_items)
                    self._ui_log_debug(
                        f"Resource Monitor UI breadcrumb phase={stage}-sort-skipped",
                        **context,
                    )
                else:
                    self._ui_log_debug(
                        f"Resource Monitor UI breadcrumb phase={stage}-sort-start",
                        **context,
                    )
                    tree.sortItems(sort_column, sort_order)
                    self._ui_log_debug(
                        f"Resource Monitor UI breadcrumb phase={stage}-sort-done",
                        **context,
                    )
            finally:
                tree.blockSignals(previous_signal_state)
                tree.setUpdatesEnabled(True)
            self._tree_signatures[tree] = signature
            self._ui_log_debug(
                f"Resource Monitor UI tree updated stage={stage}",
                **context,
                created=created,
                removed=removed,
            )

        self._run_timed_ui_stage(stage, _apply_reconcile, **context)

    def _remove_metric_row_section(self, sections: dict[str, _MetricRowSection], key: str) -> None:
        section = sections.pop(key, None)
        if section is None:
            return
        for chart in section.charts.values():
            self._chart_states.pop(chart, None)
        for card in section.cards.values():
            while card in self._metric_cards:
                self._metric_cards.remove(card)
        section.group.setParent(None)
        section.group.deleteLater()

    def _rebuild_metric_row_section_layout(
        self,
        layout: QVBoxLayout,
        sections: dict[str, _MetricRowSection],
        ordered_keys: list[str],
    ) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for key in ordered_keys:
            section = sections.get(key)
            if section is not None:
                layout.addWidget(section.group)
        layout.addStretch(1)

    def _sync_disk_device_sections(
        self,
        disk_devices: list[DiskDeviceSample],
    ) -> list[tuple[DiskDeviceSample, _MetricRowSection]]:
        samples_by_key = {sample.key: sample for sample in disk_devices}
        incoming_keys = [sample.key for sample in disk_devices]
        desired_key_set = set(incoming_keys)
        desired_keys = [key for key in self._disk_device_section_order if key in desired_key_set]
        desired_keys.extend(key for key in incoming_keys if key not in desired_keys)
        for key in list(self._disk_device_sections):
            if key not in desired_key_set:
                self._remove_metric_row_section(self._disk_device_sections, key)
        for key in desired_keys:
            sample = samples_by_key[key]
            if sample.key not in self._disk_device_sections:
                section = self._create_metric_row_section(
                    sample.display_label,
                    parent=self.disk_device_sections_container,
                    theme=self._chart_theme(series_count=8),
                    items=[
                        ("volume", "Volume"),
                        ("free", "Free Space"),
                        ("read", "Read Throughput"),
                        ("write", "Write Throughput"),
                    ],
                    columns=4,
                    chart_specs=[
                        ("usage_history", "Volume Usage"),
                        ("throughput_history", "Disk Throughput"),
                    ],
                    chart_columns=2,
                )
                self._set_empty_chart(section.charts["usage_history"], "Volume Usage", "Collecting live data...")
                self._set_empty_chart(
                    section.charts["throughput_history"],
                    "Disk Throughput",
                    "Collecting live data...",
                )
                self._disk_device_sections[sample.key] = section
            self._disk_device_sections[sample.key].group.setTitle(sample.display_label)
        if desired_keys != self._disk_device_section_order:
            self._rebuild_metric_row_section_layout(
                self.disk_device_sections_layout,
                self._disk_device_sections,
                desired_keys,
            )
            self._disk_device_section_order = list(desired_keys)
        self.disk_device_sections_container.setVisible(bool(desired_keys))
        self.disk_detail_cards = (
            self._disk_device_sections[desired_keys[0]].cards if desired_keys else {}
        )
        return [(samples_by_key[key], self._disk_device_sections[key]) for key in desired_keys]

    def _sync_network_adapter_sections(
        self,
        interfaces: list[InterfaceBandwidthEntry],
    ) -> list[tuple[InterfaceBandwidthEntry, _MetricRowSection]]:
        desired_keys = [entry.name for entry in interfaces]
        for key in list(self._network_adapter_sections):
            if key not in desired_keys:
                self._remove_metric_row_section(self._network_adapter_sections, key)
        for entry in interfaces:
            if entry.name not in self._network_adapter_sections:
                section = self._create_metric_row_section(
                    _network_adapter_display_title(entry),
                    parent=self.network_adapter_sections_container,
                    theme=self._chart_theme(series_count=8),
                    items=[
                        ("receive", "Receive"),
                        ("send", "Send"),
                        ("total_receive", "Received Since Open"),
                        ("total_send", "Sent Since Open"),
                    ],
                    columns=4,
                    chart_specs=[("bandwidth_history", "Network Bandwidth")],
                    chart_columns=1,
                )
                self._set_empty_chart(
                    section.charts["bandwidth_history"],
                    "Network Bandwidth",
                    "Collecting live data...",
                )
                self._network_adapter_sections[entry.name] = section
            self._network_adapter_sections[entry.name].group.setTitle(_network_adapter_display_title(entry))
        if desired_keys != self._network_adapter_section_order:
            self._rebuild_metric_row_section_layout(
                self.network_adapter_sections_layout,
                self._network_adapter_sections,
                desired_keys,
            )
            self._network_adapter_section_order = list(desired_keys)
        self.network_adapter_sections_container.setVisible(bool(desired_keys))
        self.network_detail_cards = (
            self._network_adapter_sections[desired_keys[0]].cards if desired_keys else {}
        )
        return [(entry, self._network_adapter_sections[entry.name]) for entry in interfaces]

    def _visible_ordered_interfaces(self, entries: list[InterfaceBandwidthEntry]) -> list[InterfaceBandwidthEntry]:
        visible = (
            list(entries)
            if self._settings.resource_monitor_show_offline_adapters
            else [entry for entry in entries if entry.is_up]
        )
        return _network_tab_interfaces(visible)

    def _create_gpu_adapter_section(self, adapter: GpuAdapterSample, *, ordinal: int) -> _GpuAdapterSection:
        title = _gpu_adapter_display_name(
            adapter.vendor,
            adapter.name,
            fallback=f"GPU {ordinal + 1}",
        )
        group = QGroupBox(title, self.gpu_adapter_sections_container)
        group.setObjectName("resourceSection")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)

        cards = self._add_metric_cards(
            layout,
            parent=group,
            theme=self._chart_theme(series_count=8),
            items=[
                ("usage", "GPU Usage"),
                ("memory", "VRAM"),
                ("temperature", "Temperature"),
                ("adapter", "Status"),
            ],
            columns=4,
        )

        charts_grid = QGridLayout()
        charts_grid.setContentsMargins(0, 0, 0, 0)
        charts_grid.setHorizontalSpacing(12)
        charts_grid.setVerticalSpacing(12)
        activity_chart = self._create_chart_view("GPU Activity", parent=group)
        temperature_chart = self._create_chart_view("GPU Temperature", parent=group)
        charts_grid.addWidget(activity_chart, 0, 0)
        charts_grid.addWidget(temperature_chart, 0, 1)
        charts_grid.setColumnStretch(0, 1)
        charts_grid.setColumnStretch(1, 1)
        layout.addLayout(charts_grid)

        status_label = self._create_status_label("", group)
        layout.addWidget(status_label)

        self._set_empty_chart(activity_chart, "GPU Activity", "Collecting live data...")
        self._set_empty_chart(temperature_chart, "GPU Temperature", "Temperature sensors are unavailable.")
        return _GpuAdapterSection(
            group=group,
            cards=cards,
            activity_chart=activity_chart,
            temperature_chart=temperature_chart,
            status_label=status_label,
        )

    def _remove_gpu_adapter_section(self, adapter_id: str) -> None:
        section = self._gpu_adapter_sections.pop(adapter_id, None)
        if section is None:
            return
        self._chart_states.pop(section.activity_chart, None)
        self._chart_states.pop(section.temperature_chart, None)
        for card in section.cards.values():
            while card in self._metric_cards:
                self._metric_cards.remove(card)
        section.group.setParent(None)
        section.group.deleteLater()

    def _rebuild_gpu_adapter_section_layout(self, adapter_ids: list[str]) -> None:
        while self.gpu_adapter_sections_layout.count():
            item = self.gpu_adapter_sections_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for adapter_id in adapter_ids:
            section = self._gpu_adapter_sections.get(adapter_id)
            if section is not None:
                self.gpu_adapter_sections_layout.addWidget(section.group)
        self.gpu_adapter_sections_layout.addStretch(1)
        self._gpu_adapter_section_order = list(adapter_ids)

    def _sync_gpu_adapter_sections(self, adapters: list[GpuAdapterSample]) -> list[tuple[GpuAdapterSample, _GpuAdapterSection]]:
        ordered_adapters = _ordered_gpu_adapters(adapters)
        desired_ids = [adapter.id or f"gpu-adapter-{index}" for index, adapter in enumerate(ordered_adapters)]
        for adapter_id in list(self._gpu_adapter_sections):
            if adapter_id not in desired_ids:
                self._remove_gpu_adapter_section(adapter_id)
        for index, adapter in enumerate(ordered_adapters):
            adapter_id = desired_ids[index]
            if adapter_id not in self._gpu_adapter_sections:
                self._gpu_adapter_sections[adapter_id] = self._create_gpu_adapter_section(adapter, ordinal=index)
            title = _gpu_adapter_display_name(
                adapter.vendor,
                adapter.name,
                fallback=f"GPU {index + 1}",
            )
            self._gpu_adapter_sections[adapter_id].group.setTitle(title)
        if desired_ids != self._gpu_adapter_section_order:
            self._rebuild_gpu_adapter_section_layout(desired_ids)
        self.gpu_adapter_sections_container.setVisible(bool(desired_ids))
        self.gpu_detail_cards = (
            self._gpu_adapter_sections[desired_ids[0]].cards if desired_ids else {}
        )
        return [
            (adapter, self._gpu_adapter_sections[adapter_id])
            for adapter, adapter_id in zip(ordered_adapters, desired_ids, strict=False)
        ]

    def _populate_filesystems(
        self,
        tree: QTreeWidget,
        entries: list[FilesystemEntry],
        disk_devices: list[DiskDeviceSample] | None = None,
    ) -> None:
        disk_devices_by_key = self._disk_devices_by_key(disk_devices or [])
        signature = tuple(
            (
                entry.device,
                entry.mountpoint,
                entry.filesystem_type,
                entry.used_bytes,
                entry.total_bytes,
                entry.free_bytes,
                entry.disk_device_key,
                disk_devices_by_key.get(entry.disk_device_key).read_bytes_per_sec
                if entry.disk_device_key in disk_devices_by_key
                else None,
                disk_devices_by_key.get(entry.disk_device_key).write_bytes_per_sec
                if entry.disk_device_key in disk_devices_by_key
                else None,
                entry.usage_percent,
                entry.is_home,
            )
            for entry in entries
        )
        self._reconcile_keyed_tree(
            tree,
            stage="populate-filesystems",
            signature=signature,
            row_count=len(entries),
            entry_key=lambda entry: entry.mountpoint,
            entries=entries,
            update_item=lambda item, entry: self._update_filesystem_item(item, entry, disk_devices_by_key),
            create_item=lambda entry: self._build_filesystem_item(entry, disk_devices_by_key),
        )

    def _populate_interfaces(self, tree: QTreeWidget, entries: list[InterfaceBandwidthEntry]) -> None:
        name_sort_roles = (
            {entry.name: index for index, entry in enumerate(entries)}
            if tree is self.network_interfaces_tree
            else {}
        )
        signature = tuple(
            (
                entry.name,
                entry.ipv4_address,
                entry.ipv6_address,
                entry.is_up,
                entry.speed_mbps,
                entry.recv_bytes_per_sec,
                entry.sent_bytes_per_sec,
                entry.recv_bytes_total,
                entry.sent_bytes_total,
            )
            for entry in entries
        )
        self._reconcile_keyed_tree(
            tree,
            stage="populate-interfaces",
            signature=signature,
            row_count=len(entries),
            entry_key=lambda entry: entry.name,
            entries=entries,
            update_item=lambda item, entry: self._update_interface_item(
                item,
                entry,
                name_sort_role=name_sort_roles.get(entry.name),
            ),
            create_item=lambda entry: self._build_interface_item(
                entry,
                name_sort_role=name_sort_roles.get(entry.name),
            ),
        )

    def _populate_cpu_cores(self, percentages: tuple[float, ...]) -> None:
        signature = tuple(percentages)
        if self._tree_signatures.get(self.cpu_cores_tree) == signature:
            return
        sort_column = self.cpu_cores_tree.sortColumn()
        header = self.cpu_cores_tree.header()
        sort_order = header.sortIndicatorOrder() if header is not None else Qt.AscendingOrder
        previous_signal_state = self.cpu_cores_tree.blockSignals(True)
        self.cpu_cores_tree.setUpdatesEnabled(False)
        try:
            self.cpu_cores_tree.setSortingEnabled(False)
            self.cpu_cores_tree.clear()
            for index, value in enumerate(percentages):
                item = _SortableTreeWidgetItem([f"CPU {index}", f"{value:.1f}%"])
                item.setData(0, _SORT_ROLE, index)
                item.setData(1, _SORT_ROLE, value)
                self.cpu_cores_tree.addTopLevelItem(item)
            self.cpu_cores_tree.setSortingEnabled(True)
            self.cpu_cores_tree.sortItems(sort_column, sort_order)
        finally:
            self.cpu_cores_tree.blockSignals(previous_signal_state)
            self.cpu_cores_tree.setUpdatesEnabled(True)
        self._tree_signatures[self.cpu_cores_tree] = signature

    def _populate_process_tree(self, snapshot: ProcessInventorySnapshot) -> None:
        signature = tuple(
            (
                entry.pid,
                entry.name,
                entry.cpu_percent,
                entry.memory_rss_bytes,
                entry.threads,
                entry.user,
                entry.status,
                entry.started_at,
                entry.command,
            )
            for entry in snapshot.entries
        )
        if self._tree_signatures.get(self.process_tree) == signature:
            self._apply_process_filter()
            self._update_process_action_state()
            return
        context = self._tree_log_context(
            self.process_tree,
            row_count=len(snapshot.entries),
            default_column=_DEFAULT_PROCESS_SORT_COLUMN,
            default_order=_DEFAULT_PROCESS_SORT_ORDER,
        )

        def _apply_process_tree() -> None:
            sort_column, sort_order = self._tree_sort_state(
                self.process_tree,
                default_column=_DEFAULT_PROCESS_SORT_COLUMN,
                default_order=_DEFAULT_PROCESS_SORT_ORDER,
            )
            header = self.process_tree.header()
            selected_pid = self._selected_process_pid()
            previous_signal_state = self.process_tree.blockSignals(True)
            self.process_tree.setUpdatesEnabled(False)
            try:
                self._ui_log_debug(
                    "Resource Monitor UI breadcrumb phase=populate-process-tree-clear-start",
                    **context,
                )
                self.process_tree.setSortingEnabled(False)
                self.process_tree.clear()
                self._ui_log_debug(
                    "Resource Monitor UI breadcrumb phase=populate-process-tree-clear-done",
                    **context,
                )

                self._current_processes = {entry.pid: entry for entry in snapshot.entries}
                self._ui_log_debug(
                    "Resource Monitor UI breadcrumb phase=populate-process-tree-row-mutation-start",
                    **context,
                )
                for entry in snapshot.entries:
                    status_text = _prettify_process_status(entry.status)
                    started_text = _format_started_at(entry.started_at)
                    user_text = entry.user or "Unknown"
                    values = [
                        entry.name,
                        str(entry.pid),
                        f"{entry.cpu_percent:.1f}",
                        _format_bytes(entry.memory_rss_bytes),
                        str(entry.threads),
                        user_text,
                        status_text,
                        started_text,
                        entry.command,
                    ]
                    item = _SortableTreeWidgetItem(values)
                    item.setData(0, Qt.UserRole, entry.pid)
                    item.setData(0, _SEARCH_ROLE, " ".join(values).lower())
                    item.setData(1, _SORT_ROLE, entry.pid)
                    item.setData(2, _SORT_ROLE, entry.cpu_percent)
                    item.setData(3, _SORT_ROLE, entry.memory_rss_bytes)
                    item.setData(4, _SORT_ROLE, entry.threads)
                    item.setData(7, _SORT_ROLE, entry.started_at or 0.0)
                    self.process_tree.addTopLevelItem(item)
                    if selected_pid is not None and entry.pid == selected_pid:
                        item.setSelected(True)
                self._ui_log_debug(
                    "Resource Monitor UI breadcrumb phase=populate-process-tree-row-mutation-done",
                    **context,
                )

                self._ui_log_debug(
                    "Resource Monitor UI breadcrumb phase=populate-process-tree-sort-start",
                    **context,
                )
                if header is not None:
                    header.setSortIndicator(sort_column, sort_order)
                self.process_tree.setSortingEnabled(True)
                self.process_tree.sortItems(sort_column, sort_order)
                self._ui_log_debug(
                    "Resource Monitor UI breadcrumb phase=populate-process-tree-sort-done",
                    **context,
                )
            finally:
                self.process_tree.blockSignals(previous_signal_state)
                self.process_tree.setUpdatesEnabled(True)
            self._tree_signatures[self.process_tree] = signature
            self._apply_process_filter()
            self._update_process_action_state()

        self._run_timed_ui_stage("populate-process-tree", _apply_process_tree, **context)

    def _selected_process_pid(self) -> int | None:
        items = self.process_tree.selectedItems()
        if not items:
            return None
        try:
            return int(items[0].data(0, Qt.UserRole))
        except (TypeError, ValueError):
            return None

    @Slot()
    def _apply_process_filter(self) -> None:
        needle = self.process_search_input.text().strip().lower()
        for index in range(self.process_tree.topLevelItemCount()):
            item = self.process_tree.topLevelItem(index)
            row_text = str(item.data(0, _SEARCH_ROLE) or "")
            item.setHidden(bool(needle) and needle not in row_text)

    @staticmethod
    def _disk_devices_by_key(disk_devices: list[DiskDeviceSample]) -> dict[str, DiskDeviceSample]:
        return {sample.key: sample for sample in disk_devices}

    @staticmethod
    def _count_value(count: int, singular: str) -> str:
        return f"{count} {singular}{'' if count == 1 else 's'}"

    @staticmethod
    def _overview_device_block(name: str, stats: str) -> str:
        cleaned_name = str(name or "").strip() or "Unknown"
        cleaned_stats = str(stats or "").strip() or "Unavailable"
        return f"{cleaned_name}\n {cleaned_stats}"

    @staticmethod
    def _preferred_filesystem_entry(entries: list[FilesystemEntry]) -> FilesystemEntry | None:
        if not entries:
            return None
        return sorted(
            entries,
            key=lambda entry: (
                0 if entry.is_home else 1,
                -entry.total_bytes,
                entry.mountpoint.casefold(),
                entry.device.casefold(),
            ),
        )[0]

    def _preferred_filesystems_by_disk_device_key(
        self,
        filesystems: list[FilesystemEntry],
    ) -> dict[str, FilesystemEntry]:
        grouped: dict[str, list[FilesystemEntry]] = {}
        for entry in filesystems:
            if not entry.disk_device_key:
                continue
            grouped.setdefault(entry.disk_device_key, []).append(entry)
        preferred: dict[str, FilesystemEntry] = {}
        for key, entries in grouped.items():
            chosen = self._preferred_filesystem_entry(entries)
            if chosen is not None:
                preferred[key] = chosen
        return preferred

    @staticmethod
    def _filesystem_mount_label(entry: FilesystemEntry) -> str:
        return f"{entry.mountpoint} (Home)" if entry.is_home else entry.mountpoint

    @staticmethod
    def _disk_capacity_detail_text(entry: FilesystemEntry) -> str:
        return (
            f"{entry.usage_percent:.1f}% used, "
            f"{_format_bytes(entry.used_bytes)} / {_format_bytes(entry.total_bytes)}, "
            f"Free {_format_bytes(entry.free_bytes)}"
        )

    @staticmethod
    def _gpu_status_value(adapter: GpuAdapterSample) -> str:
        unavailable_count = 0
        if adapter.utilization_percent is None:
            unavailable_count += 1
        if _gpu_adapter_memory_percent(adapter) is None:
            unavailable_count += 1
        if adapter.temperature_c is None:
            unavailable_count += 1
        if unavailable_count <= 0:
            return "Ready"
        if unavailable_count >= 3:
            return "Detected"
        return "Partial"

    @staticmethod
    def _gpu_status_detail_text(adapter: GpuAdapterSample) -> str:
        parts: list[str] = []
        if adapter.backend:
            parts.append(adapter.backend)
        if adapter.id:
            parts.append(adapter.id)
        return " | ".join(parts) if parts else "Adapter telemetry status"

    def _overview_disk_card(self, snapshot: ResourceMonitorSnapshot) -> tuple[str, str]:
        disk_devices = snapshot.disk_devices
        filesystems_by_key = self._preferred_filesystems_by_disk_device_key(snapshot.filesystems)
        if disk_devices:
            return (
                self._count_value(len(disk_devices), "Disk"),
                "\n".join(
                    self._overview_device_block(
                        disk_device.display_label,
                        (
                            self._disk_capacity_detail_text(filesystems_by_key[disk_device.key])
                            if disk_device.key in filesystems_by_key
                            else "Mounted volume details unavailable"
                        ),
                    )
                    for disk_device in disk_devices
                ),
            )

        sample = snapshot.sample
        mount_label = sample.disk_mountpoint or "Home Volume"
        return (
            "Home Volume",
            self._overview_device_block(
                mount_label,
                (
                    f"{sample.disk_percent:.1f}% used, "
                    f"{_format_bytes(sample.disk_used_bytes)} / {_format_bytes(sample.disk_total_bytes)}, "
                    f"Free {_format_bytes(sample.disk_free_bytes)}"
                ),
            ),
        )

    def _overview_disk_io_card(self, snapshot: ResourceMonitorSnapshot) -> tuple[str, str]:
        disk_devices = snapshot.disk_devices
        if disk_devices:
            return (
                self._count_value(len(disk_devices), "Disk"),
                "\n".join(
                    self._overview_device_block(
                        disk_device.display_label,
                        (
                            f"Read {_format_rate(disk_device.read_bytes_per_sec)} "
                            f"Write {_format_rate(disk_device.write_bytes_per_sec)}"
                        ),
                    )
                    for disk_device in disk_devices
                ),
            )

        sample = snapshot.sample
        return (
            "Aggregate",
            self._overview_device_block(
                sample.disk_mountpoint or "Home Volume",
                (
                    f"Read {_format_rate(sample.disk_read_bytes_per_sec)} "
                    f"Write {_format_rate(sample.disk_write_bytes_per_sec)}"
                ),
            ),
        )

    def _overview_cpu_card(self, sample: ResourceMonitorSample) -> tuple[str, str]:
        stats = [f"{sample.logical_cpu_count or 0} logical cores"]
        if sample.cpu_temperature_c is not None:
            stats.append(_format_temperature(sample.cpu_temperature_c))
        else:
            stats.append("Temp --")
        return f"{sample.cpu_percent:.0f}%", self._overview_device_block("CPU", " ".join(stats))

    def _overview_gpu_card(self, gpu: GpuSample) -> tuple[str, str]:
        if not gpu.detected:
            return "Unavailable", gpu.message or "GPU telemetry is unavailable."
        adapters = _ordered_gpu_adapters(gpu.adapters)
        value = self._count_value(len(adapters), "GPU")
        detail_lines: list[str] = []
        for adapter in adapters:
            name = _gpu_adapter_display_name(adapter.vendor, adapter.name, fallback="GPU")
            memory_percent = _gpu_adapter_memory_percent(adapter)
            stats_parts = [
                (
                    f"Util {adapter.utilization_percent:.0f}%"
                    if adapter.utilization_percent is not None
                    else "Util --"
                ),
                (
                    f"VRAM {memory_percent:.0f}%"
                    if memory_percent is not None
                    else "VRAM --"
                ),
                f"Temp {_format_temperature(adapter.temperature_c)}",
            ]
            detail_lines.append(self._overview_device_block(name, " ".join(stats_parts)))
        return value, "\n".join(detail_lines)

    def _overview_network_card(
        self,
        sample: ResourceMonitorSample,
        interfaces: list[InterfaceBandwidthEntry],
    ) -> tuple[str, str]:
        if interfaces:
            return (
                self._count_value(len(interfaces), "Adapter"),
                "\n".join(
                    self._overview_device_block(
                        entry.name,
                        (
                            "Offline"
                            if not entry.is_up
                            else (
                                f"Down {_format_network_rate(entry.recv_bytes_per_sec)} "
                                f"Up {_format_network_rate(entry.sent_bytes_per_sec)}"
                            )
                        ),
                    )
                    for entry in interfaces
                ),
            )
        return (
            "Network",
            self._overview_device_block(
                "All Interfaces",
                (
                    f"Down {_format_network_rate(sample.network_recv_bytes_per_sec)} "
                    f"Up {_format_network_rate(sample.network_sent_bytes_per_sec)}"
                ),
            ),
        )

    def _history_disk_usage_points(self, disk_device_key: str) -> list[float | None]:
        points: list[float | None] = []
        for snapshot in self._history:
            filesystems_by_key = self._preferred_filesystems_by_disk_device_key(snapshot.filesystems)
            entry = filesystems_by_key.get(disk_device_key)
            points.append(entry.usage_percent if entry is not None else None)
        return points

    def _history_disk_series_for_device(
        self,
        device: DiskDeviceSample,
    ) -> tuple[list[tuple[str, list[float | None]]], str]:
        read_points: list[float | None] = []
        write_points: list[float | None] = []
        for snapshot in self._history:
            sample = self._disk_devices_by_key(snapshot.disk_devices).get(device.key)
            read_points.append(sample.read_bytes_per_sec if sample is not None else None)
            write_points.append(sample.write_bytes_per_sec if sample is not None else None)
        if all(value is None for value in read_points) and all(value is None for value in write_points):
            return [], ""
        return _scaled_byte_rate_series_args(
            [
                ("Read", read_points),
                ("Write", write_points),
            ]
        )

    def _history_interface_series(
        self,
        interface_name: str,
    ) -> tuple[list[tuple[str, list[float | None]]], str]:
        receive_points: list[float | None] = []
        send_points: list[float | None] = []
        for snapshot in self._history:
            entry = next((item for item in snapshot.interfaces if item.name == interface_name), None)
            receive_points.append(entry.recv_bytes_per_sec if entry is not None else None)
            send_points.append(entry.sent_bytes_per_sec if entry is not None else None)
        if all(value is None for value in receive_points) and all(value is None for value in send_points):
            return [], ""
        return _scaled_network_series_args(
            [
                ("Receive", receive_points),
                ("Send", send_points),
            ]
        )

    def _history_disk_series(self) -> tuple[list[tuple[str, list[float | None]]], str]:
        if not self._history:
            return [], ""
        latest_disk_devices = self._history[-1].disk_devices
        if not latest_disk_devices:
            return [], ""

        series_specs: list[tuple[str, list[float | None]]] = []
        for device in latest_disk_devices:
            read_points: list[float | None] = []
            write_points: list[float | None] = []
            for snapshot in self._history:
                sample = self._disk_devices_by_key(snapshot.disk_devices).get(device.key)
                read_points.append(sample.read_bytes_per_sec if sample is not None else None)
                write_points.append(sample.write_bytes_per_sec if sample is not None else None)
            if any(value is not None and value > 0 for value in read_points):
                series_specs.append((f"{device.display_label} Read", read_points))
            if any(value is not None and value > 0 for value in write_points):
                series_specs.append((f"{device.display_label} Write", write_points))
        if not series_specs:
            return [], ""
        return _scaled_byte_rate_series_args(series_specs)

    def _history_gpu_series(self) -> list[tuple[str, list[float | None]]]:
        if not self._history:
            return []
        latest_gpu = self._history[-1].gpu
        adapters = _ordered_gpu_adapters(latest_gpu.adapters)
        series_specs: list[tuple[str, list[float | None]]] = []
        for adapter in adapters:
            base_label = _gpu_adapter_display_name(adapter.vendor, adapter.name, fallback="GPU")
            adapter_history = [
                next((entry for entry in snapshot.gpu.adapters if entry.id == adapter.id), None)
                for snapshot in self._history
            ]
            gpu_points = [
                entry.utilization_percent if entry is not None and entry.utilization_percent is not None else None
                for entry in adapter_history
            ]
            gpu_memory_points = [_gpu_adapter_memory_percent(entry) for entry in adapter_history]
            if any(point is not None for point in gpu_points):
                series_specs.append((f"{base_label} Util", gpu_points))
            if any(point is not None for point in gpu_memory_points):
                series_specs.append((f"{base_label} VRAM", gpu_memory_points))
        return series_specs

    def _update_cards(self, snapshot: ResourceMonitorSnapshot) -> None:
        sample = snapshot.sample
        gpu = snapshot.gpu
        cpu_value, cpu_detail = self._overview_cpu_card(sample)
        self._cards["cpu"].set_content(cpu_value, cpu_detail)
        memory_detail = f"Swap {_format_bytes(sample.swap_used_bytes)} / {_format_bytes(sample.swap_total_bytes)}"
        self._cards["memory"].set_content(
            f"{_format_bytes_card(sample.memory_used_bytes)} / {_format_bytes_card(sample.memory_total_bytes)}",
            f"{sample.memory_percent:.1f}% used\n{memory_detail}",
        )
        disk_value, disk_detail = self._overview_disk_card(snapshot)
        self._cards["disk"].set_content(disk_value, disk_detail)
        disk_io_value, disk_io_detail = self._overview_disk_io_card(snapshot)
        self._cards["disk_io"].set_content(disk_io_value, disk_io_detail)
        network_value, network_detail = self._overview_network_card(
            sample,
            self._visible_ordered_interfaces(snapshot.interfaces),
        )
        self._cards["network"].set_content(network_value, network_detail)
        self._cards["processes"].set_content(
            str(sample.process_count),
            f"{sample.thread_count} threads currently visible",
        )
        gpu_value, gpu_detail = self._overview_gpu_card(gpu)
        self._cards["gpu"].set_content(gpu_value, gpu_detail)

    def _update_process_card(self) -> None:
        card = self._cards.get("processes")
        if card is None:
            return
        card.set_content(
            str(self._last_process_count),
            f"{self._last_thread_count} threads currently visible",
        )

    def _history_samples(self) -> list[ResourceMonitorSnapshot]:
        return self._history

    def _refresh_overview_charts(self) -> None:
        if not self._history:
            return
        samples = [snapshot.sample for snapshot in self._history]

        self._set_line_chart(
            self.cpu_chart,
            "CPU Usage",
            [("CPU", [sample.cpu_percent for sample in samples])],
            fixed_max=_cpu_chart_fixed_max(samples),
            fixed_point_count=self._max_history,
        )
        self._set_line_chart(
            self.memory_chart,
            "Memory Usage",
            [("Memory", [sample.memory_percent for sample in samples]), ("Swap", [sample.swap_percent for sample in samples])],
            fixed_max=100.0,
            fixed_point_count=self._max_history,
        )
        disk_series_specs, disk_axis_title = self._history_disk_series()
        if not disk_series_specs:
            self._set_empty_chart(self.disk_chart, "Disk Throughput", "Per-device disk throughput is unavailable.")
        else:
            self._set_line_chart(
                self.disk_chart,
                "Disk Throughput",
                disk_series_specs,
                axis_title=disk_axis_title,
                fixed_point_count=self._max_history,
            )
        network_series_specs, network_axis_title = _network_graph_series_specs(
            [sample.network_recv_bytes_per_sec for sample in samples],
            [sample.network_sent_bytes_per_sec for sample in samples],
        )
        self._set_line_chart(
            self.network_chart,
            "Network Bandwidth",
            network_series_specs,
            axis_title=network_axis_title,
            fixed_point_count=self._max_history,
        )

        gpu_series_specs = self._history_gpu_series()
        if not gpu_series_specs:
            latest_gpu = self._history[-1].gpu
            self._set_empty_chart(self.gpu_chart, "GPU", latest_gpu.message or "GPU telemetry is unavailable.")
        else:
            self._set_line_chart(
                self.gpu_chart,
                "GPU",
                gpu_series_specs,
                fixed_max=100.0,
                fixed_point_count=self._max_history,
            )

    def _refresh_cpu_charts(self) -> None:
        if not self._history:
            return
        samples = [snapshot.sample for snapshot in self._history]
        self._set_line_chart(
            self.cpu_detail_chart,
            "CPU Usage History",
            [("CPU", [sample.cpu_percent for sample in samples])],
            fixed_max=_cpu_chart_fixed_max(samples),
            fixed_point_count=self._max_history,
        )
        temperatures = [sample.cpu_temperature_c for sample in samples]
        if all(value is None for value in temperatures):
            self._set_empty_chart(self.cpu_temp_chart, "CPU Temperature", _cpu_temperature_unavailable_text())
        else:
            self._set_line_chart(
                self.cpu_temp_chart,
                "CPU Temperature",
                [("Temp", [value if value is not None else 0.0 for value in temperatures])],
                axis_title="C",
                fixed_point_count=self._max_history,
            )

    def _refresh_ram_charts(self) -> None:
        if not self._history:
            return
        samples = [snapshot.sample for snapshot in self._history]
        self._set_line_chart(
            self.ram_usage_chart,
            "Memory Pressure",
            [("Memory", [sample.memory_percent for sample in samples]), ("Swap", [sample.swap_percent for sample in samples])],
            fixed_max=100.0,
            fixed_point_count=self._max_history,
        )
        self._set_line_chart(
            self.ram_bytes_chart,
            "Used Memory",
            [
                ("Memory", [_bytes_to_gb(sample.memory_used_bytes) for sample in samples]),
                ("Swap", [_bytes_to_gb(sample.swap_used_bytes) for sample in samples]),
            ],
            axis_title="GB",
            fixed_point_count=self._max_history,
        )

    def _refresh_disk_charts(self) -> None:
        if not self._history:
            return
        samples = [snapshot.sample for snapshot in self._history]
        self._set_line_chart(
            self.disk_usage_history_chart,
            "Home Volume Usage",
            [("Usage", [sample.disk_percent for sample in samples])],
            fixed_max=100.0,
            fixed_point_count=self._max_history,
        )
        disk_series_specs, disk_axis_title = self._history_disk_series()
        if not disk_series_specs:
            self._set_empty_chart(
                self.disk_io_history_chart,
                "Disk Throughput",
                "Per-device disk throughput is unavailable.",
            )
        else:
            self._set_line_chart(
                self.disk_io_history_chart,
                "Disk Throughput",
                disk_series_specs,
                axis_title=disk_axis_title,
                fixed_point_count=self._max_history,
            )

    def _refresh_network_charts(self) -> None:
        if not self._history:
            return
        samples = [snapshot.sample for snapshot in self._history]
        network_series_specs, network_axis_title = _network_graph_series_specs(
            [sample.network_recv_bytes_per_sec for sample in samples],
            [sample.network_sent_bytes_per_sec for sample in samples],
        )
        self._set_line_chart(
            self.network_detail_chart,
            "Network Bandwidth",
            network_series_specs,
            axis_title=network_axis_title,
            fixed_point_count=self._max_history,
        )

    def _refresh_gpu_charts(self) -> None:
        if not self._history:
            return
        latest_gpu = self._history[-1].gpu
        if not latest_gpu.adapters:
            for adapter_id in list(self._gpu_adapter_sections):
                self._remove_gpu_adapter_section(adapter_id)
            if self._gpu_adapter_section_order:
                self._rebuild_gpu_adapter_section_layout([])
            self.gpu_adapter_sections_container.setVisible(False)
            self.gpu_detail_cards = {}
            return

        for adapter, section in self._sync_gpu_adapter_sections(latest_gpu.adapters):
            if adapter.utilization_percent is None:
                section.cards["usage"].set_content("--", "Utilization telemetry unavailable.")
            else:
                section.cards["usage"].set_content(
                    f"{adapter.utilization_percent:.1f}%",
                    "Current GPU utilization",
                )

            memory_percent = _gpu_adapter_memory_percent(adapter)
            if adapter.memory_used_bytes is None or adapter.memory_total_bytes is None or memory_percent is None:
                section.cards["memory"].set_content("--", "VRAM telemetry unavailable.")
            else:
                section.cards["memory"].set_content(
                    f"{_format_bytes(adapter.memory_used_bytes)} / {_format_bytes(adapter.memory_total_bytes)}",
                    f"{memory_percent:.1f}% used",
                )

            if adapter.temperature_c is None:
                section.cards["temperature"].set_content("--", "Temperature telemetry unavailable.")
            else:
                section.cards["temperature"].set_content(
                    _format_temperature(adapter.temperature_c),
                    "Current adapter temperature",
                )

            section.cards["adapter"].set_content(
                self._gpu_status_value(adapter),
                self._gpu_status_detail_text(adapter),
            )

            adapter_history = [
                next((entry for entry in snapshot.gpu.adapters if entry.id == adapter.id), None)
                for snapshot in self._history
            ]
            gpu_points = [
                entry.utilization_percent if entry is not None and entry.utilization_percent is not None else None
                for entry in adapter_history
            ]
            gpu_memory_points = [_gpu_adapter_memory_percent(entry) for entry in adapter_history]
            gpu_temperature_points = [
                entry.temperature_c if entry is not None and entry.temperature_c is not None else None
                for entry in adapter_history
            ]

            gpu_series_specs: list[tuple[str, list[float | None]]] = []
            if any(point is not None for point in gpu_points):
                gpu_series_specs.append(("GPU", gpu_points))
            if any(point is not None for point in gpu_memory_points):
                gpu_series_specs.append(("VRAM", gpu_memory_points))
            if not gpu_series_specs:
                self._set_empty_chart(
                    section.activity_chart,
                    "GPU Activity",
                    "Utilization and VRAM telemetry are unavailable for this adapter.",
                )
            else:
                self._set_line_chart(
                    section.activity_chart,
                    "GPU Activity",
                    gpu_series_specs,
                    fixed_max=100.0,
                    fixed_point_count=self._max_history,
                )
            if all(point is None for point in gpu_temperature_points):
                self._set_empty_chart(
                    section.temperature_chart,
                    "GPU Temperature",
                    "Temperature telemetry is unavailable for this adapter.",
                )
            else:
                self._set_line_chart(
                    section.temperature_chart,
                    "GPU Temperature",
                    [("Temp", gpu_temperature_points)],
                    axis_title="C",
                    fixed_point_count=self._max_history,
                )
            section.status_label.setText(_gpu_adapter_telemetry_status_text(adapter))

    def _set_empty_chart(self, view: QChartView, title: str, message: str) -> None:
        state = self._chart_states.get(view)
        if (
            state is not None
            and state.get("kind") == "empty"
            and state.get("title") == title
            and state.get("message") == message
        ):
            return
        chart = view.chart()
        if chart is None:
            chart = QChart()
            self._swap_chart(view, chart)
        for series in list(chart.series()):
            try:
                chart.removeSeries(series)
            except RuntimeError:
                continue
        for axis in list(chart.axes()):
            try:
                chart.removeAxis(axis)
                axis.deleteLater()
            except RuntimeError:
                continue
        chart.setTitle(f"{title}\n{message}")
        chart.legend().setVisible(False)
        self._apply_chart_theme(chart)
        self._chart_states[view] = {
            "kind": "empty",
            "title": title,
            "message": message,
            "chart": chart,
        }

    def _set_line_chart(
        self,
        view: QChartView,
        title: str,
        series_specs: list[tuple[str, list[float | None]]],
        *,
        axis_title: str = "",
        fixed_max: float | None = None,
        fixed_point_count: int | None = None,
    ) -> None:
        all_values = [value for _label, values in series_specs for value in values if value is not None]
        if not all_values:
            self._set_empty_chart(view, title, "Collecting live data...")
            return

        max_points = max((len(values) for _label, values in series_specs), default=0)
        series_labels = tuple(label for label, _values in series_specs)
        data_signature = tuple((label, tuple(values)) for label, values in series_specs)
        state = self._chart_states.get(view)
        if (
            state is None
            or state.get("kind") != "line"
            or state.get("labels") != series_labels
            or state.get("axis_title") != axis_title
            or state.get("fixed_max") != fixed_max
            or state.get("fixed_point_count") != fixed_point_count
        ):
            chart = QChart()
            chart.setTitle(title)
            theme = self._apply_chart_theme(chart, series_count=max(1, len(series_specs)))

            axis_x = QValueAxis(chart)
            axis_x.setLabelsVisible(False)
            axis_x.setGridLineVisible(False)
            self._style_chart_axis(axis_x, theme)
            chart.addAxis(axis_x, Qt.AlignBottom)

            axis_y = QValueAxis(chart)
            axis_y.setTitleText(axis_title)
            self._style_chart_axis(axis_y, theme)
            chart.addAxis(axis_y, Qt.AlignLeft)

            series_colors = theme["series_colors"] if isinstance(theme["series_colors"], list) else [theme["accent"]]
            series_objects: list[QLineSeries] = []
            for index, (label, _values) in enumerate(series_specs):
                series = QSplineSeries(chart)
                series.setName(label)
                pen = QPen(series_colors[index % len(series_colors)] if series_colors else theme["accent"])
                pen.setWidthF(2.2)
                series.setPen(pen)
                chart.addSeries(series)
                series.attachAxis(axis_x)
                series.attachAxis(axis_y)
                series_objects.append(series)

            chart.legend().setVisible(len(series_specs) > 1)
            self._swap_chart(view, chart)
            state = {
                "kind": "line",
                "title": title,
                "labels": series_labels,
                "axis_title": axis_title,
                "fixed_max": fixed_max,
                "fixed_point_count": fixed_point_count,
                "chart": chart,
                "axis_x": axis_x,
                "axis_y": axis_y,
                "series": series_objects,
            }
            self._chart_states[view] = state

        if (
            state.get("data_signature") == data_signature
            and state.get("title") == title
            and state.get("fixed_point_count") == fixed_point_count
        ):
            return

        chart = state["chart"]
        axis_x = state["axis_x"]
        axis_y = state["axis_y"]
        series_objects = state["series"]
        state["title"] = title
        state["data_signature"] = data_signature
        chart.setTitle(title)
        state["fixed_point_count"] = fixed_point_count
        point_count = fixed_point_count if fixed_point_count is not None else max_points
        axis_x.setRange(0, max(1, point_count - 1))
        axis_y.setTitleText(axis_title)
        if fixed_max is not None:
            axis_y.setRange(0.0, fixed_max)
        else:
            axis_y.setRange(0.0, _nice_axis_max(max(all_values)))

        for series, (_label, values) in zip(series_objects, series_specs, strict=False):
            series.clear()
            x_offset = max(0, point_count - len(values)) if fixed_point_count is not None else 0
            for value_index, value in enumerate(values):
                if value is None:
                    continue
                series.append(float(x_offset + value_index), float(value))
        chart.legend().setVisible(len(series_specs) > 1)

    def _swap_chart(self, view: QChartView, chart: QChart) -> None:
        previous = view.chart()
        view.setChart(chart)
        if previous is not None and previous is not chart:
            try:
                previous.deleteLater()
            except RuntimeError:
                pass

    def _theme_settings(self) -> AppSettings:
        if not self._settings_explicit:
            for candidate in (
                getattr(self.parent(), "_settings", None),
                getattr(self.window(), "_settings", None),
            ):
                if isinstance(candidate, AppSettings):
                    return candidate
        if isinstance(self._settings, AppSettings):
            return self._settings
        return AppSettings.defaults()

    @staticmethod
    def _safe_qcolor(value: str, fallback: str) -> QColor:
        color = QColor(str(value).strip())
        if not color.isValid():
            color = QColor(fallback)
        return color

    def _chart_series_colors(self, accent: QColor, background: QColor, *, count: int) -> list[QColor]:
        base_hue = accent.hslHue() if accent.hslHue() >= 0 else 212
        base_saturation = max(120, accent.hslSaturation())
        dark_background = background.lightness() < 140
        base_lightness = 160 if dark_background else 110
        colors: list[QColor] = []
        for index in range(max(1, count)):
            hue = (base_hue + (index * 29)) % 360
            saturation = max(100, min(240, base_saturation + (12 if index % 3 == 0 else 0) - (8 if index % 4 == 0 else 0)))
            lightness = max(58, min(214, base_lightness + ((index % 4) - 1) * (12 if dark_background else 10)))
            colors.append(QColor.fromHsl(hue, saturation, lightness))
        if colors:
            colors[0] = accent
        return colors

    def _chart_theme(self, *, series_count: int = 8) -> dict[str, QColor | list[QColor]]:
        settings = self._theme_settings()
        background = self._safe_qcolor(settings.field_bg, "#1a222d")
        plot_background = self._safe_qcolor(
            blend_colors(background.name(), settings.app_bg_end, 0.26),
            background.name(),
        )
        accent = self._safe_qcolor(settings.accent_color, "#2d6cdf")
        text = self._safe_qcolor(
            readable_foreground_color(settings.text_color, background.name(), minimum_ratio=4.5),
            "#ffffff",
        )
        title = self._safe_qcolor(
            readable_foreground_color(text.name(), background.name(), minimum_ratio=4.5),
            text.name(),
        )
        border = self._safe_qcolor(
            settings.field_border,
            blend_colors(background.name(), text.name(), 0.18),
        )
        grid = self._safe_qcolor(
            blend_colors(border.name(), text.name(), 0.18),
            border.name(),
        )
        return {
            "background": background,
            "plot_background": plot_background,
            "accent": accent,
            "text": text,
            "title": title,
            "border": border,
            "grid": grid,
            "series_colors": self._chart_series_colors(accent, background, count=series_count),
        }

    def _chart_font(self, point_size: float, *, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        font = QApplication.font()
        font.setPointSizeF(self._scaled_point_size(point_size))
        font.setWeight(weight)
        return font

    def _apply_chart_theme(self, chart: QChart, *, series_count: int = 8) -> dict[str, QColor | list[QColor]]:
        theme = self._chart_theme(series_count=series_count)
        chart.setAnimationOptions(QChart.NoAnimation)
        chart.setBackgroundVisible(True)
        chart.setBackgroundRoundness(10.0)
        chart.setBackgroundBrush(QBrush(theme["background"]))
        chart.setBackgroundPen(QPen(theme["border"]))
        chart.setPlotAreaBackgroundVisible(True)
        chart.setPlotAreaBackgroundBrush(QBrush(theme["plot_background"]))
        chart.setPlotAreaBackgroundPen(QPen(theme["border"]))
        try:
            chart.setTitleBrush(QBrush(theme["title"]))
        except Exception:
            pass
        try:
            chart.setTitleFont(self._chart_font(10.0, weight=QFont.Weight.DemiBold))
        except Exception:
            pass
        legend = chart.legend()
        try:
            legend.setLabelColor(theme["text"])
        except Exception:
            pass
        try:
            legend.setFont(self._chart_font(9.0))
        except Exception:
            pass
        try:
            legend.setBrush(QBrush(Qt.GlobalColor.transparent))
        except Exception:
            pass
        try:
            legend.setPen(QPen(Qt.PenStyle.NoPen))
        except Exception:
            pass
        return theme

    def _style_chart_axis(self, axis, theme: dict[str, QColor | list[QColor]]) -> None:
        try:
            axis.setLabelsColor(theme["text"])
        except Exception:
            pass
        try:
            axis.setLabelsFont(self._chart_font(9.0))
        except Exception:
            pass
        try:
            axis.setGridLineColor(theme["grid"])
        except Exception:
            pass
        try:
            axis.setLinePen(QPen(theme["border"]))
        except Exception:
            pass
        try:
            axis.setLinePenColor(theme["border"])
        except Exception:
            pass
        try:
            axis.setShadesVisible(False)
        except Exception:
            pass
        try:
            axis.setTitleFont(self._chart_font(9.0, weight=QFont.Weight.DemiBold))
        except Exception:
            pass

    def _show_process_context_menu(self, point) -> None:
        if self._selected_process_pid() is None:
            return
        menu = QMenu(self)
        end_action = menu.addAction("End Task")
        force_action = menu.addAction("Force Kill")
        chosen = menu.exec(self.process_tree.viewport().mapToGlobal(point))
        if chosen == end_action:
            self._start_process_action(force=False)
        elif chosen == force_action:
            self._start_process_action(force=True)

    def _start_process_action(self, *, force: bool) -> None:
        pid = self._selected_process_pid()
        if pid is None:
            self.process_status_label.setText("Select a process first.")
            return
        entry = self._current_processes.get(pid)
        if entry is None:
            self.process_status_label.setText(f"Process {pid} is no longer available.")
            return
        if pid == os.getpid():
            QMessageBox.warning(
                self,
                "Resource Monitor",
                "SnakeSh cannot terminate its own process from the Resource Monitor.",
            )
            self.process_status_label.setText("Ignored attempt to terminate the SnakeSh process.")
            return

        title = "Force Kill Process" if force else "End Process"
        verb = "force kill" if force else "end"
        answer = QMessageBox.question(
            self,
            title,
            f"Do you want to {verb} `{entry.name}` (PID {pid})?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self.process_status_label.setText("Process action cancelled.")
            return
        self._launch_process_action(pid=pid, force=force, allow_elevation=False)

    def _launch_process_action(self, *, pid: int, force: bool, allow_elevation: bool) -> bool:
        if self._action_thread is not None or self._is_closing:
            return False
        self._set_process_action_running(True)
        self.process_status_label.setText(
            f"{'Force killing' if force else 'Ending'} PID {pid}{' with elevation' if allow_elevation else ''}..."
        )
        self._active_action_pid = pid
        self._active_action_force = force
        self._action_thread = QThread(self)
        self._action_worker = _TaskWorker(
            "resource-monitor-process-action",
            lambda _stop_callback: perform_process_action(
                pid,
                force=force,
                allow_elevation=allow_elevation,
                current_pid=os.getpid(),
            ),
            context={"pid": pid, "force": force, "allow_elevation": allow_elevation},
        )
        self._action_worker.moveToThread(self._action_thread)
        self._action_thread.started.connect(self._action_worker.run)
        self._action_worker.succeeded.connect(
            lambda payload: self._enqueue_main_thread_call("_on_process_action_ready", payload)
        )
        self._action_worker.failed.connect(
            lambda message: self._enqueue_main_thread_call("_on_process_action_failed", message)
        )
        self._action_worker.finished.connect(self._action_thread.quit)
        self._action_worker.finished.connect(self._action_worker.deleteLater)
        self._action_thread.finished.connect(lambda: self._enqueue_main_thread_call("_on_action_finished"))
        self._action_thread.finished.connect(self._action_thread.deleteLater)
        self._action_thread.start()
        return True

    def _enqueue_main_thread_call(self, method_name: str, *args: object) -> None:
        with self._main_thread_call_lock:
            self._main_thread_calls.append((method_name, args))

    @Slot()
    def _drain_main_thread_calls(self) -> None:
        with self._main_thread_call_lock:
            if not self._main_thread_calls:
                return
            pending_calls = list(self._main_thread_calls)
            self._main_thread_calls.clear()

        def _drain_pending_calls() -> None:
            for method_name, args in pending_calls:
                self._run_timed_ui_stage(
                    "dispatch-main-thread-call",
                    lambda method_name=method_name, args=args: getattr(self, method_name)(*args),
                    method=method_name,
                    queued_args=len(args),
                    queued_calls=len(pending_calls),
                )

        self._run_timed_ui_stage(
            "drain-main-thread-calls",
            _drain_pending_calls,
            queued_calls=len(pending_calls),
        )

    @Slot(object)
    def _on_process_action_ready(self, payload: object) -> None:
        if self._is_closing:
            return
        if not isinstance(payload, ProcessActionResult):
            self._on_process_action_failed("Unexpected process action response.")
            return
        pid = self._active_action_pid if self._active_action_pid is not None else payload.pid
        force = self._active_action_force
        if payload.requires_elevation:
            answer = QMessageBox.question(
                self,
                "Administrator Authorization Required",
                payload.message + "\n\nContinue with an authorization prompt?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self._pending_elevated_action = (pid, force)
            else:
                self._pending_elevated_action = None
                self.process_status_label.setText("Administrator authorization was not requested.")
            return

        self.process_status_label.setText(payload.message)
        if payload.success:
            self._start_process_refresh()
            self._start_sample_refresh()

    @Slot(str)
    def _on_process_action_failed(self, message: str) -> None:
        if self._is_closing:
            return
        self.process_status_label.setText(message)

    @Slot()
    def _on_action_finished(self) -> None:
        self._action_thread = None
        self._action_worker = None
        self._active_action_pid = None
        self._active_action_force = False
        self._set_process_action_running(False)
        pending = self._pending_elevated_action
        self._pending_elevated_action = None
        if pending is not None:
            pid, force = pending
            if self._launch_process_action(pid=pid, force=force, allow_elevation=True):
                return
        self._maybe_finish_close()

    def _set_process_action_running(self, running: bool) -> None:
        self.process_refresh_btn.setEnabled(not running and self._process_thread is None)
        self.process_search_input.setEnabled(not running)
        self._update_process_action_state(disable_for_action=running)

    def _update_process_action_state(self, *_args, disable_for_action: bool | None = None) -> None:
        if disable_for_action is None:
            disable_for_action = self._action_thread is not None
        has_selection = self._selected_process_pid() is not None
        enabled = has_selection and not disable_for_action
        self.end_task_btn.setEnabled(enabled)
        self.force_kill_btn.setEnabled(enabled)
        if self._process_thread is None and self._action_thread is None:
            self.process_refresh_btn.setEnabled(True)
        elif self._process_thread is not None:
            self.process_refresh_btn.setEnabled(False)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._is_closing = True
        self._sample_timer.stop()
        self._slow_detail_timer.stop()
        self._process_timer.stop()
        self._close_pending = True
        self._request_thread_shutdown(self._sample_thread)
        self._request_thread_shutdown(self._slow_detail_thread)
        self._request_thread_shutdown(self._process_thread)
        self._request_thread_shutdown(self._action_thread)
        if self._has_active_threads():
            if not self._close_force_timer.isActive():
                self._close_force_timer.start()
            self.setEnabled(False)
            self.overview_status_label.setText("Closing Resource Monitor after background tasks finish...")
            self.process_status_label.setText("Closing Resource Monitor after background tasks finish...")
            event.ignore()
            return
        self._close_pending = False
        self._main_thread_dispatch_timer.stop()
        if self._close_force_timer.isActive():
            self._close_force_timer.stop()
        self._remove_zoom_wheel_event_filter()
        QSettings("SnakeSh", "SnakeSh").setValue(self._GEOMETRY_KEY, self.saveGeometry())
        super().closeEvent(event)

    @staticmethod
    def _request_thread_shutdown(thread: QThread | None) -> None:
        if thread is None:
            return
        try:
            thread.requestInterruption()
            thread.quit()
        except RuntimeError:
            return

    @staticmethod
    def _thread_is_running(thread: QThread | None) -> bool:
        if thread is None:
            return False
        try:
            return thread.isRunning()
        except RuntimeError:
            return False

    def _has_active_threads(self) -> bool:
        return any(
            self._thread_is_running(thread)
            for thread in (self._sample_thread, self._slow_detail_thread, self._process_thread, self._action_thread)
        )

    def _maybe_finish_close(self) -> None:
        if self._close_pending and not self._has_active_threads():
            if self._close_force_timer.isActive():
                self._close_force_timer.stop()
            QTimer.singleShot(0, self.close)

    @Slot()
    def _force_close_threads(self) -> None:
        for thread in (self._sample_thread, self._slow_detail_thread, self._process_thread, self._action_thread):
            self._wait_for_thread_shutdown(thread)
        if self._has_active_threads():
            self.overview_status_label.setText("Waiting for background tasks to finish...")
            self.process_status_label.setText("Waiting for background tasks to finish...")
            return
        self._maybe_finish_close()

    @staticmethod
    def _wait_for_thread_shutdown(thread: QThread | None) -> None:
        if not ResourceMonitorDialog._thread_is_running(thread):
            return
        try:
            thread.wait(250)
        except RuntimeError:
            return


def _format_bytes(value: int | float) -> str:
    size = float(max(0.0, float(value)))
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    index = 0
    while size >= 1024.0 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    if index == 0:
        return f"{int(size)} {units[index]}"
    return f"{size:.1f} {units[index]}"


def _format_bytes_card(value: int | float) -> str:
    size = float(max(0.0, float(value)))
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    index = 0
    while size >= 1024.0 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    if size >= 1023.95 and index < len(units) - 1:
        size = 1.0
        index += 1
    return f"{size:.1f} {units[index]}"


def _format_rate(value: float) -> str:
    return f"{_format_bytes(value)}/s"


def _format_rate_card(value: float) -> str:
    return f"{_format_bytes_card(value)}/s"


def _bytes_to_gb(value: int | float) -> float:
    return max(0.0, float(value)) / (1024.0**3)


def _byte_graph_unit(max_bytes_per_second: float) -> tuple[float, str]:
    value = max(0.0, float(max_bytes_per_second))
    units = ["B/s", "KB/s", "MB/s", "GB/s", "TB/s", "PB/s"]
    scale = 1.0
    unit_index = 0
    while value >= 1024.0 and unit_index < len(units) - 1:
        value /= 1024.0
        scale *= 1024.0
        unit_index += 1
    return scale, units[unit_index]


def _byte_rate_graph_series_args(
    read_values_bytes_per_second: list[float],
    write_values_bytes_per_second: list[float],
) -> tuple[list[tuple[str, list[float]]], str]:
    scaled_series, axis_title = _scaled_byte_rate_series_args(
        [
            ("Read", read_values_bytes_per_second),
            ("Write", write_values_bytes_per_second),
        ]
    )
    return [
        (label, [0.0 if value is None else value for value in values])
        for label, values in scaled_series
    ], axis_title


def _scaled_byte_rate_series_args(
    series_specs: list[tuple[str, list[float | None]]],
) -> tuple[list[tuple[str, list[float | None]]], str]:
    max_value = max(
        (
            float(value)
            for _label, values in series_specs
            for value in values
            if value is not None
        ),
        default=0.0,
    )
    scale, axis_title = _byte_graph_unit(max_value)
    return [
        (
            label,
            [None if value is None else float(value) / scale for value in values],
        )
        for label, values in series_specs
    ], axis_title


def _bytes_per_second_to_bits(value: float) -> float:
    return max(0.0, float(value)) * 8.0


def _network_graph_unit(max_bits_per_second: float) -> tuple[float, str]:
    value = max(0.0, float(max_bits_per_second))
    units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps", "Pbps"]
    scale = 1.0
    unit_index = 0
    while value >= 1000.0 and unit_index < len(units) - 1:
        value /= 1000.0
        scale *= 1000.0
        unit_index += 1
    return scale, units[unit_index]


def _network_graph_series_specs(
    receive_values_bytes_per_second: list[float],
    send_values_bytes_per_second: list[float],
) -> tuple[list[tuple[str, list[float]]], str]:
    receive_bits = [_bytes_per_second_to_bits(value) for value in receive_values_bytes_per_second]
    send_bits = [_bytes_per_second_to_bits(value) for value in send_values_bytes_per_second]
    scale, axis_title = _network_graph_unit(max(receive_bits + send_bits, default=0.0))
    return [
        ("Receive", [value / scale for value in receive_bits]),
        ("Send", [value / scale for value in send_bits]),
    ], axis_title


def _scaled_network_series_args(
    series_specs: list[tuple[str, list[float | None]]],
) -> tuple[list[tuple[str, list[float | None]]], str]:
    max_value = max(
        (
            _bytes_per_second_to_bits(value)
            for _label, values in series_specs
            for value in values
            if value is not None
        ),
        default=0.0,
    )
    scale, axis_title = _network_graph_unit(max_value)
    return [
        (
            label,
            [
                None if value is None else _bytes_per_second_to_bits(value) / scale
                for value in values
            ],
        )
        for label, values in series_specs
    ], axis_title


def _format_network_rate(value: float) -> str:
    rate = _bytes_per_second_to_bits(value)
    units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps", "Pbps"]
    index = 0
    while rate >= 1000.0 and index < len(units) - 1:
        rate /= 1000.0
        index += 1
    if index == 0:
        return f"{int(rate)} {units[index]}"
    if rate >= 100.0:
        return f"{rate:.0f} {units[index]}"
    if rate >= 10.0:
        return f"{rate:.1f} {units[index]}"
    return f"{rate:.2f} {units[index]}"


def _format_temperature(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{float(value):.0f} C"


def _is_loopback_interface(entry: InterfaceBandwidthEntry) -> bool:
    name = str(entry.name or "").strip().casefold()
    if name in {"lo", "lo0"} or name.startswith("loopback"):
        return True
    for value in (entry.ipv4_address, entry.ipv6_address):
        cleaned = str(value or "").strip()
        if not cleaned or cleaned.casefold() == "unassigned":
            continue
        try:
            if ipaddress.ip_address(cleaned.split("%", 1)[0]).is_loopback:
                return True
        except ValueError:
            continue
    return False


def _network_adapter_display_title(entry: InterfaceBandwidthEntry) -> str:
    for value in (entry.ipv4_address, entry.ipv6_address):
        cleaned = str(value or "").strip()
        if cleaned and cleaned.casefold() != "unassigned":
            return f"{entry.name} ({cleaned})"
    return entry.name


def _network_tab_interfaces(entries: list[InterfaceBandwidthEntry]) -> list[InterfaceBandwidthEntry]:
    return sorted(
        entries,
        key=lambda entry: (
            0 if entry.is_up else 1,
            1 if _is_loopback_interface(entry) else 0,
            entry.name.casefold(),
        ),
    )


def _gpu_vendor_from_display_text(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return ""
    if any(token in lowered for token in ("nvidia", "geforce", "quadro", "tesla", "rtx")):
        return "NVIDIA"
    if any(token in lowered for token in ("advanced micro devices", "amd", "radeon", "ati")):
        return "AMD"
    if any(token in lowered for token in ("intel", "iris", "uhd", "arc")):
        return "Intel"
    if "apple" in lowered:
        return "Apple"
    return ""


def _gpu_adapter_display_name(vendor: str, name: str, *, fallback: str = "GPU") -> str:
    cleaned_vendor = str(vendor or "").strip()
    cleaned_name = str(name or "").strip()
    if cleaned_name:
        detected_vendor = _gpu_vendor_from_display_text(cleaned_name)
        if cleaned_vendor and detected_vendor and detected_vendor != cleaned_vendor:
            return cleaned_name
        if cleaned_vendor and cleaned_name.casefold().startswith(cleaned_vendor.casefold()):
            return cleaned_name
        if cleaned_vendor and not detected_vendor:
            return f"{cleaned_vendor} {cleaned_name}".strip()
        return cleaned_name
    if cleaned_vendor:
        return cleaned_vendor
    return fallback


def _ordered_gpu_adapters(adapters: list[GpuAdapterSample]) -> list[GpuAdapterSample]:
    return sorted(
        adapters,
        key=lambda adapter: (
            adapter.adapter_index if adapter.adapter_index is not None else 10_000,
            _gpu_adapter_display_name(adapter.vendor, adapter.name, fallback="GPU").casefold(),
            adapter.id,
        ),
    )


def _gpu_adapter_memory_percent(adapter: GpuAdapterSample | None) -> float | None:
    if adapter is None:
        return None
    if adapter.memory_used_bytes is None or adapter.memory_total_bytes is None or adapter.memory_total_bytes <= 0:
        return None
    return min(100.0, max(0.0, (adapter.memory_used_bytes / adapter.memory_total_bytes) * 100.0))


def _gpu_adapter_telemetry_status_text(adapter: GpuAdapterSample) -> str:
    unavailable: list[str] = []
    if adapter.utilization_percent is None:
        unavailable.append("utilization")
    if _gpu_adapter_memory_percent(adapter) is None:
        unavailable.append("VRAM")
    if adapter.temperature_c is None:
        unavailable.append("temperature")
    if (
        adapter.backend == "windows-counters"
        and adapter.utilization_percent is not None
        and _gpu_adapter_memory_percent(adapter) is None
        and adapter.temperature_c is None
    ):
        return "Utilization is coming from Windows GPU counters. VRAM and temperature telemetry remain best-effort for this adapter."
    if not unavailable:
        return "Live utilization, VRAM, and temperature telemetry are available for this adapter."
    if len(unavailable) == 3:
        if adapter.vendor == "Intel":
            return (
                "Intel adapter detected. Live telemetry requires intel_gpu_top/perf access "
                "or kernel DRM fdinfo counters."
            )
        return "Adapter detected, but live telemetry is unavailable for this adapter."
    return f"{', '.join(unavailable)} telemetry is unavailable for this adapter."


def _format_started_at(value: float | None) -> str:
    if value is None or value <= 0:
        return "Unknown"
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "Unknown"


def _prettify_process_status(value: str) -> str:
    cleaned = value.replace("_", " ").strip()
    return cleaned.title() if cleaned else "Unknown"


def _nice_axis_max(value: float) -> float:
    if value <= 0:
        return 1.0
    if value <= 5:
        return float(int(value + 1.0))
    magnitude = 10 ** max(0, len(str(int(value))) - 1)
    rounded = ((int((value / magnitude) * 2.0 + 0.999999)) * (magnitude / 2))
    return max(1.0, float(rounded))


def _cpu_chart_fixed_max(samples: list[ResourceMonitorSample]) -> float | None:
    cpu_values = [sample.cpu_percent for sample in samples]
    if any(value > 100.0 for value in cpu_values):
        return None
    return 100.0


def _cpu_temperature_unavailable_text() -> str:
    if platform.system().strip().lower() == "windows":
        return "Best-effort CPU temperature telemetry is unavailable."
    return "Temperature sensors are unavailable."


def _cpu_temperature_detail_text() -> str:
    if platform.system().strip().lower() == "windows":
        return "Highest available Windows CPU temperature source"
    return "Highest available CPU package/core sensor"
