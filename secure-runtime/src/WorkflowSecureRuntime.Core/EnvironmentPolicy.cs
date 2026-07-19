using System.Collections;

namespace WorkflowSecureRuntime.Core;

public static class EnvironmentPolicy
{
    private static readonly HashSet<string> ProbeKeys = new(StringComparer.OrdinalIgnoreCase)
    {
        "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS", "OS", "TEMP", "TMP"
    };

    private static readonly HashSet<string> TaskKeys = new(ProbeKeys, StringComparer.OrdinalIgnoreCase)
    {
        "MULTICA_TOKEN", "MULTICA_SERVER_URL", "MULTICA_DAEMON_PORT", "MULTICA_WORKSPACE_ID",
        "MULTICA_AGENT_NAME", "MULTICA_AGENT_ID", "MULTICA_TASK_ID", "MULTICA_TASK_SLOT",
        "MULTICA_AUTOPILOT_RUN_ID", "MULTICA_AUTOPILOT_ID", "MULTICA_QUICK_CREATE_TASK_ID"
    };

    public static Dictionary<string, string> Current() =>
        Environment.GetEnvironmentVariables()
            .Cast<DictionaryEntry>()
            .Where(item => item.Key is string && item.Value is string)
            .ToDictionary(item => (string)item.Key, item => (string)item.Value!, StringComparer.OrdinalIgnoreCase);

    public static Dictionary<string, string> ForProbe(
        IReadOnlyDictionary<string, string> source,
        string isolatedRoot,
        string executableDirectory)
    {
        var result = Select(source, ProbeKeys);
        SetTaskHomes(result, isolatedRoot);
        result["PATH"] = BuildSystemPath(source, executableDirectory);
        result["CODEX_HOME"] = Path.Combine(isolatedRoot, "codex-home");
        result["PROGRAMDATA"] = Path.Combine(isolatedRoot, "program-data");
        return result;
    }

    public static Dictionary<string, string> ForTask(
        IReadOnlyDictionary<string, string> source,
        string taskRoot,
        string codexHome,
        string executableDirectory,
        int proxyPort)
    {
        var result = Select(source, TaskKeys);
        SetTaskHomes(result, Path.Combine(taskRoot, "command-home"));
        result["PATH"] = BuildSystemPath(source, executableDirectory);
        result["CODEX_HOME"] = codexHome;
        result["PROGRAMDATA"] = Path.Combine(taskRoot, "program-data");
        result["GH_CONFIG_DIR"] = Path.Combine(taskRoot, "gh-config");
        result["GIT_CONFIG_GLOBAL"] = Path.Combine(taskRoot, "gitconfig");
        result["GIT_CONFIG_NOSYSTEM"] = "1";
        result["GIT_TERMINAL_PROMPT"] = "0";
        result["GCM_INTERACTIVE"] = "Never";
        result["HTTP_PROXY"] = $"http://127.0.0.1:{proxyPort}";
        result["HTTPS_PROXY"] = $"http://127.0.0.1:{proxyPort}";
        result["ALL_PROXY"] = $"http://127.0.0.1:{proxyPort}";
        result["NO_PROXY"] = "";
        return result;
    }

    public static Dictionary<string, string> ForDaemon(
        SecureRuntimeConfiguration config,
        IReadOnlyDictionary<string, string> source)
    {
        var root = Path.Combine(config.ServiceRoot, "service-home");
        var result = Select(source, ProbeKeys);
        SetTaskHomes(result, root);
        result["PROGRAMDATA"] = Path.Combine(config.ServiceRoot, "program-data");
        result["PATH"] = string.Join(Path.PathSeparator, new[]
        {
            Path.GetDirectoryName(config.MulticaExecutable.Path)!,
            Path.GetDirectoryName(config.GitExecutable.Path)!,
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32")
        }.Distinct(StringComparer.OrdinalIgnoreCase));
        result["MULTICA_CODEX_PATH"] = config.LauncherExecutable.Path;
        result["MULTICA_SECURE_CONFIG"] = Path.Combine(config.ServiceRoot, "runtime.local.json");
        result["MULTICA_WORKSPACES_ROOT"] = config.WorkspacesRoot;
        result["MULTICA_DAEMON_ID"] = config.MulticaDaemonId;
        result["MULTICA_AGENT_RUNTIME_NAME"] = config.MulticaRuntimeName;
        result["MULTICA_DAEMON_AUTO_UPDATE"] = "false";
        result["GIT_CONFIG_GLOBAL"] = Path.Combine(config.ServiceRoot, "gitconfig");
        result["GIT_CONFIG_NOSYSTEM"] = "1";
        result["GIT_TERMINAL_PROMPT"] = "0";
        result["GCM_INTERACTIVE"] = "Never";
        result["GIT_SSH_COMMAND"] = "cmd.exe /d /c exit 121";
        result["GH_CONFIG_DIR"] = Path.Combine(config.ServiceRoot, "gh-config");
        result["HTTP_PROXY"] = $"http://127.0.0.1:{config.ProxyPort}";
        result["HTTPS_PROXY"] = $"http://127.0.0.1:{config.ProxyPort}";
        result["ALL_PROXY"] = $"http://127.0.0.1:{config.ProxyPort}";
        result["NO_PROXY"] = "";
        return result;
    }

    private static Dictionary<string, string> Select(
        IReadOnlyDictionary<string, string> source,
        HashSet<string> allowed)
    {
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var key in allowed)
        {
            if (source.TryGetValue(key, out var value) && !string.IsNullOrEmpty(value))
            {
                result[key] = value;
            }
        }
        return result;
    }

    private static void SetTaskHomes(IDictionary<string, string> target, string root)
    {
        target["HOME"] = root;
        target["USERPROFILE"] = root;
        target["APPDATA"] = Path.Combine(root, "app-data", "roaming");
        target["LOCALAPPDATA"] = Path.Combine(root, "app-data", "local");
        target["TEMP"] = Path.Combine(root, "temp");
        target["TMP"] = target["TEMP"];
    }

    private static string BuildSystemPath(
        IReadOnlyDictionary<string, string> source,
        string executableDirectory)
    {
        var windows = source.TryGetValue("SystemRoot", out var root) && !string.IsNullOrWhiteSpace(root)
            ? root
            : Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        return string.Join(Path.PathSeparator, new[]
        {
            executableDirectory,
            Path.Combine(windows, "System32"),
            windows
        }.Distinct(StringComparer.OrdinalIgnoreCase));
    }
}
