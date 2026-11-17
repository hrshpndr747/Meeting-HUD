# Updates application files in place, preserving the environment and user data.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$TargetDirectory)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sourceRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$targetRoot = (Resolve-Path -LiteralPath $TargetDirectory).ProviderPath.TrimEnd('\')
$comparison = [StringComparison]::OrdinalIgnoreCase
if ($sourceRoot.Equals($targetRoot, $comparison) -or
    $sourceRoot.StartsWith($targetRoot + '\', $comparison) -or
    $targetRoot.StartsWith($sourceRoot + '\', $comparison)) {
    throw 'Extract the update to a separate staging folder outside the existing app folder.'
}
foreach ($name in @('main.py','config.py','launcher.pyw','ui_overlay.py','.venv\Scripts\python.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $targetRoot $name) -PathType Leaf)) {
        throw "The target is not an existing source installation with a working environment: missing $name."
    }
}
$running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and
    $_.CommandLine.IndexOf($targetRoot, $comparison) -ge 0 -and
    $_.CommandLine -match '(main\.py|launcher\.pyw)'
})
if ($running.Count -gt 0) {
    throw 'Close Meeting HUD completely using its close button, then run this updater again. No process was stopped.'
}
$manifestPath = Join-Path $sourceRoot 'release-manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.version -ne '1.5.0' -or @($manifest.files).Count -lt 20) {
    throw 'This is not a complete Meeting HUD 1.5.0 release.'
}

function Assert-PlainPath([string]$Root, [string]$Relative) {
    if ([IO.Path]::IsPathRooted($Relative) -or $Relative -match '(^|[\\/])\.\.([\\/]|$)' -or
        $Relative.Contains(':') -or $Relative -match '^(\.venv|venv|models|logs|\.git|\.env)([\\/]|$)') {
        throw 'The manifest contains an unsafe or reserved path.'
    }
    $full = [IO.Path]::GetFullPath((Join-Path $Root $Relative))
    if (-not $full.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'A release path escapes the application folder.'
    }
    $cursor = $full
    while ($cursor.Length -ge $Root.Length) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'A release path crosses a symbolic link or junction. Resolve this before updating.'
            }
        }
        if ($cursor.Equals($Root, [StringComparison]::OrdinalIgnoreCase)) { break }
        $cursor = Split-Path -Parent $cursor
    }
    return $full
}

$entries = @()
$seen = @{}
foreach ($file in $manifest.files) {
    $relative = [string]$file.path
    if (-not $relative -or $seen.ContainsKey($relative) -or [string]$file.sha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw 'The manifest contains a missing/duplicate path or invalid checksum.'
    }
    $seen[$relative] = $true
    $sourcePath = Assert-PlainPath $sourceRoot $relative
    $targetPath = Assert-PlainPath $targetRoot $relative
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf) -or
        (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash -ne $file.sha256) {
        throw "Release checksum failed: $relative. Extract a fresh copy."
    }
    if ((Test-Path -LiteralPath $targetPath) -and -not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
        throw "An existing directory conflicts with a release file: $relative."
    }
    $entries += [PSCustomObject]@{path=$relative; source=$sourcePath; target=$targetPath;
        existed=(Test-Path -LiteralPath $targetPath -PathType Leaf); sha256=[string]$file.sha256}
}

$installedManifest = Assert-PlainPath $targetRoot 'release-manifest.json'
if ((Test-Path -LiteralPath $installedManifest) -and -not (Test-Path -LiteralPath $installedManifest -PathType Leaf)) {
    throw 'An existing directory conflicts with release-manifest.json.'
}
$entries += [PSCustomObject]@{path='release-manifest.json'; source=$manifestPath; target=$installedManifest;
    existed=(Test-Path -LiteralPath $installedManifest -PathType Leaf);
    sha256=(Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash}

$backupRoot = Join-Path (Split-Path -Parent $targetRoot) ((Split-Path -Leaf $targetRoot) +
    '-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,6))
New-Item -ItemType Directory -Path $backupRoot | Out-Null
$backupFiles = Join-Path $backupRoot 'application'
New-Item -ItemType Directory -Path $backupFiles | Out-Null
# Complete and verify the backup before replacing any application file.
foreach ($entry in $entries) {
    if ($entry.existed) {
        $backupPath = Join-Path $backupFiles $entry.path
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backupPath) | Out-Null
        Copy-Item -LiteralPath $entry.target -Destination $backupPath -Force
        if ((Get-FileHash -LiteralPath $entry.target -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash) {
            throw 'Backup verification failed. No application files have been replaced.'
        }
    }
}
# Keep encrypted credential files encrypted; never print or decrypt their contents.
$settingsRoot = $null
foreach ($base in @($env:APPDATA, $env:LOCALAPPDATA, $env:USERPROFILE)) {
    if ($base -and [IO.Path]::IsPathRooted($base)) {
        $settingsRoot = Join-Path $base 'MeetingHUD'
        break
    }
}
if ($settingsRoot -and (Test-Path -LiteralPath $settingsRoot -PathType Container)) {
    Copy-Item -LiteralPath $settingsRoot -Destination (Join-Path $backupRoot 'user-settings') -Recurse
}
$record = [PSCustomObject]@{version='1.5.0'; target=$targetRoot; created=(Get-Date).ToString('o');
    files=@($entries | Select-Object path,existed)}
$record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backupRoot 'update-record.json') -Encoding UTF8
$written = @()
try {
    foreach ($entry in $entries) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $entry.target) | Out-Null
        $written += $entry
        Copy-Item -LiteralPath $entry.source -Destination $entry.target -Force
        if ((Get-FileHash -LiteralPath $entry.target -Algorithm SHA256).Hash -ne $entry.sha256) {
            throw "Installed checksum failed: $($entry.path)."
        }
    }
    $python = Join-Path $targetRoot '.venv\Scripts\python.exe'
    $version = & $python (Join-Path $targetRoot 'main.py') --version
    if ($LASTEXITCODE -ne 0 -or ($version -join '').Trim() -ne 'Meeting HUD 1.5.0') {
        throw 'Version check failed after replacement.'
    }
    Write-Output 'Updated application files to Meeting HUD 1.5.0.'
    Write-Output "Backup: $backupRoot"
    Write-Output 'The existing Python environment, models, environment files and saved settings remain in place.'
    Write-Output 'Next: follow UPGRADE.md for offline tests and the UI-only smoke check.'
} catch {
    $failure = $_
    $rollbackFailed = $false
    [array]::Reverse($written)
    foreach ($entry in $written) {
        try {
            if ($entry.existed) {
                Copy-Item -LiteralPath (Join-Path $backupFiles $entry.path) -Destination $entry.target -Force
                if ((Get-FileHash -LiteralPath $entry.target -Algorithm SHA256).Hash -ne
                    (Get-FileHash -LiteralPath (Join-Path $backupFiles $entry.path) -Algorithm SHA256).Hash) {
                    throw 'Restored checksum mismatch.'
                }
            } elseif (Test-Path -LiteralPath $entry.target -PathType Leaf) {
                Remove-Item -LiteralPath $entry.target -Force
            }
        } catch { $rollbackFailed = $true }
    }
    if ($rollbackFailed) {
        Write-Warning "Automatic rollback was incomplete. Keep the app closed and restore using $backupRoot."
    } else {
        Write-Warning "Application files were rolled back. Backup remains at $backupRoot."
    }
    throw $failure
}
