using System.Buffers.Binary;
using System.Net;
using System.Net.Sockets;
using System.Text;

namespace WorkflowSecureRuntime.Core;

public sealed class AllowlistProxy
{
    private readonly int _port;
    private readonly string[] _domains;

    public AllowlistProxy(int port, IEnumerable<string> domains)
    {
        _port = port;
        _domains = domains.Select(item => item.Trim().ToLowerInvariant())
            .Where(item => item.Length > 0)
            .Distinct(StringComparer.Ordinal)
            .ToArray();
    }

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var listener = new TcpListener(IPAddress.Loopback, _port);
        listener.Start();
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                var client = await listener.AcceptTcpClientAsync(cancellationToken).ConfigureAwait(false);
                _ = HandleClientAsync(client, cancellationToken);
            }
        }
        finally
        {
            listener.Stop();
        }
    }

    private async Task HandleClientAsync(TcpClient client, CancellationToken cancellationToken)
    {
        using (client)
        {
            client.NoDelay = true;
            var source = client.GetStream();
            var header = await ReadHeaderAsync(source, cancellationToken).ConfigureAwait(false);
            var lines = Encoding.ASCII.GetString(header).Split("\r\n", StringSplitOptions.None);
            var first = lines[0].Split(' ', StringSplitOptions.RemoveEmptyEntries);
            if (first.Length != 3 || !string.Equals(first[0], "CONNECT", StringComparison.OrdinalIgnoreCase))
            {
                await WriteFailureAsync(source, 405, "CONNECT required", cancellationToken).ConfigureAwait(false);
                return;
            }
            var authority = first[1].Split(':', 2);
            if (authority.Length != 2 || !int.TryParse(authority[1], out var port) || port != 443)
            {
                await WriteFailureAsync(source, 403, "destination port denied", cancellationToken).ConfigureAwait(false);
                return;
            }
            var host = authority[0].Trim('[', ']').ToLowerInvariant();
            if (!IsAllowedHost(host) || IPAddress.TryParse(host, out _))
            {
                await WriteFailureAsync(source, 403, "destination denied", cancellationToken).ConfigureAwait(false);
                return;
            }
            var addresses = await Dns.GetHostAddressesAsync(host, cancellationToken).ConfigureAwait(false);
            if (addresses.Length == 0 || addresses.Any(IsPrivateOrLocal))
            {
                await WriteFailureAsync(source, 403, "destination resolved to a private or local address", cancellationToken).ConfigureAwait(false);
                return;
            }
            var response = Encoding.ASCII.GetBytes("HTTP/1.1 200 Connection Established\r\n\r\n");
            await source.WriteAsync(response, cancellationToken).ConfigureAwait(false);
            var hello = await ReadTlsClientHelloAsync(
                source, cancellationToken).ConfigureAwait(false);
            if (!string.Equals(hello.ServerName, host, StringComparison.Ordinal))
            {
                return;
            }
            using var target = new TcpClient(addresses[0].AddressFamily);
            await target.ConnectAsync(addresses[0], port, cancellationToken).ConfigureAwait(false);
            var destination = target.GetStream();
            await destination.WriteAsync(hello.RawBytes, cancellationToken).ConfigureAwait(false);
            var upstream = source.CopyToAsync(destination, cancellationToken);
            var downstream = destination.CopyToAsync(source, cancellationToken);
            await Task.WhenAny(upstream, downstream).ConfigureAwait(false);
        }
    }

    private bool IsAllowedHost(string host) => _domains.Any(rule => rule switch
    {
        var value when value.StartsWith("**.", StringComparison.Ordinal) =>
            host == value[3..] || host.EndsWith("." + value[3..], StringComparison.Ordinal),
        var value when value.StartsWith("*.", StringComparison.Ordinal) =>
            host.EndsWith("." + value[2..], StringComparison.Ordinal) && host != value[2..],
        _ => string.Equals(host, rule, StringComparison.Ordinal)
    });

    public static bool IsPrivateOrLocal(IPAddress address)
    {
        if (address.IsIPv4MappedToIPv6)
        {
            return IsPrivateOrLocal(address.MapToIPv4());
        }
        if (IPAddress.IsLoopback(address) || address.IsIPv6LinkLocal || address.IsIPv6Multicast || address.IsIPv6SiteLocal)
        {
            return true;
        }
        if (address.AddressFamily == AddressFamily.InterNetwork)
        {
            var bytes = address.GetAddressBytes();
            return bytes[0] == 0 || bytes[0] == 10 || bytes[0] == 127 ||
                   (bytes[0] == 100 && bytes[1] is >= 64 and <= 127) ||
                   (bytes[0] == 169 && bytes[1] == 254) ||
                   (bytes[0] == 172 && bytes[1] is >= 16 and <= 31) ||
                   (bytes[0] == 192 && bytes[1] == 0 && bytes[2] is 0 or 2) ||
                   (bytes[0] == 192 && bytes[1] == 168) ||
                   (bytes[0] == 198 && bytes[1] is 18 or 19) ||
                   (bytes[0] == 198 && bytes[1] == 51 && bytes[2] == 100) ||
                   (bytes[0] == 203 && bytes[1] == 0 && bytes[2] == 113) ||
                   bytes[0] >= 224;
        }
        var ipv6 = address.GetAddressBytes();
        return address.Equals(IPAddress.IPv6Any) || address.Equals(IPAddress.IPv6None) ||
               address.Equals(IPAddress.IPv6Loopback) ||
               (ipv6[0] & 0xfe) == 0xfc ||
               (ipv6[0] == 0x20 && ipv6[1] == 0x01 && ipv6[2] == 0x0d && ipv6[3] == 0xb8);
    }

    private static async Task<TlsClientHello> ReadTlsClientHelloAsync(
        NetworkStream stream, CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(
            cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(10));
        using var raw = new MemoryStream();
        using var handshake = new MemoryStream();
        var expectedHandshakeLength = -1;
        while (expectedHandshakeLength < 0 || handshake.Length < expectedHandshakeLength)
        {
            var header = await ReadExactAsync(stream, 5, timeout.Token).ConfigureAwait(false);
            if (header[0] != 22)
            {
                throw new InvalidOperationException("proxy tunnel must begin with a TLS handshake");
            }
            var length = BinaryPrimitives.ReadUInt16BigEndian(header.AsSpan(3, 2));
            if (length == 0 || raw.Length + 5 + length > 65536)
            {
                throw new InvalidOperationException("TLS ClientHello is invalid or too large");
            }
            var payload = await ReadExactAsync(
                stream, length, timeout.Token).ConfigureAwait(false);
            raw.Write(header);
            raw.Write(payload);
            handshake.Write(payload);
            if (expectedHandshakeLength < 0 && handshake.Length >= 4)
            {
                var bytes = handshake.GetBuffer();
                if (bytes[0] != 1)
                {
                    throw new InvalidOperationException("TLS handshake is not a ClientHello");
                }
                expectedHandshakeLength = 4 +
                    (bytes[1] << 16 | bytes[2] << 8 | bytes[3]);
                if (expectedHandshakeLength is < 4 or > 65531)
                {
                    throw new InvalidOperationException("TLS ClientHello length is invalid");
                }
            }
        }
        var handshakeBytes = handshake.ToArray();
        if (handshakeBytes.Length != expectedHandshakeLength)
        {
            throw new InvalidOperationException("TLS record contains unexpected trailing handshake data");
        }
        return new TlsClientHello(
            raw.ToArray(), ParseTlsServerName(handshakeBytes));
    }

    public static string ParseTlsServerName(ReadOnlySpan<byte> clientHello)
    {
        if (clientHello.Length < 42 || clientHello[0] != 1)
        {
            throw new InvalidOperationException("TLS ClientHello is incomplete");
        }
        var declaredLength = clientHello[1] << 16 | clientHello[2] << 8 | clientHello[3];
        if (declaredLength != clientHello.Length - 4)
        {
            throw new InvalidOperationException("TLS ClientHello length does not match");
        }
        var offset = 4 + 2 + 32;
        offset = SkipVector(clientHello, offset, 1, "session ID");
        offset = SkipVector(clientHello, offset, 2, "cipher suites");
        offset = SkipVector(clientHello, offset, 1, "compression methods");
        var extensionsLength = ReadLength(clientHello, ref offset, 2, "extensions");
        var extensionsEnd = checked(offset + extensionsLength);
        if (extensionsEnd != clientHello.Length)
        {
            throw new InvalidOperationException("TLS ClientHello extensions are malformed");
        }
        string? serverName = null;
        var serverNameExtensionCount = 0;
        while (offset < extensionsEnd)
        {
            var type = ReadLength(clientHello, ref offset, 2, "extension type");
            var length = ReadLength(clientHello, ref offset, 2, "extension length");
            var end = checked(offset + length);
            if (end > extensionsEnd)
            {
                throw new InvalidOperationException("TLS ClientHello extension exceeds its container");
            }
            if (type == 0)
            {
                serverNameExtensionCount++;
                if (serverNameExtensionCount != 1)
                {
                    throw new InvalidOperationException(
                        "TLS ClientHello contains duplicate server name extensions");
                }
                var listOffset = offset;
                var listLength = ReadLength(clientHello, ref listOffset, 2, "server name list");
                if (listOffset + listLength != end)
                {
                    throw new InvalidOperationException("TLS server name list is malformed");
                }
                var entryCount = 0;
                while (listOffset < end)
                {
                    entryCount++;
                    var nameType = clientHello[listOffset++];
                    var nameLength = ReadLength(
                        clientHello, ref listOffset, 2, "server name");
                    if (listOffset + nameLength > end)
                    {
                        throw new InvalidOperationException("TLS server name exceeds its list");
                    }
                    if (nameType != 0 || entryCount != 1)
                    {
                        throw new InvalidOperationException(
                            "TLS server name list must contain exactly one DNS host name");
                    }
                    var nameBytes = clientHello.Slice(listOffset, nameLength);
                    var invalid = nameBytes.IsEmpty;
                    foreach (var value in nameBytes)
                    {
                        invalid |= value is < 0x21 or > 0x7e;
                    }
                    if (invalid)
                    {
                        throw new InvalidOperationException("TLS server name is invalid");
                    }
                    serverName = Encoding.ASCII.GetString(nameBytes).ToLowerInvariant();
                    listOffset += nameLength;
                }
                if (entryCount != 1 || serverName is null)
                {
                    throw new InvalidOperationException(
                        "TLS server name list must contain exactly one DNS host name");
                }
            }
            offset = end;
        }
        return serverNameExtensionCount == 1 && serverName is not null
            ? serverName
            : throw new InvalidOperationException(
                "TLS ClientHello has no unique DNS server name");
    }

    private static int SkipVector(
        ReadOnlySpan<byte> value, int offset, int width, string label)
    {
        var length = ReadLength(value, ref offset, width, label);
        var end = checked(offset + length);
        if (end > value.Length)
        {
            throw new InvalidOperationException($"TLS {label} exceeds ClientHello");
        }
        return end;
    }

    private static int ReadLength(
        ReadOnlySpan<byte> value, ref int offset, int width, string label)
    {
        if (width is < 1 or > 2 || offset + width > value.Length)
        {
            throw new InvalidOperationException($"TLS {label} length is missing");
        }
        var length = width == 1
            ? value[offset]
            : BinaryPrimitives.ReadUInt16BigEndian(value.Slice(offset, 2));
        offset += width;
        return length;
    }

    private static async Task<byte[]> ReadExactAsync(
        NetworkStream stream, int length, CancellationToken cancellationToken)
    {
        var value = new byte[length];
        var offset = 0;
        while (offset < value.Length)
        {
            var read = await stream.ReadAsync(
                value.AsMemory(offset), cancellationToken).ConfigureAwait(false);
            if (read == 0)
            {
                throw new InvalidOperationException("TLS ClientHello ended unexpectedly");
            }
            offset += read;
        }
        return value;
    }

    private static async Task<byte[]> ReadHeaderAsync(NetworkStream stream, CancellationToken cancellationToken)
    {
        using var buffer = new MemoryStream();
        var single = new byte[1];
        while (buffer.Length < 65536)
        {
            var read = await stream.ReadAsync(single, cancellationToken).ConfigureAwait(false);
            if (read == 0)
            {
                break;
            }
            buffer.WriteByte(single[0]);
            if (buffer.Length >= 4)
            {
                var bytes = buffer.GetBuffer();
                var length = (int)buffer.Length;
                if (bytes[length - 4] == '\r' && bytes[length - 3] == '\n' &&
                    bytes[length - 2] == '\r' && bytes[length - 1] == '\n')
                {
                    return buffer.ToArray();
                }
            }
        }
        throw new InvalidOperationException("proxy request header is incomplete or too large");
    }

    private static async Task WriteFailureAsync(
        NetworkStream stream,
        int status,
        string reason,
        CancellationToken cancellationToken)
    {
        var bytes = Encoding.ASCII.GetBytes($"HTTP/1.1 {status} {reason}\r\nConnection: close\r\n\r\n");
        await stream.WriteAsync(bytes, cancellationToken).ConfigureAwait(false);
    }

    private sealed record TlsClientHello(byte[] RawBytes, string ServerName);
}
