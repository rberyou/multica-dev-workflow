using System.Buffers.Binary;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using WorkflowSecureRuntime.Core;

var tests = new (string Name, Action Run)[]
{
    ("command policy permits only fixed probes and app-server", TestCommandPolicy),
    ("task environment excludes host GitHub credentials", TestEnvironmentPolicy),
    ("capability signatures reject tampering and expiry", TestCapability),
    ("repository contract rejects ref and URL injection", TestRepositoryContract),
    ("push snapshots reject Git object alternates", TestGitObjectSnapshot),
    ("private and local proxy destinations are denied", TestPrivateAddresses),
    ("proxy binds CONNECT authority to TLS SNI", TestTlsServerName),
    ("worktrees cannot escape the secure root", TestPathPolicy),
    ("agent bindings require the dedicated Runtime identity", TestAgentBindings),
    ("workflow bundle copies only hash-bound skill files", TestBundleManifest),
    ("task config selects profiles without legacy sandbox override", TestTaskEnvironment)
};

var failures = 0;
foreach (var test in tests)
{
    try
    {
        test.Run();
        Console.WriteLine($"PASS {test.Name}");
    }
    catch (Exception error)
    {
        failures++;
        Console.Error.WriteLine($"FAIL {test.Name}: {error.Message}");
    }
}
return failures == 0 ? 0 : 1;

static void TestCommandPolicy()
{
    Equal(LauncherMode.Probe, CommandPolicy.Validate(new[] { "--version" }).Mode);
    Equal(LauncherMode.Probe, CommandPolicy.Validate(new[] { "debug", "models", "--bundled" }).Mode);
    Equal(LauncherMode.ProfileProbe, CommandPolicy.Validate(new[] { "--workflow-security-probe", "workflow_reviewer" }).Mode);
    Equal(LauncherMode.Task, CommandPolicy.Validate(new[] { "app-server", "--listen", "stdio://" }).Mode);
    Throws(() => CommandPolicy.Validate(new[] { "app-server", "--listen", "stdio://", "--yolo" }));
    Throws(() => CommandPolicy.Validate(new[] { "app-server", "-c", "sandbox_mode=\"danger-full-access\"" }));
    Throws(() => CommandPolicy.Validate(new[] { "--dangerously-bypass-approvals-and-sandbox" }));
}

static void TestEnvironmentPolicy()
{
    var taskRoot = Path.Combine(Path.GetTempPath(), "runtime", "task");
    var codexHome = Path.Combine(taskRoot, "codex");
    var bin = Path.Combine(taskRoot, "bin");
    var source = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
    {
        ["SystemRoot"] = "C:\\Windows",
        ["WINDIR"] = "C:\\Windows",
        ["COMSPEC"] = "C:\\Windows\\System32\\cmd.exe",
        ["MULTICA_TOKEN"] = "mat_example_token_for_test",
        ["MULTICA_AGENT_ID"] = "agent",
        ["MULTICA_TASK_ID"] = "task",
        ["MULTICA_WORKSPACE_ID"] = "workspace",
        ["GH_TOKEN"] = "forbidden",
        ["GITHUB_TOKEN"] = "forbidden",
        ["SSH_AUTH_SOCK"] = "forbidden",
        ["USERPROFILE"] = "C:\\Users\\host"
    };
    var result = EnvironmentPolicy.ForTask(
        source, taskRoot, codexHome, bin, 46183);
    True(result.ContainsKey("MULTICA_TOKEN"));
    False(result.ContainsKey("GH_TOKEN"));
    False(result.ContainsKey("GITHUB_TOKEN"));
    False(result.ContainsKey("SSH_AUTH_SOCK"));
    Equal(Path.Combine(taskRoot, "command-home"), result["USERPROFILE"]);
    Equal("", result["NO_PROXY"]);
}

static void TestCapability()
{
    var now = DateTimeOffset.UtcNow;
    var key = RandomNumberGenerator.GetBytes(32);
    var capability = new LaunchCapability
    {
        CapabilityId = "cap",
        DaemonId = "daemon",
        RuntimeId = "runtime",
        AgentId = "agent",
        TaskId = "task",
        WorkspaceId = "workspace",
        Role = "workflow_maintainer",
        Repository = "owner/repo",
        Worktree = "C:\\work",
        LauncherPid = 10,
        LauncherStartedAtUnixMs = now.ToUnixTimeMilliseconds(),
        JobName = "job",
        Nonce = "nonce",
        IssuedAtUnixSeconds = now.ToUnixTimeSeconds(),
        ExpiresAtUnixSeconds = now.AddMinutes(5).ToUnixTimeSeconds()
    };
    var signed = CapabilitySigner.Sign(capability, key);
    Equal("cap", CapabilitySigner.Verify(signed, key, now).CapabilityId);
    Throws(() => CapabilitySigner.Verify(signed[..^1] + (signed[^1] == 'a' ? 'b' : 'a'), key, now));
    Throws(() => CapabilitySigner.Verify(signed, key, now.AddMinutes(10)));
}

static void TestRepositoryContract()
{
    True(RepositoryContract.TryParse("owner/repo", out var repository));
    Equal("owner", repository.Owner);
    False(RepositoryContract.TryParse("https://github.com/owner/repo", out _));
    False(RepositoryContract.TryParse("owner/repo/refs/tags/v1", out _));
    False(RepositoryContract.TryParse("owner/repo?token=x", out _));
}

static void TestGitObjectSnapshot()
{
    using var temp = new TempDirectory();
    var source = Path.Combine(temp.Path, "objects");
    var info = Path.Combine(source, "info");
    var loose = Path.Combine(source, "ab");
    Directory.CreateDirectory(info);
    Directory.CreateDirectory(loose);
    File.WriteAllText(Path.Combine(loose, "object"), "data");
    File.WriteAllText(Path.Combine(info, "alternates"), "C:/external/objects");
    var denied = Path.Combine(temp.Path, "denied");
    Throws(() => GitHubBrokerOperations.CopyGitObjects(source, denied));
    False(File.Exists(Path.Combine(denied, "info", "alternates")));

    File.Delete(Path.Combine(info, "alternates"));
    var accepted = Path.Combine(temp.Path, "accepted");
    GitHubBrokerOperations.CopyGitObjects(source, accepted);
    True(File.Exists(Path.Combine(accepted, "ab", "object")));
    False(File.Exists(Path.Combine(accepted, "info", "alternates")));
    False(File.Exists(Path.Combine(accepted, "info", "http-alternates")));
}

static void TestPrivateAddresses()
{
    True(AllowlistProxy.IsPrivateOrLocal(System.Net.IPAddress.Loopback));
    True(AllowlistProxy.IsPrivateOrLocal(System.Net.IPAddress.Parse("10.1.2.3")));
    True(AllowlistProxy.IsPrivateOrLocal(System.Net.IPAddress.Parse("172.20.1.2")));
    True(AllowlistProxy.IsPrivateOrLocal(System.Net.IPAddress.Parse("192.168.1.2")));
    True(AllowlistProxy.IsPrivateOrLocal(System.Net.IPAddress.Parse("100.64.1.2")));
    True(AllowlistProxy.IsPrivateOrLocal(System.Net.IPAddress.Parse("fc00::1")));
    True(AllowlistProxy.IsPrivateOrLocal(System.Net.IPAddress.Parse("::ffff:127.0.0.1")));
    False(AllowlistProxy.IsPrivateOrLocal(System.Net.IPAddress.Parse("8.8.8.8")));
}

static void TestTlsServerName()
{
    var hello = BuildClientHello("api.github.com");
    Equal("api.github.com", AllowlistProxy.ParseTlsServerName(hello));
    Throws(() => AllowlistProxy.ParseTlsServerName(BuildClientHello("")));
    Throws(() => AllowlistProxy.ParseTlsServerName(
        BuildClientHello("api.github.com", duplicateExtension: true)));
    Throws(() => AllowlistProxy.ParseTlsServerName(
        BuildClientHello("api.github.com", duplicateName: true)));
}

static byte[] BuildClientHello(
    string host, bool duplicateExtension = false, bool duplicateName = false)
{
    var hostBytes = Encoding.ASCII.GetBytes(host);
    using var names = new MemoryStream();
    var count = duplicateName ? 2 : 1;
    for (var index = 0; index < count; index++)
    {
        names.WriteByte(0);
        WriteUInt16(names, checked((ushort)hostBytes.Length));
        names.Write(hostBytes);
    }
    using var extensionData = new MemoryStream();
    WriteUInt16(extensionData, checked((ushort)names.Length));
    names.Position = 0;
    names.CopyTo(extensionData);

    using var extensions = new MemoryStream();
    var extensionCount = duplicateExtension ? 2 : 1;
    for (var index = 0; index < extensionCount; index++)
    {
        WriteUInt16(extensions, 0);
        WriteUInt16(extensions, checked((ushort)extensionData.Length));
        extensionData.Position = 0;
        extensionData.CopyTo(extensions);
    }

    using var body = new MemoryStream();
    body.Write(new byte[] { 0x03, 0x03 });
    body.Write(new byte[32]);
    body.WriteByte(0);
    WriteUInt16(body, 2);
    body.Write(new byte[] { 0x13, 0x01 });
    body.WriteByte(1);
    body.WriteByte(0);
    WriteUInt16(body, checked((ushort)extensions.Length));
    extensions.Position = 0;
    extensions.CopyTo(body);

    using var result = new MemoryStream();
    result.WriteByte(1);
    var length = checked((int)body.Length);
    result.WriteByte((byte)(length >> 16));
    result.WriteByte((byte)(length >> 8));
    result.WriteByte((byte)length);
    body.Position = 0;
    body.CopyTo(result);
    return result.ToArray();
}

static void WriteUInt16(Stream stream, ushort value)
{
    Span<byte> bytes = stackalloc byte[2];
    BinaryPrimitives.WriteUInt16BigEndian(bytes, value);
    stream.Write(bytes);
}

static void TestPathPolicy()
{
    using var temp = new TempDirectory();
    var root = Path.Combine(temp.Path, "root");
    var task = Path.Combine(root, "task");
    Directory.CreateDirectory(task);
    Equal(Path.GetFullPath(task), PathPolicy.RequireDirectoryUnderRoot(root, task, "task"));
    Throws(() => PathPolicy.RequireDirectoryUnderRoot(root, temp.Path, "task"));
}

static void TestAgentBindings()
{
    using var temp = new TempDirectory();
    var path = Path.Combine(temp.Path, "bindings.json");
    var runtime = Guid.NewGuid();
    var agent = Guid.NewGuid();
    File.WriteAllText(path, JsonSerializer.Serialize(new
    {
        schema_version = 1,
        runtime_id = runtime,
        agents = new Dictionary<string, string> { [agent.ToString()] = "workflow_maintainer" }
    }));
    Equal(runtime.ToString(), AgentBindings.Load(path).RuntimeId);
    File.WriteAllText(path, JsonSerializer.Serialize(new
    {
        schema_version = 1,
        agents = new Dictionary<string, string> { [agent.ToString()] = "workflow_maintainer" }
    }));
    Throws(() => AgentBindings.Load(path));
}

static void TestBundleManifest()
{
    using var temp = new TempDirectory();
    var bundle = Path.Combine(temp.Path, "bundle");
    var source = Path.Combine(bundle, "skills", "example", "SKILL.md");
    Directory.CreateDirectory(Path.GetDirectoryName(source)!);
    File.WriteAllText(source, "example");
    var manifestPath = Path.Combine(temp.Path, "manifest.json");
    File.WriteAllText(manifestPath, JsonSerializer.Serialize(new
    {
        schema_version = 1,
        files = new Dictionary<string, string>
        {
            ["skills/example/SKILL.md"] = Hashing.Sha256File(source)
        }
    }));
    var destination = Path.Combine(temp.Path, "destination");
    WorkflowBundleManifest.Load(manifestPath).VerifyAndCopySkills(bundle, new[] { "example" }, destination);
    Equal("example", File.ReadAllText(Path.Combine(destination, "skills", "example", "SKILL.md")));
    File.WriteAllText(source, "tampered");
    Throws(() => WorkflowBundleManifest.Load(manifestPath).VerifyAndCopySkills(bundle, new[] { "example" }, destination));
}

static void TestTaskEnvironment()
{
    using var temp = new TempDirectory();
    var files = new Dictionary<string, string>();
    var bundle = Path.Combine(temp.Path, "bundle");
    var skill = Path.Combine(bundle, "skills", "multica-workflow-maintainer", "SKILL.md");
    Directory.CreateDirectory(Path.GetDirectoryName(skill)!);
    File.WriteAllText(skill, "maintainer");
    files["skills/multica-workflow-maintainer/SKILL.md"] = Hashing.Sha256File(skill);
    var manifest = Path.Combine(temp.Path, "manifest.json");
    File.WriteAllText(manifest, JsonSerializer.Serialize(new { schema_version = 1, files }));
    var auth = Path.Combine(temp.Path, "auth.json");
    File.WriteAllText(auth, "{}");
    var executable = Path.Combine(temp.Path, "tool.exe");
    File.WriteAllText(executable, "tool");
    var hash = Hashing.Sha256File(executable);
    var codexProbe = Environment.GetEnvironmentVariable("WORKFLOW_CODEX_EXECUTABLE");
    var codexExecutable = !string.IsNullOrWhiteSpace(codexProbe) && File.Exists(codexProbe)
        ? Path.GetFullPath(codexProbe)
        : executable;
    var config = new SecureRuntimeConfiguration
    {
        SchemaVersion = 1,
        Repository = "owner/repo",
        ServiceRoot = Path.Combine(temp.Path, "service"),
        WorkspacesRoot = Path.Combine(temp.Path, "workspaces"),
        WorkflowBundleRoot = bundle,
        WorkflowBundleManifest = manifest,
        MulticaExecutable = new VerifiedExecutable { Path = executable, Sha256 = hash },
        CodexExecutable = new VerifiedExecutable { Path = codexExecutable, Sha256 = Hashing.Sha256File(codexExecutable) },
        GitExecutable = new VerifiedExecutable { Path = executable, Sha256 = hash },
        LauncherExecutable = new VerifiedExecutable { Path = executable, Sha256 = hash },
        BrokerExecutable = new VerifiedExecutable { Path = executable, Sha256 = hash },
        ConsoleExecutable = new VerifiedExecutable { Path = executable, Sha256 = hash },
        CodexCredentialPath = auth,
        AgentBindingsPath = Path.Combine(temp.Path, "bindings.json"),
        CapabilityKeyPath = Path.Combine(temp.Path, "key"),
        AuditLogPath = Path.Combine(temp.Path, "audit.jsonl"),
        GitHub = new GitHubBrokerConfiguration
        {
            AppId = 1,
            InstallationId = 2,
            PrivateKeyPath = Path.Combine(temp.Path, "app.pem")
        },
        Profiles = new Dictionary<string, SecurityProfile>
        {
            ["workflow_maintainer"] = new()
            {
                PermissionProfile = "workflow_maintainer",
                GitHubMode = "maintainer_broker",
                AllowedBranchPrefixes = new List<string> { "stabilize/" },
                AllowedBaseBranches = new List<string> { "main" },
                RequiredSkills = new List<string> { "multica-workflow-maintainer" }
            },
            ["workflow_reviewer"] = new()
            {
                PermissionProfile = "workflow_reviewer",
                GitHubMode = "none",
                RequiredSkills = new List<string> { "multica-workflow-maintainer" }
            }
        },
        DeniedRoots = new List<string> { Path.Combine(temp.Path, "host") },
        NetworkDomains = new List<string> { "api.multica.ai", "api.openai.com" }
    };
    Directory.CreateDirectory(config.WorkspacesRoot);
    var profile = config.Profiles["workflow_maintainer"];
    var validManifest = config.WorkflowBundleManifest;
    var invalidManifest = Path.Combine(temp.Path, "invalid-manifest.json");
    File.WriteAllText(
        invalidManifest,
        JsonSerializer.Serialize(new
        {
            schema_version = 1,
            files = new Dictionary<string, string>
            {
                ["skills/multica-workflow-maintainer/SKILL.md"] = new string('0', 64)
            }
        }));
    config.WorkflowBundleManifest = invalidManifest;
    Throws(() => TaskEnvironmentBuilder.Prepare(
        config, "setup-failure", "workflow_maintainer", profile,
        new Dictionary<string, string> { ["SystemRoot"] = "C:\\Windows" }));
    False(Directory.Exists(Path.Combine(config.ServiceRoot, "tasks", "setup-failure")));
    config.WorkflowBundleManifest = validManifest;
    var prepared = TaskEnvironmentBuilder.Prepare(
        config, "task", "workflow_maintainer", profile,
        new Dictionary<string, string> { ["SystemRoot"] = "C:\\Windows" });
    var codexConfig = File.ReadAllText(Path.Combine(prepared.CodexHome, "config.toml"));
    var requirements = File.ReadAllText(Path.Combine(prepared.ProgramData, "OpenAI", "Codex", "requirements.toml"));
    False(codexConfig.Contains("sandbox_mode", StringComparison.Ordinal));
    True(codexConfig.Contains("sandbox = \"elevated\"", StringComparison.Ordinal));
    True(requirements.Contains("default_permissions = \"workflow_maintainer\"", StringComparison.Ordinal));
    True(requirements.Contains("allowed_web_search_modes = []", StringComparison.Ordinal));
    True(requirements.Contains("plugins = false", StringComparison.Ordinal));
    True(requirements.Contains("[mcp_servers]", StringComparison.Ordinal));
    False(requirements.Contains(":danger-full-access", StringComparison.Ordinal));
    True(File.Exists(Path.Combine(prepared.CodexHome, "skills", "multica-workflow-maintainer", "SKILL.md")));
    if (codexExecutable != executable)
    {
        var start = new ProcessStartInfo(codexExecutable)
        {
            WorkingDirectory = config.WorkspacesRoot,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };
        start.ArgumentList.Add("--strict-config");
        start.ArgumentList.Add("--version");
        start.Environment.Clear();
        foreach (var item in prepared.Environment)
        {
            start.Environment[item.Key] = item.Value;
        }
        using var process = Process.Start(start) ?? throw new InvalidOperationException("failed to start Codex strict-config probe");
        process.WaitForExit();
        if (process.ExitCode != 0)
        {
            throw new InvalidOperationException("Codex strict-config probe rejected the secure profile: " + process.StandardError.ReadToEnd());
        }
    }
}

static void True(bool value)
{
    if (!value) throw new InvalidOperationException("expected true");
}

static void False(bool value)
{
    if (value) throw new InvalidOperationException("expected false");
}

static void Equal<T>(T expected, T actual)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new InvalidOperationException($"expected {expected}, got {actual}");
    }
}

static void Throws(Action action)
{
    try
    {
        action();
    }
    catch
    {
        return;
    }
    throw new InvalidOperationException("expected an exception");
}

internal sealed class TempDirectory : IDisposable
{
    internal TempDirectory()
    {
        Path = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "workflow-secure-runtime-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path);
    }

    internal string Path { get; }

    public void Dispose()
    {
        try
        {
            Directory.Delete(Path, true);
        }
        catch (IOException)
        {
        }
    }
}
