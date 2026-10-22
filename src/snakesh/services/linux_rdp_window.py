"""Inspect FreeRDP's children of a Qt X11 host without external utilities."""
from __future__ import annotations


class LinuxRdpWindow:
    def __init__(self) -> None:
        self._display = None

    def _connection(self):
        if self._display is None:
            from Xlib.display import Display

            self._display = Display()
        return self._display

    def close(self) -> None:
        connection, self._display = self._display, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def find_child(self, parent_id: int, process_ids: set[int]) -> int | None:
        try:
            from Xlib import Xatom

            connection = self._connection()
            parent = connection.create_resource_object("window", parent_id)
            pid_atom = connection.intern_atom("_NET_WM_PID")
            candidates = []
            # FreeRDP reparents its desktop directly into this host. Looking
            # beneath the host also works for unmapped/hidden tabs and does not
            # rely on a window manager's list of top-level windows.
            for child in parent.query_tree().children:
                try:
                    pid = child.get_full_property(pid_atom, Xatom.CARDINAL)
                    owned = pid is not None and len(pid.value) and int(pid.value[0]) in process_ids
                    classes = " ".join(child.get_wm_class() or ()).lower()
                    if not owned and "freerdp" not in classes:
                        continue
                    geometry = child.get_geometry()
                    candidates.append((geometry.width * geometry.height, child.id))
                except Exception:
                    # A viewer may replace a window between enumeration and
                    # inspection. Retry on the next timer tick.
                    continue
            return max(candidates)[1] if candidates else None
        except Exception:
            return None

    def resize(self, window_id: int, width: int, height: int) -> bool:
        try:
            from Xlib import error

            connection = self._connection()
            window = connection.create_resource_object("window", window_id)
            geometry = window.get_geometry()
            width, height = max(1, width), max(1, height)
            if (geometry.x, geometry.y, geometry.width, geometry.height) == (0, 0, width, height):
                return True
            errors = error.CatchError()
            window.configure(x=0, y=0, width=width, height=height, onerror=errors)
            connection.sync()
            return errors.get_error() is None
        except Exception:
            return False
