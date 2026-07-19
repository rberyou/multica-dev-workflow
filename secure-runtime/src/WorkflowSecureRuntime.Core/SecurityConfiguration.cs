using System.Text.Json;
using System.Text.Json.Serialization;

namespace WorkflowSecureRuntime.Core;

public sealed class SecureRuntimeConfiguration
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("repository")]
    public string Repository { get; set; } = "";

    [JsonPropertyName("service_root")]
    public string ServiceRoot { get; set; } = "";

    [JsonPropertyName("workspaces_root")]
    public string WorkspacesRoot { get; set; } = "";

    [JsonPropertyName("workflow_bundle_root")]
    public string WorkflowBundleRoot { get; set; } = "";

    [JsonPropertyName("workflow_bundle_manifest")]
    public string WorkflowBundleManifest { get; set; } = "";

    [JsonPropertyName("multica_executable")]
    public VerifiedExecutable MulticaExecutable { get; set; } = new();

    [JsonPropertyName("codex_executable")]
    public VerifiedExecutable CodexExecutable { get; set; } = new();

    [JsonPropertyName("git_executable")]
    public VerifiedExecutable GitExecutable { get; set; } = new();

    [JsonPropertyName("launcher_executable")]
    public VerifiedExecutable LauncherExecutable { get; set; } = new();

    [JsonPropertyName("broker_executable")]
    public VerifiedExecutable BrokerExecutable { get; set; } = new();

    [JsonPropertyName("console_executable")]
    public VerifiedExecutable ConsoleExecutable { get; set; } = new();

    [JsonPropertyName("codex_credential_path")]
    public string CodexCredentialPath { get; set; } = "";

    [JsonPropertyName("agent_bindings_path")]
    public string AgentBindingsPath { get; set; } = "";

    [JsonPropertyName("broker_pipe_name")]
    public string BrokerPipeName { get; set; } = "multica-workflow-broker";

    [JsonPropertyName("proxy_port")]
    public int ProxyPort { get; set; } = 46183;

    [JsonPropertyName("multica_server_url")]
    public string MulticaServerUrl { get; set; } = "https://api.multica.ai";

    [JsonPropertyName("multica_profile")]
    public string MulticaProfile { get; set; } = "workflow-secure";

    [JsonPropertyName("multica_daemon_id")]
    public string MulticaDaemonId { get; set; } = "";

    [JsonPropertyName("multica_runtime_name")]
    public string MulticaRuntimeName { get; set; } = "Workflow Secure Codex";

    [JsonPropertyName("capability_key_path")]
    public string CapabilityKeyPath { get; set; } = "";

    [JsonPropertyName("audit_log_path")]
    public string AuditLogPath { get; set; } = "";

    [JsonPropertyName("github")]
    public GitHubBrokerConfiguration GitHub { get; set; } = new();

    [JsonPropertyName("profiles")]
    public Dictionary<string, SecurityProfile> Profiles { get; set; } = new(StringComparer.Ordinal);

    [JsonPropertyName("denied_roots")]
    public List<string> DeniedRoots { get; set; } = new();

    [JsonPropertyName("network_domains")]
    public List<string> NetworkDomains { get; set; } = new();

    public static SecureRuntimeConfiguration Load(string path)
    {
        var value = JsonSerializer.Deserialize<SecureRuntimeConfiguration>(
            File.ReadAllText(path), JsonDefaults.Options)
            ?? throw new InvalidDataException("secure runtime configuration is empty");
        value.Validate();
        return value;
    }

    public void Validate()
    {
        if (SchemaVersion != 1)
        {
            throw new InvalidDataException("unsupported secure runtime schema_version");
        }
        if (!RepositoryContract.TryParse(Repository, out _))
        {
            throw new InvalidDataException("repository must be owner/name");
        }
        foreach (var required in new[]
                 {
                     ServiceRoot, WorkspacesRoot, WorkflowBundleRoot, WorkflowBundleManifest,
                     CodexCredentialPath, AgentBindingsPath, CapabilityKeyPath, AuditLogPath
                 })
        {
            if (string.IsNullOrWhiteSpace(required) || !Path.IsPathFullyQualified(required))
            {
                throw new InvalidDataException("secure runtime paths must be absolute");
            }
        }
        foreach (var executable in new[]
                 {
                     MulticaExecutable, CodexExecutable, GitExecutable,
                     LauncherExecutable, BrokerExecutable, ConsoleExecutable
                 })
        {
            executable.Validate();
        }
        if (Profiles.Count == 0 || !Profiles.ContainsKey("workflow_maintainer") ||
            !Profiles.ContainsKey("workflow_reviewer"))
        {
            throw new InvalidDataException("workflow_maintainer and workflow_reviewer profiles are required");
        }
        foreach (var item in Profiles)
        {
            item.Value.Validate(item.Key);
        }
        if (ProxyPort is < 1024 or > 65535)
        {
            throw new InvalidDataException("proxy_port must be between 1024 and 65535");
        }
        if (!Uri.TryCreate(MulticaServerUrl, UriKind.Absolute, out var server) ||
            server.Scheme != Uri.UriSchemeHttps)
        {
            throw new InvalidDataException("multica_server_url must be HTTPS");
        }
        GitHub.Validate(Repository);
    }
}

public sealed class VerifiedExecutable
{
    [JsonPropertyName("path")]
    public string Path { get; set; } = "";

    [JsonPropertyName("sha256")]
    public string Sha256 { get; set; } = "";

    public void Validate()
    {
        if (string.IsNullOrWhiteSpace(Path) || !System.IO.Path.IsPathFullyQualified(Path))
        {
            throw new InvalidDataException("executable paths must be absolute");
        }
        if (!Hashing.IsSha256(Sha256))
        {
            throw new InvalidDataException($"invalid executable sha256 for {Path}");
        }
    }
}

public sealed class SecurityProfile
{
    [JsonPropertyName("permission_profile")]
    public string PermissionProfile { get; set; } = "";

    [JsonPropertyName("github_mode")]
    public string GitHubMode { get; set; } = "none";

    [JsonPropertyName("allowed_branch_prefixes")]
    public List<string> AllowedBranchPrefixes { get; set; } = new();

    [JsonPropertyName("allowed_base_branches")]
    public List<string> AllowedBaseBranches { get; set; } = new();

    [JsonPropertyName("required_skills")]
    public List<string> RequiredSkills { get; set; } = new();

    public void Validate(string role)
    {
        if (PermissionProfile != role)
        {
            throw new InvalidDataException($"profile {role} must select its exact permission profile");
        }
        if (GitHubMode is not ("none" or "maintainer_broker"))
        {
            throw new InvalidDataException($"profile {role} has an unsupported github_mode");
        }
        if (GitHubMode == "none" && (AllowedBranchPrefixes.Count != 0 || AllowedBaseBranches.Count != 0))
        {
            throw new InvalidDataException($"tokenless profile {role} cannot define GitHub write branches");
        }
        if (GitHubMode == "maintainer_broker" &&
            (AllowedBranchPrefixes.Count == 0 || AllowedBaseBranches.Count == 0))
        {
            throw new InvalidDataException($"maintainer profile {role} requires bounded branch and base rules");
        }
        if (AllowedBranchPrefixes.Any(item => string.IsNullOrWhiteSpace(item) || item.StartsWith("refs/", StringComparison.Ordinal)))
        {
            throw new InvalidDataException($"profile {role} contains an invalid branch prefix");
        }
    }
}

public sealed class GitHubBrokerConfiguration
{
    [JsonPropertyName("api_url")]
    public string ApiUrl { get; set; } = "https://api.github.com";

    [JsonPropertyName("app_id")]
    public long AppId { get; set; }

    [JsonPropertyName("installation_id")]
    public long InstallationId { get; set; }

    [JsonPropertyName("private_key_path")]
    public string PrivateKeyPath { get; set; } = "";

    public void Validate(string repository)
    {
        if (!Uri.TryCreate(ApiUrl, UriKind.Absolute, out var api) || api.Scheme != Uri.UriSchemeHttps)
        {
            throw new InvalidDataException("github.api_url must be HTTPS");
        }
        if (AppId <= 0 || InstallationId <= 0)
        {
            throw new InvalidDataException("GitHub App and installation IDs must be positive");
        }
        if (string.IsNullOrWhiteSpace(PrivateKeyPath) || !Path.IsPathFullyQualified(PrivateKeyPath))
        {
            throw new InvalidDataException("github.private_key_path must be absolute");
        }
        if (!RepositoryContract.TryParse(repository, out _))
        {
            throw new InvalidDataException("GitHub repository contract is invalid");
        }
    }
}

public sealed class AgentBindings
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("runtime_id")]
    public string RuntimeId { get; set; } = "";

    [JsonPropertyName("agents")]
    public Dictionary<string, string> Agents { get; set; } = new(StringComparer.OrdinalIgnoreCase);

    public static AgentBindings Load(string path)
    {
        var value = JsonSerializer.Deserialize<AgentBindings>(
            File.ReadAllText(path), JsonDefaults.Options)
            ?? throw new InvalidDataException("agent bindings are empty");
        if (value.SchemaVersion != 1 || !Guid.TryParse(value.RuntimeId, out var runtimeId) ||
            runtimeId == Guid.Empty || value.Agents.Count == 0)
        {
            throw new InvalidDataException("agent bindings are invalid");
        }
        foreach (var item in value.Agents)
        {
            if (!Guid.TryParse(item.Key, out _) || string.IsNullOrWhiteSpace(item.Value))
            {
                throw new InvalidDataException("agent bindings must map UUIDs to role keys");
            }
        }
        return value;
    }
}

public readonly record struct RepositoryContract(string Owner, string Name)
{
    public static bool TryParse(string value, out RepositoryContract repository)
    {
        repository = default;
        var parts = value.Split('/', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (parts.Length != 2 || parts.Any(item => item.Length == 0 || item.Any(ch => !(char.IsLetterOrDigit(ch) || ch is '-' or '_' or '.'))))
        {
            return false;
        }
        repository = new RepositoryContract(parts[0], parts[1]);
        return true;
    }
}

public static class JsonDefaults
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNameCaseInsensitive = false,
        WriteIndented = false
    };
}
