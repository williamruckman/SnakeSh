# SnakeSh 1.91

SFTP now supports moving selected remote files and folders into another folder
in the same remote pane, including its folder navigation list. Every drop asks
for confirmation. Moves use server-side rename operations and stop on existing
destination names. Cancelling an operation preserves completed moves and leaves
the remaining items at their original paths; the remote listing refreshes.

F5 refreshes both SFTP panes. On macOS, Command-R does the same; keyboards using
media keys may require Fn-F5 to send F5.

Settings > Terminal includes an option to close shell tabs when their sessions
end. It defaults to off. Saved SSH, Telnet, and Serial sessions can inherit the
global setting, keep the tab open, or close it when the session ends. The
explicit Disconnect action continues to keep the tab open for reconnecting.

Mac native SDL RDP sessions now pass the selected fixed desktop resolution
when starting fullscreen, allowing the client to scale that desktop to the
screen. Previously the fullscreen launch omitted the selected size.

The reported Mac RDP pinwheel and lag at larger window/desktop sizes remain
under investigation. The fullscreen sizing correction is separate from that
unresolved performance issue. OpenGL with a fixed 1280x800 desktop in a moderately
enlarged window is the affected tester's current workaround; fullscreen and
higher-resolution performance are not claimed fixed.

Local release validation passed 1,465 tests and 205 subtests across the source
suite and fresh-bundle checks. Six Windows/encoder-dependent checks remain for
their native CI environments. The checks include real FreeRDP 3.32.2 command-line
parsing, a loopback SFTP move preserving bytes and nested directories, a real
local shell exit, and packaged tools surviving main-window closure.

A fresh Linux bundle passed CLI/GUI startup checks, contained VERSION 1.91,
and matched all 35 source Help files. Local Python 3.12 validation used the
project's pinned PySide6 6.11.2 and cryptography 50.0.2. CI builds use Python 3.11
and the configured portable packaging runners. Native affected-device
acceptance remains outstanding.

Pushing v1.91 starts release preparation. Package builds are requested through
GitLab's BUILD_PACKAGES trigger; publication of the GitHub draft remains a
separate step. The tag is v1.91 and the release version is 1.91.

## Release preparation status

Release commit `afe783dee20aaf7241ca33b8fdd0c1cf64c9c41f` and annotated tag
`v1.91` were pushed to GitLab and mirrored to GitHub. The tag's commit matches
on both services; GitHub main also reached that release commit.

[GitHub release preparation](https://github.com/williamruckman/SnakeSh/actions/runs/43726487685)
passed the native Windows terminal/RDP checks, the required FreeRDP parser
checks, and draft creation. The v1.91 draft is ready for publication.

The additional package-build handoff in
[GitLab pipeline 298](https://gitlab.ruckman.net/wruckman/snakesh/-/pipelines/298)
failed after branch/tag synchronization. No GitHub Build run for the release
commit was present at verification. GitHub API authentication is unavailable
in this workspace, so package builds need either publication of the draft or
a manual run of the Build workflow using tag v1.91. Package compilation and
artifact attachment have not been verified yet.
