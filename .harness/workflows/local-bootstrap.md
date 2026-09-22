# Local Bootstrap Workflow

This workflow applies before a new repository has its first GitHub remote.
A repository already governed by GitHub Issues stays under that lifecycle even
if a remote is temporarily unavailable or removed.

1. Confirm purpose, target directory, extraction scope, history policy and deliverables.
2. Record the confirmed plan, test cases and acceptance criteria locally. Such a
   bootstrap plan is not a mirror of a GitHub Issue and must not invent Issue IDs.
3. Inspect the source and fixed baseline; initialize an independent `main` history.
4. Implement only the authorized scope. Use project wiki entities for durable facts.
5. Stage the intended files so the quality entrypoint can enumerate tracked content.
6. Run the planned offline checks; record failures, repairs and evidence limits.
7. Form the initial local commit and verify a clean independent local clone.
8. For independent local review, hand off the exact commit, full tree/diff,
   acceptance mapping and verification evidence to a separate reviewer/session.
   Label the result as local review. Implementation checks alone are not independent
   review, and synthetic Issue/PR URLs are not formal R0–R3 evidence.
9. Connect and publish only when the user has authorized the destination,
   visibility and publication scope. Read back remote settings and commit identity.

Finish explicitly authorized initial publication verification and any contract-bound
repairs as part of this bootstrap handoff. After connection, use
[GitHub planning](github-planning.md) for new requirements,
implementation and formal review. Do not retroactively fabricate an Issue or PR
for the initial bootstrap. A local bootstrap report can remain as historical
source evidence; execution state for subsequent Issues lives only on GitHub.

If the user explicitly authorizes initial publication after local implementation
checks, record that boundary accurately; do not claim an independent review took
place. Subsequent ordinary delivery continues to require the agreed review flow.
