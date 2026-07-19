using System.Security.Cryptography;

namespace WorkflowSecureRuntime.Core;

public static class Hashing
{
    public static string Sha256File(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    public static string Sha256Bytes(ReadOnlySpan<byte> value) =>
        Convert.ToHexString(SHA256.HashData(value)).ToLowerInvariant();

    public static bool IsSha256(string value) =>
        value.Length == 64 && value.All(Uri.IsHexDigit);

    public static void VerifyExecutable(VerifiedExecutable executable)
    {
        if (!File.Exists(executable.Path))
        {
            throw new FileNotFoundException("verified executable is missing", executable.Path);
        }
        var observed = Sha256File(executable.Path);
        if (!CryptographicOperations.FixedTimeEquals(
                Convert.FromHexString(observed), Convert.FromHexString(executable.Sha256)))
        {
            throw new InvalidDataException($"executable hash mismatch: {executable.Path}");
        }
    }
}
