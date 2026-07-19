namespace WorkflowSecureRuntime.Core;

public sealed class LaunchSession
{
    public required LaunchCapability Capability { get; init; }
    public required string SignedCapability { get; init; }
    public required SecurityProfile Profile { get; init; }
    public required string Worktree { get; init; }
    public required WindowsJob Job { get; init; }
    public int CodexProcessId { get; set; }
    public long CodexStartedAtUnixMs { get; set; }
    public bool ChildRegistered { get; set; }
}
