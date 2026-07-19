namespace WorkflowSecureRuntime.Core;

public static class PathPolicy
{
    public static string RequireDirectoryUnderRoot(
        string rootPath, string candidatePath, string label)
    {
        var root = Path.GetFullPath(rootPath).TrimEnd(
            Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var candidate = Path.GetFullPath(candidatePath).TrimEnd(
            Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var relative = Path.GetRelativePath(root, candidate);
        if (relative == "." || Path.IsPathRooted(relative) || relative == ".." ||
            relative.StartsWith(".." + Path.DirectorySeparatorChar, StringComparison.Ordinal) ||
            !Directory.Exists(candidate))
        {
            throw new InvalidOperationException($"{label} is outside its secure root");
        }
        RejectReparsePoint(root, label);
        var current = root;
        foreach (var segment in relative.Split(
                     new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
                     StringSplitOptions.RemoveEmptyEntries))
        {
            current = Path.Combine(current, segment);
            RejectReparsePoint(current, label);
        }
        return candidate;
    }

    private static void RejectReparsePoint(string path, string label)
    {
        var attributes = File.GetAttributes(path);
        if ((attributes & FileAttributes.ReparsePoint) != 0)
        {
            throw new InvalidOperationException($"{label} cannot traverse a reparse point");
        }
    }
}
