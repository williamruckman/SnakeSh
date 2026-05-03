from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from snakesh.services.network_inspector import (
    NetworkInspectorSnapshot,
    PrivilegedPortsHelperSession,
    collect_network_snapshot,
)


class _TaskWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, task: Callable[[], object]) -> None:
        super().__init__()
        self._task = task

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._task())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class NetworkInspectorDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Network Inspector")
        self.resize(1040, 720)
        self._thread: QThread | None = None
        self._worker: _TaskWorker | None = None
        self._shortcuts: list[QShortcut] = []
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._trigger_auto_refresh)
        self._privileged_ports_session: PrivilegedPortsHelperSession | None = None
        self._privileged_ports_auto_retry_blocked = False
        self._last_refresh_used_privileged_ports = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        button_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        button_row.addWidget(self.refresh_btn)
        self.copy_selected_btn = QPushButton("Copy Selected")
        self.copy_all_btn = QPushButton("Copy All")
        button_row.addWidget(self.copy_selected_btn)
        button_row.addWidget(self.copy_all_btn)
        self.auto_refresh_input = QCheckBox("Auto Refresh")
        button_row.addWidget(self.auto_refresh_input)
        button_row.addWidget(QLabel("Every"))
        self.auto_refresh_seconds_input = QSpinBox(self)
        self.auto_refresh_seconds_input.setRange(1, 3600)
        self.auto_refresh_seconds_input.setValue(5)
        self.auto_refresh_seconds_input.setSuffix(" s")
        button_row.addWidget(self.auto_refresh_seconds_input)
        self.privileged_ports_input = QCheckBox("Privileged Ports / Processes")
        button_row.addWidget(self.privileged_ports_input)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)

        self.ip_tree = self._create_tree(["Interface", "Status", "Address / Type", "Details"])
        self.routing_tree = self._create_tree(["Family", "Destination", "Gateway", "Interface", "Metric", "Flags"])
        self.arp_tree = self._create_tree(["IP Address", "MAC Address", "Interface", "State", "Vendor"])
        self.ports_tree = self._create_tree(["Protocol", "Family", "Local Address", "PID", "Process"])
        self.dns_tree = self._create_tree(["Type", "Value"])

        self.tabs.addTab(self.ip_tree, "IP")
        self.tabs.addTab(self.routing_tree, "Routing")
        self.tabs.addTab(self.arp_tree, "ARP")
        self.tabs.addTab(self.ports_tree, "Ports")
        self.tabs.addTab(self.dns_tree, "DNS")

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.refresh_btn.clicked.connect(self._refresh_clicked)
        self.copy_selected_btn.clicked.connect(self._copy_selected_rows)
        self.copy_all_btn.clicked.connect(self._copy_all_rows)
        self.tabs.currentChanged.connect(self._update_copy_actions)
        self.auto_refresh_input.toggled.connect(self._sync_auto_refresh_state)
        self.auto_refresh_seconds_input.valueChanged.connect(self._sync_auto_refresh_state)
        self._sync_auto_refresh_state()
        QTimer.singleShot(0, self._refresh_initial)
        self._update_copy_actions()

    def _create_tree(self, headers: list[str]) -> QTreeWidget:
        tree = QTreeWidget(self)
        tree.setColumnCount(len(headers))
        tree.setHeaderLabels(headers)
        tree.setAlternatingRowColors(True)
        tree.setRootIsDecorated(False)
        tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        tree.itemSelectionChanged.connect(self._update_copy_actions)

        copy_shortcut = QShortcut(QKeySequence.Copy, tree)
        copy_shortcut.activated.connect(self._copy_selected_rows)
        select_all_shortcut = QShortcut(QKeySequence.SelectAll, tree)
        select_all_shortcut.activated.connect(tree.selectAll)
        self._shortcuts.extend([copy_shortcut, select_all_shortcut])
        return tree

    def _set_running(self, running: bool) -> None:
        self.refresh_btn.setEnabled(not running)

    @Slot()
    def _refresh_initial(self) -> None:
        self._start_refresh(manual=False)

    @Slot()
    def _refresh_clicked(self) -> None:
        self._start_refresh(manual=True)

    @Slot()
    def _trigger_auto_refresh(self) -> None:
        self._start_refresh(manual=False)

    def _sync_auto_refresh_state(self, *_args: object) -> None:
        enabled = self.auto_refresh_input.isChecked()
        self.auto_refresh_seconds_input.setEnabled(enabled)
        if not enabled:
            self._auto_refresh_timer.stop()
            return
        self._auto_refresh_timer.start(self.auto_refresh_seconds_input.value() * 1000)

    def _current_privileged_ports_session(self) -> PrivilegedPortsHelperSession | None:
        if not self.privileged_ports_input.isChecked():
            return None
        if self._privileged_ports_session is None:
            self._privileged_ports_session = PrivilegedPortsHelperSession()
        return self._privileged_ports_session

    def _collect_snapshot(self, *, manual: bool) -> NetworkInspectorSnapshot:
        session = self._current_privileged_ports_session()
        use_privileged_ports = session is not None
        allow_privileged_launch = manual or not self._privileged_ports_auto_retry_blocked
        return collect_network_snapshot(
            use_privileged_ports=use_privileged_ports,
            privileged_ports_session=session,
            allow_privileged_ports_launch=allow_privileged_launch,
        )

    def _update_privileged_ports_retry_state(self) -> None:
        if not self._last_refresh_used_privileged_ports:
            return
        session = self._privileged_ports_session
        if session is None:
            self._privileged_ports_auto_retry_blocked = False
            return
        if session.is_ready:
            self._privileged_ports_auto_retry_blocked = False
        elif session.last_start_failed:
            self._privileged_ports_auto_retry_blocked = True

    def _start_refresh(self, *, manual: bool) -> bool:
        if self._thread is not None:
            return False
        self._last_refresh_used_privileged_ports = self.privileged_ports_input.isChecked()
        self._set_running(True)
        self.status_label.setText("Refreshing network snapshot...")
        self._thread = QThread(self)
        self._worker = _TaskWorker(lambda: self._collect_snapshot(manual=manual))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._on_snapshot_ready)
        self._worker.failed.connect(self._on_snapshot_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_refresh_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        return True

    @Slot(object)
    def _on_snapshot_ready(self, payload: object) -> None:
        if not isinstance(payload, NetworkInspectorSnapshot):
            self._on_snapshot_failed("Unexpected network snapshot payload.")
            return
        self._populate_ip_tab(payload)
        self._populate_routing_tab(payload)
        self._populate_arp_tab(payload)
        self._populate_ports_tab(payload)
        self._populate_dns_tab(payload)
        if payload.errors:
            self.status_label.setText("Refresh complete with warnings: " + " | ".join(payload.errors))
        else:
            self.status_label.setText("Refresh complete.")

    @Slot(str)
    def _on_snapshot_failed(self, message: str) -> None:
        self.status_label.setText(message)

    @Slot()
    def _on_refresh_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._update_privileged_ports_retry_state()
        self._set_running(False)
        self._update_copy_actions()

    def _populate_ip_tab(self, snapshot: NetworkInspectorSnapshot) -> None:
        self.ip_tree.clear()
        for interface in snapshot.interfaces:
            header = QTreeWidgetItem(
                [
                    interface.name,
                    "Up" if interface.is_up else "Down",
                    interface.mac_address or "(no MAC)",
                    f"MTU {interface.mtu} | {interface.speed_mbps or 0} Mbps | Duplex {interface.duplex}",
                ]
            )
            header.setFirstColumnSpanned(False)
            self.ip_tree.addTopLevelItem(header)
            for address in interface.addresses:
                details = ", ".join(
                    value
                    for value in [
                        f"netmask={address.netmask}" if address.netmask else "",
                        f"broadcast={address.broadcast}" if address.broadcast else "",
                        f"peer={address.peer}" if address.peer else "",
                    ]
                    if value
                )
                header.addChild(
                    QTreeWidgetItem(
                        [
                            "",
                            "",
                            f"{address.family}: {address.address}",
                            details,
                        ]
                    )
                )
        self.ip_tree.expandAll()
        self._resize_tree(self.ip_tree)

    def _populate_routing_tab(self, snapshot: NetworkInspectorSnapshot) -> None:
        self.routing_tree.clear()
        for route in snapshot.routes:
            self.routing_tree.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        route.family,
                        route.destination,
                        route.gateway,
                        route.interface,
                        route.metric,
                        " ".join(part for part in [route.flags, route.source] if part).strip(),
                    ]
                )
            )
        self._resize_tree(self.routing_tree)

    def _populate_arp_tab(self, snapshot: NetworkInspectorSnapshot) -> None:
        self.arp_tree.clear()
        for entry in snapshot.arp_entries:
            self.arp_tree.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        entry.ip_address,
                        entry.mac_address,
                        entry.interface,
                        entry.state,
                        entry.vendor or "Unknown",
                    ]
                )
            )
        self._resize_tree(self.arp_tree)

    def _populate_ports_tab(self, snapshot: NetworkInspectorSnapshot) -> None:
        self.ports_tree.clear()
        for entry in snapshot.listening_ports:
            self.ports_tree.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        entry.protocol,
                        entry.family,
                        entry.local_address,
                        str(entry.pid or ""),
                        entry.process_name,
                    ]
                )
            )
        self._resize_tree(self.ports_tree)

    def _populate_dns_tab(self, snapshot: NetworkInspectorSnapshot) -> None:
        self.dns_tree.clear()
        config = snapshot.dns_config
        if config is None:
            self._resize_tree(self.dns_tree)
            return
        rows = [
            ("Host Name", config.host_name),
            ("FQDN", config.fqdn),
        ]
        rows.extend(("Nameserver", value) for value in config.nameservers)
        rows.extend(("Search Domain", value) for value in config.search_domains)
        rows.extend(("Note", value) for value in config.notes)
        for row_type, value in rows:
            self.dns_tree.addTopLevelItem(QTreeWidgetItem([row_type, value]))
        self._resize_tree(self.dns_tree)

    def _resize_tree(self, tree: QTreeWidget) -> None:
        for index in range(tree.columnCount()):
            tree.resizeColumnToContents(index)
        self._update_copy_actions()

    def _current_tree(self) -> QTreeWidget | None:
        current = self.tabs.currentWidget()
        if isinstance(current, QTreeWidget):
            return current
        return None

    def _update_copy_actions(self, *_args: object) -> None:
        tree = self._current_tree()
        has_rows = tree is not None and tree.topLevelItemCount() > 0
        has_selection = tree is not None and bool(tree.selectedItems())
        self.copy_selected_btn.setEnabled(has_selection)
        self.copy_all_btn.setEnabled(has_rows)

    @Slot()
    def _copy_selected_rows(self) -> None:
        tree = self._current_tree()
        if tree is None:
            return
        rows = [self._tree_item_values(tree, item) for item in tree.selectedItems()]
        self._copy_rows(tree, rows, empty_message="Select one or more rows to copy.")

    @Slot()
    def _copy_all_rows(self) -> None:
        tree = self._current_tree()
        if tree is None:
            return
        rows: list[list[str]] = []
        for index in range(tree.topLevelItemCount()):
            self._collect_rows(tree, tree.topLevelItem(index), rows)
        self._copy_rows(tree, rows, empty_message="There is no data to copy on this tab.")

    def _copy_rows(self, tree: QTreeWidget, rows: list[list[str]], *, empty_message: str) -> None:
        if not rows:
            self.status_label.setText(empty_message)
            self._update_copy_actions()
            return

        headers = [tree.headerItem().text(index) for index in range(tree.columnCount())]
        payload = "\n".join("\t".join(row) for row in [headers, *rows])
        QApplication.clipboard().setText(payload)
        tab_name = self.tabs.tabText(self.tabs.currentIndex())
        noun = "row" if len(rows) == 1 else "rows"
        self.status_label.setText(f"Copied {len(rows)} {noun} from {tab_name}.")
        self._update_copy_actions()

    def _collect_rows(self, tree: QTreeWidget, item: QTreeWidgetItem, rows: list[list[str]]) -> None:
        rows.append(self._tree_item_values(tree, item))
        for index in range(item.childCount()):
            self._collect_rows(tree, item.child(index), rows)

    def _tree_item_values(self, tree: QTreeWidget, item: QTreeWidgetItem) -> list[str]:
        return [item.text(index) for index in range(tree.columnCount())]

    @Slot(object)
    def _show_tree_context_menu(self, position) -> None:
        tree = self.sender()
        if not isinstance(tree, QTreeWidget):
            return
        self.tabs.setCurrentWidget(tree)
        menu = QMenu(self)
        copy_selected_action = menu.addAction("Copy Selected")
        copy_selected_action.setEnabled(bool(tree.selectedItems()))
        copy_all_action = menu.addAction("Copy All")
        copy_all_action.setEnabled(tree.topLevelItemCount() > 0)
        chosen = menu.exec(tree.viewport().mapToGlobal(position))
        if chosen == copy_selected_action:
            self._copy_selected_rows()
        elif chosen == copy_all_action:
            self._copy_all_rows()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._auto_refresh_timer.stop()
        if self._privileged_ports_session is not None:
            close = getattr(self._privileged_ports_session, "close", None)
            if callable(close):
                close()
            self._privileged_ports_session = None
        super().closeEvent(event)
