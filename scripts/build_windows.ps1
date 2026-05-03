param(
    [string]$PythonExe = "",
    [switch]$RunTests,
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [switch]$NoBootstrap,
    [string]$CertThumbprint = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$SkipSigning,
    [switch]$SkipChecksums
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $RootDir

$RequiredPythonMajor = 3
$RequiredPythonMinor = 11
$PythonWingetId = "Python.Python.3.11"
$InnoSetupWingetId = "JRSoftware.InnoSetup"
$ProgramFilesX86 = ${env:ProgramFiles(x86)}

function Assert-LastExitCode {
    param(
        [string]$Context
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$Context failed with exit code $LASTEXITCODE."
    }
}

function Resolve-ExecutablePath {
    param(
        [string]$Candidate
    )

    if ([string]::IsNullOrWhiteSpace($Candidate)) {
        return $null
    }

    if (Test-Path $Candidate) {
        return (Resolve-Path $Candidate).Path
    }

    $command = Get-Command $Candidate -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $command) {
        return $command.Source
    }

    return $null
}

function Resolve-ExistingPath {
    param(
        [string[]]$Candidates
    )

    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    return $null
}

function Get-RequiredPythonVersionLabel {
    return "$RequiredPythonMajor.$RequiredPythonMinor"
}

function Get-PythonVersionString {
    param(
        [string]$Executable
    )

    $version = (& $Executable -c "import sys; print('.'.join(str(part) for part in sys.version_info[:3]))").Trim()
    Assert-LastExitCode "Python version check ($Executable)"
    return $version
}

function Test-IsRequiredPythonVersion {
    param(
        [string]$Version
    )

    $parts = $Version.Split(".")
    if ($parts.Length -lt 2) {
        return $false
    }

    return $parts[0] -eq [string]$RequiredPythonMajor -and $parts[1] -eq [string]$RequiredPythonMinor
}

function Confirm-RequiredPython {
    param(
        [string]$Executable,
        [string]$Context
    )

    $version = Get-PythonVersionString $Executable
    if (-not (Test-IsRequiredPythonVersion $version)) {
        throw "$Context resolved to Python $version, but SnakeSh Windows builds require Python $(Get-RequiredPythonVersionLabel).x."
    }
    return $Executable
}

function Resolve-Winget {
    $winget = Resolve-ExecutablePath "winget.exe"
    if ($null -ne $winget) {
        return $winget
    }

    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe" -ErrorAction Stop | Out-Null
    }
    catch {
    }

    return Resolve-ExecutablePath "winget.exe"
}

function Install-WithWinget {
    param(
        [string]$PackageId,
        [string]$Label
    )

    if ($NoBootstrap) {
        throw "$Label is required but missing, and -NoBootstrap was supplied."
    }

    $winget = Resolve-Winget
    if ($null -eq $winget) {
        throw "$Label is required but missing, and winget.exe is unavailable. Install App Installer or install $Label manually."
    }

    Write-Host "Installing $Label with winget..."
    & $winget install --id $PackageId -e -s winget --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Host
    Assert-LastExitCode "winget install $PackageId"
}

function Find-Python311 {
    if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
        $explicitPath = Resolve-ExecutablePath $PythonExe
        if ($null -eq $explicitPath) {
            throw "Python executable not found: $PythonExe"
        }
        return Confirm-RequiredPython $explicitPath "-PythonExe"
    }

    $pyLauncher = Resolve-ExecutablePath "py.exe"
    if ($null -ne $pyLauncher) {
        try {
            $launcherOutput = & $pyLauncher "-$(Get-RequiredPythonVersionLabel)" -c "import sys; print(sys.executable)" 2>$null
            $launcherPath = $launcherOutput | Select-Object -Last 1
            if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($launcherPath) -and (Test-Path $launcherPath)) {
                return Confirm-RequiredPython $launcherPath "py launcher"
            }
        }
        catch {
        }
    }

    $pythonCandidates = @(
        "$env:LOCALAPPDATA\Python\pythoncore-3.11-64\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    if (-not [string]::IsNullOrWhiteSpace($ProgramFilesX86) -and $env:ProgramFiles -ne $ProgramFilesX86) {
        $pythonCandidates += "${ProgramFilesX86}\Python311\python.exe"
    }
    $candidatePath = Resolve-ExistingPath $pythonCandidates
    if ($null -ne $candidatePath) {
        return Confirm-RequiredPython $candidatePath "discovered Python"
    }

    $pythonCommand = Resolve-ExecutablePath "python.exe"
    if ($null -ne $pythonCommand) {
        try {
            return Confirm-RequiredPython $pythonCommand "python.exe"
        }
        catch {
        }
    }

    return $null
}

function Resolve-Python311 {
    $pythonPath = Find-Python311
    if ($null -ne $pythonPath) {
        return $pythonPath
    }

    Install-WithWinget $PythonWingetId "Python $(Get-RequiredPythonVersionLabel)"

    $pythonPath = Find-Python311
    if ($null -eq $pythonPath) {
        throw "Python $(Get-RequiredPythonVersionLabel) installation completed, but the interpreter could not be located automatically."
    }

    return $pythonPath
}

function Find-Iscc {
    $resolved = Resolve-ExecutablePath "iscc.exe"
    if ($null -ne $resolved) {
        return $resolved
    }

    $isccCandidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    if (-not [string]::IsNullOrWhiteSpace($ProgramFilesX86) -and $env:ProgramFiles -ne $ProgramFilesX86) {
        $isccCandidates += "${ProgramFilesX86}\Inno Setup 6\ISCC.exe"
    }

    return Resolve-ExistingPath $isccCandidates
}

function Resolve-Iscc {
    $isccPath = Find-Iscc
    if ($null -ne $isccPath) {
        return $isccPath
    }

    Install-WithWinget $InnoSetupWingetId "Inno Setup"

    $isccPath = Find-Iscc
    if ($null -eq $isccPath) {
        throw "Inno Setup installation completed, but ISCC.exe could not be located automatically."
    }

    return $isccPath
}

function Resolve-SigningThumbprint {
    if (-not [string]::IsNullOrWhiteSpace($CertThumbprint)) {
        return $CertThumbprint.Trim()
    }

    if (-not [string]::IsNullOrWhiteSpace($env:WINDOWS_CERT_THUMBPRINT)) {
        return $env:WINDOWS_CERT_THUMBPRINT.Trim()
    }

    return ""
}

function Sign-ReleaseArtifacts {
    param(
        [string]$Thumbprint,
        [string]$AppExePath,
        [string]$SetupExePath
    )

    if ([string]::IsNullOrWhiteSpace($Thumbprint)) {
        Write-Host "No Windows signing certificate thumbprint configured. Leaving release artifacts unsigned."
        return
    }

    $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($null -eq $signtool) {
        throw "signtool.exe was not found on PATH, but signing was requested."
    }

    $targets = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($AppExePath) -and (Test-Path $AppExePath)) {
        $targets.Add((Resolve-Path $AppExePath).Path)
    }
    if (-not [string]::IsNullOrWhiteSpace($SetupExePath) -and (Test-Path $SetupExePath)) {
        $targets.Add((Resolve-Path $SetupExePath).Path)
    }

    foreach ($target in $targets) {
        & $signtool.Path sign /sha1 $Thumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 "$target"
        Assert-LastExitCode "signtool signing $target"
    }

    Write-Host "Signed Windows release artifacts:"
    $targets | ForEach-Object { Write-Host " - $_" }
}

function Get-RelativeProjectPath {
    param(
        [string]$Path
    )

    $resolved = (Resolve-Path $Path).Path
    $rootUri = [System.Uri]::new(($RootDir.TrimEnd('\') + '\'))
    $pathUri = [System.Uri]::new($resolved)
    return [System.Uri]::UnescapeDataString($rootUri.MakeRelativeUri($pathUri).ToString()).Replace('/', '\')
}

function Get-ReleaseArtifactPaths {
    $artifacts = New-Object System.Collections.Generic.List[string]

    $patterns = @(
        "*-Setup.exe",
        "*.AppImage",
        "*.zip",
        "*.dmg"
    )

    foreach ($pattern in $patterns) {
        Get-ChildItem "dist" -File -Filter $pattern -ErrorAction SilentlyContinue |
            Sort-Object FullName |
            ForEach-Object {
                $artifacts.Add($_.FullName)
            }
    }

    if ($artifacts.Count -eq 0 -and (Test-Path "dist\SnakeSh\SnakeSh.exe")) {
        $artifacts.Add((Resolve-Path "dist\SnakeSh\SnakeSh.exe").Path)
    }

    return ,($artifacts.ToArray())
}

function Write-ReleaseChecksums {
    param(
        [string[]]$ArtifactPaths
    )

    if ($ArtifactPaths.Count -eq 0) {
        throw "No release artifacts were found under dist for checksum generation."
    }

    $checksumOutput = Join-Path $RootDir "dist\SHA256SUMS.txt"
    $lines = New-Object System.Collections.Generic.List[string]

    foreach ($artifactPath in $ArtifactPaths) {
        if (-not (Test-Path $artifactPath)) {
            throw "Missing artifact for checksum generation: $artifactPath"
        }

        $hash = (Get-FileHash $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $relativePath = Get-RelativeProjectPath $artifactPath
        $sidecarPath = "$artifactPath.sha256"
        $line = "$hash  $relativePath"

        Set-Content -Path $sidecarPath -Value $line
        $lines.Add($line)
    }

    Set-Content -Path $checksumOutput -Value $lines
    Write-Host "Checksums written: $checksumOutput"
}

if ($RunTests -and $SkipTests) {
    throw "Use either -RunTests or -SkipTests, not both."
}

$ResolvedPythonExe = Resolve-Python311
$PythonVersion = Get-PythonVersionString $ResolvedPythonExe
Write-Host "Using Python $PythonVersion at $ResolvedPythonExe"
$ResolvedCertThumbprint = Resolve-SigningThumbprint
$AppExePath = "dist\SnakeSh\SnakeSh.exe"
$SetupExePath = ""

& $ResolvedPythonExe -m pip install --upgrade pip
Assert-LastExitCode "pip upgrade"
& $ResolvedPythonExe -m pip install -e . pyinstaller pytest
Assert-LastExitCode "dependency installation"

if ($RunTests) {
    Write-Host "Running test suite..."
    & $ResolvedPythonExe -m pytest -q
    Assert-LastExitCode "test suite"
}
else {
    Write-Host "Skipping tests by default. Use -RunTests to execute pytest."
}

& $ResolvedPythonExe -m PyInstaller --noconfirm --clean packaging/pyinstaller/snakesh.spec
Assert-LastExitCode "PyInstaller build"

if (-not $SkipInstaller) {
    $IsccExe = Resolve-Iscc
    Write-Host "Using Inno Setup at $IsccExe"
    $version = (& $ResolvedPythonExe -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version'])").Trim()
    Assert-LastExitCode "version lookup"
    & $IsccExe "/DMyAppVersion=$version" "packaging\windows\SnakeSh.iss"
    Assert-LastExitCode "Inno Setup build"
    $latestSetup = Get-ChildItem "dist" -Filter "SnakeSh-*-Setup.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latestSetup -ne $null) {
        $SetupExePath = $latestSetup.FullName
    }
}

if (-not $SkipSigning) {
    Sign-ReleaseArtifacts -Thumbprint $ResolvedCertThumbprint -AppExePath $AppExePath -SetupExePath $SetupExePath
}

if (-not $SkipChecksums) {
    $artifactPaths = Get-ReleaseArtifactPaths
    Write-ReleaseChecksums -ArtifactPaths $artifactPaths
}

Write-Host "Windows build complete."
