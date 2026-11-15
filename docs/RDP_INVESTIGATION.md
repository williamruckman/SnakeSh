# RDP display and macOS responsiveness investigation

October 29, 2026. Baseline: SnakeSh 1.84, commit `4287d39`. The working
tree was clean before this investigation. The reporter identified the latest
SnakeSh release, native `sdl-freerdp` on macOS, and recent Windows/macOS builds.
Exact OS builds and the installed FreeRDP/SDL versions were not supplied.

## Release 1.89 parser hotfix

The native Mac screenshot after installing 1.88 shows FreeRDP exits with
code 22 before connecting: the common command-line parser rejects the
keyboard-remap argument with status **-1002**. The right-side hexadecimal
entry `0x15c=0x11d` is 11 characters, exceeding FreeRDP's 10-character limit
for each remap entry. The earlier launcher tests checked emitted strings but
did not exercise this parser; they missed the startup regression.

Version 1.89 uses `/kbd:remap:347=29,remap:348=285`. These decimal values
preserve the same left/right Command-to-Control mapping and fit the limit.
An actual FreeRDP **3.32.1** shared-library parser probe reproduced the old
failure and then passed all six regression cases after correction. It
checks the generated Default/fixed/Auto commands, fullscreen/windowed starts,
the smart-sizing recovery command, actual decoded mapping values, and
unchanged physical Control/C/V keys, without connecting to any server.
The identical length check and base-0 integer parsing were verified in
upstream **3.32.2** source. A new required FreeRDP SDK gate runs before draft
creation and before package builds. The subsequent native Mac result is
recorded below.

Local 1.89 validation passed **580 tests and 70 subtests**, including the
required real-parser checks. One native Windows-only check was skipped and
the old built-bundle Help comparison was deselected. The SDK was downloaded
and extracted locally without installing host packages; the remote Mint
server remains unchanged by this work.

### Release 1.89 publication and validation

Release [v1.89](https://github.com/williamruckman/SnakeSh/releases/tag/v1.89)
is tagged at `5d31ebe`. GitLab synchronization pipeline **285** passed.
[Prepare Release Draft 41504201037](https://github.com/williamruckman/SnakeSh/actions/runs/41504201037)
passed both the real FreeRDP CLI parser gate and the native Windows gate.
After publication,
[Build 41505750517](https://github.com/williamruckman/SnakeSh/actions/runs/41505750517)
passed the version check, full source suite, required parser gate, all four
platform package jobs, their frozen startup checks, and asset attachment.

All six packages and `SHA256SUMS.txt` were downloaded. Each package matches
both the manifest and GitHub's published SHA256 digest; the manifest's own
download also matches its GitHub digest. The extracted Linux AppImage reports
runtime VERSION **1.89** and passes `--help`. Both Mac ZIPs contain runtime
VERSION **1.89** and the expected x64/arm64 Mach-O bootloader architecture.
Windows startup validation is from its native CI runner, rather than local
execution of the installer on this Linux development host.

The user then confirmed on their Mac that the updated build connects,
copy/paste works properly, and they see no other errors. This confirms recovery
from the 1.88 argument-parsing regression on that machine. The message does
not separately establish that a failed Auto resize triggered the recovery
popup. The user plans to have the original reporter update and retest later;
the original persistent interaction pinwheel remains open pending that result.

Archive inspection also found a pre-existing packaging metadata follow-up:
`CFBundleShortVersionString` remains **0.0.0** in both Mac Info.plist files,
as it did in 1.87. The packaged runtime VERSION files contain the correct
release version. The remap hotfix does not change that plist metadata.

| Package | Verified SHA-256 |
| --- | --- |
| Linux AppImage | `8dc407e66281169b672ad4d670decf0ada72854731ab2d3e9f97e32bb3c58b42` |
| Windows Setup EXE | `efecae2c12ceabc60ec1708294b7bfd0f04b925fefa0391bf55f51a1df52d225` |
| macOS arm64 DMG | `5440d615222c4c3ac23a126df460f276ef89db2545cf5903aec6beec4e678f09` |
| macOS arm64 ZIP | `0ee1783337e97e5b9c36327a4d520276db3c2838ebedc11eed4e05cffabb3a48` |
| macOS x64 DMG | `a4b2d32c161bf6285ea42f10f5861f842a10d35e62bdd219fcc6b0a474890cc5` |
| macOS x64 ZIP | `7283c208fc8e45ef42188524b6d100835990d3a07f5c47f36228c21e5dff065e` |

## November 3 original reporter connection failure

The user forwarded a screenshot from the original reporter, rather than the
Mac that had already passed the 1.89 connection/copy-paste check. The new image
shows a private IPv4 target on TCP 3389, detached exit code 141,
`ERRCONNECT_CONNECT_FAILED [0x00020006]`, `Couldn't get socket address`, and
`ConnectLayer ... [15000ms] failed`. The user subsequently confirmed
**SnakeSh 1.89** and supplied an About This Mac screenshot identifying an
**M1 MacBook Air (2020), 16 GB RAM, macOS Tahoe 26.2**. A local Metal renderer
was created, but no established remote desktop is shown. This prevents a
meaningful retest of the persistent interaction lag. The hardware screenshot
does not establish which SnakeSh package architecture or FreeRDP binary is
running, and no architecture failure is shown by the connection error.

In pinned FreeRDP 3.32.2 `tcp.c`, `freerdp_host_connect()` tries the resolved
socket addresses and calls `get_next_addrinfo(..., FREERDP_ERROR_CONNECT_FAILED)`
after a failed socket/connect attempt. The screenshot therefore identifies
the pre-login connection stage without establishing a specific cause such as
a stopped RDP listener, wrong endpoint, network/VPN route, filtering, or local
application permissions. The 15000ms field is the configured limit, not proof
that the attempt consumed 15 seconds. No target probe was made from this
development host.

The SnakeSh version is confirmed. A single-port `nc -nvz -G 5`
check from Apple's Terminal on the affected Mac, outside SnakeSh, remains
pending. Apple
documents that Terminal-launched tools are exempt from local-network privacy,
while a helper launched by a GUI app can be attributed to that app. A successful
Terminal probe therefore does not by itself prove that SnakeSh has permission.
If the port is reachable there, check SnakeSh under System Settings > Privacy
& Security > Local Network and compare a direct RDP connection from another
already installed client. Results are pending; no additional application fix
is established by this screenshot alone.

References: [FreeRDP 3.32.2 TCP connection handling](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/libfreerdp/core/tcp.c),
[Apple local-network privacy](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy).

## Release 1.90 Local Network permission support

The Mac bundle now declares `NSLocalNetworkUsageDescription` with a reason
covering local SSH, SFTP, RDP, VNC, and network tools. On macOS 15 and later,
the main GUI shows a nonmodal explanation once per Mac user and attempts the
system permission request. A local QSettings flag records only that this
explanation was shown; it is not exported and does not represent a permission
grant. Safe mode skips this startup flow. Other platforms and older macOS
versions do not show it.

**Tools > Local Network Access...** reopens the explanation, offers **Request
Access**, and provides an **Open System Settings** shortcut plus explicit
navigation to Privacy & Security > Local Network. Prior denials still require
the user to enable SnakeSh there and reconnect. The Settings shortcut is best
effort; manual navigation remains visible if the OS opens a general page.

The request follows Apple's TN3179 approach: enumerate active broadcast-capable
interfaces, generate two random IPv6 link-local peers per local link-local
address, then connect nonblocking UDP sockets to port 9 with the interface
scope. The sockets run in the GUI process and close immediately without sending
datagrams. There is no DNS lookup, server probe, helper launch, elevation,
permission bypass, polling, or inferred permission result. Offline/no-IPv6
and socket-error paths keep the dialog and workspace usable. Ordinary local
connections can still trigger the system prompt.

Tests cover the platform gate, interface filtering and IPv6 scope, no datagram
sends, resource errors, denial without retry loops, one-time startup behavior,
safe mode, dialog close/reopen, Settings fallback, and the Mac bundle's plist
declaration. Mac package CI also checks the generated app's actual Info.plist.

Local regression validation passed **652 tests and 40 subtests**, covering
workspace startup, the new permission flow, packaging, Help, bootstrap,
settings, Qt runtime, RDP launch/resize recovery, and Mac clipboard shortcuts.
One comparison against pre-existing local Help build outputs was deselected;
those bundles predate the current source. The new dialog was also rendered and
visually inspected with Qt offscreen. Neither check verifies native macOS
permission behavior.

Native validation remains required on both Mac architectures: launch the app
from Finder (Terminal-launched processes are exempt), test a fresh Mac user
with an undetermined permission, allow access and reconnect, relaunch without
the repeated explanation, and test a denied user through the Settings shortcut.
Also check offline/no-link-local behavior and opening the Tools action while a
session is running. Linux tests cannot establish that macOS displays the system
alert, recognizes the unsigned app consistently, or honors the Settings URL.
This feature does not establish that Local Network privacy caused the original
reporter's connection failure; their port test is still pending.

Reference: [Apple TN3179](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy),
especially "macOS considerations" and "Trigger the local network alert".

### Release 1.90 preparation status

Release commit `35d190c` and annotated tag `v1.90` were pushed to GitLab, and
pipeline **286** synchronized the identical tag to GitHub. Both native gates in
[Prepare Release Draft 41693425267](https://github.com/williamruckman/SnakeSh/actions/runs/41693425267)
passed: Windows terminal/RDP and the FreeRDP parser. Draft creation then failed
with **Resource not accessible by integration** at the Create Release API.

The workflow already grants `contents: write`. GitHub documents an additional
workflow-write requirement when the release target changes workflow files
relative to its default branch. The tag can arrive before GitLab's asynchronous
main-branch mirror, making that a plausible race; the exact main-branch state
at the failed request was not captured. GitHub main and the tag were verified
identical afterward. GitLab release synchronization now pushes its main branch
without force before the tag, instead of relying on the asynchronous mirror.
No credentials were copied to this workstation, and `v1.90` remains unchanged.

GitLab pipeline **287** verified branch synchronization and left the existing
GitHub tag unchanged, but the attempted failed-job retry was rejected by
GitHub's Actions API with **HTTP 403**. That optional retry path was removed;
the configured CI token is not sufficient for that operation. GitLab pipeline
**288** then passed with that retry path removed. The user reran the failed
draft job successfully and published
[v1.90](https://github.com/williamruckman/SnakeSh/releases/tag/v1.90) at
**2026-11-03 18:52:28 UTC**, triggering
[Build 41697937789](https://github.com/williamruckman/SnakeSh/actions/runs/41697937789)
at release commit `35d190c`. The build completed successfully: version check,
full source suite, required FreeRDP parser gate, Linux/Windows/both Mac package
jobs, native frozen startup checks, Mac Info.plist checks, and asset attachment.

All six packages and `SHA256SUMS.txt` were downloaded. Every package matches
both the manifest and GitHub's published SHA256 digest, and the manifest's own
download matches its GitHub digest. Both Mac ZIPs contain runtime VERSION
**1.90**, the correct x64/arm64 Mach-O architecture, bundle identifier
`com.snakesh.app`, and the Local Network purpose text covering SSH, SFTP, RDP,
VNC, and network tools. The pre-existing `CFBundleShortVersionString` **0.0.0**
metadata follow-up remains; the actual runtime VERSION is correct. The extracted
Linux AppImage reports bundled VERSION **1.90** and passes `AppRun --help`.
Windows executable startup is validated by its native CI runner; the installer
bytes were verified locally without installing it on this Linux host.

The real macOS permission prompt and Settings shortcut still need Finder-launch
validation on a Mac, as described above. The original reporter's connection and
persistent pinwheel issues remain pending their retest and port-check result.

| Package | Verified SHA-256 |
| --- | --- |
| Linux AppImage | `52989eb87a11cb7fff13aabccf78883dd99c63f1083d8100f828760490219977` |
| Windows Setup EXE | `db5c08eb3678d599130b7d8d7149ba7181b6b553c3af7cd4381416bfaa9365be` |
| macOS arm64 DMG | `6261c93d3b710d0e933c1f79876c67b3bee488e30c787bf3337ccb1037606f35` |
| macOS arm64 ZIP | `85154c6a5fe96fddfe0cc47417a30f7fbea38b581fed0f58462a29f88f2b6e36` |
| macOS x64 DMG | `580b5d8ef2aa4b4ab7d6af9e7a3190c056d9e7aa0363fb9f6059c72ac91765bb` |
| macOS x64 ZIP | `36a252015dcd1f820582f84186e6d4fa0bd2f50c244394b66e17fc96805ff95d` |

Reference: [GitHub Create a release token requirements](https://docs.github.com/en/rest/releases/releases#create-a-release).

## November 5 forwarded MacBook Air retest

The user forwarded a batch of original-reporter screenshots and text. The
SnakeSh Local Network explanation is visible, and System Settings > Privacy
& Security > Local Network lists two SnakeSh entries, both enabled. The
reporter says RDP opens again and making it fullscreen works, while the
pinwheel and interaction lag persist. This confirms the explanation renders
on a real Mac and that access is currently enabled in the displayed settings.
It does not show Apple's native consent alert, establish that the Settings
button rather than manual navigation opened the pane, or prove that a denied
permission caused the earlier connection failure. Duplicate entries do not
by themselves identify a conflict or two currently running app instances.
Fullscreen success does not establish window-edge resize or remote pixel
resolution changes. The earlier port probe is no longer the priority now that
the reporter can connect.

The pasted **17:29 / PID 13027** log matches the previously supplied November 1
excerpt, including the cancellation timestamp and context address. It is not
independent evidence of a new run or its installed dependency versions. Newer
screenshots show separate SDL processes: **16549** initializes the framebuffer
at **18:31:03.300** and logs `Signal Terminated: 15` at **18:32:21.705**;
**16871** initializes at **19:20:08.891** and logs `Network disconnect!` at
**19:22:13.533**. Both closed tabs display exit **131**. macOS explicitly says
the user forced `sdl-freerdp` to quit, and the reporter confirms a forced quit
during their clipboard test. The shutdown messages therefore do not establish
the cause of the preceding stall or a spontaneous crash. FreeRDP 3.32.2
`sdl_utils.hpp` defines `sdl::error::CONN_FAILED = 131`; the established-session
run loop can also produce that code after event handling/reconnect failure.
It must not be decoded as a shell's `128 + SIGQUIT` without process evidence.

The "clipboard disabled" experiment writes `ClipboardSharingEnabled=0` in
Apple's `com.apple.coreservices.useractivityd` preferences, then restores it
to **1**. This targets Apple's Continuity/Universal Clipboard preference; it
does not pass **`-clipboard`** to FreeRDP or disable the RDP `cliprdr` channel.
The report does not establish whether that undocumented preference change
took effect in the system service. It therefore does not rule out FreeRDP's
own clipboard handling. The screenshots do not unambiguously pair each PID
with a particular preference state. SnakeSh does not translate that Apple
preference into an RDP option; FreeRDP 3.32.2 defaults `RedirectClipboard` to
true and parses `-clipboard` into false. Its SDL clipboard callback can wait
for a remote data response on the main thread, leaving clipboard handling a
plausible hypothesis rather than a confirmed cause. The absence of `cliprdr`
from the visible dynamic-channel list is not proof it was disabled; it is a
static virtual channel.

Next evidence: reproduce the stall, select the live **sdl-freerdp** process
in Activity Monitor, and save **Sample Process** before force quitting.
Sample SnakeSh separately only if its own windows also hang. Capture the
current client path/version and, when installed through Homebrew, FreeRDP/SDL
versions. A main-thread pasteboard/clipboard wait, a renderer wait, or lock
contention would narrow different causes; an idle background network wait
alone would not. An optional standalone A/B comparison must change only
`+clipboard` to `-clipboard`, reproduce the symptom in the enabled baseline,
and keep display/audio/security settings constant within that pair.

As of November 5, Homebrew advertises **FreeRDP 3.33.0**, released upstream on
November 2 with further SDL/clipboard changes. The reporter's earlier known
version was **3.32.2**; the current installed version has not been supplied.
SnakeSh launches this external dependency. Updating through Homebrew is a
supported follow-up after capturing a baseline, but the release notes do not
establish a fix for this particular pinwheel. No remote server changes,
application-code fix, or new release are justified by these logs alone.

References: [FreeRDP 3.32.2 exit codes](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/SDL/SDL3/sdl_utils.hpp),
[SDL connection loop](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/SDL/SDL3/sdl_context.cpp),
[FreeRDP clipboard option](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/common/cmdline.c),
[SDL clipboard callback](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/SDL/SDL3/sdl_clip.cpp),
[Apple process sampling](https://support.apple.com/guide/activity-monitor/run-system-diagnostics-actmntr2225/mac),
[Apple Universal Clipboard](https://support.apple.com/guide/mac-help/copy-and-paste-between-devices-mchl70368996/mac),
[Homebrew FreeRDP](https://formulae.brew.sh/formula/freerdp),
[FreeRDP 3.33.0 release](https://www.freerdp.com/2026/11/02/3_33_0-release).

## November 12 process samples, received November 14

The reporter supplied a completed Homebrew upgrade log: FreeRDP
**3.32.2_1 -> 3.33.1** and SDL **3.4.18 -> 3.4.20**, with dependencies installed
under `/opt/homebrew`. They report smoother interaction afterward but a
persistent pinwheel, then manually close the viewer using its X button.
The new screenshot shows a separate process **45000**, framebuffer setup at
**16:47:49.541**, disconnect at **16:48:30.806**, and exit **145**. In the
3.33.1 SDL exit-code definitions, 145 is `CONNECT_CANCELLED`, consistent with
the reported manual close; the screenshot does not independently identify
that process's FreeRDP/SDL versions or the earlier stall's cause.

Both supplied process samples describe the same native **ARM64** process
**41568**, parent SnakeSh **41534**, launched **2026-11-12 16:37:38.509 -0500**.
They identify **macOS 27.0 (26A428)**, replacing the earlier reported 26.2
baseline for these captures. Crucially, both Binary Images lists contain
**libfreerdp-client3.3.32.2.dylib**, **libfreerdp3.3.32.2.dylib**, and
**libwinpr3.3.32.2.dylib**, with matching image UUIDs across the pair. These
are samples of a 3.32.2-library process, not proof of the upgraded 3.33.1
runtime. Their SDL image is versioned **0** and has a masked path, so its
exact release cannot be established from these reports. Installing an update
does not replace libraries already mapped by a running viewer. The upgrade's
completion is established by the installation log; the samples and the later
45000 session must be assessed separately.

The comparison sample at **16:42:03.021** records **2478** main-thread
observations in SDL's Cocoa event loop (`SDL_WaitEventTimeoutNS` -> Cocoa /
AppKit event wait). This is consistent with waiting for events, without by
itself proving what the user was experiencing at that instant.

The explicitly named **PINWHEEL** sample at **16:43:29.583** records **220 of
227** main-thread observations under `SdlContext::drawToWindows`, including
`SDL_RenderPresent_REAL` -> `METAL_RunCommandQueue` ->
`METAL_ActivateRenderCommandEncoder` -> `CAMetalLayer nextDrawable` ->
semaphore wait (**96** observations in that branch). Other observations are
in texture upload and Metal command handling. Neither sample contains
`ClipDataCb`, clipboard, or pasteboard stack frames. This materially shifts
the next test toward SDL/Metal rendering and drawable/presentation waits for
the captured process, rather than treating the previous clipboard hypothesis
as established. These sample counts are observations, not measured CPU
percentages, stall durations, or proof of an indefinite deadlock. The evidence
does not yet isolate an SDL bug from FreeRDP redraw behavior or Apple's
graphics/compositor handling, and does not assign the same wait to the later
reported 3.33.1 run.

The next controlled test is a fresh launch using SDL's **OpenGL** renderer.
Close the RDP viewer, fully quit SnakeSh (Cmd-Q), and confirm
`/opt/homebrew/bin/sdl-freerdp /version` reports the upgraded version. Compare
the usual profile under a fresh normal launch against a fresh launch with:

```sh
SDL_RENDER_DRIVER=opengl /Applications/SnakeSh.app/Contents/MacOS/SnakeSh
```

Keep clipboard, display, audio, credentials, and the remote host unchanged
within this comparison. Check the client output for **Created renderer:
opengl**. Fully quitting first matters because another SnakeSh invocation
can activate the existing instance instead of applying new environment
variables. A normal restart removes this one-launch override. If the alternate
renderer fails to initialize, that is an unavailable rendering path, not a
successful workaround. If lag persists, capture the newly launched process
during the stall so its actual libraries and backend can be checked.

FreeRDP 3.33.1's `SdlWindow` calls `SDL_CreateRenderer(window, nullptr)`, which
allows the renderer hint to select the backend. Existing SnakeSh launch
environment preparation was checked to preserve `SDL_RENDER_DRIVER=opengl`;
no application release is required for the experiment. Native Mac behavior
remains untested here. A successful device comparison could justify an
explicit Mac RDP renderer compatibility option; these files alone do not
justify changing the default renderer for all Macs.

Renderer-selection detail: a software renderer alone need not avoid Metal
window-surface presentation. SDL 3.4.20 Cocoa has no native framebuffer
callbacks, and disabling framebuffer acceleration can produce "Window
framebuffer support not available." The targeted OpenGL comparison avoids
claiming that a generic software-renderer setting guarantees a Metal-free
presentation path.

References: [SDL renderer hint](https://wiki.libsdl.org/SDL3/SDL_HINT_RENDER_DRIVER),
[FreeRDP 3.33.1 window creation](https://github.com/FreeRDP/FreeRDP/blob/3.33.1/client/SDL/SDL3/sdl_window.cpp),
[FreeRDP 3.33.1 exit codes](https://github.com/FreeRDP/FreeRDP/blob/3.33.1/client/SDL/SDL3/sdl_utils.hpp),
[Apple nextDrawable](https://developer.apple.com/documentation/quartzcore/cametallayer/nextdrawable()),
[SDL 3.4.20 renderer selection](https://github.com/libsdl-org/SDL/blob/release-3.4.20/src/render/SDL_render.c),
[SDL 3.4.20 window framebuffer](https://github.com/libsdl-org/SDL/blob/release-3.4.20/src/video/SDL_video.c),
[SDL 3.4.20 Cocoa video hooks](https://github.com/libsdl-org/SDL/blob/release-3.4.20/src/video/cocoa/SDL_cocoavideo.m).

## November 14 OpenGL comparison and parent/viewer samples

The reporter supplies `/opt/homebrew/bin/sdl-freerdp /version` output
identifying **3.33.1**, followed by the one-launch
`SDL_RENDER_DRIVER=opengl` command. They report that **windowed mode works**,
but **fullscreen still pinwheels**. This establishes a reported partial
workaround on that device, not a complete fix.

`Sample_of_SnakeSh_try_2.txt` captures **SnakeSh 48259**, launched
**17:52:00.949 -0500**, sampled **17:54:19.855 -0500** on **macOS 27.0
(26A428)**, native **ARM64**. Its main thread has **840 of 841** observations
in the Cocoa/CoreFoundation event wait, with one handling a Qt timer.
Other Python threads wait in `poll` and a pipe `read`; they are not the GUI
thread. This short capture shows no sustained SnakeSh main-thread blockage.
It cannot rule out intermittent stalls outside its interval. The app bundle's
0.0.0 metadata does not establish SnakeSh's runtime release version.

The accompanying `Sample_of_sdl-freerdp_opengl.txt` was also available through
this chat's bridge artifact list before its forwarded attachment message
arrived. Its exact SHA-256 is
`500bdda4fa5f8577f779a55e88095c32c23edbec01f7bb23af8ddda7a3c3cc05`;
multiple received copies have identical content. It captures **sdl-freerdp
48271**, parent **SnakeSh 48259**, launched **17:52:55.600 -0500**, sampled
**17:56:20.290 -0500**, on the same OS and native architecture. The header
identifies **3.33.1**, and the loaded client/core/WinPR library names all
identify **3.33.1**. Unlike the older samples, this confirms the updated
viewer runtime actually in use. The SDL image is versioned 0 with a masked
path, so the sample does not independently establish its exact SDL release.

The viewer main thread records **444** observations: **278** under
`SdlContext::drawToWindows`, **165** in the Cocoa event wait, and one elsewhere
in `sdl_run`. Of the drawing observations, **225** follow
`SDL_RenderPresent_REAL` -> `Cocoa_GL_SwapWindow` ->
`NSOpenGLContext flushBuffer` -> `CGLFlushDrawable`. Those branch through
Apple's `AppleMetalOpenGLRenderer`, GPU submission, and SkyLight/WindowServer
surface flushing; **88** observations reach `_CGSWindowIsOrderedIn` and a
Mach-message wait. Other observations perform GL drawing and texture updates.
This confirms an active **OpenGL** presentation path. Apple still implements
parts of that path through its Metal-backed driver, so OpenGL does not imply
that all Metal-related code has been avoided.

There are no clipboard/pasteboard or `ClipDataCb` frames in this viewer sample.
Its RDP thread alternates ordinary event waiting with received desktop bitmap
processing. Combined with the reported windowed/fullscreen difference, the
samples strengthen the graphics/presentation lead. They do not isolate a
FreeRDP, SDL, or macOS defect, measure stall duration/CPU usage, or prove a
permanent deadlock: the viewer also returns to its event loop. The sample
itself does not encode whether the window was fullscreen at capture time.

**Right Shift + M minimizes the viewer** as expected for the SDL client's
built-in shortcut. Right Shift + Return is its default fullscreen toggle.
FreeRDP handles these window shortcuts before forwarding remapped keys to the
remote desktop; this is not evidence of a SnakeSh Command-key remap regression.

The next single-variable test retains OpenGL and disables SDL's native macOS
fullscreen Spaces integration for one fresh launch:

```sh
SDL_RENDER_DRIVER=opengl SDL_VIDEO_MAC_FULLSCREEN_SPACES=0 /Applications/SnakeSh.app/Contents/MacOS/SnakeSh
```

Close all RDP viewers and fully quit SnakeSh first. Enter fullscreen with
**Right Shift + Return**, since disabling Spaces can remove the green native
fullscreen control; alternatively use the saved profile's Fullscreen option.
Keep the same remote computer, clipboard, audio, and resolution. Record
whether fullscreen lag changes and whether returning to windowed mode removes
it. This tests the native fullscreen presentation route; it is not a verified
fix. If it persists, a sample specifically captured during this new fullscreen
stall would distinguish its wait from the existing OpenGL capture. A normal
restart after quitting removes both environment overrides. No default renderer,
clipboard policy, or remote-server package change has been made.

The earlier outgoing `snakesh-mac-fullscreen-capture-2026-11-14.txt` requested
a viewer sample before that attachment's chat message arrived. That request
is superseded by the received viewer capture and the updated Spaces test;
there is no need to repeat the same capture simply to confirm OpenGL.

References: [FreeRDP 3.33.1 SDL input handling](https://github.com/FreeRDP/FreeRDP/blob/3.33.1/client/SDL/SDL3/sdl_input.cpp),
[SDL fullscreen Spaces hint](https://wiki.libsdl.org/SDL3/SDL_HINT_VIDEO_MAC_FULLSCREEN_SPACES),
[SDL 3.4.20 Cocoa hint initialization](https://github.com/libsdl-org/SDL/blob/release-3.4.20/src/video/cocoa/SDL_cocoavideo.m).

## November 15 fullscreen Spaces and resolution/load comparison

The reporter ran the exact one-launch `SDL_RENDER_DRIVER=opengl
SDL_VIDEO_MAC_FULLSCREEN_SPACES=0` comparison. They report that a normal
window mostly works and can be enlarged by dragging. **Right Shift + Return
still produces pinwheel/lag**, and toggling back to windowed mode restores
better interaction. A remote desktop explicitly set to **2560x1440** is much
slower than the **1280x800** base desktop enlarged locally; the latter still
has some lag when made larger. Clicks at the larger desktop reportedly take
**1–2 seconds**, with visibly delayed refresh/animation. This means the
Spaces override has not solved the problem and the symptoms are not confined
to fullscreen. Remote resolution and local presentation size must be varied
independently. A 2560x1440 desktop contains **3.6 times** the pixels of
1280x800; this is pixel count, not a measured multiplier for bandwidth or lag.

Two fresh native ARM64 macOS **27.0 (26A428)** viewer samples identify
FreeRDP **3.33.1** in their headers and client/core/WinPR images. Both have
parent **SnakeSh 51173**. Their FreeRDP and SDL binary UUIDs match the prior
OpenGL sample. The SDL image's version remains 0, so it still does not
independently establish the exact SDL release.

- `Sample_of_sdl-freerdp_Right-shift_and_return.txt`: viewer **51176**,
  launched **06:56:41.014 -0500**, captured **06:59:04.086 -0500**.
  All **766** main-thread observations wait in SDL/Cocoa's ordinary event
  loop. All **766** RDP-thread observations wait for event handles in `poll`.
  There is no continuous render, clipboard, or fullscreen-transition lock in
  this capture. Its name and reported behavior do not establish which part
  of the fullscreen/return sequence the short capture covers. This limits
  the previous graphics-blocking hypothesis: no such block appears here.
- `Sample_of_sdl-freerdp_Manual_scailed_larger_with_pinwheel.txt`: viewer
  **51321**, launched **07:16:51.621 -0500**, captured **07:17:52.339 -0500**.
  Of **515** main-thread observations, **389** are under SDL's event-wait
  branch, **120** draw through `SdlContext::drawToWindows`, and six follow
  other event-loop work. Within drawing, **95** observations present through
  `Cocoa_GL_SwapWindow` / `CGLFlushDrawable`; **33** reach the SkyLight
  `_CGSWindowIsOrderedIn` Mach-message wait. Other observations upload GL
  textures, with Apple's Metal-backed OpenGL renderer below them. The RDP
  thread spends **461** observations in its event-wait branch, **53** in
  receive/desktop update processing (bitmap and bulk decompression), and
  one checking abort state. The sample confirms rendering and incoming
  desktop processing continue; it does not show a permanent deadlock or
  establish server, network, decoder, or GPU saturation.

Neither new sample contains clipboard/pasteboard or `ClipDataCb` frames.
These brief observations do not rule out intermittent waits or prove where
a reported 1–2 second action delay was spent. Do not describe the fullscreen
sample as proof of a graphics deadlock or dismiss the reproduced lag because
its main thread happened to wait for events. The strongest current device
observation is the repeatable dependence on desktop/presentation size.

### Separate fixed-resolution launch correction

Review found that `build_rdp_command()` omitted `/size:WxH` for a macOS SDL
profile starting with Fullscreen checked, even when the user selected a
fixed remote resolution. It kept `/smart-sizing`, leaving the remote size
at client defaults. The local correction preserves the explicit size for
macOS SDL fullscreen starts as well as windowed starts; Default and Auto
retain their previous behavior, as do Windows and X11 launch paths.

For example, a Mac SDL profile set to **1280x800 + Fullscreen** now requests
`/f /size:1280x800 /smart-sizing`, rather than `/f /smart-sizing`. FreeRDP's
SDL pre-connect path retains explicit desktop dimensions with smart sizing,
while the local fullscreen window can use the display size. This fixes an
application launch inconsistency. It does **not** explain or claim to fix
the tester's lag when toggling fullscreen after connecting windowed, because
that path already received the fixed size when one was explicitly selected.

Affected tests passed **137 checks**, including **10** real FreeRDP parser
cases exercising fixed 1280x800, 1920x1080 and 2560x1440 desktop dimensions,
fullscreen state, smart sizing, Auto fallback, and Command-key remapping.
The local parser SDK is repository FreeRDP **3.32.2** extracted under `/tmp`
without installing host packages or changing the remote computer. This is
Linux parser validation, not native Mac UI/performance validation. The
subsequent v1.91 release preparation is recorded in `docs/RELEASE_1.91.md`;
affected-device validation is still needed for this correction.

### Workaround and next comparison

Use the reported OpenGL **windowed**, explicitly fixed **1280x800** profile
and enlarge it moderately. Do not describe arbitrary enlargement, 2560x1440,
or fullscreen as solved. Auto can change the remote desktop workload when
resizing and is not interchangeable with local smart sizing.

The next comparison runs the same installed **sdl-freerdp** directly from
Terminal with a fixed 1280x800 desktop and `/smart-sizing`, keeping the same
renderer/Spaces settings and host. First quit SnakeSh completely. Compare
windowed -> larger window -> fullscreen -> windowed at that fixed remote
size, then optionally compare a fresh 2560x1440 connection at the same local
window size. Confirm the actual desktop dimensions in the remote system's
Display settings. If standalone shows the same lag, it reproduces without
SnakeSh and can be taken upstream with these samples; otherwise compare the
actual launch arguments/environment. This comparison has not yet been run.
No additional graphics, codec, clipboard, network, or server flags have been
changed speculatively.

References: [FreeRDP 3.33.1 SDL desktop sizing](https://github.com/FreeRDP/FreeRDP/blob/3.33.1/client/SDL/SDL3/sdl_context.cpp),
[FreeRDP 3.33.1 SDL scaling/presentation](https://github.com/FreeRDP/FreeRDP/blob/3.33.1/client/SDL/SDL3/sdl_window.cpp),
[SDL fullscreen Spaces hint](https://wiki.libsdl.org/SDL3/SDL_HINT_VIDEO_MAC_FULLSCREEN_SPACES).

## Release 1.88 preparation and known regression

The 1.88 source includes one-attempt smart-sizing recovery for the recognized
FreeRDP resize failure, native macOS SDL window scaling, and Command-key
copy/paste corrections in SnakeSh and the native SDL RDP viewer. Local affected
checks passed 551 tests and 70 subtests. Another 23 version, packaging,
bootstrap, and Qt runtime checks passed with VERSION set to 1.88.
The 1.88 package builds completed, but native Mac testing revealed the parser
regression corrected in 1.89. The original persistent pinwheel report remains
unresolved.

## Findings

| Report | Finding | Confidence |
| --- | --- | --- |
| Mac RDP opens detached and cannot attach | The session editor and `MainWindow._open_rdp_tab` explicitly force detached RDP on macOS and Windows. Only Linux has a supported embedded RDP path. | Confirmed implementation behavior, independent of recent OS updates. |
| Mac desktop is small and does not resize | Detached FreeRDP Auto does not provide an initial `/size`; it leaves initial geometry to the external client. More significantly, the fullscreen branch suppresses `/dynamic-resolution` even when Auto is selected. Default/fixed resolution also does not request dynamic resolution. | Confirmed launch behavior; the reported windowed SDL failure still needs native reproduction. |
| Mac clicks/paste cause delays and a pinwheel | The detached SDL process owns these input/drawing operations. The November 12 PINWHEEL sample places its main thread predominantly in SDL/Metal drawing, including drawable waits. On November 14, OpenGL works windowed but fullscreen still pinwheels. The new 3.33.1 viewer sample confirms OpenGL presentation through Apple graphics/WindowServer; the parent SnakeSh sample is in ordinary event waiting. | Spaces=0 did not solve the lag. November 15 shows resolution/size dependence, with no continuous graphics block in the new captures. A direct fixed-size FreeRDP comparison is next; exact upstream cause and a complete fix remain unverified. |
| Windows Auto does not change desktop resolution despite Smart sizing being checked | SnakeSh launches `mstsc.exe`. Microsoft's feature matrix lists smart sizing as supported and dynamic resolution as unsupported for MSTSC. Smart sizing scales existing desktop pixels; it does not cause remote applications to reflow at a new resolution. | Confirmed client limitation. |
| Windows Auto behaves differently with fullscreen selected | The generated profile omits both sizing settings for a fullscreen launch. For a windowed Auto launch it writes both `smart sizing:i:1` and the unsupported `dynamic resolution:i:1`. | Confirmed generated-profile defect/unsupported option. |

Source locations: `src/snakesh/protocols/rdp.py`,
`src/snakesh/ui/session_editor.py`, and `src/snakesh/ui/main_window.py`.
`src/snakesh/ui/clipboard.py` still performs synchronous Qt clipboard operations
on macOS, which is a separate potential wait when using SnakeSh's own terminal
copy/paste. Those functions are not the detached SDL viewer's clipboard path.

The SDL client's window constructor initially sets the resizable property,
but `postConnect` calls `setResizeable(false)` afterward. That helper uses
`(DynamicResolutionUpdate && enable) || SmartSizing`: Default/fixed launches
with neither mode remain non-resizable. Auto can regain resizing through the
display-control channel; loading `disp` alone does not establish that the
server sent its capabilities. The constructor also divides initial physical
geometry by display content scale; small initial windows on Retina displays
must be assessed with the actual client version and geometry. Detachment alone
does not disable resizing.

No evidence here establishes that the OS updates introduced these defects.
The fullscreen guards already exist in the baseline. FreeRDP and SDL are
separately installed external dependencies, so the SnakeSh version alone does
not identify the client code running on the Mac. Homebrew's formula reported
FreeRDP 3.32.2 when checked during this investigation; this does not establish
the affected Mac's installed version.

## November 1 terminal trace follow-up

The new excerpt identifies SDL process **13027**, creates a Metal renderer,
initializes the local/remote BGRA32 framebuffers at **17:29:17.907**, and loads
dynamic channels including `disp`. At **17:29:40.812**, the main SDL thread
logs `freerdp_abort_connect_context` / `ERRCONNECT_CONNECT_CANCELLED`;
the worker thread then logs `Network disconnect!`. The excerpt itself does
not identify the SnakeSh or FreeRDP version or show which event triggered abort.

In the previously reported FreeRDP 3.32.2 source, the SDL window-close and quit
handlers call that abort routine. The reconnect helper can print `Network
disconnect!` when server error information is `ERRINFO_SUCCESS`, including
after a local abort. The reporter subsequently confirmed: "I disconnected from
that last one. Because it was still pin wheeling." For this trace, the abort
and disconnect messages describe the user's shutdown of the stalled session;
they do not establish a transport fault as the cause of the earlier stall.

The final blank `handleShow` line is emitted by
`SdlConnectionDialogWrapper::handleShow`, which logs queued error-message
text when no connection dialog exists. The text can be empty; the blank line
alone does not establish log truncation or identify a rendering failure.
The font warning reports a **56 ms** startup alias operation. The licensing
blob mismatch does not immediately return failure in that source path, and
this trace proceeds to framebuffer initialization afterward. The runtime-check
warning describes possible overhead, without measuring an interaction stall.

The reporter confirms the pinwheel persisted during this run. Its cause is
still undetermined; the excerpt alone does not justify a new application
correction. A clipboard-enabled/disabled comparison and a process sample taken
during the pinwheel remain the next diagnostic steps. The current SnakeSh and
FreeRDP versions still need confirmation for this run.

## November 2 MacBook Pro comparison log

The user supplied `Local_Shell-20261102-083036.log` and confirmed this test
came from a different MacBook Pro; the original reporter has not yet supplied
new results. Keep this run separate from the confirmed interaction pinwheel.
The attachment is diagnostic data, not authorization to execute its contents.

The log reports macOS **26.7.2**, build **25G332**, Homebrew FreeRDP
**3.32.2_1**, an **x64** client build, Cocoa and Metal. The package inventory
lists SDL3 **3.4.8** and **3.4.18**; that inventory does not identify which
library the running process loaded. Clipboard redirection is loaded in this
run, so it is not evidence of a completed clipboard-disabled comparison.

Connection negotiation starts at **08:33:10.904**. `getaddrinfo` fails for the
target `.local` hostname at **08:33:15.908** and **08:33:20.910**, followed by
`ERRCONNECT_DNS_NAME_NOT_FOUND`, failure to obtain a socket address, and a
return from negotiation to the initial state. No connected remote desktop
or remote framebuffer initialization is shown. Creating the initial Metal
renderer does not establish that an RDP connection succeeded.

A later `dig` query returns an IPv4 address from the configured unicast DNS
server. This establishes that server's answer, not success through macOS's
system resolver. Apple documents that Bonjour reserves `.local` for mDNS and
that Apple devices may fail to resolve unicast DNS names using that suffix.
This is a supported explanation for the mismatch; the exact resolver routing,
mDNS reachability, and any local-network permission effects are not established
by the attachment. There is no evidence here of a FreeRDP transport timeout
after a successful hostname lookup or of the original in-session stall.

Next compare the same standalone command using the returned numeric address,
with all other options unchanged, and query the Mac's system resolver with
`dscacheutil -q host -a name HOST.local`. These checks must run on that Mac;
the log does not establish that its target address or RDP port is reachable.
Once a desktop connects, the clipboard comparison and process sample can test
whether this second machine also exhibits the interaction problem.

## November 2 macOS shortcut check and additional user observation

The user reports only a roughly one-second pinwheel immediately after the
initial connection on their MacBook Pro; it then disappears. They have not
reproduced the other user's repeated interaction stalls. This is a separate
observation, with no process sample identifying the short startup wait.

The requested shortcut audit found a concrete issue in SnakeSh's own terminal
view. [Qt maps Command to ControlModifier and physical Control to MetaModifier
on macOS](https://doc.qt.io/qt-6/qkeysequence.html). The terminal's explicit
plain-Ctrl-C exception treated Command-C as an interrupt even with selected
text, and the terminal input mapper did not translate physical Control back
to control characters. Corrected both paths: Command-C copies the selection
or does nothing when there is none; physical Control-C still interrupts.
Other Command combinations stay available to local shortcuts rather than
becoming terminal control characters. Command-V and the command bar's
copy/cut/paste handlers already use Qt standard keys; regression coverage
verifies those paths and multiline paste approval/cancellation.

The new shortcut checks initially failed **19 cases** and passed **13**;
after the correction all **32** passed under Qt's documented macOS modifier
representation. A native Mac keyboard check remains required.

The detached SDL RDP window owns its separate input path. In the inspected
FreeRDP **3.32.2** `sdl_input.cpp`, SDL LGUI/RGUI map to remote LWIN/RWIN, and
the configurable FreeRDP remapping table is applied before key dispatch.
Changing SnakeSh's Qt shortcuts does not by itself change that viewer's
keyboard mapping or establish a fix for its clipboard/event-loop pinwheel.
The user explicitly confirmed they want Command mapped to Ctrl in both
SnakeSh and the detached RDP viewer, including other remote Command
combinations. Native macOS SDL launches now include
`/kbd:remap:0x15b=0x1d,remap:0x15c=0x11d`: left/right Command's LWIN/RWIN
scancodes map to left/right Control, retaining right Control's extended bit.
FreeRDP's remap is applied before both press and release dispatch. This
preserves physical Control and adds no shell/key-injection helper. The
command-line syntax, RDP scancode constants, and SDL dispatch were checked
against the pinned upstream 3.32.2 sources. Linux, Windows, and the legacy
macOS X11 client keep their existing keyboard arguments.

The remote application still determines what Ctrl combinations do. For
example, a Linux terminal with Ctrl-Shift-C/V clipboard bindings requires
Command-Shift-C/V through this mapping; ordinary Windows/Linux GUI editors
use Command-C/V as remote Ctrl-C/V. These source changes require a packaged
native Mac check for both Command keys and subsequent physical Control-C.

## November 2 connected resize follow-up

The user confirmed the numeric-address connection succeeds on the second
MacBook Pro. They report that SnakeSh's session offers no edge resizing or
maximize action, whereas the standalone dynamic-resolution command permits a
resize attempt and then reports an error and exits. This is a newly reported
connected resize failure, separate from the original MacBook Air pinwheel.

The reattached `Local_Shell-20261102-083036.log` is byte-for-byte identical to
the earlier DNS-failure attachment (SHA-256
`9018dd3e992260bfcb2a1a011ed1660cd303eb10034e423828db298e9e1514c9`).
It contains no successful numeric-address connection or resize-triggered exit.
The user then confirmed the SnakeSh session used a fixed resolution; switching
it to Auto allowed a resize attempt that exited like the standalone command.
The newer `Local_Shell-20261102-085216.log` supplies that failure sequence below.

Source inspection found an additional launch defect: macOS SDL Default/fixed
sessions emitted neither smart sizing nor dynamic resolution. The SDL
post-connect gate consequently disables resizing. The local correction emits
`/smart-sizing` for native macOS SDL Default/fixed sessions, retaining their
existing desktop geometry policy and scaling into the resized local window.
Auto retains `/dynamic-resolution`; the incompatible modes are never combined.
X11 detached clients retain their previous launch options. This correction is
in the working tree after v1.87, not its published packages.

Before the correction, eight new Default/fixed SDL command-boundary cases
failed and 14 display cases passed. Afterward all 22 display cases passed;
the affected display, RDP launcher, Linux viewer behavior, certificate and
Windows helper suite passed **53 tests**, with **1 native Windows test skipped**
on Linux. A second run passed **58 session-editor, remote-viewer and Help
checks**, excluding the existing built-help/source comparison because the
checkout's old packages are not this source revision. Total affected checks:
**111 passed, 1 native Windows skip, 1 built-bundle comparison deselected**.
These prove the source launch options and UI integration, not native Mac GUI
behavior or a fix for the standalone dynamic-resolution exit.

### Confirmed dynamic-resolution abort

The newer log (2,588 lines, SHA-256
`2aaa4b222c0ffb800e665758ca0b70b6a8e5c47674c9e294a249156deeeaaf91`)
shows FreeRDP **3.32.2** completing initial activation at **08:52:35.510**.
The server supplies display-control capabilities at **08:52:35.552**, and SDL
explicitly enables resizing. Initial 1024x768 desktop pixels occupy a 512x384
logical window on the 2x display, confirming the small initial geometry in
this run without establishing the original reporter's settings.

At **08:52:45.604**, SDL sends a monitor layout for **1884x1218** pixels.
The server sends Deactivate All and Demand Active; FreeRDP confirms the new
size at **08:52:45.617**. During reactivation, FreeRDP enters
`CONNECTION_STATE_FINALIZATION_CLIENT_SYNC` and waits for
`FINALIZE_SC_SYNCHRONIZE_PDU`. Pointer messages arrive without that flag;
at **08:52:45.659** a graphics Update PDU arrives while the client remains
in that state. `update_recv` requires `CONNECTION_STATE_ACTIVE`, returns
failure, and the receive loop terminates with `STATE_RUN_FAILED`. The later
`Network disconnect!` and blank dialog line follow that protocol failure.

This localizes the Auto/standalone exit to FreeRDP/server resize reactivation,
independently of the SnakeSh window launcher. It is not evidence of an SDL
Metal renderer crash or a broken TCP connection. The user subsequently
identified the server as a **Linux Mint xrdp desktop**, and confirmed
**xrdp 0.9.24** through Matrix. The subsequent package inventory confirmed
Mint **22.3 (Zena)** / Ubuntu **Noble**, **xrdp 0.9.24-4**, and
**xorgxrdp 1:0.9.19-1** on the affected Mint server.
The trace does not establish which endpoint must be corrected, and the server
version alone does not establish that a particular upgrade fixes this sequence.
An old FreeRDP/xrdp state-machine report (#8458, fixed in 2022) has a similar
failure class, but does not establish that the historical fix resolves the
current 3.32.2 run.

A useful native comparison replaces `/dynamic-resolution` with
`/size:1920x1080 /smart-sizing` in the same numeric-address command. Smart
sizing scales a fixed remote desktop locally without requesting a new remote
resolution while dragging the window.

### Successful native smart-sizing comparison

The user tested that comparison and confirmed: "That works, but the resolution
doesn't change due to no dynamic, but it does resize." This is expected local
scaling, not a claim of working live remote-resolution updates.
`Local_Shell-20261102-090027.log` shows a 1920x1080 initial desktop/window,
followed by local pixel-size events for **2700x1386** at **09:01:04.857**,
**2880x1800** at **09:01:11.733**, and **2700x1386** at **09:01:17.668**.
Graphics traffic continues. There is no resize reactivation failure; the
session remains active until `SDL_EVENT_WINDOW_CLOSE_REQUESTED` at
**09:01:23.761**, followed by the usual local cancellation/cleanup messages.

This confirms the standalone smart-sizing workaround on this MacBook Pro
against the Mint/xrdp desktop. The source correction emits that mode for
native macOS SDL Default/fixed sessions, but a packaged SnakeSh build of the
correction has not yet been tested. Auto still requests remote resizing and
retains the confirmed reactivation incompatibility on this endpoint. The
original MacBook Air's interaction pinwheel remains a separate validation
item; no new sample or clipboard comparison has arrived from that reporter.

### Targeted server-side fix identified

Follow-up source comparison identifies an upstream correction matching the
failure mechanism: [xrdp PR 2929](https://github.com/neutrinolabs/xrdp/pull/2929),
the resize state-machine changes merged in January 2024. The report explicitly
describes the same `FINALIZATION_CLIENT_SYNC` warnings when graphics are sent
between `libxrdp_reset()` and the server Synchronize PDU. Its changes suppress
module output and wait for deactivation/reactivation before sending graphics
and pointer updates.

Upstream **v0.9.24** `libxrdp_reset()` sends Deactivate All and Demand Active,
then re-enables application processing without this output-suppression gate.
Its resize path immediately resets caches, loads pointers and resizes the
screen/backend. In **v0.10.0**, **v0.10.5**, and **v0.10.6.1**, the gate uses
`XSO_REASON_DEACTIVATE_REACTIVATE`; output is restored at the end of the
Font List/Font Map handshake, and the resize state machine waits for the
core-reset callback before reloading pointers and advancing.

FreeRDP's rejecting `update_recv()` active-state check was introduced by
commit `90bb49c576e4a5473da673c76c60ceea661c4ae2` in the **3.32.0** release
line; it is present in 3.32.1/3.32.2 and absent from the inspected 3.31.0 and
3.30.0 versions. This supports a concrete explanation for newer clients
exposing the older server's early graphics. It does not establish which
package or OS update changed the original reporter's installation.

The targeted remedy to validate is a Mint-compatible **xrdp 0.10.x** package
set containing that correction, with a compatible **xorgxrdp** package. The
upstream stable releases checked on November 2 are **xrdp 0.10.6.1** and
**xorgxrdp 0.10.5**. Both release versions are recommendations for a controlled
updated-server test, not evidence that this particular host has been upgraded
or its Auto behavior verified afterward. Keep the same FreeRDP 3.32.2 client
and `/dynamic-resolution` for that comparison.

The supplied `apt-cache policy` output shows the installed versions are also
the candidates in that host's current package lists. A subsequent check of
Ubuntu's official index found **0.9.24-4ubuntu0.1** in `noble-updates`. Its
[changelog](https://changelogs.ubuntu.com/changelogs/pool/universe/x/xrdp/xrdp_0.9.24-4ubuntu0.1/changelog)
changes certificate/symlink handling in the package's post-install script;
it does not list a resize state-machine backport. Refreshing the server's
repository lists is appropriate, but a newer package revision alone does
not establish that the Auto failure is corrected.

The user subsequently reported **0.9.24-4ubuntu0.1** as the repository update
version, then confirmed Auto resizing still fails after that update. No new
trace was supplied for the updated server. The existing trace remains the
evidence for the precise reactivation mechanism.

### Client recovery requested after the repository retest

Added a FreeRDP recovery path for Linux/macOS Auto connections. It tracks
core RDP state messages as output arrives, retaining only a bounded output
tail. The state monitor requires an established ACTIVE desktop, a subsequent
Demand Active reactivation, the specific `update_recv` active-state guard
failure during finalization, and `STATE_RUN_FAILED`. A positive client exit
with those events permits one reconnect attempt using smart sizing. Initial
login failures, normal exits, signal termination, and user cancellation do
not qualify. A successful later activation or a new negotiation clears stale
failure state. Output must finish draining before classification; a three
second drain timeout reports the original exit without recovery.

The existing launcher builders supply credentials and security options for
the reconnect. Recovery refuses to start if the destination, client, user,
or other connection/security options changed while it was being prepared.
Only dynamic resolution is replaced with smart sizing; those
modes are never combined. Existing initial sizes, parent-window IDs, and
fullscreen preferences remain applicable. A windowed fallback with no explicit
size starts at 1920x1080, matching the successful native workaround. A queued
retry is canceled by tab shutdown or a manual replacement launch.

FreeRDP closes the original process before SnakeSh can act. This is a
controlled reconnect, not an in-place change to the failed client's display
mode. A modeless popup appears once for that connection, explains smart
sizing and possible repository-supported fixes, and reports launch failure
if the retry cannot start. The tab retains a fallback hint. The saved Auto
preference stays applicable to a new connection. No remote server change is
required for this recovery.

The monitor recognizes the supplied 08:52 failed-resize trace and rejects the
09:00 successful smart-sizing trace. Automated checks cover chunked/large
output, credential/security preservation, cancellation, close/manual-launch
races, failed retries, Linux parent-window recovery, and a real child-process
replay with a live Qt heartbeat. These validate source behavior; the new
recovery still needs a packaged native Mac test against the affected server.

Final affected-source checks passed **551 tests and 70 subtests**, with one
native Windows-only check skipped and the old built-bundle Help comparison
deselected. This includes the new resize recovery tests, all existing RDP
launcher/certificate/platform checks, macOS clipboard shortcuts, terminal trace
replays, remote viewer and session-editor tests, Help rendering, and the
complete tab workspace suite. The checks used the
pinned PySide6 6.11.2 environment, Qt offscreen, isolated application data, and
the null keyring backend. These changes first shipped in 1.88 and remain in
1.89 with the parser correction and native connection/copy-paste confirmation
above. The recovery popup still needs its own native confirmation.

### Noble package candidates, November 2

Locally rebuilt the pinned Debian sources **xrdp 0.10.6.1-3** and
**xorgxrdp 1:0.10.5-2** against an isolated Ubuntu Noble amd64 filesystem.
The local binary versions append `+snakesh.noble1`; upstream code and Debian
maintainer scripts are retained. No additional repository is installed.
Original source archives, local changelogs, build metadata, checksums, and
the exact older Noble rollback packages accompany the candidates.

Both package builds completed. In the isolated Noble filesystem, installed
the user's exact older pair, upgraded both packages to the candidates, and
checked APT's downgrade plan. Package configuration completed, `dpkg --audit`
was empty, the xrdp/sesman executables reported **0.10.6.1**, and their shared
libraries resolved. The driver targets Noble's Xorg video ABI 25 / input ABI
24. Service starts were blocked with `policy-rc.d` throughout these checks.
These are packaging/dependency checks; Debian's build rules skip upstream
tests. No live RDP connection or macOS test was performed with the new server.

This development workspace is a separate host. The affected server's packages,
services, and configuration were not changed by this work. The user subsequently
declined non-repository packages on the remote Mint server. These local
candidates are retained as investigation artifacts; installing them is not
the accepted next step. The user checked official updates and reported the
**0.9.24-4ubuntu0.1** revision.
Any server update used for validation must come from the user's accepted
repositories. `apt update` refreshes lists; it does not install upgrades.
The repository update still failed the user's Auto retest. The next native
gate is the packaged client recovery against that updated server from the
same MacBook Pro. Fixed resolution with smart sizing remains the confirmed
local scaling workaround. The separate original MacBook Air pinwheel report
remains unresolved.

## Local corrections

- Keep `/dynamic-resolution` when Auto and fullscreen are both selected for
  FreeRDP. Fullscreen and resize policy are independent. Linux embedding still
  suppresses `/f`, and smart sizing is never combined with dynamic resolution.
- Enable Windows Auto's `smart sizing:i:1` for both windowed and fullscreen
  launches, including a subsequent return from fullscreen to a window. Remove
  the unsupported MSTSC dynamic-resolution property.
- For native macOS SDL sessions using Default/fixed resolution, enable smart
  sizing so a fixed remote desktop can scale into a resized local window.

These changes repair launch configuration. They do not add live resolution
changes to MSTSC, seed a larger initial Mac desktop, or establish that the Mac
pinwheel or reported windowed resize failure is fixed. True Windows dynamic
resolution requires a different client/backend or a supported hosted RDP
control with display-update handling. macOS in-tab support would likewise
require a new native embedding integration.

## Native checks still needed

November 14 update: the requested process samples have arrived. Prioritize
the fresh-client/OpenGL comparison described above; the broader baseline
checks below need not all be repeated, and the earlier clipboard theory is
not a confirmed diagnosis.

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
Native Mac/Windows GUI validation remains outstanding.
[Release v1.85](https://github.com/williamruckman/SnakeSh/releases/tag/v1.85)
is published. [GitHub build 40892252208](https://github.com/williamruckman/SnakeSh/actions/runs/40892252208)
passed the full source suite, all four platform package jobs, and release asset
attachment. All six packages match the published manifest and GitHub's SHA256
metadata. The downloaded Linux AppImage additionally passed byte-hash,
bundled-version 1.85, and frozen `--help` startup checks. This does not close
the affected-device validation gates described above.

## Primary references checked October 29, November 1 and November 2, 2026

- [Microsoft client feature matrix: MSTSC display support](https://learn.microsoft.com/en-us/previous-versions/remote-desktop-client/compare-remote-desktop-clients#display)
- [Microsoft RDP display properties](https://learn.microsoft.com/en-us/azure/virtual-desktop/rdp-properties#display-settings)
- [FreeRDP 3.32.2 SDL window creation](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/SDL/SDL3/sdl_window.cpp)
- [FreeRDP 3.32.2 SDL close handling](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/SDL/SDL3/sdl_context.cpp)
- [FreeRDP 3.32.2 reconnect logging](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/common/client.c)
- [FreeRDP 3.32.2 dialog logging](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/SDL/SDL3/dialogs/sdl_connection_dialog_wrapper.cpp)
- [FreeRDP 3.32.2 licensing blob parsing](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/libfreerdp/core/license.c)
- [FreeRDP 3.32.2 graphics update state requirement](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/libfreerdp/core/update.c)
- [FreeRDP 3.32.2 activation state handling](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/libfreerdp/core/rdp.c)
- [Historical resize state-machine failure, issue 8458](https://github.com/FreeRDP/FreeRDP/issues/8458)
- [xrdp resize output suppression and handshake sequencing, PR 2929](https://github.com/neutrinolabs/xrdp/pull/2929)
- [xrdp 0.10.6.1 stable release](https://github.com/neutrinolabs/xrdp/releases/tag/v0.10.6.1)
- [xorgxrdp 0.10.5 stable release](https://github.com/neutrinolabs/xorgxrdp/releases/tag/v0.10.5)
- [Ubuntu 24.04 base xrdp package version](https://packages.ubuntu.com/noble/xrdp)
- [FreeRDP 3.32.2 clipboard response wait](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/SDL/SDL3/sdl_clip.cpp)
- [SDL paste timeout report, FreeRDP issue 11445](https://github.com/FreeRDP/FreeRDP/issues/11445)
- [Merged SDL fullscreen desktop renegotiation, PR 13682](https://github.com/FreeRDP/FreeRDP/pull/13682)
- [FreeRDP 3.32.2 Remmina latency report, issue 13875](https://github.com/FreeRDP/FreeRDP/issues/13875)
- [Homebrew FreeRDP formula metadata](https://formulae.brew.sh/api/formula/freerdp.json)
- [Apple: internal .local domains and Bonjour resolution](https://support.apple.com/en-euro/101903)
