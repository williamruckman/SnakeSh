# RDP display and macOS responsiveness investigation

October 29, 2026. Baseline: SnakeSh 1.84, commit `4287d39`. The working
tree was clean before this investigation. The reporter identified the latest
SnakeSh release, native `sdl-freerdp` on macOS, and recent Windows/macOS builds.
Exact OS builds and the installed FreeRDP/SDL versions were not supplied.

## Findings

| Report | Finding | Confidence |
| --- | --- | --- |
| Mac RDP opens detached and cannot attach | The session editor and `MainWindow._open_rdp_tab` explicitly force detached RDP on macOS and Windows. Only Linux has a supported embedded RDP path. | Confirmed implementation behavior, independent of recent OS updates. |
| Mac desktop is small and does not resize | Detached FreeRDP Auto does not provide an initial `/size`; it leaves initial geometry to the external client. More significantly, the fullscreen branch suppresses `/dynamic-resolution` even when Auto is selected. Default/fixed resolution also does not request dynamic resolution. | Confirmed launch behavior; the reported windowed SDL failure still needs native reproduction. |
| Mac clicks/paste cause delays and a pinwheel | The detached SDL process owns these input, drawing, and clipboard operations. SnakeSh does not forward each click or paste. SDL FreeRDP's clipboard callback can synchronously wait for a remote data response. A stall there could also prevent window resize events from being handled. | Plausible clipboard/event-loop diagnosis; no affected-machine stack sample or log confirms the cause yet. |
| Windows Auto does not change desktop resolution despite Smart sizing being checked | SnakeSh launches `mstsc.exe`. Microsoft's feature matrix lists smart sizing as supported and dynamic resolution as unsupported for MSTSC. Smart sizing scales existing desktop pixels; it does not cause remote applications to reflow at a new resolution. | Confirmed client limitation. |
| Windows Auto behaves differently with fullscreen selected | The generated profile omits both sizing settings for a fullscreen launch. For a windowed Auto launch it writes both `smart sizing:i:1` and the unsupported `dynamic resolution:i:1`. | Confirmed generated-profile defect/unsupported option. |

Source locations: `src/snakesh/protocols/rdp.py`,
`src/snakesh/ui/session_editor.py`, and `src/snakesh/ui/main_window.py`.
`src/snakesh/ui/clipboard.py` still performs synchronous Qt clipboard operations
on macOS, which is a separate potential wait when using SnakeSh's own terminal
copy/paste. Those functions are not the detached SDL viewer's clipboard path.

The current SDL client's window constructor explicitly sets the resizable
property. It also divides initial physical geometry by display content scale;
small initial windows on Retina displays must be assessed with the actual
client version and geometry. Detachment alone does not disable resizing.

No evidence here establishes that the OS updates introduced these defects.
The fullscreen guards already exist in the baseline. FreeRDP and SDL are
separately installed external dependencies, so the SnakeSh version alone does
not identify the client code running on the Mac. Homebrew's formula reported
FreeRDP 3.32.2 when checked during this investigation; this does not establish
the affected Mac's installed version.

## Local corrections

- Keep `/dynamic-resolution` when Auto and fullscreen are both selected for
  FreeRDP. Fullscreen and resize policy are independent. Linux embedding still
  suppresses `/f`, and smart sizing is never combined with dynamic resolution.
- Enable Windows Auto's `smart sizing:i:1` for both windowed and fullscreen
  launches, including a subsequent return from fullscreen to a window. Remove
  the unsupported MSTSC dynamic-resolution property.

These changes repair launch configuration. They do not add live resolution
changes to MSTSC, seed a larger initial Mac desktop, or establish that the Mac
pinwheel or reported windowed resize failure is fixed. True Windows dynamic
resolution requires a different client/backend or a supported hosted RDP
control with display-update handling. macOS in-tab support would likewise
require a new native embedding integration.

## Native checks still needed

1. On the affected Mac, record `sw_vers`, `sdl-freerdp /version`, and
   `sdl-freerdp /buildconfig`, plus `brew list --versions freerdp sdl3` if it is
   a Homebrew installation. Record the saved resolution/fullscreen preferences
   and the remote server OS. Use the executable actually launched by SnakeSh.
2. Connect to the same host outside SnakeSh with the same display/audio
   settings. Compare these two windowed probes, substituting the host and
   username, and enter the password through the client's prompt:

   ```sh
   sdl-freerdp /v:HOST /u:USER /dynamic-resolution /sound /audio-mode:0 +clipboard /log-level:DEBUG
   sdl-freerdp /v:HOST /u:USER /dynamic-resolution /sound /audio-mode:0 -clipboard /log-level:DEBUG
   ```

   Test clicks, focus changes, and remote-only copy/paste as well as window
   resizing. The second probe deliberately disables local/remote clipboard
   sharing. If disabling sharing removes the click/focus pinwheel, that strongly
   implicates clipboard handling; it does not by itself identify the faulty
   endpoint. Keep normal certificate verification enabled.
3. While the pinwheel is visible, use Activity Monitor to select the SDL
   FreeRDP process and choose **Sample Process**. A clipboard/pasteboard wait
   or a wait in `sdlClip::ClipDataCb` would support the clipboard hypothesis.
   A render/window synchronization wait would point elsewhere. Sample SnakeSh
   separately if its own windows also stop responding.
4. Compare Auto with a fixed `1920x1080` resolution. Test startup fullscreen,
   leaving fullscreen, maximizing, and dragging the window edges separately.
   Record the window bounds and the remote desktop's actual pixel resolution;
   enlarging a window and changing remote resolution are different outcomes.
5. On Windows, repeat the profile in standalone MSTSC and record its build and
   the remote desktop resolution before/after resizing. Confirm Auto smart
   scaling survives a fullscreen launch and return to windowed mode. Test the
   affected display/DPI arrangement; the local tests cannot prove this behavior.

Upstream has a merged SDL fullscreen display-negotiation correction tested on a
MacBook, and an older SDL clipboard timeout report. They support feasibility,
but neither proves the cause of this reporter's windowed hang. An October 27
FreeRDP 3.32.2 latency report concerns Remmina on Arch Linux; it is not native
macOS SDL reproduction evidence or a basis for prescribing a downgrade.

## Validation

`tests/test_rdp_display_modes.py` exercises emitted FreeRDP arguments and real
generated MSTSC profiles for Auto/fixed/default and fullscreen/windowed starts.
Against the baseline it produced four failures and six passes, demonstrating
the missing fullscreen Auto options and the unsupported MSTSC property.
After the corrections, the affected source suite passed **94 tests**, with
**1 native Windows test skipped** on this Linux host and **10 unrelated tests
deselected**:

```sh
QT_QPA_PLATFORM=offscreen PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
  ./.venv/bin/python -m pytest -vv -o faulthandler_timeout=30 \
  tests/test_rdp_display_modes.py tests/test_rdp.py \
  tests/test_linux_rdp_behavior.py tests/test_remote_viewer_tab.py \
  tests/test_rdp_certificates.py tests/test_session_editor.py \
  tests/test_platform_deps.py tests/test_windows_rdp_runtime.py \
  tests/test_update_regressions.py -k 'not real_loopback and not sftp'
```

An initial broader run was interrupted after it stopped producing output in
the unrelated update-regression group. The successful affected-suite run above
excludes the loopback/SFTP checks; it is not a full-suite result.
Native Mac/Windows GUI validation and a released package remain outstanding.
The release preparation uses version 1.85. Package publication does not close
the affected-device validation gates described above.

## Primary references checked October 29, 2026

- [Microsoft client feature matrix: MSTSC display support](https://learn.microsoft.com/en-us/previous-versions/remote-desktop-client/compare-remote-desktop-clients#display)
- [Microsoft RDP display properties](https://learn.microsoft.com/en-us/azure/virtual-desktop/rdp-properties#display-settings)
- [FreeRDP 3.32.2 SDL window creation](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/SDL/SDL3/sdl_window.cpp)
- [FreeRDP 3.32.2 clipboard response wait](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/SDL/SDL3/sdl_clip.cpp)
- [SDL paste timeout report, FreeRDP issue 11445](https://github.com/FreeRDP/FreeRDP/issues/11445)
- [Merged SDL fullscreen desktop renegotiation, PR 13682](https://github.com/FreeRDP/FreeRDP/pull/13682)
- [FreeRDP 3.32.2 Remmina latency report, issue 13875](https://github.com/FreeRDP/FreeRDP/issues/13875)
- [Homebrew FreeRDP formula metadata](https://formulae.brew.sh/api/formula/freerdp.json)
