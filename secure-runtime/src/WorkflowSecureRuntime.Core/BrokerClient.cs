using System.IO.Pipes;
using System.Text;
using System.Text.Json;

namespace WorkflowSecureRuntime.Core;

public static class BrokerClient
{
    public static async Task<TResponse> SendAsync<TRequest, TResponse>(
        string pipeName,
        string operation,
        TRequest payload,
        CancellationToken cancellationToken)
    {
        using var pipe = new NamedPipeClientStream(
            ".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
        await pipe.ConnectAsync(10000, cancellationToken).ConfigureAwait(false);
        var envelope = JsonSerializer.Serialize(new
        {
            operation,
            payload
        }, JsonDefaults.Options);
        var bytes = Encoding.UTF8.GetBytes(envelope + "\n");
        await pipe.WriteAsync(bytes, cancellationToken).ConfigureAwait(false);
        await pipe.FlushAsync(cancellationToken).ConfigureAwait(false);
        using var reader = new StreamReader(pipe, Encoding.UTF8, false, 4096, true);
        var line = await reader.ReadLineAsync(cancellationToken).ConfigureAwait(false)
            ?? throw new InvalidOperationException("Broker closed the pipe without a response");
        using var document = JsonDocument.Parse(line);
        if (!document.RootElement.GetProperty("ok").GetBoolean())
        {
            var error = document.RootElement.TryGetProperty("error", out var value)
                ? value.GetString()
                : "Broker request failed";
            throw new InvalidOperationException(error);
        }
        if (!document.RootElement.TryGetProperty("payload", out var response))
        {
            throw new InvalidOperationException("Broker response has no payload");
        }
        return response.Deserialize<TResponse>(JsonDefaults.Options)
            ?? throw new InvalidOperationException("Broker response payload is invalid");
    }
}
