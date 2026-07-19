using System.Text.Json;
using System.Text.Json.Serialization;

namespace WorkflowSecureRuntime.Core;

public sealed class WorkflowBundleManifest
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("files")]
    public Dictionary<string, string> Files { get; set; } = new(StringComparer.Ordinal);

    public static WorkflowBundleManifest Load(string path)
    {
        var value = JsonSerializer.Deserialize<WorkflowBundleManifest>(
            File.ReadAllText(path), JsonDefaults.Options)
            ?? throw new InvalidDataException("workflow bundle manifest is empty");
        if (value.SchemaVersion != 1 || value.Files.Count == 0 ||
            value.Files.Any(item => !Hashing.IsSha256(item.Value) || Path.IsPathFullyQualified(item.Key) || item.Key.Contains("..", StringComparison.Ordinal)))
        {
            throw new InvalidDataException("workflow bundle manifest is invalid");
        }
        return value;
    }

    public void VerifyAndCopySkills(
        string bundleRoot,
        IEnumerable<string> skills,
        string destinationRoot)
    {
        foreach (var skill in skills.Distinct(StringComparer.Ordinal))
        {
            if (skill.Contains('/') || skill.Contains('\\') || skill is "." or "..")
            {
                throw new InvalidDataException("skill names must be simple directory names");
            }
            var prefix = $"skills/{skill}/";
            var matches = Files.Where(item => item.Key.StartsWith(prefix, StringComparison.Ordinal)).ToArray();
            if (matches.Length == 0)
            {
                throw new InvalidDataException($"workflow bundle manifest has no files for {skill}");
            }
            foreach (var item in matches)
            {
                var source = Path.GetFullPath(Path.Combine(bundleRoot, item.Key.Replace('/', Path.DirectorySeparatorChar)));
                var root = Path.GetFullPath(bundleRoot) + Path.DirectorySeparatorChar;
                if (!source.StartsWith(root, StringComparison.OrdinalIgnoreCase) || !File.Exists(source) ||
                    !string.Equals(Hashing.Sha256File(source), item.Value, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException($"workflow bundle file failed verification: {item.Key}");
                }
                var relative = item.Key[prefix.Length..].Replace('/', Path.DirectorySeparatorChar);
                var destination = Path.Combine(destinationRoot, "skills", skill, relative);
                Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
                File.Copy(source, destination, true);
            }
        }
    }
}
