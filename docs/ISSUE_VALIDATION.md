# Issue fixes and validation

Version 1.76 source changes against `a9f02db` / version 1.72, October 21, 2026.
Package verification follows the GitLab/GitHub CI builds; local source test
results below do not establish packaged or on-device behavior.

| Item | Source change | Validation boundary |
| --- | --- | --- |
| 1. Crash-log folder button | Native Windows/macOS opening and a sanitized environment for Linux file-manager launchers. | Launcher regression tests; packaged OS checks still required. |
| 2. SFTP preparation appears frozen | Show progress before overwrite checking and size discovery; run network/file work on workers. | GUI timer/tab-switch regression and real loopback transfers. |
| 3. Sixel graphics | Incremental inline decoder and rendering, RGB/HLS colors, repeats, transparency, raster dimensions, and scroll/reset handling. | Decoder, malformed-input limits, terminal integration, and existing terminal trace replays. |
| 4. Remove NoMachine | Remove launch module, dependency discovery, and session-editor controls. Keep legacy serialized records readable/exportable; reject their launch/edit explicitly. | Legacy-record and protocol-editor/dependency regressions. |
| 5. Windows clipboard hangs | Plain-text clipboard helper with a deadline, separate from the GUI. Remove clipboard reads while opening terminal context menus. | Helper-timeout/UI responsiveness regression on Linux; Windows with an active RDP clipboard still needs reproduction. |
| 6. SFTP Delete | Delete Selected buttons and Delete-key shortcuts in both file panes; retain configured confirmation. | SFTP UI regressions and loopback delete. |
| 7. Minimize to tray | Saved opt-in setting, disabled by default, with Show/Quit actions and a visible-window fallback when no tray exists. Closing the main window still exits. | Settings and tray visibility/restore regressions. |
| 8. Slow/lost network | Worker-based SFTP and credential operations; connection/login deadlines, cancellable SFTP waits, and a 30-second idle timeout that extends during transfer progress. Bound SFTP setup/cleanup and secrets CLI calls. | Stall, progress, cancellation, GUI responsiveness, and loopback tests. |
| 9. Highlight/right-click SSH/less with external RDP open | Opening a terminal menu no longer requests clipboard text. Copy/paste use the isolated Windows helper; Copy and Paste reuses the selected text directly. | Source/timeout regressions and less trace replay; Windows/RHEL 7.9 reproduction still required. |
| 10. Large deletes | Background recursive deletion with activity/count display, cancellation, and preserved completed changes. | Local/remote deletion regressions and real SFTP deletion. |
| 11. Other tabs during SFTP | Progress is modeless; only the busy SFTP pane's controls are disabled. Protect closing an active operation's tab/window. | Switch to another tab while an overwrite scan is actively awaiting the server. |
| 12. macOS-to-Windows RDP/XQuartz | Prefer installed native FreeRDP SDL; fall back to X11 clients. Prepare XQuartz off the UI thread and report a missing display instead of silently assuming `:0`. | Client/environment regressions; Tahoe 26.5.1, XQuartz 2.8.6 and the affected Windows server remain a manual reproduction gate. |
| 13. Hidden certificate Y/N | Existing Linux/macOS GUI approval already uses default No before FreeRDP TOFU. Remove automatic deletion of FreeRDP certificate pins. No hidden automatic yes response is added. | RDP launch/trust regressions. A changed certificate must be reviewed rather than silently forgetting the pin. |

Sixel limits: 2048 pixels per side, 2 MiB encoded input per image, bounded
paint work, and at most 16 images / 32 MiB retained per terminal. Graphics appear
in the live terminal; the separate searchable scrollback viewer remains text-only.

Cancellation does not undo completed file writes or deletions. SFTP deadlines
measure inactivity rather than imposing a total-duration limit on a progressing
transfer. Directory workers retain their lifetime independently of closed tabs
until their network cleanup finishes.

## Verification

The source suite is run in a temporary clean source snapshot with the existing
virtualenv interpreter and `PYTHONPATH` pointing at that snapshot. Workspace and
theme tests run in separate processes, matching the CI split. Existing ignored
`build/` and `dist/` folders contain older artifacts and are not modified to make
source tests pass. Their packaged-help equality gate requires rebuilding them.

Full local run after the Qt correction on Python 3.11.17 / PySide6 6.11.2:
**1,217 unique tests and 152 subtests passed; 3 skipped**.
The skips are the packaged-help equality check and two packaged-runtime smoke
tests, because the clean source snapshot contains no frozen bundle.

The CI split produced 357 workspace passes, 2 theme passes, and 858 remaining
passes. The focused Qt runtime, version, packaging, and bootstrap checks also
passed together (23 passes), and are counted once in the unique total above.

Commands used with `QT_QPA_PLATFORM=offscreen`, the project virtualenv Python,
and `PYTHONPATH` set to the clean snapshot's `src/`:

```sh
python -X faulthandler -m pytest -vv --durations=25 -o faulthandler_timeout=60 tests/test_tab_workspace.py
python -m pytest -q tests/test_theme.py
python -m pytest -q -o faulthandler_timeout=60 --ignore=tests/test_tab_workspace.py --ignore=tests/test_theme.py
python -m pytest -q tests/test_qt_runtime.py tests/test_version.py tests/test_packaging_spec.py tests/test_bootstrap.py
```

`python -m snakesh --help`, `python -m snakesh tool list`, and
`git diff --check` also passed. Version 1.76 is prepared for the GitLab release
job, which mirrors its tag to GitHub. GitHub builds the platform packages on
release publication or a manual workflow run. Windows clipboard/native-shell
behavior and macOS Tahoe RDP remain on-device verification gates, not confirmed
platform fixes.

## CI runtime correction

The v1.73 GitHub build aborted during workspace tests with exit code 134; no
platform packages were built. A clean Python 3.11.17 environment reproduced
`none_dealloc` with PySide6 6.12.0. A standalone loop of ordinary
`QWidget.setWindowTitle` calls lost approximately one reference to `None` per
call and aborted the process. PySide6 6.11.0 and 6.11.2 did not show that loss.
Version 1.74 pins PySide6 6.11.2 in package metadata and the source bootstrap,
and adds an isolated runtime regression before CI workspace tests. The published
v1.73 tag is preserved; the corrected build uses a new version.
The new regression fails safely with the 6.12.0 runtime and passes with 6.11.2.

The v1.74 CI run passed the Qt runtime and workspace checks, then reported one
Resource Monitor sorting-test failure: a live process refresh started by tab
activation overwrote the test's synthetic process snapshot. Version 1.75
disables live collection in the three snapshot-rendering tests. The complete
Resource Monitor test module passed afterward (64 tests); application refresh
behavior is unchanged. Published tags v1.73 and v1.74 are preserved.

The v1.75 run passed all source tests and the Linux, Windows, and Apple Silicon
package jobs. Intel macOS froze successfully but its bundle smoke test failed:
cryptography's source-built Rust extension needed `SSL_get0_group_name`, while
the bundled `libssl.3.dylib` lacked that symbol. Current cryptography releases
publish only ARM macOS wheels. Version 1.76 pins cryptography 50.0.2 and builds
it from source on Intel using upstream's static OpenSSL recipe, ignoring cached
wheels. A native linkage/encryption check runs before PyInstaller. Intel bundle
validation still requires its CI runner; Linux tests of the gate do not establish
a native macOS build result. The v1.75 tag is preserved.
The four linkage-gate regressions and the affected packaging, version, bootstrap,
security, and Qt runtime tests passed together locally (28 tests). The macOS
build script also passed Bash syntax validation.

## Upstream references

- [Cryptography: macOS static OpenSSL build](https://cryptography.io/en/latest/installation/#building-cryptography-on-macos)
- [Cryptography: macOS Intel wheel support removed in 49.0.0](https://cryptography.io/en/latest/changelog/#v49-0-0)
- [Microsoft: RDP delayed clipboard rendering](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpeclip/fa309d1b-8034-44bf-b927-adfc753e69c1)
- [Microsoft: clipboard operations and ownership](https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-operations)
- [Microsoft: delayed clipboard rendering can wait 30 seconds](https://devblogs.microsoft.com/oldnewthing/20220609-00/?p=106731)
- [Homebrew FreeRDP formula, including SDL3 frontend](https://github.com/Homebrew/homebrew-core/blob/master/Formula/f/freerdp.rb)
- [XQuartz 2.8.6 release notes](https://www.xquartz.org/releases/XQuartz-2.8.6.html)
