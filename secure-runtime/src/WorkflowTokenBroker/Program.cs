using System.Text.Json;
using WorkflowSecureRuntime.Core;

return await BrokerProgram.RunAsync(args).ConfigureAwait(false);

internal static class BrokerProgram
{
    internal static async Task<int> RunAsync(string[] args)
    {
        try
        {
            if (args.Length == 0)
            {
                throw new InvalidOperationException("expected serve, push-task-branch, upsert-pr, or read-ci");
            }
            return args[0] == "serve"
                ? await ServeAsync().ConfigureAwait(false)
                : await ClientAsync(args).ConfigureAwait(false);
        }
        catch (Exception error)
        {
            await Console.Error.WriteLineAsync("workflow-token-broker: " + error.Message).ConfigureAwait(false);
            return 78;
        }
    }

    private static async Task<int> ServeAsync()
    {
        var config = SecureRuntimeConfiguration.Load(RequiredEnvironment("MULTICA_SECURE_CONFIG"));
        Hashing.VerifyExecutable(config.BrokerExecutable);
        Hashing.VerifyExecutable(config.LauncherExecutable);
        Hashing.VerifyExecutable(config.MulticaExecutable);
        Hashing.VerifyExecutable(config.GitExecutable);
        var bindings = AgentBindings.Load(config.AgentBindingsPath);
        var key = LoadKey(config.CapabilityKeyPath);
        var audit = new AuditLog(config.AuditLogPath);
        var multicaHttp = new HttpClient(new SocketsHttpHandler { UseProxy = false, AllowAutoRedirect = false })
        {
            Timeout = TimeSpan.FromSeconds(30)
        };
        var githubHttp = new HttpClient(new SocketsHttpHandler { UseProxy = false, AllowAutoRedirect = false })
        {
            Timeout = TimeSpan.FromSeconds(60)
        };
        var githubClient = new GitHubAppClient(githubHttp, config);
        var operations = new GitHubBrokerOperations(config, githubClient, audit);
        var server = new BrokerServer(
            config,
            bindings,
            key,
            new MulticaTaskVerifier(multicaHttp),
            operations,
            audit);
        var proxy = new AllowlistProxy(config.ProxyPort, config.NetworkDomains);
        using var cancellation = new CancellationTokenSource();
        Console.CancelKeyPress += (_, eventArgs) =>
        {
            eventArgs.Cancel = true;
            cancellation.Cancel();
        };
        var tasks = new[]
        {
            server.RunAsync(cancellation.Token),
            proxy.RunAsync(cancellation.Token)
        };
        await Task.WhenAny(tasks).ConfigureAwait(false);
        cancellation.Cancel();
        await Task.WhenAll(tasks).ConfigureAwait(false);
        return 0;
    }

    private static async Task<int> ClientAsync(string[] args)
    {
        var pipe = RequiredEnvironment("WORKFLOW_BROKER_PIPE");
        object result = args[0] switch
        {
            "push-task-branch" => await BrokerClient.SendAsync<PushBranchRequest, JsonElement>(
                pipe, "push_task_branch", new PushBranchRequest
                {
                    Branch = Option(args, "--branch")
                }, CancellationToken.None).ConfigureAwait(false),
            "upsert-pr" => await BrokerClient.SendAsync<UpsertPullRequest, JsonElement>(
                pipe, "upsert_pr", new UpsertPullRequest
                {
                    Branch = Option(args, "--branch"),
                    Base = Option(args, "--base"),
                    Title = File.ReadAllText(Option(args, "--title-file")),
                    Body = File.ReadAllText(Option(args, "--body-file"))
                }, CancellationToken.None).ConfigureAwait(false),
            "read-ci" => await BrokerClient.SendAsync<ReadCiRequest, JsonElement>(
                pipe, "read_ci", new ReadCiRequest
                {
                    Branch = Option(args, "--branch")
                }, CancellationToken.None).ConfigureAwait(false),
            _ => throw new InvalidOperationException("unknown Broker client command")
        };
        Console.WriteLine(JsonSerializer.Serialize(result, JsonDefaults.Options));
        return 0;
    }

    private static byte[] LoadKey(string path)
    {
        var raw = File.ReadAllText(path).Trim();
        byte[] key;
        try
        {
            key = Convert.FromBase64String(raw);
        }
        catch (FormatException)
        {
            key = Convert.FromHexString(raw);
        }
        if (key.Length < 32)
        {
            throw new InvalidDataException("capability key must contain at least 256 bits");
        }
        return key;
    }

    private static string RequiredEnvironment(string key) =>
        Environment.GetEnvironmentVariable(key) is { Length: > 0 } value
            ? value
            : throw new InvalidOperationException($"{key} is required");

    private static string Option(IReadOnlyList<string> args, string name)
    {
        for (var index = 1; index < args.Count - 1; index++)
        {
            if (args[index] == name)
            {
                return args[index + 1];
            }
        }
        throw new InvalidOperationException($"missing {name}");
    }
}
