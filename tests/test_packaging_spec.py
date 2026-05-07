from __future__ import annotations

from pathlib import Path
import re
import unittest


class PackagingSpecTests(unittest.TestCase):
    def test_project_version_source_is_root_version_file(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        version_text = (project_root / "VERSION").read_text(encoding="utf-8").strip()
        pyproject_text = (project_root / "pyproject.toml").read_text(encoding="utf-8")
        manifest_text = (project_root / "MANIFEST.in").read_text(encoding="utf-8")

        self.assertRegex(version_text, re.compile(r"^[0-9]+([.][0-9]+)*$"))
        self.assertIn('dynamic = ["version"]', pyproject_text)
        self.assertIn('version = {file = "VERSION"}', pyproject_text)
        self.assertNotRegex(pyproject_text, re.compile(r'^version = "', re.MULTILINE))
        self.assertIn("include VERSION", manifest_text)

    def test_qt_runtime_hook_targets_bundled_qt_plugin_directory(self) -> None:
        hook_path = Path(__file__).resolve().parents[1] / "packaging" / "pyinstaller" / "runtime_hook_qt.py"
        hook_text = hook_path.read_text(encoding="utf-8")

        self.assertIn('bundle_root / "PySide6" / "Qt" / "plugins"', hook_text)
        self.assertIn("QT_QPA_PLATFORM_PLUGIN_PATH", hook_text)

    def test_pyinstaller_spec_includes_qtcharts_hidden_import(self) -> None:
        spec_path = Path(__file__).resolve().parents[1] / "packaging" / "pyinstaller" / "snakesh.spec"
        spec_text = spec_path.read_text(encoding="utf-8")

        self.assertIn('"PySide6.QtCharts"', spec_text)
        self.assertIn('collect_submodules("pysnmp")', spec_text)
        self.assertIn('collect_dynamic_libs("PySide6")', spec_text)
        self.assertIn("libQt6XcbQpa.so", spec_text)
        self.assertIn("PySide6/Qt/plugins/platforms", spec_text)
        self.assertNotIn("PySide6/Qt/plugins/platformthemes", spec_text)
        self.assertIn("_filter_duplicate_private_qt_binaries", spec_text)
        self.assertIn("a.binaries = _filter_duplicate_private_qt_binaries(a.binaries)", spec_text)
        self.assertIn('(str(PROJECT_ROOT / "VERSION"), ".")', spec_text)

    def test_packaging_scripts_read_root_version_file(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        linux_package_text = (project_root / "scripts" / "package_linux_appimage.sh").read_text(encoding="utf-8")
        windows_build_text = (project_root / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
        installer_text = (project_root / "packaging" / "windows" / "SnakeSh.iss").read_text(encoding="utf-8")

        self.assertIn('tr -d \'[:space:]\' < "${ROOT_DIR}/VERSION"', linux_package_text)
        self.assertIn('Join-Path $RootDir "VERSION"', windows_build_text)
        self.assertIn("Get-AppVersion", windows_build_text)
        self.assertIn("#error MyAppVersion must be passed from VERSION.", installer_text)
        self.assertNotIn('#define MyAppVersion "1.6"', installer_text)

    def test_github_release_workflows_split_draft_creation_from_asset_build(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        build_text = (project_root / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        draft_text = (project_root / ".github" / "workflows" / "prepare-release-draft.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("release:", build_text)
        self.assertIn("- published", build_text)
        self.assertNotIn("startsWith(github.ref, 'refs/tags/v')", build_text)
        self.assertIn("github.event.release.tag_name", build_text)
        self.assertIn("overwrite_files: false", build_text)
        self.assertIn("fail_on_unmatched_files: true", build_text)
        self.assertIn(
            "ref: ${{ github.event_name == 'release' && github.event.release.tag_name || github.ref }}",
            build_text,
        )
        self.assertIn('tags:\n      - "v*"', draft_text)
        self.assertIn("gh release view", draft_text)
        self.assertIn("exists=true", draft_text)
        self.assertIn("if: steps.release.outputs.exists == 'false'", draft_text)
        self.assertIn("draft: true", draft_text)
        self.assertIn("generate_release_notes: true", draft_text)

    def test_gitlab_release_tag_job_uses_version_without_moving_existing_tags(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        gitlab_text = (project_root / ".gitlab-ci.yml").read_text(encoding="utf-8")

        self.assertIn("changes:\n        - VERSION", gitlab_text)
        self.assertIn("VERSION_VALUE=", gitlab_text)
        self.assertIn("TAG_NAME=\"v${VERSION_VALUE}\"", gitlab_text)
        self.assertIn("git ls-remote --exit-code --tags origin", gitlab_text)
        self.assertIn("GITLAB_RELEASE_TOKEN", gitlab_text)
        self.assertIn("git tag -a", gitlab_text)
        self.assertIn("git push", gitlab_text)

    def test_linux_build_script_requires_python_311_and_portable_glibc_baseline(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_linux.sh"
        script_text = script_path.read_text(encoding="utf-8")

        self.assertIn('BUILD_SCOPE="${BUILD_SCOPE:-release}"', script_text)
        self.assertIn('USE_CONTAINER="${USE_CONTAINER:-auto}"', script_text)
        self.assertIn('PYTHON_BIN="python3.11"', script_text)
        self.assertIn("require Python 3.11.x exactly", script_text)
        self.assertIn("GLIBC", script_text)
        self.assertIn("Linux Mint 21.3", script_text)
        self.assertIn("relink_duplicate_qt_runtime_libs", script_text)
        self.assertIn("libQt6XcbQpa.so", script_text)
        self.assertIn("run_containerized_release_build", script_text)
        self.assertIn("Portable Linux release build complete", script_text)

    def test_linux_appimage_packaging_script_bundles_safe_runtime_libraries(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "package_linux_appimage.sh"
        script_text = script_path.read_text(encoding="utf-8")

        for soname in (
            "libxcb.so.1",
            "libxcb-cursor.so.0",
            "libxcb-icccm.so.4",
            "libxcb-image.so.0",
            "libxcb-keysyms.so.1",
            "libxcb-render-util.so.0",
            "libxcb-util.so.1",
            "libxkbcommon-x11.so.0",
            "libwayland-client.so.0",
            "libwayland-cursor.so.0",
            "libwayland-egl.so.1",
        ):
            self.assertIn(soname, script_text)
        for soname in (
            "libc.so.6",
            "libm.so.6",
            "libdl.so.2",
            "libpthread.so.0",
            "libresolv.so.2",
            "libGL.so.1",
            "libEGL.so.1",
            "libGLX.so.0",
            "libGLdispatch.so.0",
            "libdrm.so.2",
        ):
            self.assertIn(soname, script_text)
        for plugin_name in ("libqxcb.so", "libqwayland.so", "libqoffscreen.so"):
            self.assertIn(plugin_name, script_text)
        self.assertIn("ensure_qt_platform_plugins", script_text)
        self.assertIn("copy_missing_qt_plugin_runtime_libs", script_text)
        self.assertIn("copy_bundled_xcb_runtime_libs_into_qt_lib", script_text)
        self.assertIn("relink_duplicate_qt_runtime_libs_in_appdir", script_text)
        self.assertIn("plugins/platformthemes", script_text)
        self.assertIn("OUTPUT_APPIMAGE_STEM", script_text)
        self.assertIn(".Appimage.sha256", script_text)

    def test_containerized_release_builder_targets_python311_on_jammy(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        dockerfile_text = (project_root / "packaging" / "linux" / "release-builder" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        script_text = (project_root / "scripts" / "build_linux.sh").read_text(encoding="utf-8")
        wrapper_text = (project_root / "scripts" / "build_linux_release_container.sh").read_text(encoding="utf-8")

        self.assertIn("FROM ubuntu:22.04", dockerfile_text)
        self.assertIn("python3.11-venv", dockerfile_text)
        self.assertIn("libfontconfig1", dockerfile_text)
        self.assertIn("libpulse0", dockerfile_text)
        self.assertIn("libxcb-shape0", dockerfile_text)
        self.assertIn("libxrandr2", dockerfile_text)
        self.assertIn("libxcb-cursor0", dockerfile_text)
        self.assertIn("linuxdeploy-x86_64.AppImage", dockerfile_text)
        self.assertIn("appimagetool-x86_64.AppImage", dockerfile_text)
        self.assertIn('RELEASE_BUILDER_IMAGE="${RELEASE_BUILDER_IMAGE:-snakesh-linux-release-builder:py311-jammy}"', script_text)
        self.assertIn("set -euo pipefail", wrapper_text)
        self.assertIn("linux-release-start.stamp", script_text)
        self.assertIn("python3.11 -m venv", script_text)
        self.assertIn("SNAKESH_LINUX_RELEASE_CONTAINER=1", script_text)
        self.assertIn("bash scripts/build_linux.sh", script_text)
        self.assertIn("bash scripts/package_linux_appimage.sh", script_text)
        self.assertIn("bash scripts/make_checksums.sh", script_text)
        self.assertIn('export USE_CONTAINER="1"', wrapper_text)

    def test_windows_build_script_can_sign_and_write_checksums(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_windows.ps1"
        script_text = script_path.read_text(encoding="utf-8")

        self.assertIn("CertThumbprint", script_text)
        self.assertIn("WINDOWS_CERT_THUMBPRINT", script_text)
        self.assertIn("signtool.exe", script_text)
        self.assertIn("Get-FileHash", script_text)
        self.assertIn("SHA256SUMS.txt", script_text)
        self.assertIn(".sha256", script_text)

    def test_windows_build_script_bootstrap_uses_shared_winget_helper_without_returning_install_output(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_windows.ps1"
        script_text = script_path.read_text(encoding="utf-8")

        self.assertIn("--disable-interactivity | Out-Host", script_text)
        self.assertRegex(
            script_text,
            re.compile(
                r"function Resolve-Python311 \{.*?Install-WithWinget \$PythonWingetId "
                r'"Python \$\(Get-RequiredPythonVersionLabel\)".*?\$pythonPath = Find-Python311',
                re.DOTALL,
            ),
        )
        self.assertRegex(
            script_text,
            re.compile(
                r"function Resolve-Iscc \{.*?Install-WithWinget \$InnoSetupWingetId "
                r'"Inno Setup".*?\$isccPath = Find-Iscc',
                re.DOTALL,
            ),
        )

    def test_windows_build_script_quietly_probes_py_launcher_for_python311(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_windows.ps1"
        script_text = script_path.read_text(encoding="utf-8")

        self.assertIn('Resolve-ExecutablePath "py.exe"', script_text)
        self.assertIn('import sys; print(sys.executable)" 2>$null', script_text)
        self.assertIn("$launcherPath = $launcherOutput | Select-Object -Last 1", script_text)

    def test_windows_installer_uninstall_removes_managed_tool_launchers(self) -> None:
        installer_path = Path(__file__).resolve().parents[1] / "packaging" / "windows" / "SnakeSh.iss"
        installer_text = installer_path.read_text(encoding="utf-8")

        self.assertIn("[UninstallDelete]", installer_text)
        self.assertIn(
            'Type: filesandordirs; Name: "{userappdata}\\Microsoft\\Windows\\Start Menu\\Programs\\SnakeSh Tools"',
            installer_text,
        )

    def test_macos_build_script_is_single_release_entry_point(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_macos.sh"
        script_text = script_path.read_text(encoding="utf-8")

        self.assertIn("PACKAGE_ZIP", script_text)
        self.assertIn("PACKAGE_DMG", script_text)
        self.assertIn("MACOS_SIGN_IDENTITY", script_text)
        self.assertIn("NOTARIZE_MACOS", script_text)
        self.assertIn("bash scripts/package_macos.sh", script_text)
        self.assertIn("bash scripts/sign_macos.sh", script_text)
        self.assertIn("bash scripts/notarize_macos.sh", script_text)
        self.assertIn("bash scripts/make_checksums.sh", script_text)

    def test_macos_packages_include_uninstall_helper_for_tool_launcher_cleanup(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        helper_path = project_root / "packaging" / "macos" / "Uninstall SnakeSh.command"
        helper_text = helper_path.read_text(encoding="utf-8")
        package_script = (project_root / "scripts" / "package_macos.sh").read_text(encoding="utf-8")

        self.assertIn('TOOL_LAUNCHER_DIR="${HOME}/Applications/SnakeSh Tools"', helper_text)
        self.assertIn('remove_path "/Applications/SnakeSh.app"', helper_text)
        self.assertIn('remove_path "${HOME}/Applications/SnakeSh.app"', helper_text)
        self.assertIn('UNINSTALL_SCRIPT_NAME="Uninstall SnakeSh.command"', package_script)
        self.assertIn('stage_payload "${ZIP_STAGING_DIR}"', package_script)
        self.assertIn('stage_payload "${DMG_STAGING_DIR}"', package_script)

    def test_linux_apprun_exports_qt_plugin_paths(self) -> None:
        apprun_path = Path(__file__).resolve().parents[1] / "packaging" / "linux" / "AppDir" / "AppRun"
        apprun_text = apprun_path.read_text(encoding="utf-8")

        self.assertIn("QT_PLUGIN_PATH", apprun_text)
        self.assertIn("QT_QPA_PLATFORM_PLUGIN_PATH", apprun_text)
        self.assertIn("PySide6/Qt/plugins", apprun_text)
        self.assertIn("LD_LIBRARY_PATH", apprun_text)
        self.assertIn("_internal", apprun_text)
        self.assertIn("QT_LIB_ROOT", apprun_text)


if __name__ == "__main__":
    unittest.main()
