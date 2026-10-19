# Install tokopt on Windows, then its Claude Code hooks and rules block (A8).
#
#   irm https://raw.githubusercontent.com/lephus/toknix/main/install.ps1 | iex
#
# `irm | iex` cannot pass parameters, so each one also has an environment variable:
#   -DryRun     / $env:TOKOPT_DRY_RUN = "1"     print what would be done, change nothing
#   -Uninstall  / $env:TOKOPT_UNINSTALL = "1"   remove the hooks, the rules block and tokopt itself
#   -From SRC   / $env:TOKOPT_FROM = "SRC"      install from a local directory or a pip/uv URL
#   -NoHooks    / $env:TOKOPT_NO_HOOKS = "1"    install the command only; skip `tokopt install`
# TOKOPT_REPO (owner/name, default lephus/toknix) and TOKOPT_REF (default main) pick the GitHub source.
#
# Uses `uv tool install` when uv is on PATH; otherwise a private venv in ~\.tokopt\venv whose Scripts
# folder is added to the user PATH (tokopt.exe lives there).
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Uninstall,
    [switch]$NoHooks,
    [string]$From = ""
)

$ErrorActionPreference = "Stop"

if ($env:TOKOPT_DRY_RUN -eq "1") { $DryRun = $true }
if ($env:TOKOPT_UNINSTALL -eq "1") { $Uninstall = $true }
if ($env:TOKOPT_NO_HOOKS -eq "1") { $NoHooks = $true }
if (-not $From -and $env:TOKOPT_FROM) { $From = $env:TOKOPT_FROM }

$Repo = if ($env:TOKOPT_REPO) { $env:TOKOPT_REPO } else { "lephus/toknix" }
$Ref = if ($env:TOKOPT_REF) { $env:TOKOPT_REF } else { "main" }
$Source = if ($From) { $From } else { "git+https://github.com/$Repo@$Ref" }
$Venv = Join-Path $HOME ".tokopt\venv"
$Scripts = Join-Path $Venv "Scripts"

function Write-Step([string]$Message) { Write-Host "tokopt: $Message" }

function Invoke-Step([string]$Exe, [string[]]$Arguments) {
    if ($DryRun) {
        Write-Host "+ $Exe $($Arguments -join ' ')"
        return
    }
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "tokopt: '$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
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

function Get-TokoptCommand {
    $cmd = Get-Command tokopt -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $inVenv = Join-Path $Scripts "tokopt.exe"
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

function Uninstall-Tokopt {
    $tok = Get-TokoptCommand
    if ($tok) { Invoke-Step $tok @("uninstall") } else { Write-Step "tokopt command not found; skipping hook removal" }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $tools = (& uv tool list 2>$null) -join "`n"
        if ($tools -match "(?m)^tokopt ") { Invoke-Step "uv" @("tool", "uninstall", "tokopt") }
    }
    if (Test-Path $Venv) {
        if ($DryRun) { Write-Host "+ remove $Venv" } else { Remove-Item -Recurse -Force $Venv }
    }
    Write-Step "removed. Your data in ~\.tokopt (stats, saved outputs) is kept; delete it by hand if you like."
}

function Install-Tokopt {
    $python = Find-Python
    if (-not $python) { throw "tokopt: Python 3.9 or newer is required (py -3 / python not found or too old)" }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-Step "installing with uv from $Source"
        Invoke-Step "uv" @("tool", "install", "--force", $Source)
    } else {
        Write-Step "installing in $Venv from $Source"
        $exe = $python[0]
        $rest = @($python | Select-Object -Skip 1)
        Invoke-Step $exe ($rest + @("-m", "venv", $Venv))
        $venvPython = Join-Path $Scripts "python.exe"
        Invoke-Step $venvPython @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
        Invoke-Step $venvPython @("-m", "pip", "install", "--quiet", "--upgrade", $Source)
        Add-UserPath $Scripts
    }
    $tok = Get-TokoptCommand
    if (-not $tok) {
        if ($DryRun) { $tok = "tokopt" } else { throw "tokopt: installed, but tokopt is not on PATH; open a new terminal and run 'tokopt install'" }
    }
    if (-not $NoHooks) { Invoke-Step $tok @("install") }
    if ($DryRun) { Write-Host "+ $tok doctor" } else { & $tok doctor }
    Write-Step "done. Restart Claude Code to load the hooks."
}

if ($Uninstall) { Uninstall-Tokopt } else { Install-Tokopt }
