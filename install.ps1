# Install toknix on Windows, then its Claude Code hooks and rules block (A8).
#
#   irm https://raw.githubusercontent.com/lephus/toknix/main/install.ps1 | iex
#
# `irm | iex` cannot pass parameters, so each one also has an environment variable:
#   -DryRun     / $env:TOKNIX_DRY_RUN = "1"     print what would be done, change nothing
#   -Uninstall  / $env:TOKNIX_UNINSTALL = "1"   remove the hooks, the rules block and toknix itself
#   -From SRC   / $env:TOKNIX_FROM = "SRC"      install from a local directory or a pip/uv URL
#   -NoHooks    / $env:TOKNIX_NO_HOOKS = "1"    install the command only; skip `toknix install`
# TOKNIX_REPO (owner/name, default lephus/toknix) and TOKNIX_REF (default main) pick the GitHub source.
#
# Uses `uv tool install` when uv is on PATH; otherwise a private venv in ~\.toknix\venv whose Scripts
# folder is added to the user PATH (toknix.exe lives there).
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Uninstall,
    [switch]$NoHooks,
    [string]$From = ""
)

$ErrorActionPreference = "Stop"

if ($env:TOKNIX_DRY_RUN -eq "1") { $DryRun = $true }
if ($env:TOKNIX_UNINSTALL -eq "1") { $Uninstall = $true }
if ($env:TOKNIX_NO_HOOKS -eq "1") { $NoHooks = $true }
if (-not $From -and $env:TOKNIX_FROM) { $From = $env:TOKNIX_FROM }

$Repo = if ($env:TOKNIX_REPO) { $env:TOKNIX_REPO } else { "lephus/toknix" }
$Ref = if ($env:TOKNIX_REF) { $env:TOKNIX_REF } else { "main" }
$Source = if ($From) { $From } else { "git+https://github.com/$Repo@$Ref" }
# Python finds the home folder through USERPROFILE; PowerShell's $HOME is fixed when the shell starts.
$UserHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$Venv = Join-Path $UserHome ".toknix\venv"
$Scripts = Join-Path $Venv "Scripts"

function Write-Step([string]$Message) { Write-Host "toknix: $Message" }

function Invoke-Step([string]$Exe, [string[]]$Arguments) {
    if ($DryRun) {
        Write-Host "+ $Exe $($Arguments -join ' ')"
        return
    }
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "toknix: '$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}

function Find-Python {
    foreach ($candidate in @(@("py", "-3"), @("python"), @("python3"))) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $rest = @($candidate | Select-Object -Skip 1)
        & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return , $candidate }
    }
    return $null
}

function Get-ToknixCommand {
    $cmd = Get-Command toknix -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $inVenv = Join-Path $Scripts "toknix.exe"
    if (Test-Path $inVenv) { return $inVenv }
    return $null
}

function Add-UserPath([string]$Dir) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($current -split ";") -contains $Dir) { return }
    if ($DryRun) {
        Write-Host "+ add $Dir to the user PATH"
        return
    }
    [Environment]::SetEnvironmentVariable("Path", (@($current, $Dir) | Where-Object { $_ }) -join ";", "User")
    $env:Path = "$env:Path;$Dir"
    Write-Step "added $Dir to your user PATH (new terminals pick it up)"
}

function Uninstall-Toknix {
    $tok = Get-ToknixCommand
    if ($tok) { Invoke-Step $tok @("uninstall") } else { Write-Step "toknix command not found; skipping hook removal" }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $tools = (& uv tool list 2>$null) -join "`n"
        if ($tools -match "(?m)^toknix ") { Invoke-Step "uv" @("tool", "uninstall", "toknix") }
    }
    if (Test-Path $Venv) {
        if ($DryRun) { Write-Host "+ remove $Venv" } else { Remove-Item -Recurse -Force $Venv }
    }
    Write-Step "removed. Your data in ~\.toknix (stats, saved outputs) is kept; delete it by hand if you like."
}

function Install-Toknix {
    $python = Find-Python
    if (-not $python) { throw "toknix: Python 3.9 or newer is required (py -3 / python not found or too old)" }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-Step "installing with uv from $Source"
        # --reinstall: a re-run must pick up new code even when the version number did not change
        Invoke-Step "uv" @("tool", "install", "--force", "--reinstall", $Source)
    } else {
        Write-Step "installing in $Venv from $Source"
        $exe = $python[0]
        $rest = @($python | Select-Object -Skip 1)
        Invoke-Step $exe ($rest + @("-m", "venv", $Venv))
        $venvPython = Join-Path $Scripts "python.exe"
        Invoke-Step $venvPython @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
        Invoke-Step $venvPython @("-m", "pip", "install", "--quiet", "--upgrade", "--force-reinstall", "--no-deps", $Source)
        Add-UserPath $Scripts
    }
    $tok = Get-ToknixCommand
    if (-not $tok) {
        if ($DryRun) { $tok = "toknix" } else { throw "toknix: installed, but toknix is not on PATH; open a new terminal and run 'toknix install'" }
    }
    if (-not $NoHooks) { Invoke-Step $tok @("install") }
    if ($DryRun) { Write-Host "+ $tok doctor" } else { & $tok doctor }
    Write-Step "done. Restart Claude Code to load the hooks."
}

if ($Uninstall) { Uninstall-Toknix } else { Install-Toknix }
