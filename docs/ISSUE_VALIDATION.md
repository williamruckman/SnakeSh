# Issue fixes and validation

Version 1.84 source changes against `a9f02db` / version 1.72, October 23, 2026.
Published CI packages and Linux artifact verification are recorded below.
Source tests and package smoke checks have separate on-device validation gates.

| Item | Source change | Validation boundary |
| --- | --- | --- |
| 1. Crash-log folder button | Native Windows/macOS opening and a sanitized environment for Linux file-manager launchers. | Launcher regression tests; packaged OS checks still required. |
| 2. SFTP preparation appears frozen | Show progress before overwrite checking and size discovery; run network/file work on workers. | GUI timer/tab-switch regression and real loopback transfers. |
| 3. Sixel graphics | Independent local/remote settings, both off by default; optional converter installation on Mint/Ubuntu, macOS, and Windows; bounded inline decoder and rendering. | Real img2sixel output, terminal framebuffer pixels, decoder limits, settings/install regressions, and terminal trace replays. Windows integration runs on its CI runner. |
| 4. Remove NoMachine | Remove launch module, dependency discovery, and session-editor controls. Keep legacy serialized records readable/exportable; reject their launch/edit explicitly. | Legacy-record and protocol-editor/dependency regressions. |
| 5. Windows clipboard hangs | Plain-text clipboard helper with a deadline, separate from the GUI. Remove clipboard reads while opening terminal context menus. | Helper-timeout/UI responsiveness regression on Linux; Windows with an active RDP clipboard still needs reproduction. |
| 6. SFTP Delete | Delete Selected buttons and Delete-key shortcuts in both file panes; retain configured confirmation. | SFTP UI regressions and loopback delete. |
| 7. Minimize to tray | Saved opt-in setting, disabled by default, with Show/Quit actions and a visible-window fallback when no tray exists. Closing the main window still exits. | Settings and tray visibility/restore regressions. |
| 8. Slow/lost network | Worker-based SFTP and credential operations; connection/login deadlines, cancellable SFTP waits, and a 30-second idle timeout that extends during transfer progress. Bound SFTP setup/cleanup and secrets CLI calls. | Stall, progress, cancellation, GUI responsiveness, and loopback tests. |
| 9. Highlight/right-click SSH/less with external RDP open | Opening a terminal menu no longer requests clipboard text. Copy/paste use the isolated Windows helper; Copy and Paste reuses the selected text directly. | Source/timeout regressions and less trace replay; Windows/RHEL 7.9 reproduction still required. |
| 10. Large deletes | Background recursive deletion with activity/count display, cancellation, and preserved completed changes. | Local/remote deletion regressions and real SFTP deletion. |
| 11. Other tabs during SFTP | Progress is modeless; only the busy SFTP pane's controls are disabled. Protect closing an active operation's tab/window. | Switch to another tab while an overwrite scan is actively awaiting the server. |
| 12. macOS-to-Windows RDP/XQuartz | Prefer installed native FreeRDP SDL; fall back to X11 clients. Prepare XQuartz off the UI thread and report a missing display instead of silently assuming `:0`. | Client/environment regressions; Tahoe 26.5.1, XQuartz 2.8.6 and the affected Windows server remain a manual reproduction gate. |
| 13. Hidden certificate Y/N | Default-No GUI approval before TOFU; Linux certificate-change dialog compares previous/new fingerprints and updates only the reviewed matching host/port pin before reconnecting. | RDP launch/trust and explicit pin-replacement regressions. Refusal and concurrent pin changes preserve existing trust records. |

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

Full local run at v1.74 after the Qt correction on Python 3.11.17 / PySide6 6.11.2:
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
`git diff --check` also passed. Windows clipboard/native-shell behavior and
macOS Tahoe RDP remain on-device verification gates.

## Published release verification

GitLab synchronization pipeline 204 passed and mirrored the immutable v1.78 tag
at `c5b0b1c01206f10d14132052d715f66ae0169b0b`. After release publication,
[GitHub build 39688345730](https://github.com/williamruckman/SnakeSh/actions/runs/39688345730)
completed successfully on October 21, 2026. The Qt runtime check, workspace
suite, remaining source tests, all four platform package jobs, and release
attachment job passed.

[Release v1.78](https://github.com/williamruckman/SnakeSh/releases/tag/v1.78)
contains the Linux x86_64 AppImage, Windows x64 installer, Intel and Apple Silicon
macOS DMG/ZIP packages, and `SHA256SUMS.txt`. The macOS packages are unsigned.
Native CI startup smoke checks passed on Windows and both macOS architectures;
Linux passed the frozen executable and AppImage smoke checks.

The published checksum manifest matched GitHub's SHA256 metadata for all six
package assets. The downloaded Linux AppImage also matched its manifest hash:

```text
6769c41c039294a52fc9557b82bec8ad67a441d1ea317c8b8b4a9214bcb5f1d5
```

The downloaded AppImage passed `--help`. Its extracted bundle reported version
1.78 and was linked into a temporary clean source snapshot for the three checks
previously skipped without a package: bundled-help equality, detached Diff tool
lifetime/reuse, and detached Password Generator lifetime/reuse. All passed:
**3 tests and 36 subtests**, with no skips, using isolated application data and
Qt's offscreen platform. These checks establish the packaged Linux runtime;
the Windows clipboard/RDP and macOS Tahoe connection reproductions still need
the affected machines.

## Linux RDP and Sixel follow-up (v1.79)

The Linux Mint 22.3 main-PC log identified the reported embedded exit code 143:
FreeRDP rejected a changed saved certificate. The failure is distinct from the
laptop's incorrect detached label and missing resize behavior. The new GUI
approval requires review of both SHA256 fingerprints, defaults to No, and
replaces only the matching previous pin after approval. Other host records are
preserved; changed or missing prior records stop replacement. FreeRDP still
checks the resulting exact saved pin on reconnect.

Linux RDP now discovers FreeRDP's actual child beneath the Qt host with bundled
python-xlib 0.33, including unmapped children. Missing observations and failed
resize requests leave a running session's embedded state intact. Resizes use
physical pixels and preserve the existing child. Default/fixed desktop sizes
use smart sizing; Auto uses dynamic resolution, never both. In-tab launches
require Qt's X11 backend, and the system FreeRDP environment excludes bundled
AppImage library paths. Bounded background output capture exposes client errors
and certificate changes in the GUI.

Local Sixel and Remote Sixel are independent saved options, disabled by default.
On Mint/Ubuntu, saving Local Sixel installs the official `libsixel-bin` package
if `img2sixel` is missing; cancellation/failure leaves Settings open. Remote
Sixel applies to SSH, Telnet, and serial streams. The remote image application
runs on that host. Enabled local terminals now advertise Sixel despite the
default automatic terminal type; disabled terminals do not advertise or decode
images. The existing image/resource bounds still apply.

Focused source tests include actual X11 child discovery/resizing/replacement
without external utilities, real `img2sixel` conversion, terminal framebuffer
pixels, independent settings, installer dispatch/failure, bounded client output,
and explicit certificate approval/replacement. GitHub CI additionally runs the
X11 integration checks under Xvfb and installs `libsixel-bin` for encoder checks.
Combined local source validation on Python 3.12.3 / PySide6 6.11.2 passed
**1,244 unique tests and 152 subtests**, with 3 package-dependent skips. This
combines the 357 workspace and 2 theme checks with the remaining suite and
targeted rechecks: four migration tests required removal of the test run's
forced data-directory override, and an older framebuffer regression now opts
into local Sixel. Native X11 and real encoder checks ran locally without skips.
The final affected privilege, Resource Monitor, RDP, and Sixel group passed
142 tests. Settings, help, version, and packaging checks also passed after the
final UI change (69 tests).

GitLab synchronization pipeline 205 passed for tag v1.79 at
`e40dab2c7d2164582971016c8c5550de140d7770`.
[GitHub build 39781197237](https://github.com/williamruckman/SnakeSh/actions/runs/39781197237)
passed all source checks, the native X11/Xvfb check, all four platform package
jobs, and release attachment. [Release v1.79](https://github.com/williamruckman/SnakeSh/releases/tag/v1.79)
contains all six platform assets and the checksum manifest; macOS packages are
unsigned. The manifest matched GitHub's SHA256 metadata for all six assets.
The downloaded Linux AppImage matched this SHA256:

```text
43e6fb03b0af877fa9657ad747d242b577ffea50b7c89c9e06e9ce34437d6d9b
```

The downloaded AppImage passed `--help`; its extracted bundle contained version
1.79 and python-xlib's license metadata. The three package-dependent checks
then passed against that CI bundle: **3 tests and 36 subtests**, with no skips.

A separate probe launched the actual frozen executable under Qt X11 with a
synthetic external FreeRDP-compatible viewer and isolated application data and
test credentials. Its child fitted the tab at 1056×580, remained attached beyond
the former failure interval, and resized to 806×430 when the main window changed
size. This exercised the bundled Xlib implementation without `xdotool` or a live
RDP server. It does not establish authentication or display negotiation with the
affected Windows server. Final server connection/resizing and privileged Sixel
installation checks on the affected Mint machines remain on-device checks using
the published v1.79 package.

## Sixel converter follow-up (v1.80)

The user confirmed that Linux Mint RDP works with the v1.79 package. The Sixel
red-bar test also worked, while `img2sixel -w 800 LinuxMintLogoIcon.png` returned
without displaying an image. Reproducing with the supplied 211×211 RGBA PNG and
img2sixel 1.10.3 identified the decoder's 100,000-command limit: it discarded the
390,344-byte output partway through, despite being within the image and paint
limits. The decoder now validates dimensions/work first and paints in a second
pass, avoiding a large list of drawing-command objects. The existing encoded
size, dimensions, paint-work, and retained-image bounds remain in force.

An actual local PTY in the Linux source application ran the same converter
command and displayed the 800×800 logo. A captured terminal framebuffer contained
180,014 green image pixels. The supplied logo is not added to the repository;
regressions use generated graphics, including more than 100,000 drawing commands,
overpainting limits, and real img2sixel output.

Local setup shows a separate indeterminate progress window while the GUI event
loop continues. Settings retains only the local/remote switches; explanations
and commands live in Help. Failed installations leave Settings open. On macOS,
setup uses an existing Homebrew installation to install `libsixel` without root;
new local shells include Homebrew's standard paths even when launched from Finder.

Windows setup installs Sixel 0.7.1 from PowerShell Gallery in CurrentUser scope
for available PowerShell editions. It verifies module import and the cmdlet;
it does not change execution policy or repository trust. Windows packages include
the pinned, SHA256-verified Microsoft ConPTY 1.25.260930003 and OpenConsole,
with their MIT license. Opted-in new local terminals use that runtime so older
system consoles do not discard image sequences. The Windows build runs real
PowerShell conversion through a local terminal and checks image pixels, then
checks that the native files and license survive freezing unchanged. Those
Windows checks cannot execute on the Linux development host.

Local source validation on Python 3.12.3 / PySide6 6.11.2: **1,257 passed,
152 subtests passed**, with three package-dependent checks and two Windows
runtime cases skipped. Workspace, theme, and remaining tests ran separately in
a clean temporary source snapshot. Focused checks cover installer failures,
GUI timer heartbeats while progress is visible, macOS executable discovery,
Windows module installation arguments, and native package integrity. Release
packages must still be built by GitLab/GitHub before compiled verification;
on-device Windows and macOS image display remain separate validation gates.

The session editor now hides terminal color overrides and the color preview,
including their row labels, for RDP and VNC. SSH, SFTP, Telnet, and serial retain
those controls. Switching back restores unsaved color choices; saving a remote
desktop session excludes the irrelevant overrides. Protocol switching and saved
payloads were checked directly; the existing editor, Settings, Sixel settings,
and Help tests passed after the final UI changes.

## Windows Sixel CI follow-up (v1.81)

The v1.80 CI source suite and Linux, Intel macOS, and Apple Silicon macOS package
jobs passed. Windows stopped before freezing: both real PowerShell terminal
tests connected and closed without an image. Converter installation and the
verified Microsoft runtime download had succeeded. The combined release
attachment job was skipped, so v1.80 has no published package assets.

The new console path now releases its extra client reference after launching
the shell, as Microsoft's terminal implementation does, and reads until the
console output pipe closes. A shell process exiting is not sufficient evidence
that ConPTY has forwarded its final output. The local terminal also drains
queued bytes before inserting its own closed-session message, preserving a
partially received Sixel sequence. Tests cover that ordering and waiting for
console EOF. Native Windows coverage adds a short-lived child independent of
PowerShell conversion and records encoder/child diagnostics on failure.

Windows native checks now run before the release draft is created. Failures
produce public GitHub check annotations and a JUnit artifact, avoiding the need
to retrieve authenticated logs to see terminal test failures. Package builds
still start when the user publishes the resulting draft. Compiled verification
requires successful CI package jobs and remains pending for this version.

The v1.81 Windows gate also failed and prevented draft creation. Further review
identified a separate documented Windows startup issue: without
`STARTF_USESTDHANDLES` and explicitly null standard handles, a ConPTY child can
inherit redirected parent streams and bypass the terminal entirely. Version
1.82 uses Microsoft's startup flags, with a regression checking the exact
standard-handle configuration. Native CI validation remains required.

### Published v1.82 package verification

GitLab synchronization pipeline 208 succeeded for tag v1.82 at `ef45cff`.
The Windows terminal gate and draft creation both passed in
[Prepare Release Draft 39793663594](https://github.com/williamruckman/SnakeSh/actions/runs/39793663594).
That gate exercises PowerShell Sixel conversion through the actual local
terminal and a short-lived child writing Sixel before exit. The explicit
standard-handle configuration corrected the native Windows failures.

[Build 39794170777](https://github.com/williamruckman/SnakeSh/actions/runs/39794170777)
produced the six platform packages and checksum manifest attached to
[release v1.82](https://github.com/williamruckman/SnakeSh/releases/tag/v1.82).
The manifest matched GitHub's SHA256 metadata for every platform asset. The
downloaded Linux AppImage matched:

```text
4a76bfe140d89ddce1ed33ebe68cad9d520af0665da60fb1df1196dbeb260ecd
```

The downloaded AppImage passed `--help`; its extracted bundle contained version
1.82 and python-xlib's license metadata. Package-dependent Help and standalone
tool checks passed against that CI bundle: **3 tests and 36 subtests**, no skips.

The actual frozen executable then opened an isolated local PTY and ran
img2sixel 1.10.3 against the supplied Mint PNG at width 800. Its terminal
framebuffer displayed the logo with 180,014 green image pixels, matching the
source check. This verifies the compiled Linux output path, decoder, and canvas.
The probe used isolated application data and synthetic local credentials.
Windows native CI validation and macOS package builds are separate from
on-device graphical checks on the user's machines. macOS image display and
privileged package-manager installation remain on-device validation items.

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

The v1.76 run passed all source tests, Windows, Apple Silicon, and Intel macOS
packaging, including the previously failing Intel bundle smoke test. Its Linux
container build stopped before application compilation when the Deadsnakes
package server returned HTTP 503 for two Python packages. The existing release
can be retried without moving its tag. Follow-up changes on `main` add bounded
APT and tool-download retries for future builds; these changes are not in the
immutable v1.76 tag.

Direct probes of both failed package URLs also returned HTTP 503. Version 1.78
uses the existing direct payload-build mode on GitHub's Ubuntu 22.04 runner
with its `setup-python` Python 3.11 interpreter. The script's Python, shared
library, and GLIBC baseline checks still run; the separate AppImage packaging
and smoke checks remain enabled. This CI path avoids the unavailable PPA. Local
container builds retain the same Ubuntu/Python baseline and bounded retries.

GitHub rejected the v1.77 tag mirror with GH007 (private author email). Future
commits in this repository use the owner's GitHub no-reply address. Version
1.78 carries the same Linux CI change; existing tags and history are retained.

## Windows RDP console-window follow-up (v1.83)

The supplied Windows screenshot and connection-action confirmation identified
an extra Windows Terminal window when opening RDP. The credential preload used
the console application `cmdkey` with captured output but no console-suppression
flag. It now uses `CREATE_NO_WINDOW`, so credential preload runs without opening
a console. The Microsoft Remote Desktop viewer and its connection/security
prompts retain their existing launch behavior.

The launcher regression checks both default-port and explicit-port credential
targets. A native Windows regression launches a GUI parent (`pythonw.exe`),
substitutes a harmless console probe for the credential-writing command, and
requires that the child has no console handle. It does not write credentials.
Both the pre-draft Windows gate and Windows package job include this regression
and the existing Sixel runtime checks. Local affected source checks passed:
**79 tests**, with four native Windows checks skipped on Linux.

GitLab synchronization pipeline 209 passed for v1.83 at
`b67d2e5607bb2865f66926095657c213a3f74b48`.
[GitHub Windows gate 39822228418](https://github.com/williamruckman/SnakeSh/actions/runs/39822228418)
passed the real GUI-parent console check, the RDP launcher tests, and the
existing native Sixel checks, then created the release draft.

After publication, [GitHub build 39823340844](https://github.com/williamruckman/SnakeSh/actions/runs/39823340844)
passed the full source suite, all four platform package jobs, and release
attachment. The Windows job repeated the RDP helper and native Sixel checks,
then passed its frozen-app startup smoke check. [Release v1.83](https://github.com/williamruckman/SnakeSh/releases/tag/v1.83)
contains all six platform packages and `SHA256SUMS.txt`; macOS packages remain
unsigned. The manifest matches GitHub's SHA256 metadata for all six packages.
Downloaded Windows and Linux assets matched their manifest hashes:

```text
87cf552c06743cbf98a476bb844458d9ab8ccce158d622167c0386d6c41a1496  SnakeSh-1.83-Setup.exe
6f604cf790f25a0c28910df0a029a3ec8b841c881dd292d1250f165f422aadc5  SnakeSh-1.83-x86_64.AppImage
```

The downloaded CI AppImage passed `--help`, and the extracted bundle reports
version 1.83. Bundled-help equality and both detached-tool lifetime/reuse checks
passed against that compiled bundle: **3 tests and 36 subtests**, with no skips.
The checks used a clean tagged source snapshot, temporary application data,
Qt offscreen, a loopback test secrets endpoint, and a disabled OS keyring backend.
The user confirmed on October 22, 2026 that the v1.83 Windows update resolves
the extra terminal opening when connecting to RDP. The affected-machine
validation gate for this issue is complete.

## SFTP transfer controls and stable progress (v1.84)

The local pane now has an Upload button beside Delete Selected; the remote
pane has Download beside Delete Selected. They use the existing selected-item
actions, transferring into the opposite pane's current directory with the same
overwrite confirmation and cancellation behavior. Both buttons are disabled
during an active file operation and restored afterward; other tabs remain usable.

Transfer progress and activity callbacks previously alternated the top line
between Item and Processing. Once the first transfer progress update arrives,
the dialog now keeps its Item counter and file names visible. Preparation and
deletion continue showing activity messages. Percentage, speed, elapsed time,
ETA, and subsequent item updates continue as before. Help describes the buttons
and stable transfer summary.

Focused SFTP source checks passed: **60 tests and 4 subtests**. Additional
affected checks passed: **68 tests**, with the packaged-help comparison skipped
because the clean source snapshot has no CI-built bundle. A direct Qt source
probe clicked both buttons with selected items, verified the transfer direction
and current destinations, and checked their placement beside Delete Selected.
The full workspace suite also passed: **357 tests and 40 subtests**, with the
OS keyring disabled for that local test process.

GitLab synchronization pipeline 212 passed for v1.84 at
`722a03bd826913393bb9c793ecd114b4ce9325c6`.
[GitHub gate 39952676049](https://github.com/williamruckman/SnakeSh/actions/runs/39952676049)
passed the native Windows terminal/Sixel and RDP helper checks, then created
the release draft.

After publication, [GitHub build 39953040898](https://github.com/williamruckman/SnakeSh/actions/runs/39953040898)
passed the full source suite, all four platform package jobs, and release
attachment. [Release v1.84](https://github.com/williamruckman/SnakeSh/releases/tag/v1.84)
contains all six platform packages and the checksum manifest. The manifest
matches GitHub's SHA256 metadata for all six packages. Downloaded Linux and
Windows assets also matched their manifest hashes:

```text
bd00be3076b85820357d5ddc67cc9404959e8038bb8d74c13ad2d9693c8aab4b  SnakeSh-1.84-x86_64.AppImage
ce1052edc41a1c232dd4c26c569a5aab1ee04a17fac5b2901972e3f6216b15c3  SnakeSh-1.84-Setup.exe
```

The downloaded AppImage passed `--help`; the extracted bundle reports version
1.84. Its bundled Help matches the tagged source: **1 test and 36 subtests**,
with no skips.

A graphical probe launched that actual frozen executable in a temporary private
Xvfb display, with isolated application data, generated test credentials, a
loopback test secrets endpoint, and the OS keyring disabled. Clicking Upload
transferred the selected 9,100,000-byte local file into the displayed remote
directory; clicking Download transferred the selected 9,000,000-byte remote
file into the displayed local directory. Both destination files matched their
source bytes exactly.

The loopback SFTP server deliberately limited transfer speed. Once data flow
was established, the visible summary prefix stayed on Item across 67 upload
frames and 65 download frames, sampled about every 25 milliseconds. Captured
progress windows showed Item 1/1 with the correct file names while percentages
advanced. This validates the compiled Linux controls and sampled progress
behavior; the user's own SFTP sessions remain the on-device check.

## Upstream references

- [FreeRDP scaling options are mutually exclusive](https://github.com/FreeRDP/FreeRDP/blob/stable-2.0/client/common/cmdline.c)
- [FreeRDP X11 resize and dynamic resolution handling](https://github.com/FreeRDP/FreeRDP/blob/stable-2.0/client/X11/xf_event.c)
- [libsixel image converters](https://github.com/libsixel/libsixel)
- [Homebrew libsixel formula](https://formulae.brew.sh/formula/libsixel)
- [Sixel PowerShell module 0.7.1](https://www.powershellgallery.com/packages/Sixel/0.7.1)
- [Microsoft ConPTY package](https://www.nuget.org/packages/Microsoft.Windows.Console.ConPTY/1.25.260930003)
- [Microsoft process creation flags: CREATE_NO_WINDOW](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)
- [Python X Library 0.33](https://pypi.org/project/python-xlib/0.33/)

- [Cryptography: macOS static OpenSSL build](https://cryptography.io/en/latest/installation/#building-cryptography-on-macos)
- [Cryptography: macOS Intel wheel support removed in 49.0.0](https://cryptography.io/en/latest/changelog/#v49-0-0)
- [Microsoft: RDP delayed clipboard rendering](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpeclip/fa309d1b-8034-44bf-b927-adfc753e69c1)
- [Microsoft: clipboard operations and ownership](https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-operations)
- [Microsoft: delayed clipboard rendering can wait 30 seconds](https://devblogs.microsoft.com/oldnewthing/20220609-00/?p=106731)
- [Homebrew FreeRDP formula, including SDL3 frontend](https://github.com/Homebrew/homebrew-core/blob/master/Formula/f/freerdp.rb)
- [XQuartz 2.8.6 release notes](https://www.xquartz.org/releases/XQuartz-2.8.6.html)
