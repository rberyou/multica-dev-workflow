using System.Text.Json;

namespace WorkflowSecureRuntime.Core;

public sealed class AuditLog
{
    private readonly string _path;
    private readonly SemaphoreSlim _gate = new(1, 1);

    public AuditLog(string path)
    {
        _path = path;
    }

    public async Task WriteAsync(string eventName, object details, CancellationToken cancellationToken = default)
    {
        var line = JsonSerializer.Serialize(new
        {
            at = DateTimeOffset.UtcNow,
            event_name = eventName,
            details
        }, JsonDefaults.Options);
        Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
        await _gate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            await File.AppendAllTextAsync(_path, line + Environment.NewLine, cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            _gate.Release();
        }
    }
}
