# Saved-session startup recovery

November 1, 2026. Version 1.86 release preparation after v1.85 / `1cdba64`.
Package publication is pending; native macOS verification remains separate.
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

## Primary references

- [Cryptography 50.0.2 Fernet decryption errors](https://cryptography.io/en/50.0.2/fernet/)
- [FreeRDP 3.32.2 license blob parsing](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/libfreerdp/core/license.c)
- [FreeRDP 3.32.2 SDL display control](https://github.com/FreeRDP/FreeRDP/blob/3.32.2/client/SDL/SDL3/sdl_disp.cpp)
