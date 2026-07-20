using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace WorkflowSecureRuntime.Core;

public sealed class LaunchCapability
{
    [JsonPropertyName("capability_id")]
    public string CapabilityId { get; set; } = "";

    [JsonPropertyName("daemon_id")]
    public string DaemonId { get; set; } = "";

    [JsonPropertyName("runtime_id")]
    public string RuntimeId { get; set; } = "";

    [JsonPropertyName("agent_id")]
    public string AgentId { get; set; } = "";

    [JsonPropertyName("task_id")]
    public string TaskId { get; set; } = "";

    [JsonPropertyName("workspace_id")]
    public string WorkspaceId { get; set; } = "";

    [JsonPropertyName("role")]
    public string Role { get; set; } = "";

    [JsonPropertyName("repository")]
    public string Repository { get; set; } = "";

    [JsonPropertyName("worktree")]
    public string Worktree { get; set; } = "";

    [JsonPropertyName("launcher_pid")]
    public int LauncherPid { get; set; }

    [JsonPropertyName("launcher_started_at")]
    public long LauncherStartedAtUnixMs { get; set; }

    [JsonPropertyName("job_name")]
    public string JobName { get; set; } = "";

    [JsonPropertyName("nonce")]
    public string Nonce { get; set; } = "";

    [JsonPropertyName("issued_at")]
    public long IssuedAtUnixSeconds { get; set; }

    [JsonPropertyName("expires_at")]
    public long ExpiresAtUnixSeconds { get; set; }
}

public static class CapabilitySigner
{
    public static string Sign(LaunchCapability capability, ReadOnlySpan<byte> key)
    {
        var payload = JsonSerializer.SerializeToUtf8Bytes(capability, JsonDefaults.Options);
        var signature = HMACSHA256.HashData(key, payload);
        return Base64Url(payload) + "." + Base64Url(signature);
    }

    public static LaunchCapability Verify(string token, ReadOnlySpan<byte> key, DateTimeOffset now)
    {
        var parts = token.Split('.', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length != 2)
        {
            throw new InvalidDataException("launch capability is malformed");
        }
        var payload = Base64UrlDecode(parts[0]);
        var signature = Base64UrlDecode(parts[1]);
        var expected = HMACSHA256.HashData(key, payload);
        if (!CryptographicOperations.FixedTimeEquals(signature, expected))
        {
            throw new InvalidDataException("launch capability signature is invalid");
        }
        var capability = JsonSerializer.Deserialize<LaunchCapability>(payload, JsonDefaults.Options)
            ?? throw new InvalidDataException("launch capability payload is empty");
        if (capability.ExpiresAtUnixSeconds <= now.ToUnixTimeSeconds() ||
            capability.IssuedAtUnixSeconds > now.AddMinutes(1).ToUnixTimeSeconds())
        {
            throw new InvalidDataException("launch capability is expired or not yet valid");
        }
        return capability;
    }

    private static string Base64Url(ReadOnlySpan<byte> value) =>
        Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    private static byte[] Base64UrlDecode(string value)
    {
        var padded = value.Replace('-', '+').Replace('_', '/');
        padded += new string('=', (4 - padded.Length % 4) % 4);
        return Convert.FromBase64String(padded);
    }
}
