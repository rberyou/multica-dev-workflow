using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace WorkflowSecureRuntime.Core;

public readonly record struct ProcessIdentity(
    int ProcessId,
    int ParentProcessId,
    string ImagePath,
    long StartedAtUnixMs);

public static class ProcessInspector
{
    public static ProcessIdentity Current() => Inspect(Environment.ProcessId);

    public static ProcessIdentity Inspect(int processId)
    {
        using var process = Process.GetProcessById(processId);
        var path = GetImagePath(processId);
        var start = new DateTimeOffset(process.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds();
        return new ProcessIdentity(processId, GetParentProcessId(processId), path, start);
    }

    public static bool IsDescendantOf(int processId, int ancestorPid, int maxDepth = 32)
    {
        var current = processId;
        for (var depth = 0; depth < maxDepth && current > 0; depth++)
        {
            if (current == ancestorPid)
            {
                return true;
            }
            current = GetParentProcessId(current);
        }
        return false;
    }

    public static int GetNamedPipeClientProcessId(SafePipeHandle handle)
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("named pipe client PID lookup requires Windows");
        }
        if (!Native.GetNamedPipeClientProcessId(handle, out var processId))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "GetNamedPipeClientProcessId failed");
        }
        return checked((int)processId);
    }

    public static void VerifyImage(ProcessIdentity process, VerifiedExecutable expected)
    {
        var observed = Path.GetFullPath(process.ImagePath);
        var required = Path.GetFullPath(expected.Path);
        if (!string.Equals(observed, required, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException($"unexpected process image: {observed}");
        }
        Hashing.VerifyExecutable(expected);
    }

    private static int GetParentProcessId(int processId)
    {
        if (!OperatingSystem.IsWindows())
        {
            return 0;
        }
        var snapshot = Native.CreateToolhelp32Snapshot(Native.Th32CsSnapProcess, 0);
        if (snapshot.IsInvalid)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateToolhelp32Snapshot failed");
        }
        using (snapshot)
        {
            var entry = new Native.ProcessEntry32 { DwSize = (uint)Marshal.SizeOf<Native.ProcessEntry32>() };
            if (!Native.Process32First(snapshot, ref entry))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Process32First failed");
            }
            do
            {
                if (entry.Th32ProcessId == (uint)processId)
                {
                    return checked((int)entry.Th32ParentProcessId);
                }
            } while (Native.Process32Next(snapshot, ref entry));
        }
        throw new InvalidOperationException($"process {processId} is not present in the process snapshot");
    }

    private static string GetImagePath(int processId)
    {
        if (!OperatingSystem.IsWindows())
        {
            using var process = Process.GetProcessById(processId);
            return process.MainModule?.FileName
                ?? throw new InvalidOperationException("process image is unavailable");
        }
        using var handle = Native.OpenProcess(Native.ProcessQueryLimitedInformation, false, (uint)processId);
        if (handle.IsInvalid)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "OpenProcess failed");
        }
        var size = 32768;
        var buffer = new char[size];
        if (!Native.QueryFullProcessImageName(handle, 0, buffer, ref size))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "QueryFullProcessImageName failed");
        }
        return new string(buffer, 0, size);
    }

    private static class Native
    {
        internal const uint Th32CsSnapProcess = 0x00000002;
        internal const uint ProcessQueryLimitedInformation = 0x1000;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        internal struct ProcessEntry32
        {
            internal uint DwSize;
            internal uint CntUsage;
            internal uint Th32ProcessId;
            internal UIntPtr Th32DefaultHeapId;
            internal uint Th32ModuleId;
            internal uint CntThreads;
            internal uint Th32ParentProcessId;
            internal int PcPriClassBase;
            internal uint DwFlags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
            internal string SzExeFile;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern SafeFileHandle CreateToolhelp32Snapshot(uint flags, uint processId);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool Process32First(SafeFileHandle snapshot, ref ProcessEntry32 entry);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool Process32Next(SafeFileHandle snapshot, ref ProcessEntry32 entry);

        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern SafeProcessHandle OpenProcess(uint access, bool inherit, uint processId);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool QueryFullProcessImageName(
            SafeProcessHandle process, uint flags, [Out] char[] path, ref int size);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool GetNamedPipeClientProcessId(SafePipeHandle pipe, out uint clientProcessId);
    }
}
