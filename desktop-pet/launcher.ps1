# Desktop pet launcher.
#
# Finds a Python that can `import tkinter, PIL`, then starts pet.py detached
# from this console and from our process tree, and exits.
#
# Why detachment matters: a plain child process shares this console, and closing
# the terminal makes Windows send CTRL_CLOSE_EVENT to every process attached to
# it, which kills the pet too. The actual launch is done by detach_launch.ps1
# (CreateProcess with DETACHED_PROCESS | CREATE_NO_WINDOW |
# CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB). pythonw.exe is used as
# the runner because it never creates a console at all.
#
# ASCII ONLY, and CRLF line endings, on purpose:
#   * Windows PowerShell 5.1 reads a BOM-less .ps1 using the ANSI codepage, so
#     a non-ASCII byte anywhere (even in a comment) can corrupt parsing.
#   * PowerShell 5.1 also mis-parses LF-only scripts: here-strings and block
#     comments can swallow the rest of the file and produce bogus
#     "missing }" errors. Keep this file CRLF, and avoid here-strings here.
#
# Usage:
#   launcher.ps1
#   launcher.ps1 -KeepOpen
#   launcher.ps1 -ExtraArgs "--selftest"

param(
    [switch]$KeepOpen,
    [string]$ExtraArgs = ""
)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $here "pet.log"

function Say([string]$msg, [string]$color = "Gray") {
    Write-Host $msg -ForegroundColor $color
    try {
        Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) -Encoding UTF8
    } catch { }
}

"=== desktop pet start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Set-Content -LiteralPath $log -Encoding UTF8

# ---------- 1. find an interpreter that has tkinter + PIL ----------
$candidates = New-Object System.Collections.Generic.List[string]
if ($env:DSH_PET_PYTHON) { $candidates.Add($env:DSH_PET_PYTHON) }
$candidates.AddRange([string[]]@(
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "C:\Python313\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe",
    "C:\Python310\python.exe",
    "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
))

$probe = "import tkinter, PIL, sys; print(sys.version.split()[0])"
$chosen = $null
$probeErrors = @()

foreach ($c in $candidates) {
    if ([string]::IsNullOrWhiteSpace($c)) { continue }
    if (-not (Test-Path -LiteralPath $c)) { continue }
    $out = & $c -c $probe 2>&1
    if ($LASTEXITCODE -eq 0) {
        $chosen = $c
        Say "interpreter: $c  (Python $out)" "Green"
        break
    }
    $probeErrors += ("{0} -> {1}" -f $c, ($out -join " | "))
}

if (-not $chosen) {
    foreach ($name in @("py", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $out = & $name -c $probe 2>&1
        if ($LASTEXITCODE -eq 0) {
            $chosen = $name
            Say "interpreter: $name  (Python $out)" "Green"
            break
        }
        $probeErrors += ("{0} -> {1}" -f $name, ($out -join " | "))
    }
}

if (-not $chosen) {
    Say "No usable Python found. Probe results:" "Red"
    foreach ($e in $probeErrors) { Say "  $e" "DarkGray" }
    Say ""
    Say "Fix (either one):" "Yellow"
    Say "  1) install Pillow for your Python:  python -m pip install --user pillow"
    Say "  2) point this launcher at a Python that already has it:"
    Say "     set DSH_PET_PYTHON=C:\path\to\python.exe"
    if ($KeepOpen) { Read-Host "Press Enter to exit" }
    exit 1
}

# ---------- 2. sanity check ----------
$petPy = Join-Path $here "pet.py"
if (-not (Test-Path -LiteralPath $petPy)) {
    Say "pet.py not found in $here" "Red"
    if ($KeepOpen) { Read-Host "Press Enter to exit" }
    exit 1
}

# ---------- 3. prefer a console-less runner ----------
$runner = $chosen
if ($chosen -like "*.exe") {
    $pythonw = Join-Path (Split-Path -Parent $chosen) "pythonw.exe"
    if (Test-Path -LiteralPath $pythonw) { $runner = $pythonw }
}
Say "runner: $runner" "Gray"

# ---------- 4. launch detached ----------
$petArgs = @($petPy)
if ($ExtraArgs) { $petArgs += ($ExtraArgs -split "\s+") }

$detacher = Join-Path $here "detach_launch.ps1"
$startedAt = Get-Date
$launched = $false

if (Test-Path -LiteralPath $detacher) {
    $psExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path -LiteralPath $psExe)) { $psExe = "powershell.exe" }
    $dArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $detacher,
               "-Exe", $runner, "-WorkDir", $here, "-ArgList") + $petArgs
    $dOut = & $psExe @dArgs 2>&1
    foreach ($line in $dOut) { Say ("  " + $line) "DarkGray" }
    if ($LASTEXITCODE -eq 0) { $launched = $true }
}

if (-not $launched) {
    Say "detach helper unavailable, starting as a normal child process" "Yellow"
    $startArgs = @{
        FilePath         = $runner
        ArgumentList     = $petArgs
        WorkingDirectory = $here
        PassThru         = $true
    }
    if ($runner -notlike "*pythonw.exe") { $startArgs.WindowStyle = "Hidden" }
    Start-Process @startArgs | Out-Null
}

# ---------- 5. confirm it is really running ----------
if ($ExtraArgs -like "*--selftest*") {
    Start-Sleep -Seconds 4
    Say "dry run finished; see pet.log for the selftest result." "Green"
    exit 0
}

$petProc = $null
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    $petProc = Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
        Where-Object { $_.StartTime -ge $startedAt.AddSeconds(-3) } |
        Select-Object -First 1
    if ($petProc) { break }
}

if (-not $petProc) {
    Say "no pet process appeared." "Red"
    Say "check pet_crash.log and pet.log in $here" "Yellow"
    if ($KeepOpen) { Read-Host "Press Enter to exit" }
    exit 1
}

Start-Sleep -Seconds 6
if ($null -eq (Get-Process -Id $petProc.Id -ErrorAction SilentlyContinue)) {
    Say "pet exited within seconds of starting (pid $($petProc.Id))." "Red"
    Say "check pet_crash.log and pet.log in $here" "Yellow"
    if ($KeepOpen) { Read-Host "Press Enter to exit" }
    exit 1
}

Say "OK - pet is running (pid $($petProc.Id)), detached from this console." "Green"
Say "You can close this window; the pet keeps running." "Gray"
Say "To stop the pet: right-click it and pick the exit item (last one)," "Gray"
Say "or run:  Get-Process pythonw | Stop-Process" "DarkGray"
exit 0
