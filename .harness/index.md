# Harness Index

## Read Order

Choose reading scope from the root entry; reuse applicable context already read.
A copy or string edit usually needs only the target and its direct constraints.
For non-trivial work, read [rules](rules.md), use the [catalog](catalog.md) to select
one applicable workflow, and inspect the relevant source and tests.
Use the wiki index to select domain topics; follow cross-topic links only when
the task touches that boundary. Read [tooling and runtime](tooling-runtime.md)
before repository tools or environment diagnosis. Do not require a full wiki,
all workflows or historical bootstrap plans for each task.

## Responsibilities

- `.harness/` owns process and cross-task tool discovery.
- `.project-wiki/` owns durable knowledge and the project's operating model.
- Root entry files route agents to these authorities.
- Before the first remote exists, use [local bootstrap](workflows/local-bootstrap.md).
- After connection, use [GitHub planning](workflows/github-planning.md).

## Large Change Gate

Large changes require an implementation plan, test cases and user confirmation
before implementation. Existing explicit authorization remains valid within its
accepted scope; routine implementation choices do not require repeated approval.

## Knowledge Gate

When durable behavior, contracts, decisions, repeated failures or workflow rules
change, update the owning topic or record why a wiki update is unnecessary.
Keep full business rules in their topic pages, navigation in the wiki index,
and short change summaries in the log. Root entries change only for routing or
agent-wide workflow rules; do not copy each confirmed requirement into them.

## Engineering Gate

Apply [engineering principles](../.project-wiki/decisions/engineering-principles.md)
and the project's explicitly accepted operating model. Correctness, appropriate
recovery, precise destructive targeting and secret protection remain the floor.
High-cost controls must state a concrete current risk, resource and recovery
cost, and lower-cost alternatives. Evidence collection is itself proportionate.

## Data Evidence Gate

When a project ingests or derives data, preserve the relevant immutable source
or checksum, record provenance, distinguish observations from inference, and
include a bounded replay case before claiming the affected behavior verified.
This conditional gate does not prescribe a business data model or service stack.
