using System.Text.Json;
using System.Text.Json.Serialization;

namespace WorkflowSecureRuntime.Core;

public sealed class BrokerRequest
{
    [JsonPropertyName("operation")]
    public string Operation { get; set; } = "";

    [JsonPropertyName("payload")]
    public JsonElement Payload { get; set; }
}

public sealed class BrokerResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }

    [JsonPropertyName("payload")]
    public object? Payload { get; set; }

    public static BrokerResponse Success(object? payload = null) => new() { Ok = true, Payload = payload };

    public static BrokerResponse Failure(string error) => new() { Ok = false, Error = error };
}

public sealed class BeginLaunchRequest
{
    [JsonPropertyName("agent_id")]
    public string AgentId { get; set; } = "";

    [JsonPropertyName("task_id")]
    public string TaskId { get; set; } = "";

    [JsonPropertyName("workspace_id")]
    public string WorkspaceId { get; set; } = "";

    [JsonPropertyName("multica_token")]
    public string MulticaToken { get; set; } = "";

    [JsonPropertyName("worktree")]
    public string Worktree { get; set; } = "";
}

public sealed class BeginLaunchResponse
{
    [JsonPropertyName("capability_id")]
    public string CapabilityId { get; set; } = "";

    [JsonPropertyName("capability")]
    public string Capability { get; set; } = "";

    [JsonPropertyName("job_name")]
    public string JobName { get; set; } = "";

    [JsonPropertyName("runtime_id")]
    public string RuntimeId { get; set; } = "";

    [JsonPropertyName("role")]
    public string Role { get; set; } = "";

    [JsonPropertyName("expires_at")]
    public long ExpiresAtUnixSeconds { get; set; }
}

public sealed class RegisterChildRequest
{
    [JsonPropertyName("capability_id")]
    public string CapabilityId { get; set; } = "";

    [JsonPropertyName("capability")]
    public string Capability { get; set; } = "";

    [JsonPropertyName("child_pid")]
    public int ChildPid { get; set; }

    [JsonPropertyName("child_started_at")]
    public long ChildStartedAtUnixMs { get; set; }
}

public sealed class PushBranchRequest
{
    [JsonPropertyName("branch")]
    public string Branch { get; set; } = "";
}

public sealed class UpsertPullRequest
{
    [JsonPropertyName("branch")]
    public string Branch { get; set; } = "";

    [JsonPropertyName("base")]
    public string Base { get; set; } = "";

    [JsonPropertyName("title")]
    public string Title { get; set; } = "";

    [JsonPropertyName("body")]
    public string Body { get; set; } = "";
}

public sealed class ReadCiRequest
{
    [JsonPropertyName("branch")]
    public string Branch { get; set; } = "";
}
