# Third-Party Notices

This file highlights third-party licensing items that require special attention
when redistributing SnakeSh source or packaged builds. It is not a complete
inventory of every transitive dependency license.

## PySide6 / Qt for Python

SnakeSh depends on `PySide6` and frozen release builds bundle Qt for Python and
Qt runtime components.

- Upstream project: Qt for Python / PySide6
- Upstream licensing information: https://doc.qt.io/qtforpython-6/licenses.html
- Redistribution note: official SnakeSh release artifacts should preserve the
  upstream Qt/PySide license texts and notices that accompany the bundled
  components.

## Release Checklist

Before publishing a source or binary release:

- Confirm `LICENSE`, `NOTICE`, and this file ship with the release artifact.
- Confirm frozen builds preserve the upstream Qt/PySide license texts and
  notices that accompany bundled components.
- Review any newly bundled third-party binaries, assets, or data files and add
  notice entries before release.
