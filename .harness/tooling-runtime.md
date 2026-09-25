# Tooling And Runtime Guide

## Discovery Order

1. Use the root entry or catalog to locate the applicable workflow and relevant
   knowledge; reuse context already read instead of traversing every document.
2. Find the canonical command in [scripts](../scripts/README.md).
3. Check tool availability and the declared version contract.
4. Reuse an installed compatible runtime; scope environment overrides to one command.
5. Resolve non-secret values from explicit arguments, process environment and
   explicitly approved repository configuration, in that order.
6. Apply the active phase, network and filesystem policy.
7. Preserve the original failure and classify it before retrying.

Never execute or `source` a machine-local `.env` or an example environment file.
A tracked, reviewed tool-version declaration is Shell configuration owned by the
repository and is intentionally sourced by quality scripts. Do not print tokens,
passwords or credential files, or install tools merely to diagnose availability.

## Canonical Commands

| Use | Command |
| --- | --- |
| Offline tool availability and versions | `scripts/quality/check.sh --check-tools-only` |
| Offline quality and workflow regressions | `scripts/quality/check.sh` |
| Template files and references | `scripts/quality/check_template.sh` |
| Issue lifecycle, after GitHub connection | `scripts/github/issue_status.sh` |
| Live PR contract | `scripts/github/pr_contract.sh` |
| Local review JSON structure | `scripts/github/review_convergence.sh RECORD.json` |

The [tool runbook](../docs/runbooks/issue-pr-quality-tooling.md) owns installation.
Default checks use simulated GitHub clients and need neither a real `gh` binary
nor GitHub authentication. Live modes require `gh` and authorized network access.
A project's language runtime and service requirements belong in its wiki and
runbooks. No business interpreter, database or search service is a harness default.

## Phase And Quality Boundaries

Requirements work permits bounded read-only discovery, not full acceptance.
Run full checks in authorized implementation or independent review. Checks return
failure for missing or wrong tools; do not weaken the version contract to pass.

For a committed review surface, use the recorded immutable merge base:

```bash
review_base=${REVIEW_BASE_SHA:?set the workflow-recorded base SHA}
git cat-file -e "${review_base}^{commit}"
git diff --check "${review_base}...HEAD"
```

Check worktree and index separately with `git diff --check` and
`git diff --cached --check`. For an initial root commit, inspect
`git show --format= --check HEAD`; a nonexistent parent is not a review base.

## GitHub Network And Authentication

Network calls require authorization. In a network-restricted sandbox, retry an
allowed call outside it with narrowly scoped approval. A `proxyconnect`, socket,
permission or timeout error does not prove invalid credentials. Before declaring
a token invalid, run both checks in an environment permitted to reach GitHub:

```bash
gh auth status -h github.com
gh api user --jq '.login'
```

Classify an authentication failure only after reaching the service. Check the
actual repository owner/name before a write. CLI and connector authentication
are separate; success through one does not prove access through the other.

When GitHub is unavailable after connection, diagnose and stop live Issue work.
An outage does not enable local-bootstrap mode or make an old Issue export current.

## Optional Wiki Graph

A graph is runtime assistance derived from [wiki schema](../.project-wiki/schema.md)
and frontmatter, never a separately maintained source of truth. If the current
client exposes a read-only wiki graph capability, inspect its actual tool schema
and use that exact tool. Do not invent an MCP name or install a missing integration.

When no graph capability is available, use repository search:

```bash
rg -n '^(id:|type:|owners:|sources:|related:)' .project-wiki
rg -n 'TASK_TERM_OR_ENTITY_ID' .project-wiki scripts
```

Read the matched entities, then their owning source paths. If `rg` is absent,
use an installed read-only search equivalent. Generated graph data is disposable.

## Failure Boundaries

Distinguish missing tools, wrong versions, unresolved non-secret parameters,
network failures, service authentication failures and application verification
failures. State the original error and unresolved requirement. An unavailable
service is not a passing test. Ask only for missing information or authority;
never expose a secret, change credentials or expand the task during diagnosis.
