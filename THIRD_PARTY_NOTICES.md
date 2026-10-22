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

## Python X Library (Linux)

Linux builds bundle python-xlib 0.33 for querying and resizing embedded FreeRDP
windows. It is licensed under LGPL 2.1 or later. Its distribution metadata and
license accompany the frozen Linux package.

- Upstream source and license: https://github.com/python-xlib/python-xlib
- Release source: https://pypi.org/project/python-xlib/0.33/#files

## Microsoft Windows Console (ConPTY)

Windows packages include Microsoft's MIT-licensed ConPTY and OpenConsole runtime
1.25.260930003 for Sixel passthrough in opted-in local terminals. The build fetches
the official NuGet package and verifies SHA256
`02b07b349af66d801159bdf9e440d4a1ce78bb951f37fc8609731665afdae7ee`.
The Microsoft license accompanies the files in `windows-conpty/LICENSE.txt`.

- Package: https://www.nuget.org/packages/Microsoft.Windows.Console.ConPTY/1.25.260930003
- Source: https://github.com/microsoft/terminal

Local Sixel setup can separately install trackd's Sixel PowerShell module 0.7.1
from PowerShell Gallery into the current user's module directories. This module
is not redistributed in SnakeSh packages.

- Module and license: https://github.com/trackd/Sixel
- Package: https://www.powershellgallery.com/packages/Sixel/0.7.1

## Release Checklist

Before publishing a source or binary release:

- Confirm `LICENSE`, `NOTICE`, and this file ship with the release artifact.
- Confirm frozen builds preserve the upstream Qt/PySide license texts and
  notices that accompany bundled components.
- Review any newly bundled third-party binaries, assets, or data files and add
  notice entries before release.
