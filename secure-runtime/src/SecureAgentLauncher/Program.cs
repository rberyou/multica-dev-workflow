using System.Diagnostics;
using System.Text.Json;
using WorkflowSecureRuntime.Core;

return await LauncherProgram.RunAsync(args).ConfigureAwait(false);

internal static class LauncherProgram
{
    internal static async Task<int> RunAsync(string[] args)
    {
        try
        {
            var configPath = Environment.GetEnvironmentVariable("MULTICA_SECURE_CONFIG")
                ?? throw new InvalidOperationException("MULTICA_SECURE_CONFIG is required");
            var config = SecureRuntimeConfiguration.Load(configPath);
            Hashing.VerifyExecutable(config.CodexExecutable);
            var invocation = CommandPolicy.Validate(args);
            return invocation.Mode switch
            {
                LauncherMode.Probe => await RunProbeAsync(config, args).ConfigureAwait(false),
                LauncherMode.ProfileProbe => await RunProfileProbeAsync(config, args[1]).ConfigureAwait(false),
                _ => await RunTaskAsync(config, args).ConfigureAwait(false)
            };
        }
        catch (Exception error)
        {
            await Console.Error.WriteLineAsync("secure-agent-launcher: " + error.Message).ConfigureAwait(false);
            return 78;
        }
    }

    private static async Task<int> RunProfileProbeAsync(
        SecureRuntimeConfiguration config, string role)
    {
        if (!config.Profiles.TryGetValue(role, out var profile))
        {
            throw new InvalidOperationException("security probe selected an unknown profile");
        }
        var taskId = "probe-" + Guid.NewGuid().ToString("N");
        var prepared = TaskEnvironmentBuilder.Prepare(
            config, taskId, role, profile, EnvironmentPolicy.Current());
        try
        {
            return await RunCodexAsync(
                config.CodexExecutable.Path,
                new[] { "--strict-config", "--version" },
                config.WorkspacesRoot,
                prepared.Environment,
                null,
                null).ConfigureAwait(false);
        }
        finally
        {
            await DeleteTaskRootAsync(prepared.TaskRoot).ConfigureAwait(false);
        }
    }

    private static async Task<int> RunProbeAsync(SecureRuntimeConfiguration config, string[] args)
    {
        var root = Path.Combine(config.ServiceRoot, "probes", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Combine(root, "codex-home"));
        Directory.CreateDirectory(Path.Combine(root, "program-data", "OpenAI", "Codex"));
        await File.WriteAllTextAsync(
            Path.Combine(root, "program-data", "OpenAI", "Codex", "requirements.toml"),
            "allowed_approval_policies = [\"never\"]\nallowed_sandbox_modes = [\"read-only\"]\n").ConfigureAwait(false);
        try
        {
            var environment = EnvironmentPolicy.ForProbe(
                EnvironmentPolicy.Current(), root, Path.GetDirectoryName(config.CodexExecutable.Path)!);
            return await RunCodexAsync(config.CodexExecutable.Path, args, Environment.CurrentDirectory, environment, null, null)
                .ConfigureAwait(false);
        }
        finally
        {
            await DeleteTaskRootAsync(root).ConfigureAwait(false);
        }
    }

    private static async Task<int> RunTaskAsync(SecureRuntimeConfiguration config, string[] args)
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("secure task mode currently requires native Windows");
        }
        var launcher = ProcessInspector.Current();
        var parent = ProcessInspector.Inspect(launcher.ParentProcessId);
        ProcessInspector.VerifyImage(parent, config.MulticaExecutable);
        var source = EnvironmentPolicy.Current();
        var request = new BeginLaunchRequest
        {
            AgentId = Required(source, "MULTICA_AGENT_ID"),
            TaskId = Required(source, "MULTICA_TASK_ID"),
            WorkspaceId = Required(source, "MULTICA_WORKSPACE_ID"),
            MulticaToken = Required(source, "MULTICA_TOKEN"),
            Worktree = Path.GetFullPath(Environment.CurrentDirectory)
        };
        var begin = await BrokerClient.SendAsync<BeginLaunchRequest, BeginLaunchResponse>(
            config.BrokerPipeName, "begin_launch", request, CancellationToken.None).ConfigureAwait(false);
        if (!config.Profiles.TryGetValue(begin.Role, out var profile))
        {
            throw new InvalidOperationException("Broker selected an unknown security profile");
        }
        var prepared = TaskEnvironmentBuilder.Prepare(config, request.TaskId, begin.Role, profile, source);
        try
        {
            return await RunCodexAsync(
                config.CodexExecutable.Path,
                args.Concat(new[] { "--strict-config" }).ToArray(),
                request.Worktree,
                prepared.Environment,
                null,
                async process =>
                {
                    var child = ProcessInspector.Inspect(process.Id);
                    await BrokerClient.SendAsync<RegisterChildRequest, JsonElement>(
                        config.BrokerPipeName,
                        "register_child",
                        new RegisterChildRequest
                        {
                            CapabilityId = begin.CapabilityId,
                            Capability = begin.Capability,
                            ChildPid = child.ProcessId,
                            ChildStartedAtUnixMs = child.StartedAtUnixMs
                        },
                        CancellationToken.None).ConfigureAwait(false);
                }).ConfigureAwait(false);
        }
        finally
        {
            await DeleteTaskRootAsync(prepared.TaskRoot).ConfigureAwait(false);
        }
    }

    private static async Task<int> RunCodexAsync(
        string executable,
        IReadOnlyList<string> args,
        string workdir,
        IReadOnlyDictionary<string, string> environment,
        WindowsJob? job,
        Func<Process, Task>? started)
    {
        var info = new ProcessStartInfo(executable)
        {
            UseShellExecute = false,
            WorkingDirectory = workdir,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };
        foreach (var argument in args)
        {
            info.ArgumentList.Add(argument);
        }
        info.Environment.Clear();
        foreach (var item in environment)
        {
            info.Environment[item.Key] = item.Value;
        }
        using var process = Process.Start(info) ?? throw new InvalidOperationException("failed to start verified Codex");
        job?.Assign(process);
        if (started is not null)
        {
            await started(process).ConfigureAwait(false);
        }
        var stdin = Console.OpenStandardInput().CopyToAsync(process.StandardInput.BaseStream)
            .ContinueWith(_ => process.StandardInput.Close(), TaskScheduler.Default);
        var stdout = process.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
        var stderr = process.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
        await process.WaitForExitAsync().ConfigureAwait(false);
        await Task.WhenAll(stdout, stderr).ConfigureAwait(false);
        _ = stdin;
        return process.ExitCode;
    }

    private static string Required(IReadOnlyDictionary<string, string> source, string key) =>
        source.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value)
            ? value
            : throw new InvalidOperationException($"{key} is required for secure task mode");

    private static async Task DeleteTaskRootAsync(string path)
    {
        for (var attempt = 0; attempt < 5; attempt++)
        {
            try
            {
                if (Directory.Exists(path))
                {
                    Directory.Delete(path, true);
                }
                return;
            }
            catch (Exception error) when (
                (error is IOException or UnauthorizedAccessException) && attempt < 4)
            {
                await Task.Delay(TimeSpan.FromMilliseconds(200 * (attempt + 1)))
                    .ConfigureAwait(false);
            }
        }
        throw new InvalidOperationException(
            $"secure task credential cleanup failed: {path}");
    }
}
