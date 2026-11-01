# Saved-session startup recovery

November 1, 2026. Version 1.87 release verification after v1.86 / `504d97d`.
Version 1.86 passed the source suite, but GitHub cancelled its Apple Silicon
package job during the scheduled macOS 14 runner brownout. Version 1.87 uses
the supported macOS 15 ARM runner; the published v1.86 tag is preserved.
All release packages are published and verified below; native macOS GUI
verification remains separate.
Report supplied by czargamingco. The supplied report is evidence, not
authorization to execute its state-removal commands.

## Confirmed failure and limits of the report

The traceback follows `app.main` to `SessionService`, then
`EncryptedSessionStore.load_payload`, where an uncaught Fernet `InvalidToken`
exits the process. A temporary encrypted database opened with another key
reproduced this exception on the baseline without touching real app data.

`InvalidToken` does not distinguish damaged ciphertext from an incorrect key.
The baseline key lookup also generated and persisted a new key when no key was
returned, even if an encrypted database already existed. A stubbed backend
reproduced that behavior. The report does not establish which condition
occurred on the Mac, or why its original key became unavailable.

The default macOS data location really is `~/.local/share/snakesh`, through the
shared Unix path implementation. It is not an exceptional fallback taken only
when startup fails. `SNAKESH_DATA_DIR` can override it. This fix keeps existing
paths and data in place. Qt preferences are used for some dialog geometry;
the stack does not establish that deleting the macOS preferences plist was
necessary. Removing the entire data directory also discards unrelated state.

## Source correction

- Key selection uses the actual store path. Existing ciphertext, including an
  empty file, never silently triggers generation of a replacement key.
- A candidate key must decrypt existing data before it is selected or migrated.
  Backend read failures without an available key stop key generation too.
- Startup presents **Saved Sessions Unavailable**, with **Close** as both the
  default and Escape action. **Retry** retries access to the configured backend.
- **Back Up and Start Fresh** is offered for an existing file. Only this
  explicit action preserves the encrypted bytes in a uniquely named,
  exclusively created private file before removing the active session file.
  The exact backup path is displayed. Settings and password records are not reset.
- Backup failure prevents reset. A changed input file is retained. If fresh
  initialization fails, the original is restored without overwriting a
  concurrently created file; the encrypted backup is retained either way.
- Decryption/format failures produce a typed error and prevent subsequent saves
  through the failed store. Invalid decrypted JSON/schema is not treated as an
  empty database. Writes use a flushed temporary file and atomic replacement
  so an interrupted save does not truncate the previous encrypted database.

An encrypted backup cannot recover a lost key. It preserves the option of
recovery if the original key or backend access is restored. A readable export
is a separate backup mechanism.

## RDP evidence received with this report

The supplied terminal excerpt identifies FreeRDP **3.32.2**, the native SDL
frontend and a Metal renderer. It reaches framebuffer initialization and
dynamic-channel loading, including `disp`. Loading that plugin alone does not
prove the server acknowledged a display update.

The missing-font alias operation took 55 milliseconds at startup. The verbose
assertion build warning reports a possible overhead, not the cause of a
specific pause. The license blob type message alone is not evidence of a fatal
licensing failure: that source path logs a mismatch without immediately
returning failure, and the supplied log subsequently initializes rendering.

At 09:13:26 the client reports `ERRCONNECT_CONNECT_CANCELLED` and disconnects;
the trailing `handleShow` message is incomplete. This does not establish why
the connection was cancelled or what caused the earlier pinwheel. The supplied
file is a startup traceback, not an Activity Monitor sample of a running SDL
process. The pinwheel and the startup exception remain separate issues.

## Validation

Tests use generated keys, temporary encrypted stores and stubbed secrets
backends. The report's removal commands were not executed, and no data on the
reporter's Mac was modified by this work. Relevant checks include preservation under wrong or
missing keys, legacy-key recovery, bad decrypted payloads, backup and restore
failures, concurrent-file preservation, interrupted writes, and the default
GUI action. Source startup probes additionally exercise the real application
and Qt event loop in isolated data directories for Close, Retry and Start Fresh.

The final affected suite passed **237 tests and 17 subtests**. The full source
snapshot passed **1,300 tests and 152 subtests**, with **9 skips** and two
existing Python `forkpty` deprecation warnings. Workspace, theme and remaining
tests ran in separate processes. The snapshot isolated default home/config
paths and excluded existing build outputs so old packaged help was not used
as source validation.

The local `.venv` was behind the repository pins. Final validation used an
isolated Python 3.12 environment with **PySide6 6.11.2**, **cryptography 50.0.2**
and **python-xlib 0.33**; the original virtualenv was not upgraded. All three
actual source-GUI startup probes passed with those dependencies: Close exited
normally, Retry opened the existing library after key access was restored, and
Start Fresh opened an empty library while preserving the original ciphertext.

Native macOS Keychain and packaged macOS GUI validation remain required. The
RDP pinwheel still needs an Activity Monitor **Sample Process** of the exact
`sdl-freerdp` process during the stall, plus the Mac OS build and SnakeSh version.

## Release publication

[Version 1.87](https://github.com/williamruckman/SnakeSh/releases/tag/v1.87)
is tagged at `928b75a`. GitLab synchronization pipeline 283 passed, and both
remotes advertise the same annotated tag object and source commit.
[Prepare Release Draft 41344729827](https://github.com/williamruckman/SnakeSh/actions/runs/41344729827)
passed its native Windows terminal/RDP gate. After publication,
[Build 41345227194](https://github.com/williamruckman/SnakeSh/actions/runs/41345227194)
passed the complete source suite, Linux, Windows, both macOS architectures,
their frozen startup smoke checks, and release attachment.

All six package files were downloaded. Their bytes match both the published
`SHA256SUMS.txt` and GitHub asset digests. The manifest's own downloaded digest
also matches GitHub metadata. The downloaded AppImage extracts successfully,
contains bundled version **1.87**, and exits successfully from `--help` with
isolated application data. Both downloaded Mac ZIPs contain version **1.87**
and the expected native Mach-O architecture. These are unsigned Mac packages;
the CI smoke checks and archive inspection do not establish on-device
Keychain recovery or a fix for the RDP pinwheel.

| Package | Verified SHA-256 |
| --- | --- |
| Linux AppImage | `07d8219efeb86f5125bed5bf7e92e2818514e4cbaba11d5591b84aa71a4d1385` |
| Windows Setup EXE | `b0f350fdb2ea8dbbb024e3acab659fab4b866b981bc7fe3bef030417b7123b90` |
| macOS arm64 DMG | `b9fb360967c2b578d3a81f2260487c87bc142513fd6a511d62835cf143cfbba7` |
| macOS arm64 ZIP | `ff4c0a0531705f7bf5237e104ac47c93bd7980b15e61bcb97cb5ca6b54da1c13` |
| macOS x64 DMG | `155ee5b6914937cbb6a632cbc9cc144f3a6b7736fbad0496d5ff219f88fe8cda` |
| macOS x64 ZIP | `5f54244059b01f882eb904a109d6370601d0796485b2462bb3e7aa405fba25c1` |

## Primary references

- [GitHub macOS 14 runner retirement and brownout schedule](https://github.com/actions/runner-images/issues/13518)
- [Cryptography 50.0.2 Fernet decryption errors](https://cryptography.io/en/50.0.2/fernet/)
- [FreeRDP 3.32.2 license blob parsing](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/libfreerdp/core/license.c)
- [FreeRDP 3.32.2 SDL display control](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/SDL/SDL3/sdl_disp.cpp)
