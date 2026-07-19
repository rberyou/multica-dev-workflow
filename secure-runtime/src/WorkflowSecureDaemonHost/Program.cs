using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using WorkflowSecureRuntime.Core;

return await SecureDaemonProgram.RunAsync(args).ConfigureAwait(false);

internal static class SecureDaemonProgram
{
    internal const string ServiceName = "MulticaWorkflowSecureRuntime";

    internal static async Task<int> RunAsync(string[] args)
    {
        try
        {
            var config = Option(args, "--config");
            if (args.Contains("--service", StringComparer.Ordinal))
            {
                if (!OperatingSystem.IsWindows())
                {
                    throw new PlatformNotSupportedException("Windows service mode requires Windows");
                }
                WindowsService.Run(config);
                return 0;
            }
            using var cancellation = new CancellationTokenSource();
            Console.CancelKeyPress += (_, eventArgs) =>
            {
                eventArgs.Cancel = true;
                cancellation.Cancel();
            };
            await new SecureRuntimeSupervisor(config).RunAsync(
                cancellation.Token).ConfigureAwait(false);
            return 0;
        }
        catch (Exception error)
        {
            await Console.Error.WriteLineAsync("workflow-secure-daemon-host: " + error.Message).ConfigureAwait(false);
            return 78;
        }
    }

    private static string Option(IReadOnlyList<string> args, string name)
    {
        for (var index = 0; index < args.Count - 1; index++)
        {
            if (args[index] == name)
            {
                return Path.GetFullPath(args[index + 1]);
            }
        }
        throw new InvalidOperationException($"missing {name}");
    }
}

internal sealed class SecureRuntimeSupervisor
{
    private readonly string _configPath;

    internal SecureRuntimeSupervisor(string configPath)
    {
        _configPath = configPath;
    }

    internal async Task RunAsync(
        CancellationToken cancellationToken, Action? ready = null)
    {
        var config = SecureRuntimeConfiguration.Load(_configPath);
        foreach (var executable in new[]
                 {
                     config.MulticaExecutable, config.CodexExecutable, config.GitExecutable,
                     config.LauncherExecutable, config.BrokerExecutable, config.ConsoleExecutable
                 })
        {
            Hashing.VerifyExecutable(executable);
        }
        Directory.CreateDirectory(config.ServiceRoot);
        Directory.CreateDirectory(config.WorkspacesRoot);
        var logs = Path.Combine(config.ServiceRoot, "logs");
        Directory.CreateDirectory(logs);

        using var broker = StartBroker(config, logs);
        await Task.Delay(TimeSpan.FromSeconds(1), cancellationToken).ConfigureAwait(false);
        if (broker.HasExited)
        {
            throw new InvalidOperationException("Token Broker exited during startup");
        }
        using var multica = StartMultica(config, logs);
        await Task.Delay(TimeSpan.FromSeconds(2), cancellationToken).ConfigureAwait(false);
        if (broker.HasExited || multica.HasExited)
        {
            throw new InvalidOperationException(
                $"secure runtime child exited during startup: broker={ExitCode(broker)}, multica={ExitCode(multica)}");
        }
        ready?.Invoke();
        using var registration = cancellationToken.Register(() =>
        {
            KillTree(multica);
            KillTree(broker);
        });
        var brokerExit = broker.WaitForExitAsync(cancellationToken);
        var multicaExit = multica.WaitForExitAsync(cancellationToken);
        await Task.WhenAny(brokerExit, multicaExit).ConfigureAwait(false);
        if (!cancellationToken.IsCancellationRequested)
        {
            KillTree(multica);
            KillTree(broker);
            throw new InvalidOperationException(
                $"secure runtime child exited unexpectedly: broker={broker.ExitCode}, multica={multica.ExitCode}");
        }
    }

    private Process StartBroker(SecureRuntimeConfiguration config, string logs)
    {
        var environment = EnvironmentPolicy.ForProbe(
            EnvironmentPolicy.Current(),
            Path.Combine(config.ServiceRoot, "broker-home"),
            Path.GetDirectoryName(config.BrokerExecutable.Path)!);
        environment["MULTICA_SECURE_CONFIG"] = _configPath;
        return StartChild(
            config.BrokerExecutable.Path,
            new[] { "serve" },
            config.ServiceRoot,
            environment,
            Path.Combine(logs, "broker.stdout.log"),
            Path.Combine(logs, "broker.stderr.log"));
    }

    private Process StartMultica(SecureRuntimeConfiguration config, string logs)
    {
        var environment = EnvironmentPolicy.ForDaemon(config, EnvironmentPolicy.Current());
        environment["MULTICA_SECURE_CONFIG"] = _configPath;
        return StartChild(
            config.MulticaExecutable.Path,
            new[]
            {
                "--profile", config.MulticaProfile,
                "daemon", "start", "--foreground", "--no-auto-update",
                "--daemon-id", config.MulticaDaemonId,
                "--runtime-name", config.MulticaRuntimeName,
                "--max-concurrent-tasks", "2"
            },
            config.ServiceRoot,
            environment,
            Path.Combine(logs, "multica.stdout.log"),
            Path.Combine(logs, "multica.stderr.log"));
    }

    private static Process StartChild(
        string executable,
        IEnumerable<string> arguments,
        string workdir,
        IReadOnlyDictionary<string, string> environment,
        string stdoutPath,
        string stderrPath)
    {
        var start = new ProcessStartInfo(executable)
        {
            WorkingDirectory = workdir,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };
        foreach (var argument in arguments)
        {
            start.ArgumentList.Add(argument);
        }
        start.Environment.Clear();
        foreach (var item in environment)
        {
            start.Environment[item.Key] = item.Value;
        }
        var process = Process.Start(start) ?? throw new InvalidOperationException("failed to start secure runtime child");
        _ = PumpAsync(process.StandardOutput, stdoutPath);
        _ = PumpAsync(process.StandardError, stderrPath);
        return process;
    }

    private static async Task PumpAsync(StreamReader reader, string path)
    {
        await using var stream = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite);
        await using var writer = new StreamWriter(stream) { AutoFlush = true };
        while (await reader.ReadLineAsync().ConfigureAwait(false) is { } line)
        {
            await writer.WriteLineAsync(line).ConfigureAwait(false);
        }
    }

    private static void KillTree(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(true);
            }
        }
        catch (InvalidOperationException)
        {
        }
    }

    private static string ExitCode(Process process) =>
        process.HasExited ? process.ExitCode.ToString() : "running";
}

internal static class WindowsService
{
    private static ServiceMainDelegate? _serviceMain;
    private static ServiceControlHandler? _handler;
    private static IntPtr _statusHandle;
    private static CancellationTokenSource? _cancellation;
    private static string _configPath = "";

    internal static void Run(string configPath)
    {
        _configPath = configPath;
        _serviceMain = ServiceMain;
        var table = new[]
        {
            new ServiceTableEntry { Name = SecureDaemonProgram.ServiceName, Main = _serviceMain },
            new ServiceTableEntry { Name = null, Main = null }
        };
        if (!StartServiceCtrlDispatcher(table))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "StartServiceCtrlDispatcher failed");
        }
    }

    private static void ServiceMain(int _, IntPtr __)
    {
        _handler = Handler;
        _statusHandle = RegisterServiceCtrlHandler(SecureDaemonProgram.ServiceName, _handler);
        if (_statusHandle == IntPtr.Zero)
        {
            return;
        }
        SetStatus(ServiceState.StartPending, 30000);
        _cancellation = new CancellationTokenSource();
        try
        {
            new SecureRuntimeSupervisor(_configPath).RunAsync(
                _cancellation.Token,
                () => SetStatus(ServiceState.Running, 0)).GetAwaiter().GetResult();
            SetStatus(ServiceState.Stopped, 0);
        }
        catch
        {
            SetStatus(ServiceState.Stopped, 0, 1);
        }
    }

    private static void Handler(uint control)
    {
        if (control is 1 or 5)
        {
            SetStatus(ServiceState.StopPending, 30000);
            _cancellation?.Cancel();
        }
    }

    private static void SetStatus(ServiceState state, uint waitHint, uint exitCode = 0)
    {
        var status = new ServiceStatus
        {
            ServiceType = 0x10,
            CurrentState = (uint)state,
            ControlsAccepted = state == ServiceState.Running ? 0x1u : 0,
            Win32ExitCode = exitCode,
            WaitHint = waitHint
        };
        _ = SetServiceStatus(_statusHandle, ref status);
    }

    private enum ServiceState : uint
    {
        Stopped = 1,
        StartPending = 2,
        StopPending = 3,
        Running = 4
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct ServiceTableEntry
    {
        [MarshalAs(UnmanagedType.LPWStr)]
        internal string? Name;
        internal ServiceMainDelegate? Main;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ServiceStatus
    {
        internal uint ServiceType;
        internal uint CurrentState;
        internal uint ControlsAccepted;
        internal uint Win32ExitCode;
        internal uint ServiceSpecificExitCode;
        internal uint CheckPoint;
        internal uint WaitHint;
    }

    private delegate void ServiceMainDelegate(int argc, IntPtr argv);
    private delegate void ServiceControlHandler(uint control);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool StartServiceCtrlDispatcher([In] ServiceTableEntry[] serviceTable);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr RegisterServiceCtrlHandler(string serviceName, ServiceControlHandler handler);

    [DllImport("advapi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetServiceStatus(IntPtr serviceStatusHandle, ref ServiceStatus serviceStatus);
}
