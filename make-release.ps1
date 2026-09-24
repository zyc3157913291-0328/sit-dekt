# Package the distributable set into release/sit-dekt-<version>.zip
#
# ASCII-only on purpose: PowerShell 5.1 reads BOM-less .ps1 as the system
# codepage, which mangles non-ASCII literals. Everything here is English.
#
# Usage:  pwsh -File make-release.ps1
#         powershell -File make-release.ps1

param(
    [string]$OutDir = 'release'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

$pkg = Get-Content (Join-Path $root 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = $pkg.version
$stage = Join-Path $root (Join-Path $OutDir "sit-dekt-$ver")

Write-Host "Staging version $ver -> $stage"

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

# The working directory is already trimmed to program files (exploration
# leftovers live in _archive/), so a whitelist by extension is enough.
$include = @('*.js', '*.py', '*.json', '*.md', '*.csv', '*.txt', 'LICENSE', '.gitignore')
# Never ship secrets, run output, backups, or one-off diagnostics.
$exclude = @('config.local.json', 'daily-result.txt', 'jw-menu.html', '*.bak')

$copied = 0
foreach ($pattern in $include) {
    Get-ChildItem -Path $root -Filter $pattern -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $skip = $false
        foreach ($ex in $exclude) { if ($_.Name -like $ex) { $skip = $true } }
        if ($skip) { return }
        Copy-Item $_.FullName (Join-Path $stage $_.Name) -Force
        $copied++
    }
}

Write-Host "Copied $copied files."

$zip = Join-Path $root (Join-Path $OutDir "sit-dekt-$ver.zip")
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip
Write-Host "Wrote $zip"

Write-Host ''
Write-Host 'Release contents:'
Get-ChildItem $stage | Select-Object Name, Length | Format-Table -AutoSize
