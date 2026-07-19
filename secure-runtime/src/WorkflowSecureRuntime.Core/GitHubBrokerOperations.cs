using System.Diagnostics;
using System.Net.Http.Headers;
using System.Text.Json;

namespace WorkflowSecureRuntime.Core;

public sealed class GitHubBrokerOperations
{
    private static readonly string[] DangerousConfigPrefixes =
    {
        "credential.", "include.", "includeif.", "url.", "http.", "https.",
        "protocol.", "filter.", "core.hookspath", "core.sshcommand",
        "core.gitproxy", "core.fsmonitor"
    };

    private readonly SecureRuntimeConfiguration _config;
    private readonly GitHubAppClient _github;
    private readonly AuditLog _audit;
    private readonly RepositoryContract _repository;

    public GitHubBrokerOperations(
        SecureRuntimeConfiguration config,
        GitHubAppClient github,
        AuditLog audit)
    {
        _config = config;
        _github = github;
        _audit = audit;
        RepositoryContract.TryParse(config.Repository, out _repository);
    }

    public async Task<object> PushTaskBranchAsync(
        LaunchSession session,
        PushBranchRequest request,
        CancellationToken cancellationToken)
    {
        ValidateBranch(session, request.Branch);
        VerifyWorktree(session.Worktree);
        var repository = InspectRepository(session.Worktree, request.Branch);
        var sourceCommit = repository.Commit;
        var snapshot = await CreatePushSnapshotAsync(
            repository, cancellationToken).ConfigureAwait(false);
        var askPass = Path.Combine(_config.ServiceRoot, "broker-temp", $"askpass-{Guid.NewGuid():N}.cmd");
        Directory.CreateDirectory(Path.GetDirectoryName(askPass)!);
        try
        {
            await using var lease = await _github.MintMaintainerTokenAsync(
                cancellationToken).ConfigureAwait(false);
            await File.WriteAllTextAsync(
                askPass,
                "@echo off\r\nset prompt=%~1\r\necho %prompt% | %SystemRoot%\\System32\\findstr.exe /I Username >NUL\r\nif %ERRORLEVEL% EQU 0 (echo x-access-token) else (echo %WORKFLOW_GITHUB_TOKEN%)\r\n",
                cancellationToken).ConfigureAwait(false);
            var environment = SafeGitEnvironment(lease.Token, askPass);
            var remote = $"https://github.com/{_repository.Owner}/{_repository.Name}.git";
            var result = await RunProcessAsync(
                _config.GitExecutable.Path,
                new[]
                {
                    "-c", "core.hooksPath=NUL",
                    "-c", "credential.helper=",
                    "-c", "http.proxy=",
                    "-c", "https.proxy=",
                    "--git-dir", snapshot,
                    "push", "--no-verify", remote,
                    $"refs/heads/source:refs/heads/{request.Branch}"
                },
                snapshot,
                environment,
                cancellationToken).ConfigureAwait(false);
            if (result.ExitCode != 0)
            {
                throw new InvalidOperationException("bounded Git push failed: " + Redact(result.Error));
            }
            await _audit.WriteAsync("github.push_task_branch", new
            {
                session.Capability.CapabilityId,
                branch = request.Branch,
                commit = sourceCommit,
                repository = _config.Repository,
                result = "success"
            }, cancellationToken).ConfigureAwait(false);
            return new { branch = request.Branch, commit = sourceCommit, pushed = true };
        }
        finally
        {
            try
            {
                File.Delete(askPass);
            }
            finally
            {
                Directory.Delete(snapshot, true);
            }
        }
    }

    public async Task<object> UpsertPullRequestAsync(
        LaunchSession session,
        UpsertPullRequest request,
        CancellationToken cancellationToken)
    {
        ValidateBranch(session, request.Branch);
        ValidateBase(session, request.Base);
        VerifyWorktree(session.Worktree);
        _ = InspectRepository(session.Worktree, request.Branch);
        if (string.IsNullOrWhiteSpace(request.Title) || request.Title.Length > 256 || request.Body.Length > 65536)
        {
            throw new InvalidOperationException("pull request title/body is invalid");
        }
        await using var lease = await _github.MintMaintainerTokenAsync(cancellationToken).ConfigureAwait(false);
        var head = Uri.EscapeDataString($"{_repository.Owner}:{request.Branch}");
        var relative = $"repos/{_repository.Owner}/{_repository.Name}/pulls?state=open&head={head}&base={Uri.EscapeDataString(request.Base)}";
        using var listRequest = _github.CreateTokenRequest(HttpMethod.Get, relative, lease.Token);
        using var listResponse = await SharedHttp.Client.SendAsync(listRequest, cancellationToken).ConfigureAwait(false);
        var listBody = await listResponse.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        if (!listResponse.IsSuccessStatusCode)
        {
            throw new InvalidOperationException("GitHub PR lookup failed");
        }
        using var listDocument = JsonDocument.Parse(listBody);
        var items = listDocument.RootElement.EnumerateArray().ToArray();
        if (items.Length > 1)
        {
            throw new InvalidOperationException("multiple open pull requests match the bounded branch");
        }
        HttpRequestMessage mutation;
        if (items.Length == 1)
        {
            var number = items[0].GetProperty("number").GetInt32();
            mutation = _github.CreateTokenRequest(
                HttpMethod.Patch,
                $"repos/{_repository.Owner}/{_repository.Name}/pulls/{number}", lease.Token);
            mutation.Content = GitHubAppClient.JsonContent(new { title = request.Title, body = request.Body, @base = request.Base });
        }
        else
        {
            mutation = _github.CreateTokenRequest(
                HttpMethod.Post,
                $"repos/{_repository.Owner}/{_repository.Name}/pulls", lease.Token);
            mutation.Content = GitHubAppClient.JsonContent(new
            {
                title = request.Title,
                body = request.Body,
                head = request.Branch,
                @base = request.Base,
                maintainer_can_modify = false
            });
        }
        using (mutation)
        using (var response = await SharedHttp.Client.SendAsync(mutation, cancellationToken).ConfigureAwait(false))
        {
            var body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                throw new InvalidOperationException("GitHub PR upsert failed");
            }
            using var document = JsonDocument.Parse(body);
            var result = new
            {
                number = document.RootElement.GetProperty("number").GetInt32(),
                url = document.RootElement.GetProperty("html_url").GetString(),
                head = request.Branch,
                @base = request.Base
            };
            await _audit.WriteAsync("github.upsert_pr", new
            {
                session.Capability.CapabilityId,
                result.number,
                branch = request.Branch,
                @base = request.Base
            }, cancellationToken).ConfigureAwait(false);
            return result;
        }
    }

    public async Task<object> ReadCiAsync(
        LaunchSession session,
        ReadCiRequest request,
        CancellationToken cancellationToken)
    {
        ValidateBranch(session, request.Branch);
        await using var lease = await _github.MintMaintainerTokenAsync(cancellationToken).ConfigureAwait(false);
        var uri = $"repos/{_repository.Owner}/{_repository.Name}/actions/runs?branch={Uri.EscapeDataString(request.Branch)}&per_page=20";
        using var message = _github.CreateTokenRequest(HttpMethod.Get, uri, lease.Token);
        using var response = await SharedHttp.Client.SendAsync(message, cancellationToken).ConfigureAwait(false);
        var body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException("GitHub CI read failed");
        }
        using var document = JsonDocument.Parse(body);
        var runs = document.RootElement.GetProperty("workflow_runs")
            .EnumerateArray()
            .Select(item => new
            {
                id = item.GetProperty("id").GetInt64(),
                status = item.GetProperty("status").GetString(),
                conclusion = item.TryGetProperty("conclusion", out var conclusion) ? conclusion.GetString() : null,
                head_sha = item.GetProperty("head_sha").GetString(),
                url = item.GetProperty("html_url").GetString()
            })
            .ToArray();
        await _audit.WriteAsync("github.read_ci", new
        {
            session.Capability.CapabilityId,
            branch = request.Branch,
            run_count = runs.Length
        }, cancellationToken).ConfigureAwait(false);
        return new { branch = request.Branch, runs };
    }

    private void VerifyWorktree(string worktree)
    {
        _ = PathPolicy.RequireDirectoryUnderRoot(
            _config.WorkspacesRoot, worktree, "Broker worktree");
    }

    private void ValidateBranch(LaunchSession session, string branch)
    {
        if (string.IsNullOrWhiteSpace(branch) || branch.StartsWith("refs/", StringComparison.Ordinal) ||
            branch.Contains("..", StringComparison.Ordinal) || branch.EndsWith(".lock", StringComparison.OrdinalIgnoreCase) ||
            branch.Any(char.IsWhiteSpace) || branch.Contains('~') || branch.Contains('^') || branch.Contains(':') || branch.Contains('\\'))
        {
            throw new InvalidOperationException("branch name is invalid");
        }
        if (!session.Profile.AllowedBranchPrefixes.Any(prefix => branch.StartsWith(prefix, StringComparison.Ordinal)))
        {
            throw new InvalidOperationException("branch is outside the profile allowlist");
        }
        if (branch.Equals("main", StringComparison.OrdinalIgnoreCase) || branch.StartsWith("v", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("main and release tags are never writable through the Maintainer Broker");
        }
    }

    private static void ValidateBase(LaunchSession session, string value)
    {
        if (!session.Profile.AllowedBaseBranches.Contains(value, StringComparer.Ordinal))
        {
            throw new InvalidOperationException("pull request base is outside the profile allowlist");
        }
    }

    private RepositoryState InspectRepository(string worktree, string expectedBranch)
    {
        var root = PathPolicy.RequireDirectoryUnderRoot(
            _config.WorkspacesRoot, worktree, "Broker worktree");
        var dotGit = Path.Combine(root, ".git");
        string gitDirectory;
        if (Directory.Exists(dotGit))
        {
            gitDirectory = PathPolicy.RequireDirectoryUnderRoot(
                _config.WorkspacesRoot, dotGit, "Git metadata directory");
        }
        else
        {
            var pointer = ReadSecureTextFile(dotGit, "Git metadata pointer").Trim();
            const string prefix = "gitdir:";
            if (!pointer.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("worktree .git pointer is invalid");
            }
            var candidate = Path.GetFullPath(
                Path.Combine(root, pointer[prefix.Length..].Trim()));
            gitDirectory = PathPolicy.RequireDirectoryUnderRoot(
                _config.WorkspacesRoot, candidate, "Git metadata directory");
        }
        var commonPointer = Path.Combine(gitDirectory, "commondir");
        var commonDirectory = File.Exists(commonPointer)
            ? Path.GetFullPath(Path.Combine(
                gitDirectory,
                ReadSecureTextFile(commonPointer, "Git common directory pointer").Trim()))
            : gitDirectory;
        commonDirectory = PathPolicy.RequireDirectoryUnderRoot(
            _config.WorkspacesRoot, commonDirectory, "Git common directory");
        RejectDangerousGitConfig(
            Path.Combine(commonDirectory, "config"),
            Path.Combine(gitDirectory, "config.worktree"));
        var head = ReadSecureTextFile(
            Path.Combine(gitDirectory, "HEAD"), "Git HEAD").Trim();
        var expectedRef = $"refs/heads/{expectedBranch}";
        if (!string.Equals(head, $"ref: {expectedRef}", StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                "Broker mutation branch must equal the task worktree current branch");
        }
        var commit = ReadReference(gitDirectory, commonDirectory, expectedRef);
        var objects = PathPolicy.RequireDirectoryUnderRoot(
            _config.WorkspacesRoot,
            Path.Combine(commonDirectory, "objects"),
            "Git object directory");
        var alternates = Path.Combine(objects, "info", "alternates");
        if (File.Exists(alternates) &&
            !string.IsNullOrWhiteSpace(
                ReadSecureTextFile(alternates, "Git alternates file")))
        {
            throw new InvalidOperationException(
                "Git object alternates are not supported by the Maintainer Broker");
        }
        return new RepositoryState(
            root, gitDirectory, commonDirectory, objects, expectedBranch, commit);
    }

    private static string ReadReference(
        string gitDirectory, string commonDirectory, string reference)
    {
        foreach (var root in new[] { gitDirectory, commonDirectory }.Distinct(
                     StringComparer.OrdinalIgnoreCase))
        {
            var loose = Path.Combine(root, reference.Replace('/', Path.DirectorySeparatorChar));
            if (File.Exists(loose))
            {
                return RequireCommitId(
                    ReadSecureTextFile(loose, "Git branch reference").Trim());
            }
        }
        var packed = Path.Combine(commonDirectory, "packed-refs");
        if (File.Exists(packed))
        {
            foreach (var line in ReadSecureTextFile(
                         packed, "Git packed references").Split('\n'))
            {
                var value = line.Trim();
                if (value.Length == 0 || value[0] is '#' or '^')
                {
                    continue;
                }
                var split = value.Split(' ', 2, StringSplitOptions.RemoveEmptyEntries);
                if (split.Length == 2 && string.Equals(
                        split[1], reference, StringComparison.Ordinal))
                {
                    return RequireCommitId(split[0]);
                }
            }
        }
        throw new InvalidOperationException("task branch reference is missing");
    }

    private static string RequireCommitId(string value)
    {
        if (value.Length is < 40 or > 64 || value.Any(item => !Uri.IsHexDigit(item)))
        {
            throw new InvalidOperationException("task branch reference is not a commit ID");
        }
        return value.ToLowerInvariant();
    }

    private static string ReadSecureTextFile(string path, string label)
    {
        var full = Path.GetFullPath(path);
        for (var parent = Directory.GetParent(full); parent is not null; parent = parent.Parent)
        {
            if ((File.GetAttributes(parent.FullName) & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException($"{label} cannot traverse a reparse point");
            }
        }
        if (!File.Exists(full) ||
            (File.GetAttributes(full) & FileAttributes.ReparsePoint) != 0)
        {
            throw new InvalidOperationException($"{label} is missing or is a reparse point");
        }
        return File.ReadAllText(full);
    }

    private static void RejectDangerousGitConfig(params string[] paths)
    {
        foreach (var path in paths.Where(File.Exists))
        {
            var section = "";
            foreach (var raw in ReadSecureTextFile(path, "Git config").Split('\n'))
            {
                var line = raw.Trim();
                if (line.Length == 0 || line[0] is '#' or ';')
                {
                    continue;
                }
                if (line[0] == '[' && line[^1] == ']')
                {
                    section = line[1..^1].Split(' ', 2)[0].Trim().ToLowerInvariant();
                    continue;
                }
                var key = line.Split('=', 2)[0].Trim().ToLowerInvariant();
                var fullKey = string.IsNullOrEmpty(section) ? key : $"{section}.{key}";
                if (DangerousConfigPrefixes.Any(prefix => fullKey.StartsWith(
                        prefix, StringComparison.OrdinalIgnoreCase)))
                {
                    throw new InvalidOperationException(
                        $"local Git config contains a forbidden key: {fullKey}");
                }
            }
        }
    }

    private async Task<string> CreatePushSnapshotAsync(
        RepositoryState repository,
        CancellationToken cancellationToken)
    {
        var root = Path.Combine(_config.ServiceRoot, "broker-temp");
        Directory.CreateDirectory(root);
        var snapshot = Path.Combine(root, $"push-{Guid.NewGuid():N}.git");
        Directory.CreateDirectory(snapshot);
        try
        {
            var environment = SafeGitEnvironment(null, null);
            var initialize = await RunProcessAsync(
                _config.GitExecutable.Path,
                new[] { "init", "--bare", snapshot },
                root,
                environment,
                cancellationToken).ConfigureAwait(false);
            if (initialize.ExitCode != 0)
            {
                throw new InvalidOperationException("unable to initialize trusted push snapshot");
            }
            CopyGitObjects(repository.ObjectsDirectory, Path.Combine(snapshot, "objects"));
            foreach (var alternateName in new[] { "alternates", "http-alternates" })
            {
                if (File.Exists(Path.Combine(snapshot, "objects", "info", alternateName)))
                {
                    throw new InvalidOperationException(
                        "trusted push snapshot cannot contain Git object alternates");
                }
            }
            var sourceRef = Path.Combine(snapshot, "refs", "heads", "source");
            Directory.CreateDirectory(Path.GetDirectoryName(sourceRef)!);
            await File.WriteAllTextAsync(
                sourceRef, repository.Commit + Environment.NewLine,
                cancellationToken).ConfigureAwait(false);
            var verify = await RunProcessAsync(
                _config.GitExecutable.Path,
                new[]
                {
                    "--git-dir", snapshot, "fsck", "--connectivity-only",
                    "refs/heads/source"
                },
                root,
                environment,
                cancellationToken).ConfigureAwait(false);
            var current = InspectRepository(repository.Worktree, repository.Branch);
            if (verify.ExitCode != 0 || !string.Equals(
                    current.Commit, repository.Commit, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "task branch changed while the trusted push snapshot was created");
            }
            return snapshot;
        }
        catch
        {
            Directory.Delete(snapshot, true);
            throw;
        }
    }

    internal static void CopyGitObjects(string source, string destination)
    {
        var sourceRoot = Path.GetFullPath(source).TrimEnd(
            Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var pending = new Stack<(string Source, string Destination)>();
        pending.Push((sourceRoot, destination));
        while (pending.Count > 0)
        {
            var current = pending.Pop();
            if ((File.GetAttributes(current.Source) & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException(
                    "Git object database cannot contain reparse points");
            }
            Directory.CreateDirectory(current.Destination);
            foreach (var file in Directory.EnumerateFiles(current.Source))
            {
                var relative = Path.GetRelativePath(sourceRoot, file)
                    .Replace(Path.DirectorySeparatorChar, '/');
                if (relative is "info/alternates" or "info/http-alternates")
                {
                    throw new InvalidOperationException(
                        "Git object alternates are not supported by the Maintainer Broker");
                }
                if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0)
                {
                    throw new InvalidOperationException(
                        "Git object database cannot contain reparse points");
                }
                File.Copy(file, Path.Combine(current.Destination, Path.GetFileName(file)), true);
            }
            foreach (var directory in Directory.EnumerateDirectories(current.Source))
            {
                pending.Push((
                    directory,
                    Path.Combine(current.Destination, Path.GetFileName(directory))));
            }
        }
    }

    private Dictionary<string, string> SafeGitEnvironment(string? token, string? askPass)
    {
        var source = EnvironmentPolicy.Current();
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["SystemRoot"] = source.GetValueOrDefault("SystemRoot", Environment.GetFolderPath(Environment.SpecialFolder.Windows)),
            ["WINDIR"] = source.GetValueOrDefault("WINDIR", Environment.GetFolderPath(Environment.SpecialFolder.Windows)),
            ["COMSPEC"] = source.GetValueOrDefault("COMSPEC", Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "cmd.exe")),
            ["PATH"] = string.Join(Path.PathSeparator, new[]
            {
                Path.GetDirectoryName(_config.GitExecutable.Path)!,
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32")
            }),
            ["HOME"] = Path.Combine(_config.ServiceRoot, "broker-home"),
            ["USERPROFILE"] = Path.Combine(_config.ServiceRoot, "broker-home"),
            ["GIT_CONFIG_NOSYSTEM"] = "1",
            ["GIT_CONFIG_GLOBAL"] = "NUL",
            ["GIT_TERMINAL_PROMPT"] = "0",
            ["GCM_INTERACTIVE"] = "Never",
            ["GIT_SSH_COMMAND"] = "cmd.exe /d /c exit 121",
            ["NO_PROXY"] = "github.com,api.github.com"
        };
        if (token is not null && askPass is not null)
        {
            result["WORKFLOW_GITHUB_TOKEN"] = token;
            result["GIT_ASKPASS"] = askPass;
        }
        return result;
    }

    private static async Task<ProcessResult> RunProcessAsync(
        string executable,
        IEnumerable<string> arguments,
        string workdir,
        IReadOnlyDictionary<string, string> environment,
        CancellationToken cancellationToken)
    {
        var start = new ProcessStartInfo(executable)
        {
            WorkingDirectory = workdir,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };
        foreach (var argument in arguments)
        {
            start.ArgumentList.Add(argument);
        }
        start.Environment.Clear();
        foreach (var item in environment)
        {
            start.Environment[item.Key] = item.Value;
        }
        using var process = Process.Start(start) ?? throw new InvalidOperationException("failed to start Broker child process");
        var output = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var error = process.StandardError.ReadToEndAsync(cancellationToken);
        await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        return new ProcessResult(process.ExitCode, await output.ConfigureAwait(false), await error.ConfigureAwait(false));
    }

    private static string Redact(string value) =>
        value.Length <= 2048 ? value : value[^2048..];

    private sealed record RepositoryState(
        string Worktree,
        string GitDirectory,
        string CommonDirectory,
        string ObjectsDirectory,
        string Branch,
        string Commit);

    private sealed record ProcessResult(int ExitCode, string Output, string Error);
}

public static class SharedHttp
{
    public static readonly HttpClient Client = new(new SocketsHttpHandler
    {
        UseProxy = false,
        AllowAutoRedirect = false
    })
    {
        Timeout = TimeSpan.FromSeconds(60)
    };
}
