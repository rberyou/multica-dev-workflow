using System.Collections.Concurrent;
using System.IO.Pipes;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace WorkflowSecureRuntime.Core;

public sealed class BrokerServer
{
    private readonly SecureRuntimeConfiguration _config;
    private readonly AgentBindings _bindings;
    private readonly byte[] _capabilityKey;
    private readonly MulticaTaskVerifier _taskVerifier;
    private readonly GitHubBrokerOperations _github;
    private readonly AuditLog _audit;
    private readonly ConcurrentDictionary<string, LaunchSession> _sessions = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, string> _activeTasks = new(StringComparer.OrdinalIgnoreCase);

    public BrokerServer(
        SecureRuntimeConfiguration config,
        AgentBindings bindings,
        byte[] capabilityKey,
        MulticaTaskVerifier taskVerifier,
        GitHubBrokerOperations github,
        AuditLog audit)
    {
        _config = config;
        _bindings = bindings;
        _capabilityKey = capabilityKey;
        _taskVerifier = taskVerifier;
        _github = github;
        _audit = audit;
    }

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            var pipe = new NamedPipeServerStream(
                _config.BrokerPipeName,
                PipeDirection.InOut,
                NamedPipeServerStream.MaxAllowedServerInstances,
                PipeTransmissionMode.Byte,
                PipeOptions.Asynchronous);
            await pipe.WaitForConnectionAsync(cancellationToken).ConfigureAwait(false);
            _ = HandleConnectionAsync(pipe, cancellationToken);
        }
    }

    private async Task HandleConnectionAsync(NamedPipeServerStream pipe, CancellationToken cancellationToken)
    {
        await using (pipe.ConfigureAwait(false))
        {
            try
            {
                var clientPid = ProcessInspector.GetNamedPipeClientProcessId(pipe.SafePipeHandle);
                using var reader = new StreamReader(pipe, Encoding.UTF8, false, 65536, true);
                var line = await reader.ReadLineAsync(cancellationToken).ConfigureAwait(false);
                if (line is null || line.Length > 262144)
                {
                    throw new InvalidOperationException("Broker request is empty or too large");
                }
                using var document = JsonDocument.Parse(line);
                var operation = document.RootElement.GetProperty("operation").GetString() ?? "";
                var payload = document.RootElement.GetProperty("payload");
                var response = await DispatchAsync(clientPid, operation, payload, cancellationToken).ConfigureAwait(false);
                var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(response, JsonDefaults.Options) + "\n");
                await pipe.WriteAsync(bytes, cancellationToken).ConfigureAwait(false);
                await pipe.FlushAsync(cancellationToken).ConfigureAwait(false);
            }
            catch (Exception error) when (error is not OperationCanceledException)
            {
                var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(
                    BrokerResponse.Failure(error.Message), JsonDefaults.Options) + "\n");
                await pipe.WriteAsync(bytes, cancellationToken).ConfigureAwait(false);
                await pipe.FlushAsync(cancellationToken).ConfigureAwait(false);
            }
        }
    }

    private async Task<BrokerResponse> DispatchAsync(
        int clientPid,
        string operation,
        JsonElement payload,
        CancellationToken cancellationToken)
    {
        return operation switch
        {
            "begin_launch" => BrokerResponse.Success(await BeginLaunchAsync(
                clientPid, payload.Deserialize<BeginLaunchRequest>(JsonDefaults.Options)
                    ?? throw new InvalidOperationException("begin_launch payload is invalid"), cancellationToken).ConfigureAwait(false)),
            "register_child" => BrokerResponse.Success(await RegisterChildAsync(
                clientPid, payload.Deserialize<RegisterChildRequest>(JsonDefaults.Options)
                    ?? throw new InvalidOperationException("register_child payload is invalid"), cancellationToken).ConfigureAwait(false)),
            "push_task_branch" => BrokerResponse.Success(await _github.PushTaskBranchAsync(
                AuthorizeAgentClient(clientPid), payload.Deserialize<PushBranchRequest>(JsonDefaults.Options)
                    ?? throw new InvalidOperationException("push payload is invalid"), cancellationToken).ConfigureAwait(false)),
            "upsert_pr" => BrokerResponse.Success(await _github.UpsertPullRequestAsync(
                AuthorizeAgentClient(clientPid), payload.Deserialize<UpsertPullRequest>(JsonDefaults.Options)
                    ?? throw new InvalidOperationException("PR payload is invalid"), cancellationToken).ConfigureAwait(false)),
            "read_ci" => BrokerResponse.Success(await _github.ReadCiAsync(
                AuthorizeAgentClient(clientPid), payload.Deserialize<ReadCiRequest>(JsonDefaults.Options)
                    ?? throw new InvalidOperationException("CI payload is invalid"), cancellationToken).ConfigureAwait(false)),
            _ => throw new InvalidOperationException("Broker operation is not allowed")
        };
    }

    private async Task<BeginLaunchResponse> BeginLaunchAsync(
        int clientPid,
        BeginLaunchRequest request,
        CancellationToken cancellationToken)
    {
        var launcher = ProcessInspector.Inspect(clientPid);
        ProcessInspector.VerifyImage(launcher, _config.LauncherExecutable);
        var parent = ProcessInspector.Inspect(launcher.ParentProcessId);
        ProcessInspector.VerifyImage(parent, _config.MulticaExecutable);
        if (!_bindings.Agents.TryGetValue(request.AgentId, out var role) ||
            !_config.Profiles.TryGetValue(role, out var profile))
        {
            throw new InvalidOperationException("Agent is not bound to an approved security profile");
        }
        var worktree = PathPolicy.RequireDirectoryUnderRoot(
            _config.WorkspacesRoot, request.Worktree, "task worktree");
        if (!_activeTasks.TryAdd(request.TaskId, "pending"))
        {
            throw new InvalidOperationException("task already has an active launch capability");
        }
        WindowsJob? pendingJob = null;
        try
        {
            var task = await _taskVerifier.VerifyAsync(_config, request, cancellationToken).ConfigureAwait(false);
            if (!string.Equals(task.RuntimeId, _bindings.RuntimeId, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("task runtime does not match the secure Runtime binding");
            }
            var now = DateTimeOffset.UtcNow;
            var job = WindowsJob.Create($"Global\\MulticaWorkflow-{Guid.NewGuid():N}");
            pendingJob = job;
            using (var launcherProcess = System.Diagnostics.Process.GetProcessById(launcher.ProcessId))
            {
                job.Assign(launcherProcess);
            }
            var capability = new LaunchCapability
            {
                CapabilityId = Guid.NewGuid().ToString("N"),
                DaemonId = _config.MulticaDaemonId,
                RuntimeId = task.RuntimeId,
                AgentId = request.AgentId,
                TaskId = request.TaskId,
                WorkspaceId = request.WorkspaceId,
                Role = role,
                Repository = _config.Repository,
                Worktree = worktree,
                LauncherPid = launcher.ProcessId,
                LauncherStartedAtUnixMs = launcher.StartedAtUnixMs,
                JobName = job.Name,
                Nonce = Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant(),
                IssuedAtUnixSeconds = now.ToUnixTimeSeconds(),
                ExpiresAtUnixSeconds = now.AddHours(8).ToUnixTimeSeconds()
            };
            var signed = CapabilitySigner.Sign(capability, _capabilityKey);
            var session = new LaunchSession
            {
                Capability = capability,
                SignedCapability = signed,
                Profile = profile,
                Worktree = worktree,
                Job = job
            };
            if (!_sessions.TryAdd(capability.CapabilityId, session))
            {
                throw new InvalidOperationException("failed to register launch capability");
            }
            _activeTasks[request.TaskId] = capability.CapabilityId;
            _ = MonitorSessionAsync(session);
            pendingJob = null;
            await _audit.WriteAsync("launch.begin", new
            {
                capability.CapabilityId,
                capability.RuntimeId,
                capability.AgentId,
                capability.TaskId,
                capability.Role,
                capability.Worktree,
                launcher_pid = launcher.ProcessId
            }, cancellationToken).ConfigureAwait(false);
            return new BeginLaunchResponse
            {
                CapabilityId = capability.CapabilityId,
                Capability = signed,
                JobName = capability.JobName,
                RuntimeId = capability.RuntimeId,
                Role = capability.Role,
                ExpiresAtUnixSeconds = capability.ExpiresAtUnixSeconds
            };
        }
        catch
        {
            pendingJob?.Dispose();
            _activeTasks.TryRemove(request.TaskId, out _);
            throw;
        }
    }

    private async Task MonitorSessionAsync(LaunchSession session)
    {
        try
        {
            using var launcher = System.Diagnostics.Process.GetProcessById(
                session.Capability.LauncherPid);
            await launcher.WaitForExitAsync().ConfigureAwait(false);
        }
        catch (ArgumentException)
        {
        }
        finally
        {
            _sessions.TryRemove(session.Capability.CapabilityId, out _);
            _activeTasks.TryRemove(session.Capability.TaskId, out _);
            session.Job.Dispose();
            try
            {
                await _audit.WriteAsync("launch.ended", new
                {
                    session.Capability.CapabilityId,
                    session.Capability.TaskId
                }, CancellationToken.None).ConfigureAwait(false);
            }
            catch (IOException)
            {
            }
        }
    }

    private async Task<object> RegisterChildAsync(
        int clientPid,
        RegisterChildRequest request,
        CancellationToken cancellationToken)
    {
        if (!_sessions.TryGetValue(request.CapabilityId, out var session) || session.ChildRegistered)
        {
            throw new InvalidOperationException("launch capability is missing, replayed, or already registered");
        }
        var verified = CapabilitySigner.Verify(request.Capability, _capabilityKey, DateTimeOffset.UtcNow);
        if (!string.Equals(verified.CapabilityId, session.Capability.CapabilityId, StringComparison.Ordinal) ||
            !string.Equals(request.Capability, session.SignedCapability, StringComparison.Ordinal))
        {
            throw new InvalidOperationException("launch capability does not match the pending session");
        }
        var launcher = ProcessInspector.Inspect(clientPid);
        if (launcher.ProcessId != session.Capability.LauncherPid ||
            launcher.StartedAtUnixMs != session.Capability.LauncherStartedAtUnixMs)
        {
            throw new InvalidOperationException("register_child was not sent by the original Launcher process");
        }
        var child = ProcessInspector.Inspect(request.ChildPid);
        if (child.ParentProcessId != launcher.ProcessId || child.StartedAtUnixMs != request.ChildStartedAtUnixMs)
        {
            throw new InvalidOperationException("Codex child identity does not match the Launcher process");
        }
        if (!WindowsJob.Contains(session.Capability.JobName, child.ProcessId))
        {
            throw new InvalidOperationException("Codex child is not in the capability-bound Job Object");
        }
        session.CodexProcessId = child.ProcessId;
        session.CodexStartedAtUnixMs = child.StartedAtUnixMs;
        session.ChildRegistered = true;
        await _audit.WriteAsync("launch.child_registered", new
        {
            session.Capability.CapabilityId,
            codex_pid = child.ProcessId,
            session.Capability.JobName
        }, cancellationToken).ConfigureAwait(false);
        return new { registered = true };
    }

    private LaunchSession AuthorizeAgentClient(int clientPid)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var matches = _sessions.Values.Where(session =>
                session.ChildRegistered &&
                session.Capability.ExpiresAtUnixSeconds > now &&
                ProcessInspector.IsDescendantOf(clientPid, session.CodexProcessId) &&
                WindowsJob.Contains(session.Capability.JobName, clientPid))
            .ToArray();
        if (matches.Length != 1)
        {
            throw new InvalidOperationException("Broker client is not inside exactly one active secure task Job");
        }
        var session = matches[0];
        if (session.Profile.GitHubMode != "maintainer_broker")
        {
            throw new InvalidOperationException("this security profile has no mutating GitHub Broker lease");
        }
        return session;
    }
}
