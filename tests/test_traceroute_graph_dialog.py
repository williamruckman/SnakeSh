from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCharts import QBarSeries, QScatterSeries, QSplineSeries
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication

from snakesh.services.mtr_trace import MTRHopSnapshot, MTRProbeSample, MTRTraceSnapshot
from snakesh.ui.traceroute_graph_dialog import TracerouteGraphDialog, _HeatmapWidget


def _build_snapshot() -> MTRTraceSnapshot:
    return MTRTraceSnapshot(
        state="running",
        message="Cycle 2: processed hop 3.",
        cycle=2,
        target="8.8.8.8",
        protocol="ICMP",
        ipv6=False,
        hops=[
            MTRHopSnapshot(
                hop=1,
                host="router-1",
                address="192.0.2.1",
                sent=2,
                received=1,
                loss_percent=50.0,
                last_ms=1.2,
                avg_ms=1.2,
                best_ms=1.2,
                worst_ms=1.2,
                stdev_ms=None,
                reached_destination=False,
            ),
            MTRHopSnapshot(
                hop=2,
                host="router-2",
                address="192.0.2.2",
                sent=2,
                received=2,
                loss_percent=0.0,
                last_ms=7.8,
                avg_ms=6.9,
                best_ms=6.0,
                worst_ms=7.8,
                stdev_ms=0.9,
                reached_destination=False,
            ),
        ],
    )


def _build_samples() -> list[MTRProbeSample]:
    return [
        MTRProbeSample(
            sample_index=1,
            timestamp_ms=1000,
            cycle=1,
            hop=1,
            host="router-1",
            address="192.0.2.1",
            success=True,
            timeout=False,
            rtt_ms=1.2,
            reached_destination=False,
        ),
        MTRProbeSample(
            sample_index=2,
            timestamp_ms=1100,
            cycle=1,
            hop=1,
            host="router-1",
            address="192.0.2.1",
            success=False,
            timeout=True,
            rtt_ms=None,
            reached_destination=False,
        ),
        MTRProbeSample(
            sample_index=3,
            timestamp_ms=2000,
            cycle=2,
            hop=2,
            host="router-2",
            address="192.0.2.2",
            success=True,
            timeout=False,
            rtt_ms=6.0,
            reached_destination=False,
        ),
        MTRProbeSample(
            sample_index=4,
            timestamp_ms=2100,
            cycle=2,
            hop=2,
            host="router-2",
            address="192.0.2.2",
            success=True,
            timeout=False,
            rtt_ms=7.8,
            reached_destination=False,
        ),
    ]


class TracerouteGraphDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def test_graph_dialog_populates_tabs_and_status(self) -> None:
        dialog = TracerouteGraphDialog()
        try:
            dialog.set_trace_data(_build_snapshot(), _build_samples())
            QApplication.processEvents()

            self.assertEqual(dialog.tabs.count(), 8)
            self.assertEqual(dialog.current_sample_count(), 4)
            self.assertTrue(dialog.export_image_btn.isEnabled())
            self.assertTrue(dialog.export_csv_btn.isEnabled())
            self.assertIn("probe samples", dialog.status_label.text())
            self.assertEqual(dialog.rtt_chart_view.chart().title(), "RTT Over Time")
            self.assertEqual(dialog.latency_summary_view.chart().title(), "Latency Summary")
            self.assertEqual(dialog.packet_loss_view.chart().title(), "Packet Loss")
            self.assertEqual(dialog.jitter_view.chart().title(), "Jitter")
            self.assertEqual(dialog.worst_hops_view.chart().title(), "Worst Hops")
            self.assertEqual(dialog.cumulative_path_view.chart().title(), "Cumulative Path")
            self.assertEqual(dialog.reachability_trend_view.chart().title(), "Reachability Trend")
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_line_charts_use_spline_series_and_non_line_series_stay_discrete(self) -> None:
        dialog = TracerouteGraphDialog()
        try:
            dialog.set_trace_data(_build_snapshot(), _build_samples())
            QApplication.processEvents()

            rtt_series = dialog.rtt_chart_view.chart().series()
            latency_series = dialog.latency_summary_view.chart().series()
            cumulative_series = dialog.cumulative_path_view.chart().series()
            reachability_series = dialog.reachability_trend_view.chart().series()

            self.assertTrue(any(isinstance(series, QSplineSeries) for series in rtt_series))
            self.assertTrue(any(isinstance(series, QScatterSeries) for series in rtt_series))
            self.assertTrue(any(isinstance(series, QBarSeries) for series in latency_series))
            self.assertIsInstance(cumulative_series[0], QSplineSeries)
            self.assertIsInstance(reachability_series[0], QSplineSeries)
            self.assertTrue(any(isinstance(series, QScatterSeries) for series in reachability_series))
            self.assertTrue(dialog.rtt_chart_view.renderHints() & QPainter.RenderHint.Antialiasing)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_graph_dialog_export_csv_uses_filters_but_keeps_timeout_rows(self) -> None:
        dialog = TracerouteGraphDialog()
        try:
            dialog.set_trace_data(_build_snapshot(), _build_samples())
            dialog.include_timeouts_check.setChecked(False)
            dialog.hop_filter_button.set_selected_hops({1})
            QApplication.processEvents()

            with tempfile.TemporaryDirectory() as tmp:
                export_path = Path(tmp) / "trace-samples.csv"
                with patch(
                    "snakesh.ui.traceroute_graph_dialog.QFileDialog.getSaveFileName",
                    return_value=(str(export_path), "CSV Files (*.csv)"),
                ):
                    dialog._export_current_csv()

                csv_text = export_path.read_text(encoding="utf-8")

            self.assertIn("sample_index,timestamp_ms,cycle,hop,host,address,success,timeout,rtt_ms,reached_destination", csv_text)
            self.assertIn("1,1000,1,1,router-1,192.0.2.1,true,false,1.200,false", csv_text)
            self.assertIn("2,1100,1,1,router-1,192.0.2.1,false,true,,false", csv_text)
            self.assertNotIn(",2,router-2,192.0.2.2", csv_text)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_graph_dialog_export_image_writes_svg_and_png(self) -> None:
        dialog = TracerouteGraphDialog()
        try:
            dialog.set_trace_data(_build_snapshot(), _build_samples())
            dialog.show()
            QApplication.processEvents()

            with tempfile.TemporaryDirectory() as tmp:
                svg_path = Path(tmp) / "trace-graph.svg"
                png_path = Path(tmp) / "trace-graph.png"

                with patch(
                    "snakesh.ui.traceroute_graph_dialog.QFileDialog.getSaveFileName",
                    return_value=(str(svg_path), "SVG Image (*.svg)"),
                ):
                    dialog._export_current_image()
                with patch(
                    "snakesh.ui.traceroute_graph_dialog.QFileDialog.getSaveFileName",
                    return_value=(str(png_path), "PNG Image (*.png)"),
                ):
                    dialog._export_current_image()

                self.assertTrue(svg_path.exists())
                self.assertGreater(svg_path.stat().st_size, 0)
                self.assertTrue(png_path.exists())
                self.assertGreater(png_path.stat().st_size, 0)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_graph_dialog_keeps_show_all_hops_selected_as_new_hops_arrive(self) -> None:
        dialog = TracerouteGraphDialog()
        try:
            dialog.set_trace_data(_build_snapshot(), _build_samples())
            QApplication.processEvents()
            self.assertEqual(dialog.hop_filter_button.selected_hops(), {1, 2})

            updated_samples = [
                *_build_samples(),
                MTRProbeSample(
                    sample_index=5,
                    timestamp_ms=2200,
                    cycle=2,
                    hop=3,
                    host="router-3",
                    address="192.0.2.3",
                    success=True,
                    timeout=False,
                    rtt_ms=12.4,
                    reached_destination=False,
                ),
            ]
            updated_snapshot = MTRTraceSnapshot(
                state="running",
                message="Cycle 2: processed hop 3.",
                cycle=2,
                target="8.8.8.8",
                protocol="ICMP",
                ipv6=False,
                hops=[
                    *_build_snapshot().hops,
                    MTRHopSnapshot(
                        hop=3,
                        host="router-3",
                        address="192.0.2.3",
                        sent=1,
                        received=1,
                        loss_percent=0.0,
                        last_ms=12.4,
                        avg_ms=12.4,
                        best_ms=12.4,
                        worst_ms=12.4,
                        stdev_ms=None,
                        reached_destination=False,
                    ),
                ],
            )

            dialog.set_trace_data(updated_snapshot, updated_samples)
            QApplication.processEvents()

            self.assertEqual(dialog.hop_filter_button.selected_hops(), {1, 2, 3})
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_graph_dialog_preserves_explicit_hop_subset_as_new_hops_arrive(self) -> None:
        dialog = TracerouteGraphDialog()
        try:
            dialog.set_trace_data(_build_snapshot(), _build_samples())
            dialog.hop_filter_button.set_selected_hops({1})
            QApplication.processEvents()

            updated_samples = [
                *_build_samples(),
                MTRProbeSample(
                    sample_index=5,
                    timestamp_ms=2200,
                    cycle=2,
                    hop=3,
                    host="router-3",
                    address="192.0.2.3",
                    success=True,
                    timeout=False,
                    rtt_ms=12.4,
                    reached_destination=False,
                ),
            ]

            dialog.set_trace_data(_build_snapshot(), updated_samples)
            QApplication.processEvents()

            self.assertEqual(dialog.hop_filter_button.selected_hops(), {1})
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_heatmap_layout_keeps_axis_label_and_legend_separate(self) -> None:
        widget = _HeatmapWidget()
        try:
            widget.resize(900, 520)
            widget.set_samples(_build_samples())
            QApplication.processEvents()

            layout = widget._layout_metrics()

            self.assertGreater(layout["axis_label_rect"].top(), layout["grid_rect"].bottom())
            self.assertFalse(layout["axis_label_rect"].intersects(layout["legend_box_rect"]))
            self.assertFalse(layout["axis_label_rect"].intersects(layout["legend_text_rect"]))
        finally:
            widget.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
