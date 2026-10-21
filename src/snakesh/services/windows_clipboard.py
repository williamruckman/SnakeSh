"""Immediate plain-text clipboard IO in an expendable Windows helper process."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
from pathlib import Path


def clipboard_text(text: str | None = None) -> str:
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user.CreateWindowExW.restype = wintypes.HWND
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    user.DestroyWindow.argtypes = [wintypes.HWND]
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalSize.restype = ctypes.c_size_t
    window = user.CreateWindowExW(0, "STATIC", "SnakeSh Clipboard", 0, 0, 0, 0, 0, -3, None, None, None)
    if not window:
        raise ctypes.WinError(ctypes.get_last_error())
    opened = False
    try:
        if not user.OpenClipboard(window):
            raise OSError("The clipboard is busy. Try again shortly.")
        opened = True
        if text is None:
            handle = user.GetClipboardData(13)  # CF_UNICODETEXT; may wait for RDP.
            if not handle:
                return ""
            size = kernel.GlobalSize(handle)
            if size > 16 * 1024 * 1024:
                raise ValueError("Clipboard text exceeds the 16 MiB limit.")
            address = kernel.GlobalLock(handle)
            if not address:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return ctypes.string_at(address, size).decode("utf-16-le", errors="replace").split("\0", 1)[0]
            finally:
                kernel.GlobalUnlock(handle)
        data = (text + "\0").encode("utf-16-le")
        handle = kernel.GlobalAlloc(2, len(data))
        if not handle:
            raise MemoryError("Unable to allocate clipboard text.")
        try:
            address = kernel.GlobalLock(handle)
            if not address:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                ctypes.memmove(address, data, len(data))
            finally:
                kernel.GlobalUnlock(handle)
            if not user.EmptyClipboard() or not user.SetClipboardData(13, handle):
                raise ctypes.WinError(ctypes.get_last_error())
            handle = None  # Windows now owns the memory.
        finally:
            if handle:
                kernel.GlobalFree(handle)
        return text
    finally:
        if opened:
            user.CloseClipboard()
        user.DestroyWindow(window)


def run_clipboard_helper(directory: str) -> int:
    root = Path(directory)
    try:
        request = json.loads((root / "request.json").read_text(encoding="utf-8"))
        result = {"ok": True, "text": clipboard_text(request.get("text"))}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    (root / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return 0 if result["ok"] else 1
