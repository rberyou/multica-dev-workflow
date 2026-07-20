namespace WorkflowSecureRuntime.Core;

public enum LauncherMode
{
    Probe,
    ProfileProbe,
    Task
}

public readonly record struct LauncherInvocation(LauncherMode Mode, IReadOnlyList<string> Arguments);

public static class CommandPolicy
{
    private static readonly string[][] ProbeCommands =
    {
        new[] { "--version" },
        new[] { "debug", "models", "--bundled" }
    };

    private static readonly string[] TaskCommand = { "app-server", "--listen", "stdio://" };

    public static LauncherInvocation Validate(IReadOnlyList<string> args)
    {
        if (ProbeCommands.Any(command => command.SequenceEqual(args, StringComparer.Ordinal)))
        {
            return new LauncherInvocation(LauncherMode.Probe, args);
        }
        if (args.Count == 2 && args[0] == "--workflow-security-probe" &&
            args[1] is "workflow_maintainer" or "workflow_reviewer")
        {
            return new LauncherInvocation(LauncherMode.ProfileProbe, args);
        }
        if (TaskCommand.SequenceEqual(args, StringComparer.Ordinal))
        {
            return new LauncherInvocation(LauncherMode.Task, args);
        }
        throw new InvalidOperationException(
            "secure launcher accepts only fixed probes or app-server --listen stdio://");
    }
}
