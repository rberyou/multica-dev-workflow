using System.Diagnostics;

try
{
    if (args.Length == 0 || args[0] != "status")
    {
        throw new InvalidOperationException("RC4 workflow-console supports only the read-only status command");
    }
    if (!string.IsNullOrEmpty(Environment.GetEnvironmentVariable("MULTICA_AGENT_ID")) ||
        !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("MULTICA_TASK_ID")))
    {
        throw new InvalidOperationException("workflow-console host commands are unavailable inside Agent runtimes");
    }
    var repoIndex = Array.IndexOf(args, "--repo");
    if (repoIndex < 0 || repoIndex + 1 >= args.Length)
    {
        throw new InvalidOperationException("status requires --repo <multica-dev-workflow checkout>");
    }
    var repo = Path.GetFullPath(args[repoIndex + 1]);
    var script = Path.Combine(repo, "scripts", "workflow.py");
    if (!File.Exists(script))
    {
        throw new InvalidOperationException("workflow.py was not found in the selected checkout");
    }
    var start = new ProcessStartInfo("python")
    {
        WorkingDirectory = repo,
        UseShellExecute = false
    };
    start.ArgumentList.Add(script);
    start.ArgumentList.Add("audit");
    start.ArgumentList.Add("--scope");
    start.ArgumentList.Add("all");
    start.ArgumentList.Add("--output");
    start.ArgumentList.Add("json");
    for (var index = 1; index < args.Length; index++)
    {
        if (index == repoIndex || index == repoIndex + 1)
        {
            continue;
        }
        start.ArgumentList.Add(args[index]);
    }
    using var process = Process.Start(start) ?? throw new InvalidOperationException("failed to start workflow status command");
    await process.WaitForExitAsync().ConfigureAwait(false);
    return process.ExitCode;
}
catch (Exception error)
{
    await Console.Error.WriteLineAsync("workflow-console: " + error.Message).ConfigureAwait(false);
    return 78;
}
