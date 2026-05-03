from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from pathlib import Path

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QScatterSeries,
    QSplineSeries,
    QValueAxis,
)
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QPainter, QPen
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from snakesh.services.mtr_trace import MTRProbeSample, MTRTraceSnapshot, format_mtr_samples_csv


@dataclass(slots=True)
class _HopSummary:
    hop: int
    host: str = ""
    address: str = ""
    sent: int = 0
    received: int = 0
    last_ms: float | None = None
    best_ms: float | None = None
    worst_ms: float | None = None
    avg_ms: float | None = None
    stdev_ms: float | None = None
    reached_destination: bool = False


class _HopFilterButton(QToolButton):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._available_hops: list[int] = []
        self._selected_hops: set[int] = set()
        self._follow_all_hops = True
        self._menu = QMenu(self)
        self.setText("All hops")
        self.setPopupMode(QToolButton.InstantPopup)
        self.setMenu(self._menu)

    def set_available_hops(self, hops: list[int]) -> None:
        normalized = sorted({int(hop) for hop in hops if int(hop) > 0})
        if normalized == self._available_hops:
            if self._follow_all_hops:
                self._selected_hops = set(normalized)
                self._update_label()
            return
        self._available_hops = normalized
        available_set = set(normalized)
        if self._follow_all_hops:
            self._selected_hops = set(normalized)
        else:
            valid_selected = self._selected_hops & available_set
            if valid_selected:
                self._selected_hops = valid_selected
            else:
                self._selected_hops = set(normalized)
                self._follow_all_hops = True
        self._rebuild_menu()
        self._update_label()

    def selected_hops(self) -> set[int]:
        return set(self._selected_hops)

    def set_selected_hops(self, hops: set[int]) -> None:
        valid_selected = {hop for hop in hops if hop in self._available_hops}
        self._selected_hops = valid_selected or set(self._available_hops)
        self._follow_all_hops = self._selected_hops == set(self._available_hops)
        self._rebuild_menu()
        self._update_label()
        self.changed.emit()

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        select_all = self._menu.addAction("All hops")
        select_all.triggered.connect(self._select_all_hops)
        self._menu.addSeparator()

        for hop in self._available_hops:
            action = QAction(f"Hop {hop}", self._menu)
            action.setCheckable(True)
            action.setChecked(hop in self.selected_hops())
            action.toggled.connect(lambda checked, hop_value=hop: self._toggle_hop(hop_value, checked))
            self._menu.addAction(action)

    def _select_all_hops(self) -> None:
        self._selected_hops = set(self._available_hops)
        self._follow_all_hops = True
        self._rebuild_menu()
        self._update_label()
        self.changed.emit()

    def _toggle_hop(self, hop: int, checked: bool) -> None:
        if checked:
            self._selected_hops.add(hop)
        else:
            self._selected_hops.discard(hop)
        if not self._selected_hops:
            self._selected_hops = set(self._available_hops)
        self._follow_all_hops = self._selected_hops == set(self._available_hops)
        self._update_label()
        self.changed.emit()

    def _update_label(self) -> None:
        selected = sorted(self.selected_hops())
        if not selected or selected == self._available_hops:
            self.setText("All hops")
            return
        if len(selected) <= 4:
            self.setText("Hops: " + ", ".join(str(hop) for hop in selected))
            return
        self.setText(f"{len(selected)} hops selected")


class _HeatmapWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._samples: list[MTRProbeSample] = []
        self.setMinimumHeight(320)

    def set_samples(self, samples: list[MTRProbeSample]) -> None:
        self._samples = list(samples)
        self.update()

    def _layout_metrics(self) -> dict[str, QRect]:
        font_metrics = self.fontMetrics()
        left_margin = 44
        top_margin = 26
        right_margin = 12
        axis_label_height = font_metrics.height()
        legend_box_size = 12
        legend_row_height = max(axis_label_height, legend_box_size)
        bottom_margin = axis_label_height + legend_row_height + 18
        grid_rect = self.rect().adjusted(left_margin, top_margin, -right_margin, -bottom_margin)
        axis_label_rect = QRect(
            grid_rect.left(),
            grid_rect.bottom() + 6,
            max(0, grid_rect.width()),
            axis_label_height,
        )
        legend_top = axis_label_rect.bottom() + 4
        legend_box_rect = QRect(
            8,
            legend_top + max(0, (legend_row_height - legend_box_size) // 2),
            legend_box_size,
            legend_box_size,
        )
        legend_text_rect = QRect(
            legend_box_rect.right() + 6,
            legend_top,
            font_metrics.horizontalAdvance("Timeout") + 4,
            legend_row_height,
        )
        return {
            "grid_rect": grid_rect,
            "axis_label_rect": axis_label_rect,
            "legend_box_rect": legend_box_rect,
            "legend_text_rect": legend_text_rect,
        }

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#111827"))

        if not self._samples:
            painter.setPen(QColor("#cbd5e1"))
            painter.drawText(self.rect(), Qt.AlignCenter, "No probe samples available.")
            return

        hops = sorted({sample.hop for sample in self._samples})
        sample_indexes = sorted({sample.sample_index for sample in self._samples})
        max_rtt = max((sample.rtt_ms or 0.0) for sample in self._samples if sample.success) if any(
            sample.success for sample in self._samples
        ) else 1.0
        sample_map = {(sample.sample_index, sample.hop): sample for sample in self._samples}

        layout = self._layout_metrics()
        grid_rect = layout["grid_rect"]
        if grid_rect.width() <= 0 or grid_rect.height() <= 0:
            return

        cell_width = max(1.0, grid_rect.width() / max(1, len(sample_indexes)))
        cell_height = max(1.0, grid_rect.height() / max(1, len(hops)))

        painter.setPen(QColor("#64748b"))
        painter.drawText(8, 36, "Hop")
        painter.drawText(layout["axis_label_rect"], Qt.AlignHCenter | Qt.AlignVCenter, "Sample Index / Cycle")

        cycle_boundaries: list[tuple[int, int]] = []
        last_cycle = None
        for column, sample_index in enumerate(sample_indexes):
            sample = next((item for item in self._samples if item.sample_index == sample_index), None)
            if sample is None:
                continue
            if last_cycle is not None and sample.cycle != last_cycle:
                cycle_boundaries.append((column, sample.cycle))
            last_cycle = sample.cycle

        for row, hop in enumerate(hops):
            y = grid_rect.top() + int(row * cell_height)
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(6, y + int(cell_height * 0.7), str(hop))

        for column, sample_index in enumerate(sample_indexes):
            x = grid_rect.left() + int(column * cell_width)
            for row, hop in enumerate(hops):
                y = grid_rect.top() + int(row * cell_height)
                sample = sample_map.get((sample_index, hop))
                color = QColor("#1f2937")
                if sample is not None:
                    if sample.timeout:
                        color = QColor("#dc2626")
                    elif sample.success and sample.rtt_ms is not None:
                        ratio = min(1.0, max(0.0, sample.rtt_ms / max_rtt))
                        color = QColor.fromHsvF(max(0.0, 0.62 - (0.55 * ratio)), 0.85, 0.92)
                painter.fillRect(x, y, max(1, int(cell_width)), max(1, int(cell_height)), color)

        painter.setPen(QPen(QColor("#334155")))
        for row in range(len(hops) + 1):
            y = grid_rect.top() + int(row * cell_height)
            painter.drawLine(grid_rect.left(), y, grid_rect.right(), y)
        for column in range(len(sample_indexes) + 1):
            x = grid_rect.left() + int(column * cell_width)
            painter.drawLine(x, grid_rect.top(), x, grid_rect.bottom())

        painter.setPen(QPen(QColor("#e2e8f0"), 1))
        for boundary_column, cycle in cycle_boundaries:
            x = grid_rect.left() + int(boundary_column * cell_width)
            painter.drawLine(x, grid_rect.top(), x, grid_rect.bottom())
            painter.drawText(x + 4, 20, f"C{cycle}")

        painter.fillRect(layout["legend_box_rect"], QColor("#dc2626"))
        painter.setPen(QColor("#cbd5e1"))
        painter.drawText(layout["legend_text_rect"], Qt.AlignLeft | Qt.AlignVCenter, "Timeout")


class TracerouteGraphDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Traceroute Graphs")
        self.resize(1220, 860)
        self.setMinimumSize(880, 620)

        self._snapshot: MTRTraceSnapshot | None = None
        self._samples: list[MTRProbeSample] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Hops"))
        self.hop_filter_button = _HopFilterButton(self)
        controls.addWidget(self.hop_filter_button)
        self.include_timeouts_check = QCheckBox("Include timeouts")
        self.include_timeouts_check.setChecked(True)
        controls.addWidget(self.include_timeouts_check)
        controls.addWidget(QLabel("Cycles"))
        self.cycle_min_input = QSpinBox(self)
        self.cycle_min_input.setRange(0, 0)
        self.cycle_min_input.setSpecialValueText("All")
        self.cycle_min_input.setValue(0)
        self.cycle_max_input = QSpinBox(self)
        self.cycle_max_input.setRange(0, 0)
        self.cycle_max_input.setSpecialValueText("All")
        self.cycle_max_input.setValue(0)
        controls.addWidget(self.cycle_min_input)
        controls.addWidget(QLabel("to"))
        controls.addWidget(self.cycle_max_input)
        controls.addStretch(1)
        self.export_image_btn = QPushButton("Export Image")
        self.export_csv_btn = QPushButton("Export CSV")
        controls.addWidget(self.export_image_btn)
        controls.addWidget(self.export_csv_btn)
        root.addLayout(controls)

        self.tabs = QTabWidget(self)
        root.addWidget(self.tabs, 1)

        self.rtt_chart_view = self._create_chart_view()
        self.latency_summary_view = self._create_chart_view()
        self.packet_loss_view = self._create_chart_view()
        self.jitter_view = self._create_chart_view()
        self.heatmap_widget = _HeatmapWidget(self)
        self.cumulative_path_view = self._create_chart_view()
        self.reachability_trend_view = self._create_chart_view()

        self.worst_rank_mode = QComboBox(self)
        self.worst_rank_mode.addItem("Average RTT", "avg")
        self.worst_rank_mode.addItem("Worst RTT", "worst")
        self.worst_rank_mode.addItem("Loss %", "loss")
        self.worst_hops_view = self._create_chart_view()

        self.tabs.addTab(self.rtt_chart_view, "RTT Over Time")
        self.tabs.addTab(self.latency_summary_view, "Latency Summary")
        self.tabs.addTab(self.packet_loss_view, "Packet Loss")
        self.tabs.addTab(self.jitter_view, "Jitter")
        self.tabs.addTab(self._wrap_widget(self.heatmap_widget), "Hop Heatmap")
        self.tabs.addTab(self._build_worst_hops_page(), "Worst Hops")
        self.tabs.addTab(self.cumulative_path_view, "Cumulative Path")
        self.tabs.addTab(self.reachability_trend_view, "Reachability Trend")

        self.status_label = QLabel("No traceroute graph data loaded.")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.hop_filter_button.changed.connect(self._refresh_views)
        self.include_timeouts_check.toggled.connect(self._refresh_views)
        self.cycle_min_input.valueChanged.connect(self._on_cycle_range_changed)
        self.cycle_max_input.valueChanged.connect(self._on_cycle_range_changed)
        self.export_image_btn.clicked.connect(self._export_current_image)
        self.export_csv_btn.clicked.connect(self._export_current_csv)
        self.worst_rank_mode.currentIndexChanged.connect(self._refresh_views)

        self._refresh_views()

    def set_trace_data(self, snapshot: MTRTraceSnapshot | None, samples: list[MTRProbeSample]) -> None:
        self._snapshot = snapshot
        self._samples = list(samples)
        self.setWindowTitle(f"Traceroute Graphs - {snapshot.target}" if snapshot is not None else "Traceroute Graphs")
        self._sync_filters_to_data()
        self._refresh_views()

    def clear_trace_data(self) -> None:
        self.set_trace_data(None, [])

    def current_sample_count(self) -> int:
        return len(self._samples)

    def _sync_filters_to_data(self) -> None:
        hops = sorted({sample.hop for sample in self._samples})
        self.hop_filter_button.set_available_hops(hops)

        max_cycle = max((sample.cycle for sample in self._samples), default=0)
        for widget in (self.cycle_min_input, self.cycle_max_input):
            widget.blockSignals(True)
            widget.setRange(0, max_cycle)
            if widget.value() > max_cycle:
                widget.setValue(0)
            widget.blockSignals(False)

    def _on_cycle_range_changed(self) -> None:
        lower = self.cycle_min_input.value()
        upper = self.cycle_max_input.value()
        if lower and upper and lower > upper:
            sender = self.sender()
            if sender is self.cycle_min_input:
                self.cycle_max_input.blockSignals(True)
                self.cycle_max_input.setValue(lower)
                self.cycle_max_input.blockSignals(False)
            else:
                self.cycle_min_input.blockSignals(True)
                self.cycle_min_input.setValue(upper)
                self.cycle_min_input.blockSignals(False)
        self._refresh_views()

    def _selected_cycle_range(self) -> tuple[int | None, int | None]:
        minimum = self.cycle_min_input.value() or None
        maximum = self.cycle_max_input.value() or None
        return minimum, maximum

    def _scoped_samples(self) -> list[MTRProbeSample]:
        selected_hops = self.hop_filter_button.selected_hops()
        cycle_min, cycle_max = self._selected_cycle_range()
        scoped: list[MTRProbeSample] = []
        for sample in self._samples:
            if selected_hops and sample.hop not in selected_hops:
                continue
            if cycle_min is not None and sample.cycle < cycle_min:
                continue
            if cycle_max is not None and sample.cycle > cycle_max:
                continue
            scoped.append(sample)
        return scoped

    def _plot_samples(self) -> list[MTRProbeSample]:
        scoped = self._scoped_samples()
        if self.include_timeouts_check.isChecked():
            return scoped
        return [sample for sample in scoped if not sample.timeout]

    def _refresh_views(self) -> None:
        scoped_samples = self._scoped_samples()
        plot_samples = self._plot_samples()
        summaries = _summarize_probe_samples(scoped_samples)

        self._update_rtt_chart(plot_samples, scoped_samples)
        self._update_latency_summary_chart(summaries)
        self._update_packet_loss_chart(summaries)
        self._update_jitter_chart(summaries)
        self._update_heatmap(plot_samples)
        self._update_worst_hops_chart(summaries)
        self._update_cumulative_path_chart(summaries)
        self._update_reachability_chart(scoped_samples)

        has_samples = bool(scoped_samples)
        self.export_image_btn.setEnabled(has_samples)
        self.export_csv_btn.setEnabled(has_samples)
        if self._snapshot is None:
            self.status_label.setText("No traceroute graph data loaded.")
        elif not scoped_samples:
            self.status_label.setText("No probe samples match the current graph filters.")
        else:
            self.status_label.setText(
                f"{len(scoped_samples)} probe samples across {len({sample.hop for sample in scoped_samples})} hop(s)."
            )

    def _create_chart_view(self) -> QChartView:
        chart = _empty_chart("Waiting for traceroute data", "No samples available.")
        view = QChartView(chart, self)
        view.setRenderHint(QPainter.Antialiasing, True)
        return view

    def _wrap_widget(self, widget: QWidget) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(widget, 1)
        return page

    def _build_worst_hops_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel("Rank by"))
        row.addWidget(self.worst_rank_mode)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(self.worst_hops_view, 1)
        return page

    def _update_rtt_chart(self, plot_samples: list[MTRProbeSample], scoped_samples: list[MTRProbeSample]) -> None:
        title = "RTT Over Time"
        chart = _styled_chart(title)
        success_samples = [sample for sample in plot_samples if sample.success and sample.rtt_ms is not None]
        if not success_samples and not (self.include_timeouts_check.isChecked() and any(sample.timeout for sample in scoped_samples)):
            self.rtt_chart_view.setChart(_empty_chart(title, "No RTT samples in the current filter range."))
            return

        y_max = max((sample.rtt_ms or 0.0) for sample in success_samples) if success_samples else 1.0
        x_max = max((sample.sample_index for sample in scoped_samples), default=1)
        grouped_success: dict[int, list[MTRProbeSample]] = defaultdict(list)
        for sample in success_samples:
            grouped_success[sample.hop].append(sample)
        for hop in sorted(grouped_success):
            series = QSplineSeries(chart)
            series.setName(f"Hop {hop}")
            series.setPointsVisible(True)
            for sample in grouped_success[hop]:
                assert sample.rtt_ms is not None
                series.append(sample.sample_index, sample.rtt_ms)
            chart.addSeries(series)

        if self.include_timeouts_check.isChecked():
            timeout_samples = [sample for sample in scoped_samples if sample.timeout]
            if timeout_samples:
                timeout_series = QScatterSeries(chart)
                timeout_series.setName("Timeout")
                timeout_series.setColor(QColor("#ef4444"))
                timeout_series.setMarkerSize(8.0)
                for sample in timeout_samples:
                    timeout_series.append(sample.sample_index, 0.0)
                chart.addSeries(timeout_series)

        axis_x = QValueAxis(chart)
        axis_x.setTitleText("Sample Index")
        axis_x.setLabelFormat("%d")
        axis_x.setRange(1, max(1, x_max))
        axis_y = QValueAxis(chart)
        axis_y.setTitleText("RTT (ms)")
        axis_y.setRange(0, _nice_axis_max(y_max))
        chart.addAxis(axis_x, Qt.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignLeft)
        for series in chart.series():
            series.attachAxis(axis_x)
            series.attachAxis(axis_y)
        self.rtt_chart_view.setChart(chart)

    def _update_latency_summary_chart(self, summaries: list[_HopSummary]) -> None:
        title = "Latency Summary"
        if not summaries:
            self.latency_summary_view.setChart(_empty_chart(title, "No hop summaries available."))
            return
        categories = [str(summary.hop) for summary in summaries]
        last_set = QBarSet("Last")
        avg_set = QBarSet("Avg")
        best_set = QBarSet("Best")
        worst_set = QBarSet("Worst")
        max_value = 1.0
        for summary in summaries:
            values = [
                summary.last_ms or 0.0,
                summary.avg_ms or 0.0,
                summary.best_ms or 0.0,
                summary.worst_ms or 0.0,
            ]
            last_set.append(values[0])
            avg_set.append(values[1])
            best_set.append(values[2])
            worst_set.append(values[3])
            max_value = max(max_value, *values)
        chart = _styled_chart(title)
        series = QBarSeries(chart)
        for bar_set in (last_set, avg_set, best_set, worst_set):
            series.append(bar_set)
        chart.addSeries(series)
        _attach_category_axes(chart, series, categories, y_title="Latency (ms)", y_max=max_value)
        self.latency_summary_view.setChart(chart)

    def _update_packet_loss_chart(self, summaries: list[_HopSummary]) -> None:
        title = "Packet Loss"
        if not summaries:
            self.packet_loss_view.setChart(_empty_chart(title, "No packet loss data available."))
            return
        categories = [str(summary.hop) for summary in summaries]
        loss_values = [0.0 if summary.sent == 0 else ((summary.sent - summary.received) / summary.sent) * 100.0 for summary in summaries]
        loss_set = QBarSet("Loss %")
        loss_set.append(loss_values)
        chart = _styled_chart(title)
        series = QBarSeries(chart)
        series.append(loss_set)
        chart.addSeries(series)
        _attach_category_axes(chart, series, categories, y_title="Loss %", y_max=max(1.0, max(loss_values, default=0.0)))
        loss_labels = [
            f"Hop {summary.hop}: {loss:.1f}% loss ({summary.received}/{summary.sent} received)"
            for summary, loss in zip(summaries, loss_values, strict=False)
        ]
        _bind_bar_tooltips(self.packet_loss_view, loss_set, loss_labels)
        self.packet_loss_view.setChart(chart)

    def _update_jitter_chart(self, summaries: list[_HopSummary]) -> None:
        title = "Jitter"
        if not summaries:
            self.jitter_view.setChart(_empty_chart(title, "No jitter data available."))
            return
        categories = [str(summary.hop) for summary in summaries]
        jitter_values = [summary.stdev_ms or 0.0 for summary in summaries]
        jitter_set = QBarSet("StDev")
        jitter_set.append(jitter_values)
        chart = _styled_chart(title)
        series = QBarSeries(chart)
        series.append(jitter_set)
        chart.addSeries(series)
        _attach_category_axes(chart, series, categories, y_title="StDev (ms)", y_max=max(1.0, max(jitter_values, default=0.0)))
        self.jitter_view.setChart(chart)

    def _update_heatmap(self, plot_samples: list[MTRProbeSample]) -> None:
        self.heatmap_widget.set_samples(plot_samples)

    def _update_worst_hops_chart(self, summaries: list[_HopSummary]) -> None:
        title = "Worst Hops"
        if not summaries:
            self.worst_hops_view.setChart(_empty_chart(title, "No ranked hop data available."))
            return
        mode = str(self.worst_rank_mode.currentData() or "avg")
        def _metric(summary: _HopSummary) -> float:
            if mode == "loss":
                return 0.0 if summary.sent == 0 else ((summary.sent - summary.received) / summary.sent) * 100.0
            if mode == "worst":
                return summary.worst_ms or 0.0
            return summary.avg_ms or 0.0

        ranked = sorted(summaries, key=_metric, reverse=True)
        categories = [str(summary.hop) for summary in ranked]
        values = [_metric(summary) for summary in ranked]
        label = "Loss %" if mode == "loss" else ("Worst RTT" if mode == "worst" else "Avg RTT")
        rank_set = QBarSet(label)
        rank_set.append(values)
        chart = _styled_chart(title)
        series = QBarSeries(chart)
        series.append(rank_set)
        chart.addSeries(series)
        y_title = "Loss %" if mode == "loss" else "Latency (ms)"
        _attach_category_axes(chart, series, categories, y_title=y_title, y_max=max(1.0, max(values, default=0.0)))
        self.worst_hops_view.setChart(chart)

    def _update_cumulative_path_chart(self, summaries: list[_HopSummary]) -> None:
        title = "Cumulative Path"
        valid_summaries = [summary for summary in summaries if summary.avg_ms is not None]
        if not valid_summaries:
            self.cumulative_path_view.setChart(_empty_chart(title, "No cumulative path data available."))
            return
        chart = _styled_chart(title)
        series = QSplineSeries(chart)
        series.setName("Cumulative Avg RTT")
        cumulative = 0.0
        for summary in valid_summaries:
            cumulative += summary.avg_ms or 0.0
            series.append(summary.hop, cumulative)
        chart.addSeries(series)
        axis_x = QValueAxis(chart)
        axis_x.setTitleText("Hop")
        axis_x.setLabelFormat("%d")
        axis_x.setRange(valid_summaries[0].hop, valid_summaries[-1].hop)
        axis_y = QValueAxis(chart)
        axis_y.setTitleText("Cumulative Avg RTT (ms)")
        axis_y.setRange(0, _nice_axis_max(cumulative))
        chart.addAxis(axis_x, Qt.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_x)
        series.attachAxis(axis_y)
        self.cumulative_path_view.setChart(chart)

    def _update_reachability_chart(self, scoped_samples: list[MTRProbeSample]) -> None:
        title = "Reachability Trend"
        cycle_stats = _cycle_reachability_stats(scoped_samples)
        if not cycle_stats:
            self.reachability_trend_view.setChart(_empty_chart(title, "No cycle reachability data available."))
            return
        chart = _styled_chart(title)
        highest_series = QSplineSeries(chart)
        highest_series.setName("Highest Replying Hop")
        reached_series = QScatterSeries(chart)
        reached_series.setName("Destination Reached")
        reached_series.setColor(QColor("#22c55e"))
        reached_series.setMarkerSize(10.0)
        max_highest_hop = 1.0
        for cycle, highest_hop, reached_destination in cycle_stats:
            highest_series.append(cycle, highest_hop)
            max_highest_hop = max(max_highest_hop, float(highest_hop))
            if reached_destination:
                reached_series.append(cycle, 1.0)
        chart.addSeries(highest_series)
        chart.addSeries(reached_series)
        axis_x = QValueAxis(chart)
        axis_x.setTitleText("Cycle")
        axis_x.setLabelFormat("%d")
        axis_x.setRange(cycle_stats[0][0], cycle_stats[-1][0])
        hop_axis = QValueAxis(chart)
        hop_axis.setTitleText("Highest Replying Hop")
        hop_axis.setRange(0, _nice_axis_max(max_highest_hop))
        reached_axis = QValueAxis(chart)
        reached_axis.setTitleText("Destination Reached (0/1)")
        reached_axis.setRange(0, 1)
        reached_axis.setTickCount(2)
        chart.addAxis(axis_x, Qt.AlignBottom)
        chart.addAxis(hop_axis, Qt.AlignLeft)
        chart.addAxis(reached_axis, Qt.AlignRight)
        highest_series.attachAxis(axis_x)
        highest_series.attachAxis(hop_axis)
        reached_series.attachAxis(axis_x)
        reached_series.attachAxis(reached_axis)
        self.reachability_trend_view.setChart(chart)

    def _export_current_image(self) -> None:
        if self.tabs.currentWidget() is None:
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Traceroute Graph",
            "",
            "PNG Image (*.png);;SVG Image (*.svg)",
        )
        if not path:
            return
        export_path = Path(path)
        if not export_path.suffix:
            export_path = export_path.with_suffix(".svg" if "svg" in selected_filter.lower() else ".png")

        widget = self.tabs.currentWidget()
        assert widget is not None
        if export_path.suffix.lower() == ".svg":
            generator = QSvgGenerator()
            generator.setFileName(str(export_path))
            generator.setSize(widget.size())
            generator.setViewBox(widget.rect())
            painter = QPainter(generator)
            widget.render(painter)
            painter.end()
            return

        widget.grab().save(str(export_path), "PNG")

    def _export_current_csv(self) -> None:
        samples = self._scoped_samples()
        if not samples:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Traceroute Samples",
            "",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        export_path = Path(path)
        if not export_path.suffix:
            export_path = export_path.with_suffix(".csv")
        export_path.write_text(format_mtr_samples_csv(samples), encoding="utf-8")


def _empty_chart(title: str, message: str) -> QChart:
    chart = _styled_chart(title)
    chart.setTitle(f"{title}\n{message}")
    chart.legend().setVisible(False)
    return chart


def _styled_chart(title: str) -> QChart:
    chart = QChart()
    chart.setTitle(title)
    chart.setTheme(QChart.ChartThemeDark)
    chart.setAnimationOptions(QChart.NoAnimation)
    chart.legend().setVisible(True)
    chart.legend().setAlignment(Qt.AlignBottom)
    return chart


def _attach_category_axes(chart: QChart, series: QBarSeries, categories: list[str], *, y_title: str, y_max: float) -> None:
    axis_x = QBarCategoryAxis(chart)
    axis_x.append(categories)
    axis_y = QValueAxis(chart)
    axis_y.setTitleText(y_title)
    axis_y.setRange(0, _nice_axis_max(y_max))
    chart.addAxis(axis_x, Qt.AlignBottom)
    chart.addAxis(axis_y, Qt.AlignLeft)
    series.attachAxis(axis_x)
    series.attachAxis(axis_y)


def _bind_bar_tooltips(chart_view: QChartView, bar_set: QBarSet, labels: list[str]) -> None:
    def _on_hovered(status: bool, index: int) -> None:
        if not status or index < 0 or index >= len(labels):
            return
        QToolTip.showText(QCursor.pos(), labels[index], chart_view)

    bar_set.hovered.connect(_on_hovered)


def _nice_axis_max(value: float) -> float:
    if value <= 0:
        return 1.0
    if value <= 5:
        return math.ceil(value + 1.0)
    magnitude = 10 ** max(0, len(str(int(value))) - 1)
    rounded = math.ceil(value / magnitude * 2) * (magnitude / 2)
    return max(1.0, float(rounded))


def _summarize_probe_samples(samples: list[MTRProbeSample]) -> list[_HopSummary]:
    summaries: dict[int, _HopSummary] = {}
    running_means: dict[int, float] = defaultdict(float)
    running_m2: dict[int, float] = defaultdict(float)

    for sample in samples:
        summary = summaries.setdefault(sample.hop, _HopSummary(hop=sample.hop))
        summary.sent += 1
        if sample.host:
            summary.host = sample.host
        if sample.address:
            summary.address = sample.address
        summary.reached_destination = summary.reached_destination or sample.reached_destination
        if sample.success and sample.rtt_ms is not None:
            summary.received += 1
            summary.last_ms = sample.rtt_ms
            if summary.best_ms is None or sample.rtt_ms < summary.best_ms:
                summary.best_ms = sample.rtt_ms
            if summary.worst_ms is None or sample.rtt_ms > summary.worst_ms:
                summary.worst_ms = sample.rtt_ms
            delta = sample.rtt_ms - running_means[sample.hop]
            running_means[sample.hop] += delta / summary.received
            delta2 = sample.rtt_ms - running_means[sample.hop]
            running_m2[sample.hop] += delta * delta2
            summary.avg_ms = running_means[sample.hop]
            if summary.received > 1:
                summary.stdev_ms = (running_m2[sample.hop] / summary.received) ** 0.5
            else:
                summary.stdev_ms = None

    return [summaries[hop] for hop in sorted(summaries)]


def _cycle_reachability_stats(samples: list[MTRProbeSample]) -> list[tuple[int, int, bool]]:
    cycle_map: dict[int, tuple[int, bool]] = {}
    for sample in samples:
        highest_hop, reached = cycle_map.get(sample.cycle, (0, False))
        if sample.success:
            highest_hop = max(highest_hop, sample.hop)
        reached = reached or sample.reached_destination
        cycle_map[sample.cycle] = (highest_hop, reached)
    return [(cycle, data[0], data[1]) for cycle, data in sorted(cycle_map.items())]
