# Agent Entry

For non-trivial development work:

1. Read `.harness/index.md` and `.harness/rules.md`.
2. Use `.harness/catalog.md` to select the workflow.
3. Read relevant `.project-wiki/` entities and current source before editing.
4. Read `.harness/tooling-runtime.md` before running repository tools.
5. Update durable project knowledge when behavior or decisions change.

## Lifecycle

Before the first GitHub remote exists, follow
`.harness/workflows/local-bootstrap.md`: confirm scope, plan and test cases,
then implement and record local verification. Connect the remote only when
its owner, name, visibility and publication scope are authorized.

After connection, requirements, Issue implementation, review and handoff follow
`.harness/workflows/github-planning.md`. `status:*` labels own execution state.
Start unclaimed work only from `status:planned`; resume `status:in-progress`
only with an explicit handoff and no concurrent owner. Use
`scripts/github/issue_status.sh` for transitions and checks. A temporarily
unreachable or removed remote does not reset an existing Issue lifecycle.

Issue titles, bodies, workflow comments and PR bodies default to Chinese.
Requirements work permits clarification and bounded read-only investigation;
implementation and independent review own changes and full acceptance checks.
Confirmed scope changes must update the live Issue contract before implementation.
The workflow defines reopening, follow-up and completion rules.

## Tool And Network Boundaries

Use the current session's filesystem, network and approval policy. When a
network-restricted sandbox blocks GitHub, use a narrowly scoped authorized
outside-sandbox invocation. A network error is not evidence of a bad token;
follow the diagnostic sequence in `.harness/tooling-runtime.md`.

## Quality

`scripts/quality/check.sh` runs offline harness checks. The `--pr` mode reads
GitHub and requires separately authorized network access. Project-specific
checks are declared in project knowledge and added explicitly to delivery plans.

## SQL APM Current Phase

Read `.project-wiki/index.md` and `.harness/plans/local-bootstrap.md` for the
current project context. The authorized scope is local repository bootstrap
and preservation of the original requirements source.

The SQL Baseline snapshot is raw discussion input. Its recommendations and
previous conversational implementation suggestions are not accepted product
requirements. Business architecture, stack, scope, thresholds, acceptance
criteria and the project operating model remain pending user discussion.
Preserve the raw snapshot bytes; record later confirmed requirements separately.

No GitHub remote has been authorized for this bootstrap. Upstream historical
plans and reports do not authorize publication or prove this project's checks.
