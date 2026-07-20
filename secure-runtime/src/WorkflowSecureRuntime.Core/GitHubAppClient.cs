using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace WorkflowSecureRuntime.Core;

public sealed class GitHubAppClient
{
    private readonly HttpClient _client;
    private readonly SecureRuntimeConfiguration _config;
    private readonly RepositoryContract _repository;

    public GitHubAppClient(HttpClient client, SecureRuntimeConfiguration config)
    {
        _client = client;
        _config = config;
        if (!RepositoryContract.TryParse(config.Repository, out _repository))
        {
            throw new InvalidDataException("invalid repository contract");
        }
    }

    public async Task<InstallationTokenLease> MintMaintainerTokenAsync(
        CancellationToken cancellationToken)
    {
        var jwt = CreateAppJwt();
        using var request = new HttpRequestMessage(
            HttpMethod.Post,
            new Uri(new Uri(_config.GitHub.ApiUrl.TrimEnd('/') + "/"),
                $"app/installations/{_config.GitHub.InstallationId}/access_tokens"));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", jwt);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        request.Headers.Add("X-GitHub-Api-Version", "2022-11-28");
        request.Content = JsonContent(new
        {
            repositories = new[] { _repository.Name },
            permissions = new Dictionary<string, string>
            {
                ["actions"] = "read",
                ["contents"] = "write",
                ["metadata"] = "read",
                ["pull_requests"] = "write"
            }
        });
        using var response = await _client.SendAsync(request, cancellationToken).ConfigureAwait(false);
        var body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException(
                $"GitHub installation token request failed with HTTP {(int)response.StatusCode}");
        }
        using var document = JsonDocument.Parse(body);
        var token = document.RootElement.GetProperty("token").GetString() ?? "";
        var expires = document.RootElement.GetProperty("expires_at").GetDateTimeOffset();
        if (string.IsNullOrWhiteSpace(token) || expires <= DateTimeOffset.UtcNow.AddMinutes(1))
        {
            throw new InvalidOperationException("GitHub returned an invalid installation token");
        }
        await VerifyInstallationAsync(token, cancellationToken).ConfigureAwait(false);
        return new InstallationTokenLease(_client, _config.GitHub.ApiUrl, token, expires);
    }

    private async Task VerifyInstallationAsync(string token, CancellationToken cancellationToken)
    {
        using var installation = CreateTokenRequest(HttpMethod.Get, "installation", token);
        using var response = await _client.SendAsync(installation, cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException("GitHub installation identity readback failed");
        }
        using var document = JsonDocument.Parse(
            await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false));
        var appId = document.RootElement.GetProperty("app_id").GetInt64();
        var installationId = document.RootElement.GetProperty("id").GetInt64();
        var account = document.RootElement.GetProperty("account").GetProperty("login").GetString() ?? "";
        var selection = document.RootElement.GetProperty("repository_selection").GetString() ?? "";
        if (appId != _config.GitHub.AppId || installationId != _config.GitHub.InstallationId ||
            !string.Equals(account, _repository.Owner, StringComparison.OrdinalIgnoreCase) ||
            selection != "selected")
        {
            throw new InvalidOperationException("GitHub installation token identity does not match the Broker config");
        }
        var permissions = document.RootElement.GetProperty("permissions");
        var expected = new Dictionary<string, string>(StringComparer.Ordinal)
        {
            ["actions"] = "read",
            ["contents"] = "write",
            ["metadata"] = "read",
            ["pull_requests"] = "write"
        };
        var observed = permissions.EnumerateObject().ToDictionary(
            property => property.Name,
            property => property.Value.GetString() ?? "",
            StringComparer.Ordinal);
        if (observed.Count != expected.Count || expected.Any(item =>
                !observed.TryGetValue(item.Key, out var value) || value != item.Value))
        {
            throw new InvalidOperationException("GitHub App permissions differ from the bounded Maintainer contract");
        }
        using var repositories = CreateTokenRequest(HttpMethod.Get, "installation/repositories?per_page=100", token);
        using var repositoryResponse = await _client.SendAsync(repositories, cancellationToken).ConfigureAwait(false);
        if (!repositoryResponse.IsSuccessStatusCode)
        {
            throw new InvalidOperationException("GitHub installation repository readback failed");
        }
        using var repositoryDocument = JsonDocument.Parse(
            await repositoryResponse.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false));
        var names = repositoryDocument.RootElement.GetProperty("repositories")
            .EnumerateArray()
            .Select(item => item.GetProperty("full_name").GetString() ?? "")
            .Where(item => item.Length > 0)
            .ToArray();
        var totalCount = repositoryDocument.RootElement.GetProperty("total_count").GetInt32();
        if (totalCount != 1 || names.Length != 1 ||
            !string.Equals(names[0], _config.Repository, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("Maintainer App token is not restricted to the workflow repository");
        }
    }

    public HttpRequestMessage CreateTokenRequest(HttpMethod method, string relative, string token)
    {
        var request = new HttpRequestMessage(
            method,
            new Uri(new Uri(_config.GitHub.ApiUrl.TrimEnd('/') + "/"), relative));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        request.Headers.Add("X-GitHub-Api-Version", "2022-11-28");
        request.Headers.UserAgent.ParseAdd("multica-workflow-token-broker/1.1.0-rc.4");
        return request;
    }

    public static StringContent JsonContent(object value) => new(
        JsonSerializer.Serialize(value, JsonDefaults.Options),
        Encoding.UTF8,
        "application/json");

    private string CreateAppJwt()
    {
        using var rsa = RSA.Create();
        rsa.ImportFromPem(File.ReadAllText(_config.GitHub.PrivateKeyPath));
        var now = DateTimeOffset.UtcNow;
        var header = Base64Url(JsonSerializer.SerializeToUtf8Bytes(new { alg = "RS256", typ = "JWT" }));
        var payload = Base64Url(JsonSerializer.SerializeToUtf8Bytes(new
        {
            iat = now.AddSeconds(-30).ToUnixTimeSeconds(),
            exp = now.AddMinutes(8).ToUnixTimeSeconds(),
            iss = _config.GitHub.AppId
        }));
        var input = Encoding.ASCII.GetBytes(header + "." + payload);
        var signature = rsa.SignData(input, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        return header + "." + payload + "." + Base64Url(signature);
    }

    private static string Base64Url(ReadOnlySpan<byte> value) =>
        Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
}

public sealed class InstallationTokenLease : IAsyncDisposable
{
    private readonly HttpClient _client;
    private readonly string _apiUrl;
    private bool _disposed;

    public InstallationTokenLease(HttpClient client, string apiUrl, string token, DateTimeOffset expiresAt)
    {
        _client = client;
        _apiUrl = apiUrl;
        Token = token;
        ExpiresAt = expiresAt;
    }

    public string Token { get; }
    public DateTimeOffset ExpiresAt { get; }

    public async ValueTask DisposeAsync()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        using var request = new HttpRequestMessage(
            HttpMethod.Delete,
            new Uri(new Uri(_apiUrl.TrimEnd('/') + "/"), "installation/token"));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", Token);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        request.Headers.Add("X-GitHub-Api-Version", "2022-11-28");
        request.Headers.UserAgent.ParseAdd("multica-workflow-token-broker/1.1.0-rc.4");
        try
        {
            using var _ = await _client.SendAsync(request).ConfigureAwait(false);
        }
        catch (HttpRequestException)
        {
            // Expiry remains bounded to one hour even if best-effort revocation fails.
        }
    }
}
