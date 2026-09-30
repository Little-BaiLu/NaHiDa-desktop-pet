# Detach a process from this console and from our process tree.
#
# Launching the pet as a normal child process is not enough: it shares our
# console, and closing the terminal sends CTRL_CLOSE_EVENT to every process in
# that console. On top of that, the hosting environment may put us inside a job
# object that kills all children when we exit.
#
# CreateProcess with DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
# (+ CREATE_BREAKAWAY_FROM_JOB) solves both: the pet gets its own console-less
# process, in its own process group, outside our job object.
#
# ASCII ONLY and CRLF only. Windows PowerShell 5.1 mis-parses LF-only scripts
# (here-strings and block comments swallow the rest of the file) and reads
# BOM-less files as ANSI, so keep both invariants.

param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [string[]]$ArgList = @(),
    [string]$WorkDir = ".",
    [switch]$NoBreakaway
)

$src = @'
using System;
using System.Runtime.InteropServices;

public static class DetachLaunch
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct STARTUPINFO
    {
        public int cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_INFORMATION
    {
        public IntPtr hProcess, hThread;
        public int dwProcessId, dwThreadId;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool CreateProcess(
        string lpApplicationName, string lpCommandLine,
        IntPtr lpProcessAttributes, IntPtr lpThreadAttributes,
        bool bInheritHandles, uint dwCreationFlags,
        IntPtr lpEnvironment, string lpCurrentDirectory,
        ref STARTUPINFO si, out PROCESS_INFORMATION pi);

    const uint DETACHED_PROCESS = 0x00000008;
    const uint CREATE_NO_WINDOW = 0x08000000;
    const uint CREATE_NEW_PROCESS_GROUP = 0x00000200;
    const uint CREATE_BREAKAWAY_FROM_JOB = 0x01000000;

    public static int Start(string exe, string commandLine, string cwd, bool noBreakaway)
    {
        STARTUPINFO si = new STARTUPINFO();
        si.cb = Marshal.SizeOf(si);
        PROCESS_INFORMATION pi;
        uint flags = DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP;
        if (!noBreakaway) flags |= CREATE_BREAKAWAY_FROM_JOB;

        bool ok = CreateProcess(exe, commandLine, IntPtr.Zero, IntPtr.Zero,
            false, flags, IntPtr.Zero, cwd, ref si, out pi);
        if (!ok && !noBreakaway)
        {
            // job object refused breakaway; retry without it
            flags = DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP;
            ok = CreateProcess(exe, commandLine, IntPtr.Zero, IntPtr.Zero,
                false, flags, IntPtr.Zero, cwd, ref si, out pi);
        }
        if (!ok) return -Marshal.GetLastWin32Error();
        return pi.dwProcessId;
    }
}
'@

Add-Type -TypeDefinition $src -ErrorAction Stop

$cmdLine = '"' + $Exe + '"'
foreach ($a in $ArgList) { $cmdLine = $cmdLine + ' "' + $a + '"' }

$rc = [DetachLaunch]::Start($Exe, $cmdLine, (Resolve-Path -LiteralPath $WorkDir).Path, [bool]$NoBreakaway)
if ($rc -lt 0) {
    Write-Host ("detach launch failed, win32 error " + (-$rc))
    exit 1
}
Write-Host ("detached pid " + $rc)
exit 0
