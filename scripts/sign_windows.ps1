param(
    [Parameter(Mandatory = $true)]
    [string]$CertThumbprint,
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [string]$AppExePath = "dist\SnakeSh\SnakeSh.exe",
    [string]$SetupExePath = ""
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $RootDir

$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if ($null -eq $signtool) {
    throw "signtool.exe was not found on PATH."
}

$targets = New-Object System.Collections.Generic.List[string]
if (-not (Test-Path $AppExePath)) {
    throw "File not found: $AppExePath"
}
$targets.Add((Resolve-Path $AppExePath).Path)

if ([string]::IsNullOrWhiteSpace($SetupExePath)) {
    $latestSetup = Get-ChildItem "dist" -Filter "SnakeSh-*-Setup.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latestSetup -ne $null) {
        $targets.Add($latestSetup.FullName)
    }
}
else {
    if (-not (Test-Path $SetupExePath)) {
        throw "File not found: $SetupExePath"
    }
    $targets.Add((Resolve-Path $SetupExePath).Path)
}

foreach ($target in $targets) {
    & $signtool.Path sign /sha1 $CertThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 "$target"
}

Write-Host "Signed files:"
$targets | ForEach-Object { Write-Host " - $_" }
