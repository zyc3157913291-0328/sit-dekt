# Package the distributable set into release/sit-dekt-<version>.zip
#
# ASCII-only on purpose: PowerShell 5.1 reads BOM-less .ps1 as the system
# codepage, which mangles non-ASCII literals. Everything here is English.
#
# The file list comes from `git ls-files`, NOT from a hand-written whitelist.
# That is deliberate: whatever .gitignore keeps out of the repository (student
# ID, name, timetable, local config) is also kept out of the release. A manual
# list once shipped personal data by accident.
#
# Usage:  powershell -File make-release.ps1

param(
    [string]$OutDir = 'release'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) { throw "git not found on PATH; a git checkout is required to build a release." }

$pkg = Get-Content (Join-Path $root 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = $pkg.version
$stage = Join-Path $root (Join-Path $OutDir "sit-dekt-$ver")

Write-Host "Staging version $ver -> $stage"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

$tracked = & $gitCmd.Source -C $root ls-files
if (-not $tracked) { throw "git ls-files returned nothing; is $root a git checkout?" }

$copied = 0
foreach ($rel in $tracked) {
    $src = Join-Path $root $rel
    if (-not (Test-Path $src -PathType Leaf)) { continue }
    $dst = Join-Path $stage $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
    Copy-Item $src $dst -Force
    $copied++
}

Write-Host "Copied $copied tracked files (personal data is excluded by .gitignore)."

$zip = Join-Path $root (Join-Path $OutDir "sit-dekt-$ver.zip")
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip
Write-Host "Wrote $zip"
Write-Host ''
Write-Host 'Release contents:'
Get-ChildItem $stage | Select-Object Name, Length | Format-Table -AutoSize
