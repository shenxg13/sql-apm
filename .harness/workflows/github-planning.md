# GitHub Issue Delivery Workflow

Use this workflow for requirements, issues, roadmap items, RFCs, implementation
handoffs, and pull requests after this repository has a GitHub remote.
Before the first remote exists, use `.harness/workflows/local-bootstrap.md`.
An unavailable existing remote is an access failure, not local-bootstrap mode.
Use `.harness/tooling-runtime.md` for cross-task availability, runtime and safe
diagnostic rules; this page remains authoritative for GitHub phases, network
access, contracts and remote state transitions.

GitHub issues and pull requests own execution state. `.project-wiki/` owns
durable project facts. `.harness/` owns how agents move between them. An issue
is not a substitute for promoting accepted architecture, contracts, decisions,
or repeated failure modes into the wiki.

## Sources Of Truth

- The issue body is the current accepted requirement and verification contract.
- Issue comments preserve scope-change proposals, decisions, blockers,
  ownership, branch handoffs, and progress evidence.
- Repository `status:*` labels are the only execution-state source of truth.
- Pull requests preserve implementation, review, and test evidence.
- `.project-wiki/` preserves durable architecture, contract, decision, feature,
  and method knowledge.

GitHub is the only authoritative store for an individual issue. Do not create
or commit per-issue Markdown mirrors in the repository. A temporary local
export may be used for bounded processing, but it is disposable and must never
be treated as current. At the start of issue work and before handoff, fetch the
live body and comments outside the sandbox:

```bash
gh issue view ISSUE -R OWNER/REPO --comments
```

If GitHub cannot be reached, diagnose access using the network gate below; do
not continue from a stale local issue copy. Promote accepted, durable facts to
`.project-wiki/` instead of copying the issue verbatim.

Issue titles, Issue bodies, workflow comments, and pull request bodies should
be written in Chinese by default. Preserve code identifiers, paths, command
lines, protocol terms, and source quotations in their original language when
that is clearer. Use another language only when the user or an external
collaboration boundary requires it.
The two machine-readable headings `## 验收标准` and `## 需求确认`, plus the
fixed confirmation item `- [x] 用户已确认目标、范围、非目标和验收标准。`,
must remain in Chinese. Other body content may use another language.

Do not require or manually mirror a personal GitHub Project. A future Project
owned by the repository owner may provide views, but it must not become a second
manually maintained status source.

## GitHub CLI Network Gate

Commands that contact `github.com`, including `gh auth status`, `gh api`,
`gh issue`, `gh pr`, `gh project`, and `gh repo`, require authorized network
access. When the active sandbox blocks that access, use a narrowly scoped
approved outside-sandbox invocation. A blocked network or proxy can make
`gh auth status` report a valid credential as invalid.

Treat any sandbox-side authentication result as inconclusive when it includes
`proxyconnect`, `operation not permitted`, or another network error. Before
reporting that a token is invalid, retry both checks outside the sandbox:

```bash
gh auth status -h github.com
gh api user --jq '.login'
```

Only diagnose an invalid credential when the outside-sandbox checks reach
GitHub and fail with an authentication response. Do not print or expose the
stored token while diagnosing access.

## Session Boundaries

### Phase Action Matrix

| Action | Requirements | Implementation | Independent review |
| --- | --- | --- | --- |
| Clarify requirements and edit the live Issue contract | Allowed | Only through the scope-change workflow | Only through the scope-change workflow |
| Inspect code, wiki, live GitHub state, and bounded evidence read-only | Allowed | Allowed | Allowed |
| Run a bounded read-only diagnostic to confirm current behavior | Allowed when needed for requirement evidence | Allowed | Allowed |
| Modify repository files or create an implementation branch, commit, or PR | Prohibited | Allowed within accepted scope | Prohibited except explicitly handed-off remediation |
| Install Issue-specific tools through the implementation Agent | Prohibited | Allowed only when the accepted contract authorizes it | Prohibited unless review setup explicitly requires it |
| Run ShellCheck, formatting checks, complete tests, or acceptance commands to satisfy the Issue | Prohibited; record only the planned command and expected result | Required as planned | Required independently as planned |
| Claim or transition implementation status | Prohibited | Allowed through the audited status command | Allowed only for the review or remediation handoff |

A requirements diagnostic is bounded when it answers a concrete question
about current behavior, is read-only, and stops once evidence is collected. It
becomes premature implementation when it changes repository or remote state,
creates delivery artifacts, installs the Issue toolchain through the
implementation Agent, or runs the full verification plan to satisfy acceptance
criteria. User-prepared tools outside the requirements session do not start
implementation and do not count as acceptance evidence.

### Requirements Session

1. Read the harness, relevant wiki pages, current code, and available evidence.
2. Clarify objective, scope, non-goals, risks, dependencies, and acceptance
   criteria.
3. Create a Chinese issue from `.github/ISSUE_TEMPLATE/requirement.md`; the
   template starts at `status:triage`.
4. Resolve every open question that could materially change implementation and
   keep the issue body synchronized as the current contract.
5. Obtain user confirmation and check the confirmation item in the issue body.
6. After the executable-contract gate below passes, transition the issue to
   `status:planned`.
7. Return the issue URL for a separate implementation session. Do not implement
   it in the requirements session.

The requirements body records verification commands as a future plan and must
not claim that implementation-stage quality gates already passed.

`blank_issues_enabled: false` limits the normal Blank entry shown to ordinary
contributors in GitHub's template chooser. It cannot absolutely prevent issue
creation through every API, CLI, permission, or alternate UI path. The
enforced acceptance meaning is therefore: a blank, placeholder, incomplete, or
unconfirmed issue cannot enter the claimable `status:planned` state.

### Implementation Session

1. Receive an explicit issue number or URL and fetch its live body and comments
   from GitHub; do not rely on a local issue copy.
2. Run `scripts/github/issue_status.sh check ISSUE` outside the sandbox. The
   command rejects pull request numbers and `/pull/N` URLs.
3. Start unclaimed work only when the issue has exactly `status:planned` and is
   open. Resume `status:in-progress` only with an explicit handoff, after
   confirming no other session owns the branch or active work.
4. Claim planned work with an audit comment containing owner, branch, and
   handoff context:

   ```bash
   scripts/github/issue_status.sh set ISSUE in-progress \
     --comment "owner=SESSION; branch=BRANCH; handoff=CONTEXT"
   ```

5. Immediately re-read the live label and comments after claiming. GitHub label
   edits do not provide a true compare-and-swap, so the command cannot by itself
   prevent two sessions that read the same old state from racing.
6. Use an `issue-N-short-description` branch from the current `main` baseline.
7. Implement only the accepted scope and run the planned verification.
8. A Draft PR remains implementation work and keeps `status:in-progress`.
   When the PR is marked Ready for review and is ready for a separate reviewer,
   transition the issue to `status:needs-review`.

Normal `in-progress -> in-progress` is rejected so it cannot be mistaken for a
new claim. An explicit resume or owner transfer uses a handoff comment and the
audited escape hatch:

```bash
scripts/github/issue_status.sh set ISSUE in-progress --force \
  --comment "reason=owner transfer; evidence=no concurrent owner; target=in-progress; owner=NEW_SESSION; branch=BRANCH; handoff=EXPLICIT_CONTEXT"
```

### Review And Completion Session

1. Follow the `Review Convergence Protocol` in
   `.harness/workflows/review-sync.md` for R0 admission, at most three ordinary
   formal review rounds, blocker evidence, and any convergence adjudication.
   Review the diff, reported checks, acceptance criteria, and durable knowledge
   updates within that protocol.
2. Keep a Draft or change-request remediation at `status:in-progress`; mark it
   Ready for review and restore `status:needs-review` only at the next formal
   independent-review handoff.
3. Merge only after required pre-merge verification passes.
4. Because PRs use `Refs #N`, merge does not close the issue. If the contract
   requires merge-time or production verification, keep the issue open with
   `status:needs-review` until those checks pass.
5. After every current acceptance criterion and required post-merge check has
   passed, manually close the issue with reason completed, then immediately set
   `status:done` and check it:

   ```bash
   gh issue close ISSUE -R OWNER/REPO --reason completed
   scripts/github/issue_status.sh set ISSUE done -R OWNER/REPO
   scripts/github/issue_status.sh check ISSUE -R OWNER/REPO
   ```

## Executable Issue Contract

An executable issue must include:

- objective and motivation;
- investigation context and evidence;
- in-scope work;
- explicit non-goals;
- testable acceptance criteria;
- verification plan;
- risk and compatibility notes;
- dependencies and blockers;
- related source paths, wiki pages, issues, and pull requests;
- open questions; and
- explicit user confirmation.

An issue is ready for `status:planned` only when the body contract is complete,
its acceptance criteria are testable, dependencies are known, material open
questions are resolved, and the fixed user-confirmation item is checked. The
same gate runs when setting planned, checking an issue already labeled planned,
and claiming `planned -> in-progress`. It rejects an empty body, absent or
empty checklist items, whitespace or HTML-comment-only items, `TODO`, `TBD`,
`TBC`, `待补充`, template placeholder text, an unchecked or loosely worded
confirmation, and negative statements such as `用户未确认` or `用户尚未确认`.
Large changes still require the plan, test cases, and user confirmation defined
by `.harness/workflows/large-change.md`.

### Conditional Performance And Threat-Model Contract

Apply this additional requirement only when a non-trivial Issue involves scale,
runtime, resource, concurrency, recovery or security tradeoffs that could
materially change its design or acceptance. In that case, the Issue must state
the relevant in-scope and out-of-scope threats, expected performance/resource
impact, and the proportionate evidence level: `qualitative`, `modeled` or
`measured`. If it proposes a high-cost control, it must also record the concrete
risk, performance/recovery cost and lower-cost alternatives.

The information may live in the Issue's existing scope, risk and verification
sections; do not add a fixed form or heading. Ordinary and low-impact Issues do
not require a benchmark or numeric budget, and qualitative or modeled evidence
must not be described as measured. Evidence collection is itself costed: use a
bounded probe or stronger evidence only when uncertainty could materially
change the decision, rather than defaulting to an experiment whose cost
approaches implementation.

## Status Model

Every open implementation issue must have exactly one of these labels:

| Label | Meaning |
| --- | --- |
| `status:triage` | Requirement is incomplete or has returned to discussion |
| `status:planned` | Contract is confirmed and ready for an implementation owner |
| `status:in-progress` | One recorded implementation session owns active work, including Draft PR remediation |
| `status:blocked` | Work cannot continue until a documented condition changes |
| `status:needs-review` | Implementation is Ready for review or awaiting required post-merge verification |

A successfully completed issue is closed with reason `completed` and has only
`status:done`. A duplicate, rejected, or canceled issue is closed as
`not planned`, has no `status:*` label, and should use a reason label such as
`duplicate`, `wontfix`, or `invalid` when applicable.

Normal transitions are exactly:

| Source | Allowed target |
| --- | --- |
| `status:triage` | `status:planned`, `not-planned` |
| `status:planned` | `status:in-progress`, `status:triage`, `not-planned` |
| `status:in-progress` | `status:blocked`, `status:needs-review`, `status:triage`, `not-planned` |
| `status:blocked` | `status:in-progress`, `status:triage`, `not-planned` |
| `status:needs-review` | `status:in-progress`, `status:triage`, `not-planned` |
| closed as `completed` with `status:needs-review` | `status:done` |

Every other transition, including a same-state transition or
`triage -> needs-review`, is rejected before a remote write. Use the repository
command for checks and transitions:

```bash
scripts/github/issue_status.sh check ISSUE
scripts/github/issue_status.sh set ISSUE planned
scripts/github/issue_status.sh set ISSUE in-progress \
  --comment "owner=SESSION; branch=BRANCH; handoff=CONTEXT"
scripts/github/issue_status.sh set ISSUE blocked \
  --comment "reason=BLOCKER; unblock=EXACT_CONDITION"
scripts/github/issue_status.sh set ISSUE needs-review
scripts/github/issue_status.sh set ISSUE done
scripts/github/issue_status.sh set ISSUE not-planned \
  --comment "reason=CANCELLATION_REASON"
```

Run network-dependent invocations outside the sandbox. Active transitions
require an open issue. `triage` and `not-planned` require `reason=...`;
`in-progress` requires `owner=...; branch=...; handoff=...`; `blocked` requires
`reason=...; unblock=...`. Field values must be non-empty after trimming. The
command preserves non-status labels, re-reads the remote object, and asserts
the exact requested target. A different legal final state is still a failure
and is reported as a possible ineffective write or concurrent overwrite.

Normal transitions refuse anomalous data. Repair it only after resolving the
intended state and recording why. Both explicit forms require an audit comment,
still reject PR targets, still enforce open/closed target semantics, and never
bypass the planned-contract gate:

```bash
scripts/github/issue_status.sh repair ISSUE triage \
  --comment "reason=ANOMALY; evidence=OBSERVATION; target=triage"
scripts/github/issue_status.sh set ISSUE triage --force \
  --comment "reason=ANOMALY; evidence=OBSERVATION; target=triage"
```

`repair` and `--force` always require exactly `reason`, `evidence`, and `target`
and must also satisfy the target-specific fields. For example, repairing to
`in-progress` additionally requires `owner`, `branch`, and `handoff`.

For `blocked`, the command records the complete reason/unblock comment before
changing the label, so a comment failure leaves the old state. For
`not-planned`, it first records `reason`, then sends one Update Issue `PATCH`
containing `state=closed`, `state_reason=not_planned`, and the final ordinary
label set, as supported by the
[GitHub Update Issue REST endpoint](https://docs.github.com/en/rest/issues/issues#update-an-issue).
If PATCH reports an error, the command re-reads: the exact target is
reported as applied, the unchanged original legal state fails, and every other
combination fails with an explicit repair requirement. An audited
`repair ISSUE not-planned` removes residual status labels from an already
`CLOSED/NOT_PLANNED` Issue.

## Scope Changes And Blocking

An implementation session must not silently expand, reduce, delete, or defer
accepted scope or acceptance criteria. A new user instruction that changes the
live issue contract follows the same process. When a material change is found:

1. Add an issue comment headed `拟议范围变更（Proposed scope change）` with
   evidence and impact.
2. Route by source state:
   - from `status:in-progress`, use `status:blocked` only for an external
     condition with an exact unblock condition, or use `status:triage` for a
     contract change;
   - from `status:planned` or `status:needs-review`, return to
     `status:triage`; never jump directly to `status:blocked`.
3. Return to requirements discussion.
4. Update the issue body only after user confirmation; preserve the decision in
   comments.
5. Resume from `status:planned` or `status:in-progress` with the required audit
   comment and handoff.

A blocked transition must use `reason=...; unblock=...`. A triage transition
must use `reason=...`.
Explicit user instruction outranks harness defaults, but it does not silently
rewrite a live Issue contract; `.harness/rules.md` requires this synchronization
before implementation.

## Branch And Pull Request Contract

The current repository flow is:

```text
issue -> issue-N-short-description branch -> pull request -> main
```

Do not assume a `dev` branch exists. A pull request must target `main`, include
`Refs #N` rather than an auto-closing keyword, map its work to the current
acceptance criteria, record tests and manual checks, identify wiki/harness
updates, and link confirmed follow-up issues. If GitHub reports no checks, say
“未配置/未报告检查”; do not describe nonexistent checks as pending.

PR bodies use the repository's Chinese headings and Chinese prose by default.
They must not store Draft, Ready, `status:*`, or similar mutable execution
state; GitHub's native Draft/Ready state, Issue labels, and audited handoff
comments own that information. Before an independent-review handoff, run the
read-only live contract check through
`scripts/quality/check.sh --pr PR --repo OWNER/REPO`. The automated check
enforces machine-readable structure only; authors and reviewers remain
responsible for confirming that the natural-language body is clear Chinese.

The PR must separately list all pre-merge acceptance and every required
post-merge acceptance. Post-merge items remain tracked by the open
`status:needs-review` Issue.

## Decomposition

One implementation issue should represent one independently verifiable
deliverable. Use parent/sub-issue and blocked-by/blocking relationships for
larger work. A parent issue completes only after all required sub-issues and its
own current acceptance criteria are complete.

## Completion Rules

Before marking an issue done:

1. Confirm the implementation is merged into `main`.
2. Confirm every current acceptance criterion is satisfied. An original
   criterion cannot simply be deferred to a follow-up while the original issue
   is completed.
3. If a criterion truly must be deleted or deferred, first follow the scope
   change process, obtain user confirmation, and update the issue body. A
   follow-up may then carry only the newly confirmed separate scope.
4. Record automated tests, manual checks, and required production verification.
5. Link relevant pull requests, commits, sub-issues, and confirmed follow-ups.
6. Update `.project-wiki/` if durable facts changed.
7. Update `.harness/` if workflow rules changed.
8. Close the issue as completed, set only `status:done`, and run the status
   check.
9. Use `.harness/workflows/review-sync.md` for non-trivial handoff.

## Discoveries After Completion

`status:done` means the issue's current accepted contract was satisfied. It does
not mean the same area can never change again. Use exactly these three outcomes
for every post-completion discovery:

| Discovery | Required handling |
| --- | --- |
| Bug, regression, omission, or failed verification proving a current acceptance criterion was not satisfied | Reopen the original issue |
| Ambiguity in the current contract that must return to requirements discussion | Reopen the original issue as `status:triage` |
| New capability, changed expectation, optimization, or other independently verifiable scope beyond the completed contract | Create a linked follow-up issue; keep the original issue done |

When reopening the original issue:

1. Add a reopening comment with the evidence, affected acceptance criterion,
   and required outcome.
2. Reopen it as a GitHub issue:

   ```bash
   gh issue reopen ISSUE -R OWNER/REPO \
     --comment "重新打开原因、证据和受影响的验收标准"
   ```

3. Reopening temporarily leaves an invalid active-state combination. Use an
   audited repair immediately to remove stale `status:done` and choose exactly
   one active status:
   - `status:triage` when requirements must be clarified;
   - `status:planned` when the correction contract is confirmed but unclaimed;
   - `status:in-progress` when a session has an explicit owner, branch, and
     handoff.
4. Update the issue body as the current contract after confirmation and keep
   historical completion/reopening evidence in comments.
5. Implement on an issue-linked branch and pull request. After merge and all
   current checks, close as completed again and restore only `status:done`.

Example repair after reopening:

```bash
scripts/github/issue_status.sh repair ISSUE triage \
  -R OWNER/REPO \
  --comment "reason=原验收失败并已重开; evidence=受影响验收与证据链接; target=triage"
```

For genuinely new scope, create a new issue from the requirement template,
state `Follow-up to #ORIGINAL` and the boundary from the completed work, and add
a reciprocal comment on the original issue. The original issue remains closed
with `status:done`; the follow-up follows the normal lifecycle independently.
