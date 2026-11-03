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
| Mac clicks/paste cause delays and a pinwheel | The detached SDL process owns these input, drawing, and clipboard operations. SnakeSh does not forward each click or paste. SDL FreeRDP's clipboard callback can synchronously wait for a remote data response. A stall there could also prevent window resize events from being handled. | Plausible clipboard/event-loop diagnosis; no affected-machine stack sample or log confirms the cause yet. |
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
