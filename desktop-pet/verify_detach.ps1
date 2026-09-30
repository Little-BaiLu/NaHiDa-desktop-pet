# Verify that the pet really ends up with NO console.
#
# This is the property that matters: Windows sends CTRL_CLOSE_EVENT to every
# process attached to a console, so a console-less pet cannot be killed by
# closing the terminal window.
#
# ASCII only, CRLF only (PowerShell 5.1 parsing quirks).

param(
    [string]$WorkDir = "."
)

$src = @'
using System;
using System.Runtime.InteropServices;

public static class ConsoleCheck
{
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AttachConsole(uint dwProcessId);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool FreeConsole();

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern uint GetConsoleProcessList(uint[] lpdwProcessList, uint dwProcessCount);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);

    // Returns the number of processes attached to the console of pid.
    // 0 means that process has no console at all.
    public static int ConsoleProcessCount(uint pid)
    {
        FreeConsole();
        if (!AttachConsole(pid)) return 0;
        uint[] buf = new uint[64];
        uint n = GetConsoleProcessList(buf, 64);
        FreeConsole();
        return (int)n;
    }
}
'@

Add-Type -TypeDefinition $src -ErrorAction Stop

# start the pet detached
$detacher = Join-Path $PSScriptRoot "detach_launch.ps1"
$pythonw = Join-Path $env:USERPROFILE ".dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
$petPy = Join-Path (Resolve-Path -LiteralPath $WorkDir).Path "pet.py"

$out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $detacher `
    -Exe $pythonw -ArgList $petPy -WorkDir $WorkDir 2>&1
Write-Host ("detach launcher said: " + ($out -join " "))

Start-Sleep -Seconds 3

$pet = Get-Process -Name pythonw -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pet) {
    Write-Host "FAIL: no pythonw process is running"
    exit 1
}

$count = [ConsoleCheck]::ConsoleProcessCount([uint32]$pet.Id)
Write-Host ("pet pid {0}: console process count = {1}" -f $pet.Id, $count)

$self = [ConsoleCheck]::ConsoleProcessCount([uint32]$PID)
Write-Host ("this shell pid {0}: console process count = {1}" -f $PID, $self)

if ($count -eq 0) {
    Write-Host "PASS: the pet has no console, so closing a terminal cannot kill it."
    exit 0
}
Write-Host "FAIL: the pet is still attached to a console."
exit 1
