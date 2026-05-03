from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import ipaddress

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QLineEdit, QVBoxLayout, QWidget

from snakesh.services.network_inspector import InterfaceInfo, collect_interface_info


CUSTOM_BIND_HOST_VALUE = "__custom_bind_host__"


@dataclass(frozen=True, slots=True)
class BindHostChoice:
    label: str
    value: str


class BindHostSelector(QWidget):
    value_changed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        initial_value: str = "127.0.0.1",
        interface_info_provider: Callable[[], list[InterfaceInfo]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._updating_controls = False
        self._interface_info_provider = interface_info_provider or collect_interface_info

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.value_input = QLineEdit(initial_value, self)
        self.value_input.hide()
        self.preset_input = QComboBox(self)
        self.custom_input = QLineEdit(initial_value, self)
        self.custom_input.setPlaceholderText("Enter custom IPv4 or IPv6 address")
        self.custom_input.hide()

        layout.addWidget(self.preset_input)
        layout.addWidget(self.custom_input)

        self.preset_input.currentIndexChanged.connect(self._on_choice_changed)
        self.custom_input.textChanged.connect(self._on_custom_text_changed)
        self.reload_choices()
        self.set_value(initial_value)

    @staticmethod
    def fixed_choices() -> list[BindHostChoice]:
        return [
            BindHostChoice("All IPv4 interfaces (0.0.0.0)", "0.0.0.0"),
            BindHostChoice("All IPv6 interfaces (::)", "::"),
            BindHostChoice("Loopback IPv4 (127.0.0.1)", "127.0.0.1"),
            BindHostChoice("Loopback IPv6 (::1)", "::1"),
        ]

    def discovered_choices(self) -> list[BindHostChoice]:
        try:
            interfaces = self._interface_info_provider()
        except Exception:
            return []

        seen = {choice.value for choice in self.fixed_choices()}
        discovered: list[tuple[str, int, str, str, bool]] = []
        for interface in interfaces:
            interface_name = interface.name.strip()
            if not interface_name:
                continue
            for address in interface.addresses:
                family = str(address.family).strip()
                value = str(address.address).strip()
                if family not in {"IPv4", "IPv6"} or not value or value in seen:
                    continue
                try:
                    parsed = ipaddress.ip_address(value)
                except ValueError:
                    continue
                discovered.append(
                    (
                        interface_name.lower(),
                        0 if parsed.version == 4 else 1,
                        value,
                        interface_name,
                        interface.is_up,
                    )
                )
        discovered.sort(key=lambda item: (item[0], item[1], item[2]))

        results: list[BindHostChoice] = []
        for _sort_name, _family_order, value, interface_name, is_up in discovered:
            if value in seen:
                continue
            seen.add(value)
            label = f"{interface_name} - {value}"
            if not is_up:
                label += " (down)"
            results.append(BindHostChoice(label, value))
        return results

    def reload_choices(self) -> None:
        current_value = self.value()
        choices = [
            *self.fixed_choices(),
            *self.discovered_choices(),
            BindHostChoice("Custom...", CUSTOM_BIND_HOST_VALUE),
        ]
        self.preset_input.blockSignals(True)
        self.preset_input.clear()
        for choice in choices:
            self.preset_input.addItem(choice.label, choice.value)
        self.preset_input.blockSignals(False)
        self.set_value(current_value)

    def value(self) -> str:
        return self.value_input.text().strip() or "127.0.0.1"

    def set_value(self, value: str) -> None:
        normalized = value.strip() or "127.0.0.1"
        self._updating_controls = True
        try:
            self.value_input.setText(normalized)
            self.custom_input.setText(normalized)
            preset_index = self.preset_input.findData(normalized)
            if preset_index >= 0:
                self.preset_input.setCurrentIndex(preset_index)
                self.custom_input.hide()
                return
            custom_index = self.preset_input.findData(CUSTOM_BIND_HOST_VALUE)
            if custom_index >= 0:
                self.preset_input.setCurrentIndex(custom_index)
            self.custom_input.show()
        finally:
            self._updating_controls = False
        self.value_changed.emit(normalized)

    def _on_choice_changed(self, *_args: object) -> None:
        if self._updating_controls:
            return
        selected = self.preset_input.currentData()
        if not isinstance(selected, str):
            return

        current_value = self.value()
        self._updating_controls = True
        try:
            if selected == CUSTOM_BIND_HOST_VALUE:
                self.custom_input.setText(current_value)
                self.value_input.setText(current_value)
                self.custom_input.show()
            else:
                self.value_input.setText(selected)
                self.custom_input.setText(selected)
                self.custom_input.hide()
        finally:
            self._updating_controls = False
        if selected == CUSTOM_BIND_HOST_VALUE:
            self.custom_input.setFocus()
            self.custom_input.selectAll()
        self.value_changed.emit(self.value())

    def _on_custom_text_changed(self, text: str) -> None:
        if self._updating_controls:
            return
        self._updating_controls = True
        try:
            self.value_input.setText(text)
        finally:
            self._updating_controls = False
        self.value_changed.emit(self.value())
