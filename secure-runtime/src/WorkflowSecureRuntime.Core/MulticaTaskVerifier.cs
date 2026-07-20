using System.Net.Http.Headers;
using System.Text.Json;

namespace WorkflowSecureRuntime.Core;

public sealed record VerifiedMulticaTask(
    string TaskId,
    string RuntimeId,
    string AgentId,
    string WorkspaceId,
    string IssueId,
    string Status);

public sealed class MulticaTaskVerifier
{
    private readonly HttpClient _client;

    public MulticaTaskVerifier(HttpClient client)
    {
        _client = client;
    }

    public async Task<VerifiedMulticaTask> VerifyAsync(
        SecureRuntimeConfiguration config,
        BeginLaunchRequest request,
        CancellationToken cancellationToken)
    {
        if (!request.MulticaToken.StartsWith("mat_", StringComparison.Ordinal) ||
            request.MulticaToken.Length < 20)
        {
            throw new InvalidOperationException("secure tasks require a task-scoped mat_ token");
        }
        if (!Guid.TryParse(request.AgentId, out _) || !Guid.TryParse(request.TaskId, out _) ||
            !Guid.TryParse(request.WorkspaceId, out _))
        {
            throw new InvalidOperationException("task, agent, and workspace IDs must be UUIDs");
        }
        var uri = new Uri(
            new Uri(config.MulticaServerUrl.TrimEnd('/') + "/"),
            $"api/agents/{Uri.EscapeDataString(request.AgentId)}/tasks");
        using var message = new HttpRequestMessage(HttpMethod.Get, uri);
        message.Headers.Authorization = new AuthenticationHeaderValue("Bearer", request.MulticaToken);
        message.Headers.Add("X-Workspace-ID", request.WorkspaceId);
        message.Headers.Add("X-Task-ID", request.TaskId);
        using var response = await _client.SendAsync(message, cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException(
                $"Multica task attestation failed with HTTP {(int)response.StatusCode}");
        }
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken).ConfigureAwait(false);
        var tasks = document.RootElement.ValueKind == JsonValueKind.Array
            ? document.RootElement.EnumerateArray()
            : throw new InvalidOperationException("Multica task attestation returned a non-array response");
        foreach (var task in tasks)
        {
            var taskId = String(task, "id");
            if (!string.Equals(taskId, request.TaskId, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            var verified = new VerifiedMulticaTask(
                taskId,
                String(task, "runtime_id"),
                String(task, "agent_id"),
                String(task, "workspace_id"),
                String(task, "issue_id"),
                String(task, "status"));
            if (!string.Equals(verified.AgentId, request.AgentId, StringComparison.OrdinalIgnoreCase) ||
                !string.Equals(verified.WorkspaceId, request.WorkspaceId, StringComparison.OrdinalIgnoreCase) ||
                string.IsNullOrWhiteSpace(verified.RuntimeId) ||
                verified.Status is not ("dispatched" or "running"))
            {
                throw new InvalidOperationException("Multica task attestation fields do not match the launch");
            }
            return verified;
        }
        throw new InvalidOperationException("Multica task attestation did not return the current task");
    }

    private static string String(JsonElement value, string property) =>
        value.TryGetProperty(property, out var item) && item.ValueKind == JsonValueKind.String
            ? item.GetString() ?? ""
            : "";
}
