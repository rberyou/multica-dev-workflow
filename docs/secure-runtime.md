# Secure Agent Runtime

Status: reviewed Phase 3 target. Phase 1 keeps `secure_runtime.phase=planned` and does not claim these controls are active in the Canary workspace.

RC4 isolates workflow maintenance Agents from the host user's GitHub, Git,
Codex, browser, and operating-system credentials. The host user may keep using
their normal `gh` and SSH setup. Managed Agents run only through the dedicated
Windows service and Secure Agent Launcher.

## Trust Boundaries

Trusted components:

- the human Windows administrator;
- the reviewed repository and release asset;
- the Windows service virtual account;
- the Secure Agent Launcher and Token Broker hashes;
- Multica task-scoped `mat_` tokens;
- the dedicated Codex credential installed for the service;
- the Maintainer and Publisher GitHub Apps;
- the host-only Dispatcher GitHub App and short-lived human-held token.

Untrusted inputs:

- Agent prompts and generated shell commands;
- repository content and local Git configuration;
- inherited host environment variables;
- MCP, plugin, hook, browser, and Computer Use configuration;
- task-supplied branch, PR, URL, and worktree values;
- DNS responses and proxy destinations.

The model does not protect against a compromised Windows administrator,
kernel, Multica service, GitHub service, OpenAI service, or replacement of all
reviewed binaries and policy evidence by the human owner.

## Components

`workflow-secure-daemon-host.exe`

- runs as `NT SERVICE\MulticaWorkflowSecureRuntime`;
- starts the Token Broker and a dedicated Multica daemon;
- verifies every configured executable hash before startup;
- supplies the Secure Agent Launcher through `MULTICA_CODEX_PATH`;
- starts from a rebuilt environment without host GitHub credentials.

`secure-agent-launcher.exe`

- accepts only fixed Codex probes and `app-server --listen stdio://`;
- verifies its Multica parent process path and hash;
- asks the Broker to attest the task, Agent, Workspace, Runtime, and role;
- creates isolated task homes, `CODEX_HOME`, Git/GH config, and ProgramData;
- copies only hash-bound workflow Skills and the dedicated Codex auth file,
  then deletes the complete task home when Codex exits and fails closed if
  credential cleanup cannot complete;
- uses Codex permission profiles and the elevated Windows sandbox;
- disables MCP, plugins, hooks, Apps, browser, Computer Use, memories,
  multi-agent mode, remote control, Appshots, and web search.

`workflow-token-broker.exe`

- owns the Maintainer App private key; the key and raw installation token never
  enter an Agent environment;
- binds the Launcher to a Windows Job Object before Codex starts, so every
  descendant inherits the Job without a post-start race;
- authorizes Broker RPCs by process ancestry, Job membership, capability
  signature, task identity, and Security Profile;
- mints one short-lived installation token per operation and revokes it after
  use;
- exposes only `push-task-branch`, `upsert-pr`, and `read-ci`;
- refuses main, tag-like, out-of-profile, non-current, or injected branch refs;
- rejects dangerous local Git config, external Git metadata, and reparse-point
  worktrees;
- snapshots the approved branch commit into a Broker-owned bare repository
  before minting a token, then pushes only from that immutable private snapshot;
- runs a loopback CONNECT proxy that permits only reviewed HTTPS domains and
  rejects local, private, mapped-private, documentation, and non-routable IPs;
- parses the first TLS ClientHello and requires SNI to equal the reviewed
  CONNECT authority before opening the upstream tunnel.

`workflow-console.exe` and `multica-workflow-console`

- are host-only and do not receive service or App credentials;
- provide read-only workflow status in RC4;
- are not attached to managed Agents.

## Security Profiles

`workflow_maintainer`:

- can write only its isolated task worktree through the Codex sandbox;
- can call the three bounded Broker RPCs;
- cannot receive a raw GitHub token;
- cannot merge, push main, create tags or Releases, or administer GitHub.

`workflow_reviewer`:

- is tokenless;
- has no mutating Broker lease;
- reviews an isolated worktree and binds the result to `plan_revision` and the
  full commit SHA;
- is used by both the Maintenance Reviewer and Observer.

OpenCode is not an approved RC4 maintenance Runtime.

## Local Layout

Default installation root: `C:\ProgramData\MulticaWorkflow`.

```text
bin/                 reviewed service, launcher, broker, console, Multica
config/              final Agent bindings
credentials/codex/   dedicated auth.json
credentials/github/  Maintainer App private key
service-home/        dedicated Multica profile and daemon home
bundle/              reviewed repository bundle and Skill hashes
workspaces/           secure Multica task worktrees
tasks/                ephemeral task homes and Codex configuration
audit/                Broker and launch audit events
runtime.local.json    executable hashes and local policy
```

Credentials, config, service home, and runtime policy must not grant access to
Everyone, Authenticated Users, or the local Users group. The executable
directory may grant read/execute because parent/hash/task attestation remains
mandatory.

## Acceptance Matrix

The deployment is accepted only when:

- all Python, schema, packaging, and .NET security tests pass on Windows,
  Linux, and macOS CI;
- the Windows self-contained runtime asset builds reproducibly;
- both role-specific `--workflow-security-probe` commands pass against the
  installed Codex executable with `--strict-config`;
- the service uses the reviewed virtual account and is running;
- the service reports Running only after the Broker and Multica daemon survive
  startup validation;
- executable hashes, bundle hashes, ACL checks, firewall checks, and final
  bindings pass `doctor.ps1`;
- final bindings are not marked `bootstrap` and bind the dedicated Runtime UUID;
- a Maintainer can push only its current allowed branch and upsert its PR;
- a Reviewer cannot call a mutating Broker RPC or obtain a GitHub token;
- host `gh`, SSH, `.gitconfig`, Credential Manager, browser state, and personal
  Codex state remain unavailable inside Agent tasks.
- install/reinstall and final-binding updates restore the previous files and
  service state if startup fails.

## Residual Risk

The human repository owner can still reconfigure Apps, Rulesets, Environments,
secrets, or repository visibility. Administrator evidence and digest-bound
release planning detect reviewed-state drift but cannot prevent a malicious
owner. The first secure-runtime deployment therefore uses an external human
commit/push bootstrap and remains blocked until final bindings and doctor checks
pass.
