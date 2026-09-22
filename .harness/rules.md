# Harness Rules

## Facts, Intent And Process

- Explicit user instructions and confirmed requirements define desired behavior
  and authorized scope, subject to the execution environment's higher-level rules.
- Current code, bounded observations and verified results establish implemented
  behavior; immutable source evidence establishes relevant input facts.
- Harness rules define the development process within that scope.
- Wiki claims require sources. Historical material is not current authority until
  checked against source and active decisions.
- A discrepancy between implementation and requirements is recorded and resolved;
  existing behavior does not silently change the accepted goal.

## Progressive, Surgical Development

Prefer the easiest valid step, keep one main variable changing at a time, reuse
suitable existing components and add abstractions only when they reduce real
complexity. Each changed line must trace to the request, accepted plan or needed
verification and knowledge sync. Evaluate compatibility and architecture risks.

## Evidence And Data

When relevant to a data-processing task, preserve raw evidence, provenance and
observed values separately from transformations and inference. Reject or
quarantine malformed input; make repair rules explicit and testable. Large
source packages, generated databases, indexes and model weights require an
explicit storage decision before they can be committed.

## Live Issue Contracts

When a live Issue governs the work, a changed user request is a scope change.
Record the proposal and confirmation, update the Issue body as the current
contract, then implement. Follow `.harness/workflows/github-planning.md`.
Chat context must not silently diverge from the live contract.

## Uncertainty And Authorization

Ask about material ambiguity after inspecting source and wiki. Carry existing
explicit authorization forward. Local workflow text cannot grant network,
filesystem or credential access denied by the current environment.
