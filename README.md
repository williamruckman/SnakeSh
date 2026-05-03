# SnakeSh

SnakeSh is a cross-platform remote access workspace in Python with a modern Qt UI and secure session storage.
Supported protocols currently include SSH, SFTP, RDP, VNC, NoMachine, Telnet, and Serial.

## Current Features

- Session library with folders/tags metadata
- Full-height searchable folder/group session tree for large environments
- Session list panel supports `Show`, `Auto`, and `Float` modes; `Auto` collapses the tree until you hover the splitter handle, while `Float` moves the panel into its own always-on-top window
- Session tree folder expand/collapse state persists across refreshes and app restarts
- Session tree supports folder/subfolder creation, recursive folder deletion warnings, and right-click delete actions
- Session tree right-click menu supports renaming individual sessions and folders
- Session tree right-click menu supports duplicating a session with copied settings and an auto-numbered unique name
- Session tree supports same-protocol bulk edit across multi-selected saved sessions
- Multi-select session drag-drop between folders for bulk reorganization
- Multi-select connect from the session tree (toolbar Connect or right-click Connect) with a safety warning before opening multiple sessions
- SSH connectivity test using `asyncssh`
- In-app Telnet terminal tabs with optional TLS, server certificate validation controls, and configurable terminal type/connect timeout
- In-app Serial terminal tabs with configurable baud/data bits/parity/stop bits/flow control (native POSIX backend with PySerial fallback)
- Dual-pane SFTP workspace (local left, remote right) with split navigation/list panes
- SFTP transfers via double-click on remote files (download), double-click on local files (upload), right-click upload/download/rename/new-folder actions, `F5` refresh, and drag-drop upload
- Upload progress dialog with percentage, elapsed time, transfer speed, and cancel support
- SFTP delete and overwrite actions can prompt for confirmation before replacing or removing files
- Right-click SSH/SFTP session action to open an SFTP tab reusing the same session credentials
- SSH/SFTP sessions support default SFTP local and remote start folders
- RDP launcher integration (`mstsc` on Windows, `xfreerdp` on Linux)
- RDP launcher uses saved session credentials when available (Windows via `cmdkey`, Linux via `xfreerdp` stdin credential handoff)
- Linux RDP sessions can run inside SnakeSh tabs (Open Mode: In tab) or detached; VNC, NoMachine, and non-Linux RDP use detached window mode only
- Linux RDP launches ask certificate trust in a GUI confirmation dialog before starting `xfreerdp` (no hidden terminal prompt)
- VNC launcher integration with client auto-detection on Windows/Linux and optional default viewer auto-install (TigerVNC)
- NoMachine launcher integration (`nxplayer`) with detached-window workflow
- RDP sessions support a dedicated domain field; X11 forwarding is only exposed for SSH/SFTP sessions
- RDP and VNC sessions support per-session resolution, fullscreen, and color depth preferences
- Session editor/details/context menus are protocol-aware (SSH/SFTP-only actions are hidden for RDP/VNC/NoMachine)
- X11 forwarding-ready SSH config flags
- SSH sessions support dynamic (SOCKS) and static (local/remote) port-forward tunnel profiles
- SSH/Telnet/Serial sessions support optional post-connect automation flows (command/sleep/expect steps with timeout actions)
- SSH/Telnet/Serial sessions can show a per-session shell banner with custom color and optional blink for warnings or environment reminders
- SSH/SFTP uses a single global command bar (bottom of workspace); commands target the active terminal tab or its group. Grouped tabs show `*` and can be managed/renamed from a Group Manager dialog
- Saved Fast Commands are protocol-aware: they type into terminal tabs and copy to the clipboard when the active tab is an RDP, VNC, or NoMachine viewer
- Multiple tabs to the same host are automatically numbered (for example `DatacenterSSH(1)`, `DatacenterSSH(2)`)
- SSH/SFTP session tabs support horizontal/vertical split workspace layouts with drag-drop tab movement across panes
- SSH/SFTP/Telnet tabs can be detached into independent non-modal windows and reattached back to the main workspace
- Local Shell tabs can also be detached into independent non-modal windows and reattached later
- Main window supports borderless fullscreen with a configurable shortcut and optional hiding of the top action bar and bottom command bar while fullscreen is active
- Terminal tab context menu includes `Disconnect` for keeping a tab open after ending the live shell, `Reconnect Session` for reconnectable closed tabs in place, and `Rename Tab` for session-lifetime custom tab labels
- Settings include active/inactive tab background/foreground color controls
- Windows X11 forwarding helper: prefers VcXsrv, falls back to other installed/running X servers, and can install/launch VcXsrv on demand
- Tabbed multi-session workspace for concurrent connections
- Resource Monitor provides a Mission Center-style local dashboard with live CPU, memory, disk, disk I/O, network, process, dual-stack interface summaries, best-effort NVIDIA/AMD/Intel GPU telemetry, configurable display/refresh settings, and a searchable process table with optional privilege-prompted end-task actions
- Built-in Local Shell tab launcher (PowerShell/cmd on Windows, shell detection on Linux/macOS)
- In-app SSH terminal tabs with VT/ANSI emulation (supports color and full-screen TUIs like `top`/`htop`)
- Terminal key forwarding keeps `Tab`/`Shift+Tab` inside the SSH session instead of switching GUI focus
- Terminal and command-bar paste handling reviews multi-line input before sending it to a live terminal
- SSH terminal context menu includes `Open SFTP Tab` for the active SSH session and terminal context menus include `Disconnect` for active shell tabs
- Non-modal terminal scrollback window with live case-sensitive or regex search and configurable scrollback size
- Terminal session logging supports per-tab manual logging and optional global auto-logging with folder-based paths by session folder hierarchy
- Session log cleanup policy is configurable in Settings and defaults to enabled (7-day retention)
- Tools menu launches detached standalone GUI processes for Resource Monitor, Network Inspector, IP Scan, Ping, Dig, Traceroute, Whois, ASN Lookup, File Hash, Web Server, Syslog / SNMP Monitor, OUI Lookup, Help, MTU / MSS Calculator, Subnet Calculator, Password Generator, and Diff Tool so a busy tool cannot stall the main console UI
- Standalone tool CLI exposes full `--help` output, every registered tool key, and Ping prefill flags through `snakesh tool`
- Settings includes per-user launcher management for individual tools on Linux, Windows, and macOS in addition to the main-app Linux desktop integration controls
- MTU / MSS Calculator computes effective MTU, max Ping payload, max UDP payload, and TCP MSS from an outer/interface MTU plus common overhead presets, and can prefill the Ping tool without auto-running it
- Network Inspector shows local interfaces, routes, ARP neighbors with offline OUI/vendor resolution, listening ports, and DNS configuration, and supports auto refresh, copy/export actions, and optional privileged port/process visibility where supported
- IP Scan is a built-in TCP connect scanner for hostname/IP/CIDR targets with Common TCP 20/Common TCP 100/custom-port modes, live progress, stop support, search/filter boxes on both result tabs, and host-click drilldown from Hosts to a pre-filtered Open Ports view
- Traceroute provides repeated MTR-style path tracing with an unprivileged command backend by default, optional faster native probing with elevation when needed, live hop statistics, resizable/copyable tables, graph views, and export actions across Windows, Linux, and macOS
- ASN Lookup performs WHOIS-based autonomous system lookups, accepts bare numbers or `AS`-prefixed values, and shows both structured summary fields and the raw registry response
- File Hash can generate MD5, SHA1, SHA256, SHA384, SHA512, and BLAKE2b digests and verify pasted or checksum-file values using plain, GNU coreutils, or BSD-style checksum formats
- Password Generator uses the local OS CSPRNG, supports complexity presets, required/excluded characters, batch generation, and remembers the last-used options
- Diff Tool compares two text buffers or files side by side with synchronized scrolling, per-change and apply-all copy actions, line and inline highlights, and a shared find bar with case-sensitive or regex search
- Web Server tool can launch a GUI-managed static HTTP/HTTPS server or reverse proxy with detected-interface and wildcard bind-address presets plus custom manual entry, manual/self-signed/certbot TLS options, optional chain certificates, extra request headers, archived per-run logs, saved configuration profiles, settings-driven log cleanup, and GUI-based privileged-port elevation where supported
- Syslog / SNMP Monitor receives syslog plus SNMP notifications through a GUI-managed helper, provides a dedicated Settings tab for listener setup, a Monitor tab for filters plus live or archived event review, a top-right running/stopped status badge, per-profile display timezone selection, double-click event popups, row or column copy plus CSV/JSON export, clear-data maintenance, a live aggregated alerts window with terminal-bell-style sound, theme-aware dashboard charts, close-time stop warnings, and GUI elevation for privileged ports without exposing a CLI
- OUI Lookup uses a bundled offline OUI vendor snapshot so lookups work without runtime network access
- Help opens an in-depth built-in guide with a clickable index on the left and the selected document on the right
- Settings dialog covers appearance presets, terminal colors/font/scrollback, main-window fullscreen controls, session and web-server log retention, fatal crash logging, BEL/visual bell behavior, Local Shell defaults, and restore defaults
- Default theme preset is Onyx Blue
- Session editor supports per-session terminal background/foreground color overrides and password reveal for saved credentials
- Profiles system can save/restore workspace state (split layout, open sessions, tab labels, detached windows with geometry), and a default startup profile can be set
- Optional master-password gates for opening the main app and for launching standalone tools (configured in Settings with double-entry confirmation)
- Settings toggle to disable/enable SFTP delete confirmation warnings
- Settings dialog includes import/export for settings and sessions (separately or combined)
- Export supports selective session picking (share only chosen sessions)
- Optional password-protected encrypted export (PSK-derived key); unencrypted export when no password is set
- First-party exports include configuration metadata, source-platform metadata, profiles, fast commands, web-server profiles, and Syslog / SNMP monitor profiles, but do not embed OS-keyring or secrets-backend passwords and auth tokens
- Import supports session overwrite or merge mode, with explicit overwrite warnings
- Cross-platform settings imports keep portable preferences and data, but automatically reset OS-specific geometry, local-shell paths, and launcher paths to safe defaults on the destination OS
- Settings includes a `Third Party Import` entry for importing SecureCRT XML, OpenSSH config, and PuTTY registry sessions (third-party export is intentionally disabled)
- Secrets backend abstraction supports OS keyring, 1Password CLI, Bitwarden CLI, Keeper Commander CLI, KeePass 2.x (KeePassXC CLI), and HashiCorp Vault KV v2 with in-settings backend setup/testing
- Optional password save in the configured secrets backend (per-session setting)
- Public-key install helper for SSH/SFTP sessions only: auto-discovers existing local keys, prompts to generate/import a key pair when missing, and appends local `*.pub` to remote `authorized_keys`
- Encrypted-at-rest session data using `cryptography` + configured secrets backend

## Security Model

- Session store is encrypted with Fernet (AES128 + HMAC) in a local app data file.
- Encryption key is generated once and stored in the configured secrets backend.
- Saved session passwords are written through the configured secrets backend instead of the encrypted session store.
- First-party export bundles include configuration metadata but do not embed passwords or backend auth values stored in the OS keyring or external secrets backend.
- A master password can gate the main application at startup and, separately, standalone tool launches. SnakeSh stores only derived verification material for that gate, not the plaintext password.

## Freeze Diagnostics

For Windows hang or freeze investigations, launch SnakeSh with a per-run diagnostics session instead of relying only on the Settings crash-log toggle:

- Start SnakeSh with `--debug-level debug` to write a session diagnostics log.
- Optionally add `--debug-log-file <PATH>` to force a specific log location.
- Without `--debug-log-file`, SnakeSh writes diagnostics logs under `%LOCALAPPDATA%\SnakeSh\logs\debug\YYYY\MM\`.
- The Settings `Crash Logging` option remains useful for fatal faults and native crashes, but it does not diagnose a live GUI freeze by itself.

Basic validation flow:

- Launch `snakesh --debug-level debug`.
- Open Resource Monitor and leave it running until the issue reproduces.
- After the freeze or forced close, collect the newest debug log from `%LOCALAPPDATA%\SnakeSh\logs\debug\...`.
- If the UI stops servicing timers for roughly 15 seconds during the run, the same log should also contain an all-thread traceback dump from the watchdog.
- External desktop clients may use their own profile or credential handoff mechanisms; SnakeSh limits permissions on locally generated launch profiles where the platform allows it.
- SSH/SFTP host keys are pinned in an app-managed `known_hosts` file after explicit trust approval.
- Prefer SSH keys over passwords whenever possible.

## Install

### Windows (x64)

1. Download `SnakeSh-<version>-Setup.exe` from Releases.
2. Double-click the installer and follow the wizard.
3. Launch SnakeSh from the Start Menu.

Uninstalling SnakeSh from Windows removes the main app shortcuts and managed tool launchers.

No terminal, Python install, or virtual environment is required for end users.

### macOS (Intel + Apple Silicon)

1. Download the correct macOS release asset:
   - Intel Macs: `SnakeSh-macos-x64.dmg` when signed builds are configured, otherwise `SnakeSh-macos-x64-unsigned.dmg` or `SnakeSh-macos-x64-unsigned.zip`
   - Apple Silicon Macs: `SnakeSh-macos-arm64.dmg` when signed builds are configured, otherwise `SnakeSh-macos-arm64-unsigned.dmg` or `SnakeSh-macos-arm64-unsigned.zip`
2. If you downloaded a `.dmg`, open it and drag `SnakeSh.app` into `Applications`.
3. If you downloaded a `.zip`, extract it and move `SnakeSh.app` to `Applications` (optional), then launch it.

Use `Uninstall SnakeSh.command` from the macOS release package to remove SnakeSh from common install locations and remove managed tool launchers.

Unsigned macOS builds may trigger Gatekeeper warnings on first launch.

### Linux (x64 AppImage)

1. Download `SnakeSh-<version>-x86_64.AppImage` from Releases.
2. Mark it executable if needed (`chmod +x SnakeSh-<version>-x86_64.AppImage`).
3. Double-click to run.
4. On first launch, click `Install to App Menu` in SnakeSh to create a per-user desktop/menu entry.
5. Optional: open `Settings` and use `Manage Tool Launchers...` to add or remove standalone launcher entries for individual tools.

No system-wide package install is required for normal use.

## Run

- Windows: open SnakeSh from Start Menu.
- macOS: open `SnakeSh.app`.
- Linux: open SnakeSh from your app menu after desktop integration, or run the AppImage directly.

`securepython` remains available as a compatibility launcher alias.

Main app CLI:
- `snakesh`
- `snakesh <export.ssx>`
- `snakesh --install-desktop`
- `snakesh --uninstall-desktop`
- `snakesh --remove-tool-launchers`
- `snakesh --debug-level info`
- `snakesh --debug-level debug`
- `snakesh --debug-level trace`
- `snakesh --debug-level debug --debug-log-file /path/to/snakesh.log`

Internal helper launch modes:
- `snakesh --web-server-helper <instance_dir>`
- `snakesh --network-inspector-ports-helper <session_dir>`
- `snakesh --mtr-helper <session_dir>`
- `snakesh --syslog-snmp-monitor-helper <profile_id>`

The helper modes above are real CLI options and appear in `snakesh --help`, but they are intended for SnakeSh-managed child processes rather than normal manual use.

Standalone tool CLI:
- `snakesh tool --help`
- `snakesh tool list`
- `snakesh tool resource_monitor`
- `snakesh tool network_inspector`
- `snakesh tool whois`
- `snakesh tool asn_lookup`
- `snakesh tool dig`
- `snakesh tool traceroute`
- `snakesh tool ping`
- `snakesh tool ip_scan`
- `snakesh tool mtu_calculator`
- `snakesh tool file_hash`
- `snakesh tool oui_lookup`
- `snakesh tool web_server`
- `snakesh tool syslog_snmp_monitor`
- `snakesh tool subnet_calculator`
- `snakesh tool password_generator`
- `snakesh tool diff`
- `snakesh tool help`

Ping tool prefill options:
- `snakesh tool ping --packet-size 1472`
- `snakesh tool ping --ipv6`
- `snakesh tool ping --packet-size 1452 --ipv6`

`snakesh tool help` launches the Help tool itself. Use `snakesh tool --help` when you want the command reference instead of the Help window.

Every standalone tool also accepts `--help`, `--debug-level {info,debug,trace}`, and `--debug-log-file <PATH>` after the tool key, for example `snakesh tool resource_monitor --help` or `snakesh tool resource_monitor --debug-level debug`. Debug flags can also be placed before the tool key in `snakesh tool` mode.

Standalone tools use the same saved settings and theme as the main app, and they honor the optional standalone tool-launch master-password gate. They run in separate processes and can stay open after the main SnakeSh window closes.

## Development Setup (from source)

SnakeSh requires Python 3.11+.

### Linux/macOS

1. Run `bash scripts/install.sh`
2. Activate virtual environment: `source .venv/bin/activate`
3. Start SnakeSh: `snakesh` (or `python -m snakesh`)

### Windows (PowerShell)

1. `py -3.11 -m venv .venv`
2. `.\.venv\Scripts\Activate.ps1`
3. `python -m pip install --upgrade pip`
4. `python -m pip install -e .`
5. Start SnakeSh: `snakesh` (or `python -m snakesh`)

### Tests

1. `python -m pip install pytest`
2. `python -m pytest -q`

## Troubleshooting

- Missing protocol tools:
SnakeSh checks protocol dependencies on demand (for example `xfreerdp`, `mstsc`, `vncviewer`, `nxplayer`, `xauth`, `ssh-keygen`, and the Python SNMP runtime used by the Syslog / SNMP Monitor) and shows an in-app prompt if installation is needed.

- Permission prompts:
When a dependency install or privileged listener port needs elevated privileges, SnakeSh will ask for admin/root confirmation through the OS prompt.

- macOS first-launch warning:
Unsigned macOS builds may show "app is damaged" or developer verification warnings. Use Finder `Open` from the app context menu (or remove quarantine manually if your policy allows it).

- Linux desktop integration repair/remove:
Open `Settings` and use `Install/Repair Desktop Integration` or `Remove Desktop Integration`. Removing desktop integration also removes managed tool launcher entries.

- Tool launchers:
Open `Settings` and use `Manage Tool Launchers...` to install or remove per-user launcher entries for standalone tools. `snakesh --remove-tool-launchers` removes all managed standalone tool launchers without changing app data.

- RDP support on macOS:
RDP launcher support is currently Windows/Linux only. Use SSH/SFTP, VNC, NoMachine, Telnet, or Serial sessions on macOS.

- Fast Commands on remote desktops:
When the active tab is RDP, VNC, or NoMachine, Fast Commands copy the saved text to the clipboard instead of trying to inject keystrokes into the viewer. Paste manually inside the remote desktop session.

- Network Inspector copy/export:
Use `Copy Selected`, `Copy All`, or `Ctrl+C` on the focused Network Inspector tab to copy the visible data as tab-delimited text with headers.

- Session list seems missing:
Use the `Show` / `Auto` / `Float` dropdown beside the session search box. In `Auto` mode the list collapses until you hover the main splitter handle. In `Float` mode the search box, session tree, and mode dropdown move into a separate always-on-top window, and closing that window returns the list to `Show`.

- Imported settings/sessions are missing saved passwords:
First-party exports intentionally omit passwords and backend auth tokens stored in the OS keyring or external secrets backend. Syslog / SNMP monitor history archives also stay local instead of being packed into the export. Re-enter secrets or reconnect the backend on the destination machine.

- Cross-platform imports reset some settings:
When you import a backup from a different OS, SnakeSh keeps portable settings such as themes, fast commands, profiles, and saved sessions, but it intentionally resets foreign window geometry, detached-window geometry, local-shell launch overrides, and other OS-specific paths so the destination install stays launchable.

## Release Packaging (Maintainers)

- GitHub Actions workflow:
`.github/workflows/build.yml`
- CI runs the full test suite before any packaging job.
- CI builds release artifacts on GitHub-hosted runners for:
  - Linux x64 AppImage
  - Windows x64 Setup EXE
  - macOS x64 dmg/zip (Intel)
  - macOS arm64 dmg/zip (Apple Silicon)
- Tag-based release publishing:
Push a tag matching `v*` (for example `v0.9.5`) to build all artifacts and publish a GitHub Release automatically.
- Local Linux full release build:
`bash scripts/build_linux.sh`
- By default, `scripts/build_linux.sh` uses the containerized release builder when Docker or Podman is available. To force a direct host build on the pinned portable GLIBC baseline (currently GLIBC 2.34-2.35), use:
`USE_CONTAINER=0 PYTHON_BIN=python3.11 bash scripts/build_linux.sh`
- The Linux build script still refuses newer direct-host GLIBC release builds because they raise the bundled `libpython` runtime floor and break older targets such as Linux Mint 21.3.
- The containerized release builder uses [packaging/linux/release-builder/Dockerfile](packaging/linux/release-builder/Dockerfile), which pins the baseline to Ubuntu 22.04 / GLIBC 2.35 with Python 3.11 and bakes in `linuxdeploy` + `appimagetool`.
- Use `CONTAINER_ENGINE=docker` or `CONTAINER_ENGINE=podman` to force the engine, and `REBUILD_IMAGE=1` to refresh the builder image after Dockerfile changes.
- `scripts/build_linux_release_container.sh` remains as a compatibility wrapper around `scripts/build_linux.sh` when you want to force the container path explicitly.
- PyInstaller on Linux also requires the selected interpreter's shared `libpython` to be available to the build host (for example `libpython3.11.so.1.0`).
- Local Linux AppImage packaging (requires `linuxdeploy` and `appimagetool` on `PATH`, or set `LINUXDEPLOY_BIN` / `APPIMAGETOOL_BIN`):
`scripts/package_linux_appimage.sh`
- The AppImage bundles the safe non-core display/runtime libraries it needs (for example `libxcb` and Wayland client libraries), but still relies on host-provided glibc and graphics-driver stack libraries.
- Local Linux checksum generation:
`scripts/make_checksums.sh`
- Local Windows full release build:
`powershell -File scripts/build_windows.ps1`
- On Windows, the build script auto-resolves Python 3.11 and `ISCC.exe`, installs Python 3.11 plus Inno Setup with `winget` if they are missing, builds the Setup EXE by default, optionally signs when `-CertThumbprint` or `WINDOWS_CERT_THUMBPRINT` is set, and writes `.sha256` sidecars plus `dist\SHA256SUMS.txt`. Use `-RunTests` to execute `pytest`, `-SkipInstaller` to build only the frozen app, `-SkipSigning` or `-SkipChecksums` to suppress those release steps, or `-NoBootstrap` to disable automatic dependency installation.
- `scripts/sign_windows.ps1` remains available if you want to sign already-built artifacts separately.
- Local macOS full release build:
`bash scripts/build_macos.sh`
- `scripts/build_macos.sh` now builds `dist/SnakeSh.app`, packages dmg/zip artifacts by default, signs when `MACOS_SIGN_IDENTITY` is set, notarizes when notary credentials are configured or `NOTARIZE_MACOS=1`, and writes `dist/SHA256SUMS.txt`. Use `PACKAGE_ZIP=0` and/or `PACKAGE_DMG=0` to trim outputs, or `PACKAGE_CHECKSUMS=0` to skip checksum generation.
- The lower-level macOS helpers remain available when you need to rerun individual stages manually:
  - `bash scripts/package_macos.sh`
  - `MACOS_SIGN_IDENTITY="Developer ID Application: Example Org (TEAMID)" bash scripts/sign_macos.sh`
  - `TARGET_PATH=dist/SnakeSh.app APPLE_ID=<APPLE_ID> TEAM_ID=<TEAM_ID> APP_SPECIFIC_PASSWORD=<APP_PASSWORD> bash scripts/notarize_macos.sh`
- Optional GitHub secrets for signed/notarized macOS releases:
  - `MACOS_CERT_BASE64`
  - `MACOS_CERT_PASSWORD`
  - `MACOS_SIGN_IDENTITY`
  - `MACOS_KEYCHAIN_PASSWORD`
  - `MACOS_NOTARY_APPLE_ID`
  - `MACOS_NOTARY_TEAM_ID`
  - `MACOS_NOTARY_PASSWORD`
- Release publishing generates a single `SHA256SUMS.txt` covering all uploaded assets.

## Bundled Data Refresh (Maintainers)

- Quick merge of a vendor export into the bundled snapshot using the standard local IEEE data:
```bash
python scripts/merge_vendor_oui_export.py /path/to/mac-vendors-export.csv
```
- Refresh the bundled offline OUI vendor snapshot from the current official IEEE feeds:
```bash
python scripts/build_oui_snapshot.py --download-official --output src/snakesh/assets/oui_snapshot.json
```
- If you already have the IEEE CSV files locally, you can still build from explicit inputs:
```bash
python scripts/build_oui_snapshot.py \
  --input /path/to/oui.csv \
  --input /path/to/mam.csv \
  --input /path/to/oui36.csv \
  --input /path/to/iab.csv \
  --output src/snakesh/assets/oui_snapshot.json
```
- The builder also accepts compatible vendor-export CSV files with `Mac Prefix` and `Vendor Name` columns, so you can merge third-party data into the bundled snapshot by adding another `--input /path/to/vendor-export.csv`. Later inputs win on duplicate prefixes.
- `merge_vendor_oui_export.py` is the convenience wrapper for the common maintainer case: use local `/usr/share/ieee-data` as the base and add one or more vendor exports on top. Pass `--download-official` to that wrapper if you want it to fetch the IEEE base data instead.
- Use `--cache-dir <DIR>` with `--download-official` if you want to keep the downloaded CSVs instead of using a temporary directory.

## License

SnakeSh is licensed under the Apache License 2.0. See `LICENSE` and `NOTICE`.

Bundled third-party components keep their own licenses, permissions, and notices. See `THIRD_PARTY_NOTICES.md` for the current Qt/PySide and OUI data notes.

## Notes on Protocol Support

- SSH/SFTP: Native and secure via `asyncssh`.
- SSH/SFTP/Telnet tabs can run in the main workspace or be detached into independent windows, then reattached later.
- Local Shell tabs use the same terminal workspace and detach/reattach flow.
- SSH/SFTP auth: key auth is supported, and password prompt fallback appears on auth failure.
- RDP: Supported on Windows (`mstsc`) and Linux (`xfreerdp`) only. In-tab mode is supported on Linux only.
- VNC: Launched through installed platform viewers (TigerVNC/compatible clients, Remmina/gvncviewer where available); authentication prompts are handled by the selected viewer. Display options are applied when supported by the selected viewer.
- NoMachine: Launched through installed NoMachine Player (`nxplayer`) in detached mode (Windows/Linux/macOS).
- Telnet: Native in-app terminal client with option negotiation (terminal type + window resize), optional TLS, and optional certificate verification.
- Serial: Native in-app serial terminal with configurable line parameters (baud/data bits/parity/stop bits/flow control).
- X11 Redirection: Enabled through SSH X11 forwarding settings; requires a local X server on Windows and a local X environment on Linux.

## Secrets Backend Notes

- `keyring` backend uses the OS credential store and works without extra setup.
- `1password` backend requires the `op` CLI to be installed and authenticated. Optional service-account token can be set in SnakeSh and is stored in OS keyring.
- `bitwarden` backend requires the `bw` CLI. Optional Bitwarden session key can be set in SnakeSh and is stored in OS keyring.
- `keeper` backend requires Keeper Commander CLI (`keeper`). Configure Keeper user/folder in SnakeSh; master password can be stored in SnakeSh (OS keyring) or provided via `KEEPER_PASSWORD`.
- `keepass` backend requires `keepassxc-cli` and a KeePass 2.x (`.kdbx`) database path. Master password can be set in SnakeSh (stored in OS keyring) or read from the configured environment variable (default: `KEEPASSXC_PASSWORD`).
- `vault` backend expects Vault KV v2. Vault token can be set in SnakeSh (stored in OS keyring) or read from the configured environment variable (default: `VAULT_TOKEN`).
- Settings now include `Setup Backend` and `Test Backend` actions so backend readiness can be validated from SnakeSh.
