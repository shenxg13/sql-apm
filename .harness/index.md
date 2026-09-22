# Harness Index

## Read Order

1. Read the root agent entry.
2. Read this index and [rules](rules.md).
3. Read [tooling and runtime](tooling-runtime.md) before invoking tools.
4. Use the [catalog](catalog.md) to select a workflow.
5. Read relevant project wiki entities and inspect current source and tests.

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
change, update the wiki or record why a wiki update is unnecessary.

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
