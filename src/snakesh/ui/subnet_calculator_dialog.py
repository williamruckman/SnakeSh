from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from snakesh.services.subnet_tools import PlannedSubnet, SubnetSummary, split_network, summarize_cidr


@dataclass(slots=True)
class _SubnetTabState:
    version: int
    address_input: QLineEdit
    address_calc_btn: QPushButton
    summary_fields: dict[str, QLineEdit]
    summary_last_label: str
    summary_range_label: str
    summary_usable_label: str
    network_input: QLineEdit
    target_prefix_input: QSpinBox
    max_subnets_input: QSpinBox
    generate_btn: QPushButton
    subnet_table: QTreeWidget
    status_label: QLabel


class SubnetCalculatorDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Subnet Calculator")
        self.resize(1040, 700)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._tabs = QTabWidget(self)
        root.addWidget(self._tabs, 1)

        self._states: dict[int, _SubnetTabState] = {}
        ipv4_page, ipv4_state = self._build_protocol_tab(
            version=4,
            default_address="198.51.100.55/24",
            default_network="198.18.0.0/16",
            default_target_prefix=24,
        )
        self._tabs.addTab(ipv4_page, "IPv4")
        self._states[4] = ipv4_state

        ipv6_page, ipv6_state = self._build_protocol_tab(
            version=6,
            default_address="2001:db8:abcd::1/64",
            default_network="2001:db8:1000::/48",
            default_target_prefix=64,
        )
        self._tabs.addTab(ipv6_page, "IPv6")
        self._states[6] = ipv6_state

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.clicked.connect(self.accept)
        root.addWidget(buttons)

        self._set_monospace_fields()
        for state in self._states.values():
            self._analyze_address(state)
            self._generate_subnets(state)

    def _build_protocol_tab(
        self,
        *,
        version: int,
        default_address: str,
        default_network: str,
        default_target_prefix: int,
    ) -> tuple[QWidget, _SubnetTabState]:
        page = QWidget(self)
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        root.addWidget(self._section_label("Address Analysis"))
        analysis_row = QHBoxLayout()
        address_input = QLineEdit()
        if version == 4:
            address_input.setPlaceholderText("Enter IPv4 address/CIDR (example: 198.51.100.55/24)")
        else:
            address_input.setPlaceholderText("Enter IPv6 address/CIDR (example: 2001:db8::1/64)")
        address_calc_btn = QPushButton("Analyze")
        analysis_row.addWidget(address_input, 1)
        analysis_row.addWidget(address_calc_btn, 0)
        root.addLayout(analysis_row)

        if version == 4:
            summary_last_label = "Broadcast"
            summary_range_label = "Host Range"
            summary_usable_label = "Usable Hosts"
        else:
            summary_last_label = "Last Address"
            summary_range_label = "Address Range"
            summary_usable_label = "Usable Addresses"

        summary_fields: dict[str, QLineEdit] = {}
        summary_container = QWidget(self)
        summary_form = QFormLayout(summary_container)
        summary_form.setContentsMargins(0, 0, 0, 0)
        summary_form.setSpacing(6)
        for label in (
            "Input",
            "IP Address",
            "Network",
            "Prefix",
            "Netmask",
            "Wildcard",
            summary_last_label,
            summary_range_label,
            "Total Addresses",
            summary_usable_label,
            "Host Bits",
        ):
            field = QLineEdit()
            field.setReadOnly(True)
            summary_fields[label] = field
            summary_form.addRow(QLabel(label), field)
        root.addWidget(summary_container)

        root.addSpacing(8)
        root.addWidget(self._section_label("Subnet Planner"))

        planner_top = QHBoxLayout()
        network_input = QLineEdit()
        if version == 4:
            network_input.setPlaceholderText("Base IPv4 network (example: 198.18.0.0/16)")
        else:
            network_input.setPlaceholderText("Base IPv6 network (example: 2001:db8:1000::/48)")
        target_prefix_input = QSpinBox()
        target_prefix_input.setRange(0, 32 if version == 4 else 128)
        target_prefix_input.setPrefix("/")
        target_prefix_input.setValue(default_target_prefix)
        max_subnets_input = QSpinBox()
        max_subnets_input.setRange(1, 4096)
        max_subnets_input.setValue(64)
        generate_btn = QPushButton("Generate")
        planner_top.addWidget(QLabel("Network"), 0)
        planner_top.addWidget(network_input, 1)
        planner_top.addWidget(QLabel("Target Prefix"), 0)
        planner_top.addWidget(target_prefix_input, 0)
        planner_top.addWidget(QLabel("Max"), 0)
        planner_top.addWidget(max_subnets_input, 0)
        planner_top.addWidget(generate_btn, 0)
        root.addLayout(planner_top)

        subnet_table = QTreeWidget()
        subnet_table.setColumnCount(5)
        if version == 4:
            subnet_table.setHeaderLabels(("Subnet", "Usable Hosts", "Host Range", "Broadcast", "Netmask"))
        else:
            subnet_table.setHeaderLabels(("Subnet", "Usable Addresses", "Address Range", "Last Address", "Prefix"))
        subnet_table.setAlternatingRowColors(True)
        subnet_table.setRootIsDecorated(False)
        subnet_table.setUniformRowHeights(True)
        root.addWidget(subnet_table, 1)

        status_label = QLabel("Ready.")
        status_label.setStyleSheet("color: #0f172a;")
        root.addWidget(status_label)

        state = _SubnetTabState(
            version=version,
            address_input=address_input,
            address_calc_btn=address_calc_btn,
            summary_fields=summary_fields,
            summary_last_label=summary_last_label,
            summary_range_label=summary_range_label,
            summary_usable_label=summary_usable_label,
            network_input=network_input,
            target_prefix_input=target_prefix_input,
            max_subnets_input=max_subnets_input,
            generate_btn=generate_btn,
            subnet_table=subnet_table,
            status_label=status_label,
        )

        state.address_input.setText(default_address)
        state.network_input.setText(default_network)
        state.address_calc_btn.clicked.connect(lambda _checked=False, s=state: self._analyze_address(s))
        state.address_input.returnPressed.connect(lambda s=state: self._analyze_address(s))
        state.generate_btn.clicked.connect(lambda _checked=False, s=state: self._generate_subnets(s))
        return page, state

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-size: 11pt; font-weight: 700;")
        return label

    def _set_monospace_fields(self) -> None:
        families = QFontDatabase.families()
        preferred = ("Cascadia Mono", "Consolas", "Courier New", "Courier")
        family = next((name for name in preferred if name in families), "")
        if not family:
            return
        for state in self._states.values():
            for field in state.summary_fields.values():
                font = field.font()
                font.setFamily(family)
                field.setFont(font)
            table_font = state.subnet_table.font()
            table_font.setFamily(family)
            state.subnet_table.setFont(table_font)

    def _analyze_address(self, state: _SubnetTabState) -> None:
        raw = state.address_input.text().strip()
        try:
            summary = summarize_cidr(raw, ip_version=state.version)
        except ValueError as exc:
            self._set_error(state, str(exc))
            return
        self._apply_summary(state, summary)
        state.status_label.setStyleSheet("color: #166534;")
        state.status_label.setText(f"IPv{state.version} address analysis complete for {summary.input_value}.")
        state.network_input.setText(f"{summary.network}/{summary.prefix}")
        max_bits = 32 if state.version == 4 else 128
        state.target_prefix_input.setValue(min(max_bits, max(summary.prefix, state.target_prefix_input.value())))

    def _apply_summary(self, state: _SubnetTabState, summary: SubnetSummary) -> None:
        host_range = f"{summary.first_host} - {summary.last_host}"
        values = {
            "Input": summary.input_value,
            "IP Address": summary.ip_address,
            "Network": summary.network,
            "Prefix": f"/{summary.prefix}",
            "Netmask": summary.netmask,
            "Wildcard": summary.wildcard,
            state.summary_last_label: summary.broadcast,
            state.summary_range_label: host_range,
            "Total Addresses": f"{summary.total_addresses:,}",
            state.summary_usable_label: f"{summary.usable_hosts:,}",
            "Host Bits": str(summary.host_bits),
        }
        for key, field in state.summary_fields.items():
            field.setText(values.get(key, ""))

    def _generate_subnets(self, state: _SubnetTabState) -> None:
        base = state.network_input.text().strip()
        new_prefix = state.target_prefix_input.value()
        max_subnets = state.max_subnets_input.value()
        try:
            planned = split_network(
                base,
                new_prefix=new_prefix,
                max_results=max_subnets,
                ip_version=state.version,
            )
        except ValueError as exc:
            self._set_error(state, str(exc))
            return
        self._apply_subnet_table(state, planned)
        state.status_label.setStyleSheet("color: #166534;")
        state.status_label.setText(f"Generated {len(planned)} IPv{state.version} subnet(s) from {base}.")

    def _apply_subnet_table(self, state: _SubnetTabState, subnets: list[PlannedSubnet]) -> None:
        state.subnet_table.clear()
        for subnet in subnets:
            host_range = f"{subnet.first_host} - {subnet.last_host}"
            mask_or_prefix = subnet.netmask if state.version == 4 else f"/{subnet.prefix}"
            item = QTreeWidgetItem(
                [
                    f"{subnet.subnet}/{subnet.prefix}",
                    f"{subnet.usable_hosts:,}",
                    host_range,
                    subnet.broadcast,
                    mask_or_prefix,
                ]
            )
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            state.subnet_table.addTopLevelItem(item)
        for column in range(state.subnet_table.columnCount()):
            state.subnet_table.resizeColumnToContents(column)

    def _set_error(self, state: _SubnetTabState, message: str) -> None:
        state.status_label.setStyleSheet("color: #b91c1c;")
        state.status_label.setText(message)
        QMessageBox.warning(self, f"Subnet Calculator (IPv{state.version})", message)
