using System.Text;

namespace WorkflowSecureRuntime.Core;

public sealed record PreparedTaskEnvironment(
    string TaskRoot,
    string CodexHome,
    string ProgramData,
    IReadOnlyDictionary<string, string> Environment);

public static class TaskEnvironmentBuilder
{
    public static PreparedTaskEnvironment Prepare(
        SecureRuntimeConfiguration config,
        string taskId,
        string role,
        SecurityProfile profile,
        IReadOnlyDictionary<string, string> sourceEnvironment)
    {
        var taskRoot = Path.Combine(config.ServiceRoot, "tasks", taskId);
        var codexHome = Path.Combine(taskRoot, "codex-home");
        var programData = Path.Combine(taskRoot, "program-data");
        try
        {
            foreach (var path in new[]
                     {
                         taskRoot, codexHome, programData,
                         Path.Combine(taskRoot, "command-home"),
                         Path.Combine(taskRoot, "gh-config")
                     })
            {
                Directory.CreateDirectory(path);
            }
            File.WriteAllText(Path.Combine(taskRoot, "gitconfig"), "[credential]\n\thelper =\n[core]\n\thooksPath = NUL\n", Encoding.UTF8);
            File.WriteAllText(Path.Combine(codexHome, "config.toml"), RenderCodexConfig(config), Encoding.UTF8);
            var requirementsDir = Path.Combine(programData, "OpenAI", "Codex");
            Directory.CreateDirectory(requirementsDir);
            File.WriteAllText(
                Path.Combine(requirementsDir, "requirements.toml"),
                RenderRequirements(config, role), Encoding.UTF8);
            File.Copy(config.CodexCredentialPath, Path.Combine(codexHome, "auth.json"), true);
            var manifest = WorkflowBundleManifest.Load(config.WorkflowBundleManifest);
            manifest.VerifyAndCopySkills(config.WorkflowBundleRoot, profile.RequiredSkills, codexHome);
            var environment = EnvironmentPolicy.ForTask(
                sourceEnvironment, taskRoot, codexHome, Path.GetDirectoryName(config.CodexExecutable.Path)!, config.ProxyPort);
            environment["PATH"] = Path.GetDirectoryName(config.BrokerExecutable.Path)! +
                                  Path.PathSeparator + environment["PATH"];
            environment["WORKFLOW_BROKER_PIPE"] = config.BrokerPipeName;
            return new PreparedTaskEnvironment(taskRoot, codexHome, programData, environment);
        }
        catch (Exception error)
        {
            try
            {
                if (Directory.Exists(taskRoot))
                {
                    Directory.Delete(taskRoot, true);
                }
            }
            catch (Exception cleanup)
            {
                throw new AggregateException(
                    "secure task setup failed and credential cleanup also failed",
                    error,
                    cleanup);
            }
            throw;
        }
    }

    private static string RenderCodexConfig(SecureRuntimeConfiguration config)
    {
        var domains = string.Join(", ", config.NetworkDomains
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(item => item, StringComparer.OrdinalIgnoreCase)
            .Select(item => $"{TomlString(item)} = \"allow\""));
        return """
            [windows]
            sandbox = "elevated"
            sandbox_private_desktop = true

            [shell_environment_policy]
            inherit = "none"
            include_only = ["PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "MULTICA_TOKEN", "MULTICA_SERVER_URL", "MULTICA_DAEMON_PORT", "MULTICA_WORKSPACE_ID", "MULTICA_AGENT_NAME", "MULTICA_AGENT_ID", "MULTICA_TASK_ID", "MULTICA_TASK_SLOT", "MULTICA_AUTOPILOT_RUN_ID", "MULTICA_AUTOPILOT_ID", "MULTICA_QUICK_CREATE_TASK_ID", "WORKFLOW_BROKER_PIPE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"]

            [features]
            apps = false
            hooks = false
            browser_use = false
            browser_use_full_cdp_access = false
            browser_use_external = false
            in_app_browser = false
            computer_use = false
            memories = false
            multi_agent = false
            plugins = false
            remote_plugin = false
            workspace_dependencies = false
            network_proxy = true

            [features.network_proxy]
            enabled = true
            domains = { __DOMAINS__ }
            """.Replace("__DOMAINS__", domains, StringComparison.Ordinal) + Environment.NewLine;
    }

    private static string RenderRequirements(SecureRuntimeConfiguration config, string role)
    {
        var denied = new StringBuilder();
        foreach (var root in config.DeniedRoots.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            denied.AppendLine($"{TomlString(Path.GetFullPath(root))} = \"deny\"");
        }
        denied.AppendLine($"{TomlString(Path.GetFullPath(config.ServiceRoot))} = \"deny\"");
        var domains = string.Join(", ", config.NetworkDomains
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(item => item, StringComparer.OrdinalIgnoreCase)
            .Select(TomlString));
        return $"""
            default_permissions = "{role}"
            allowed_approval_policies = ["never"]
            allowed_web_search_modes = []
            allow_appshots = false
            allow_remote_control = false
            allow_managed_hooks_only = true

            [allowed_permission_profiles]
            {role} = true

            [permissions.{role}]
            description = "Managed Multica workflow profile."
            extends = ":workspace"

            [permissions.{role}.filesystem]
            ":minimal" = "read"
            {denied}
            [permissions.{role}.network]
            enabled = true

            [permissions.{role}.network.domains]
            {string.Join(Environment.NewLine, config.NetworkDomains.Distinct(StringComparer.OrdinalIgnoreCase).Select(item => $"{TomlString(item)} = \"allow\""))}

            [windows]
            allowed_sandbox_implementations = ["elevated"]

            [features]
            apps = false
            hooks = false
            browser_use = false
            browser_use_full_cdp_access = false
            browser_use_external = false
            in_app_browser = false
            computer_use = false
            memories = false
            multi_agent = false
            plugin_sharing = false
            plugins = false
            remote_plugin = false
            workspace_dependencies = false

            [mcp_servers]

            [plugins]

            [marketplaces]
            restrict_to_allowed_sources = true

            [experimental_network]
            enabled = true
            allowed_domains = [{domains}]
            managed_allowed_domains_only = true
            """ + Environment.NewLine;
    }

    private static string TomlString(string value) =>
        "\"" + value.Replace("\\", "\\\\", StringComparison.Ordinal)
            .Replace("\"", "\\\"", StringComparison.Ordinal) + "\"";
}
