# Review And Sync Workflow

For a repository without its first GitHub remote, use the local review handoff
in `.harness/workflows/local-bootstrap.md`. The formal R0–R3 protocol below
applies to real GitHub Issue/PR handoffs after connection.

Use a separate detached worktree pinned to the reviewed `head_sha`, or inspect
with read-only commands such as `git show` and `git diff` that do not change any
checkout. Never switch branches or reset another session's checkout. A review
worktree is a sibling named `REPO-issue-N-review-ROUND`; use the frozen SHA, not a
moving branch name. From the repository checkout:

```bash
issue_number=2
review_round=R1
review_head=${HEAD_SHA:?set the fixed reviewed head SHA}
repo_root=$(git rev-parse --show-toplevel)
repo_name=$(basename "$repo_root")
worktree_path="$(dirname "$repo_root")/${repo_name}-issue-${issue_number}-review-${review_round}"
git worktree add --detach "$worktree_path" "$review_head"
cd "$worktree_path"
```

After posting the round's evidence, the session completing the review removes
its worktree using [Worktree Cleanup](github-planning.md#worktree-cleanup),
including inspection and preservation of ignored evidence/data. Cancellation
also requires cleanup. Tool and local resource isolation follows
[tooling and runtime](../tooling-runtime.md#worktrees-and-local-resources).
These checkout rules do not change formal rounds or the fixed review surface.

1. Review the diff for unintended changes.
2. Verify relevant reported checks. If GitHub reports none, record
   “未配置/未报告检查” rather than saying checks are pending.
3. Confirm project wiki reflects durable behavior changes.
4. Confirm root entry files still route to `.harness/index.md`.
5. For work tied to a GitHub issue, identify the issue from the branch, pull
   request, commit, or handoff and run:

   ```bash
   scripts/github/issue_status.sh check ISSUE
   ```

6. Confirm the label-only lifecycle matches the remote work:
   - branch work and Draft PR remediation are `status:in-progress`;
   - a pull request to `main` that is Ready for review is
     `status:needs-review`;
   - requested changes return the issue to `status:in-progress` and record an
     explicit `owner=...; branch=...; handoff=...`;
   - scope changes route from `status:in-progress` to `status:blocked` only for
     an external blocker, otherwise to `status:triage`; `status:planned` and
     `status:needs-review` return to `status:triage` and never jump to blocked;
   - merge does not by itself complete or close the Issue;
   - required post-merge verification remains open at `status:needs-review`;
   - after all current acceptance criteria pass, completed work is manually
     closed as completed and then given `status:done` as its only status label,
     preserving type and other ordinary labels;
   - canceled, rejected, and duplicate work is closed as not planned with no
     `status:*` label;
   - blocked work records `reason=...; unblock=...` before the label changes;
   - `repair` and `--force` record non-empty `reason`, `evidence`, and `target`,
     plus every target-specific audit field; and
   - each write re-read proves the exact requested target, not merely some
     structurally legal state.
7. Confirm the PR uses `Refs #N`, not an auto-closing keyword, unless an
   explicitly approved repository workflow says otherwise.
8. Run `scripts/quality/check.sh --pr PR --repo OWNER/REPO` outside the sandbox
   when it reads live GitHub data. Confirm the PR body uses the required Chinese
   sections, maps current acceptance criteria, records verification, and does
   not duplicate Draft, Ready, or `status:*` execution state. The structural
   check does not replace human review of Chinese natural-language quality.
9. Confirm no original acceptance criterion was silently deleted or deferred.
   Any reduction or deferral requires a confirmed scope change and synchronized
   Issue body before completion; follow-ups contain only confirmed separate
   scope.
10. For discoveries after completion, confirm exactly one of three outcomes:

    - an unmet current acceptance criterion reopens the original Issue;
    - ambiguity in the current contract reopens the original Issue and returns
      it to `status:triage` with an audited repair;
    - genuinely new scope uses a separately verifiable follow-up Issue while
      the original remains done, and the two Issues link to each other.

11. Confirm production verification is recorded when the current acceptance
    criteria require it.
12. Confirm pull requests, commits, follow-up issues, and durable knowledge
    updates are linked.

Repository `status:*` labels are the execution-state source of truth. Do not
require or manually mirror a personal GitHub Project status. Do not assume a
remote `dev` branch exists. GitHub label edits do not provide a true
compare-and-swap, so review handoffs must re-read the live state and preserve
owner/branch/handoff comments.

## Performance And Threat-Model Review

Review against the project's explicitly accepted operating model and
`.project-wiki/decisions/engineering-principles.md`, while
preserving every current acceptance criterion and non-waivable gate. “Safer” by
itself does not establish that a proposed control is appropriate.

When the change adds or requests a full scan, full replay, long transaction,
global exclusive lock, repeated row-by-row validation or another high-cost
control, the Reviewer must verify that the evidence records:

1. the concrete current threat or failure and its impact;
2. the performance, concurrency, resource and recovery cost; and
3. lower-cost alternatives and why they do not satisfy the contract.

A control aimed mainly at threats outside the project's explicitly accepted
operating model is not adopted by default. If the current contract still requires
it, the handoff must contain explicit maintainer confirmation. The Reviewer
must also verify that `qualitative`, `modeled` and `measured` evidence is labeled
accurately and that evidence collection itself is proportionate; do not demand
an implementation-scale benchmark for ordinary or low-impact work.

## Review Convergence Protocol

This section is the repository authority for formal review rounds and
convergence. It limits ordinary review iteration; it never overrides the Issue
contract, severity, an acceptance criterion, or a non-waivable merge or release
gate. Three exhausted rounds force an explicit decision, not an automatic
merge, downgrade, waiver, or risk acceptance.

Use `.github/review_convergence_comment.md` for the Issue handoff and PR evidence
comments. Save the filled JSON evidence block locally and validate it with:

```bash
scripts/github/review_convergence.sh RECORD.json
```

### Evidence Ownership

- The live Issue must be open with exactly `status:needs-review` before a formal
  review handoff. Its audit comment owns `review_id`, `review_round`, fixed
  SHAs, owner, Reviewer, links to PR evidence, `adjudication_id`, and the
  adjudication result.
- A PR Review or top-level PR comment owns coverage, blind spots, the blocker
  ledger, verdict, and finding disposition. It links back to the Issue handoff
  with the same stable `review_id` or `adjudication_id`.
- The PR body, a personal Project, and additional `status:review-*` labels must
  not store mutable round state.
- Multiple domain Reviewers may work concurrently in one round, but the round
  has one fixed review surface, one evidence record, and one consolidated
  blocker ledger.

### Remediation Scope And Observations

After a `changes_requested` verdict, or a convergence adjudication authorizing
batch repair, the delta up to the next handoff is limited to changes needed to
satisfy open ledger exit conditions, plus necessary regressions and documentation
sync caused by those changes. An observation may be included only through the
Maintainer adoption below.

The implementation owner posts a PR remediation record listing changed files
under each `finding_id`. Every file in the repair delta must map to a ledger
`finding_id` or an adopted `observation_id`, with the adopting Issue comment
linked for each adopted observation.

Before accepting the next handoff, the Reviewer compares the file list from
the previous round's head to the new head with that mapping. An unmapped file
means the handoff is not accepted and consumes no round. The implementation
owner must remove the unrelated changes, or the Maintainer must adopt the
observation under the rule below, before the handoff can be accepted. This
file-level check does not replace review of the complete repair delta.

Review evidence records non-blocking observations in a separate section, each
with a stable `observation_id`, summary and evidence. Observations are not exit
conditions and are outside the current PR by default. A finding that meets the
existing blocker criteria must enter the ledger; it cannot be recorded as an
observation.

A Maintainer may adopt an observation into the current PR in a live Issue
comment that states its `observation_id` and exit condition. The implementation
owner may then address it during remediation. The next review records and
verifies it as a ledger entry, filling all fields under the existing blocker
criteria; an unmet adopted exit condition is a blocker. If addressing it would
change the Issue scope or acceptance criteria, adoption is not available: use
the existing [scope-change workflow](github-planning.md#scope-changes-and-blocking).

After approval and before Issue completion, the Maintainer decides for each
unadopted observation whether to open a follow-up Issue or take no action. The
completion record lists each disposition as required by
[Completion Rules](github-planning.md#completion-rules).

### Fixed Review Surface And Round Accounting

R0 records this immutable tuple before R1 begins:

- `target_tip_sha`: the target branch tip at R0;
- `merge_base_sha`: the merge-base of that target tip and `head_sha`;
- `head_sha`: the reviewed PR head; and
- `initial_head_sha`: the head at the first formal R1 handoff.

The review surface is the diff derived from the fixed tuple plus the directly
affected call chains, data chains, trust boundaries, current Issue acceptance
criteria, and non-waivable gates. A branch name alone is not a stable base.

One ordinary formal review round begins only when all of these are true:

1. The PR is Ready, the linked Issue uniquely has `status:needs-review`, and R0
   passed.
2. An Issue audit handoff records `review_round`, the fixed tuple, review
   surface, Reviewer, owner, and stable `review_id`.
3. The independent Reviewer accepts that handoff and starts work against those
   exact SHAs.

The round ends when the Reviewer posts one complete evidence record and verdict
for that surface, or records an aborted handoff. Clarification or additional
evidence against the same SHAs does not create another round. A completed repair
followed by a new independent-review handoff does.

Any target, merge-base, or head change requires recomputing the effective diff.
If the effective diff changed and the Reviewer cannot still complete the round,
the handoff is `aborted`; it consumes that round but does not complete or skip
the round's assigned responsibility. The next round inherits every unfinished
discovery or verification responsibility. Pushes, rebases, target advancement,
PR reopen, Reviewer replacement, and reopening the same content in another PR
do not reset or decrement the counter.

R1, R2, and R3 are the only ordinary formal rounds. Their number increases
monotonically within the PR. R0 is an entry gate and does not count. A fourth
ordinary handoff is invalid and must enter Convergence Adjudication.

Any of R1, R2, or R3 may end the sequence with `approve`. In every round
`approve` is valid only when the consolidated ledger has no current blocker and
that round verified every current acceptance criterion and non-waivable gate
against its `head_sha`. An approving round is the last ordinary round; no later
round is required or consumed.

### R0: Entry Gate

R0 freezes and records the current Issue contract and review surface. Its
evidence must contain:

- the fixed SHA tuple and planned `initial_head_sha`;
- a mapping from every current acceptance criterion to implementation evidence;
- planned automated and manual checks;
- known risks and blind spots; and
- Reviewer domains sufficient for the affected surface.

An incomplete R0 cannot start R1. A changed effective diff before R1 requires a
new R0 record but consumes no formal round. Its only successful output is
`admitted`; otherwise R1 does not start.

### R1: Comprehensive Discovery

R1 owns complete discovery over the whole fixed surface. The independent
Reviewer must inspect the complete diff, directly affected call/data chains,
trust boundaries, current acceptance criteria, and non-waivable gates. Finding
the first blocker does not end the planned review and findings must not be
dripped out across multiple handoffs. The round ends with declared coverage,
known blind spots, one consolidated ledger, and one verdict.

A completed R1 verdict is `approve` when no blocker remains,
`changes_requested` when contract-bound repair is required, or `escalate` when
the review cannot safely or validly continue. An aborted handoff records
`aborted` instead of a completed verdict.

The only early-stop exception is an active risk of secret disclosure,
destructive testing, a production incident, or another concrete safety hazard.
The Reviewer must stop safely, record the hazard, and return `escalate`. This is
not permission to stop after an ordinary blocker. An aborted or safety-stopped
R1 leaves comprehensive discovery unfinished; the next round must inherit and
finish it before performing its own stage duties.

### R2: Repair Verification

R2 starts only after a complete R1 ledger exists. It must:

1. verify every ledger exit condition;
2. review the complete repair delta, interactions between repairs, and
   regressions; and
3. perform a bounded sibling or class-wide sweep for every newly found pattern.

Every finding first reported in R2 or R3 records two independent dimensions:

- `finding_origin=preexisting_in_base|initial_pr_head|repair_delta`; and
- `evidence_timing=on_time|late`.

Origin is determined from the fixed `merge_base_sha`, the R1
`initial_head_sha`, and adjacent repair heads. `preexisting_in_base` means the
defect exists at the fixed merge-base; `initial_pr_head` means it first appears
between that base and the initial head; `repair_delta` means it first appears
between adjacent repair heads. `late` describes only when evidence was found
and never replaces origin. A late finding also requires `late_reason`, why the
earlier stage missed it, and evidence that the bounded sibling/class-wide sweep
was completed.

A completed R2 verdict is `approve`, `changes_requested`, or `escalate`.
`approve` is valid only when the consolidated ledger has no current blocker and
R2 has also verified every current acceptance criterion and non-waivable gate
against the reviewed head; it is the final approval and no R3 follows. If any
current blocker remains, a completed R2 must return `changes_requested` or
`escalate`; it cannot consume R3 merely by carrying the blocker forward. A
blocker-free R2 that cannot validly complete that verification returns
`escalate`. An aborted handoff records `aborted` and the unfinished
verification responsibilities.

The retired R2 verdict `pass_to_R3` (no current blocker, approval deferred to
R3) appears only in records made before a project adopted this rule and is no
longer issued.

### R3: Final Ordinary Verification

R3 verifies every remaining blocker, the latest repair delta and interactions,
all current acceptance criteria, all non-waivable gates, and regression checks.
Its only completed verdicts are `approve` and `escalate`. An aborted R3 consumes
the round without becoming a completed verdict. A failed R3 cannot open an
ordinary R4; it enters Convergence Adjudication.

### Blocker Ledger And Routing

Each finding must record at least:

- stable `finding_id`, severity, category, evidence or reproduction;
- affected current acceptance criterion or gate;
- fixed SHA provenance and affected surface;
- `introduced_by_pr`, `worsened_by_pr`, and
  `reachability_expanded_by_pr` booleans;
- whether it violates the current contract or a non-waivable gate;
- whether an explicit repository P0/P1 stop-release policy applies;
- origin, evidence timing, and required late-finding evidence;
- exit condition, regression checks, disposition, and disposition evidence.

A finding blocks the current PR when the PR introduced it, worsened it, expanded
its reachability, violated a current acceptance criterion, or violated a
non-waivable gate. A finding that already exists in the fixed base, is not
worsened or made more reachable by the PR, and is unrelated to both the current
contract and non-waivable gates is routed to a mutually linked urgent Issue; it
does not silently expand the current PR. A repository P0/P1 stop-release policy
instead uses an explicit blocking Issue and exact unblock condition.

Accepted risk requires an audit decision from a
[Maintainer/Risk Owner](#maintainer-and-risk-owner), or a person explicitly
authorized by a Maintainer in the live Issue. With no such authorization, the
finding remains a blocker. No Maintainer, Risk Owner, Reviewer, implementation
Agent, or automated tool may waive a gate explicitly defined as non-waivable.

### Maintainer And Risk Owner

Determine the Maintainer/Risk Owner for risk acceptance and convergence
adjudication using these rules:

1. Use the declaration in the project's confirmed operating-model decision.
2. Without a confirmed declaration, if the repository belongs to an individual
   account, that account's owner is both Maintainer and Risk Owner.
3. Without a confirmed declaration, an organization-owned repository has no
   default. Until the operating model declares the Maintainer/Risk Owner, no
   one may accept risk or adjudicate; unresolved blocking findings remain
   blockers.

A Maintainer may explicitly authorize another person in the live Issue; that
existing delegation remains valid. The risk-acceptance field
`risk_authorization_evidence` and adjudication field `authorization_evidence`
must identify the applicable rule above and its basis: the confirmed
operating-model declaration or individual repository ownership. When authority
is delegated, also link the explicit live Issue authorization. No separate
ownership file or hard-coded username is required.

### Convergence Adjudication

After three consumed rounds, freeze the current SHA tuple and complete ledger.
A [Maintainer/Risk Owner](#maintainer-and-risk-owner) must record one decision
with a stable `adjudication_id`:

1. `approve`: no blocker remains; normal merge gates still apply.
2. `batch_repair_final_verification`: contract-bound blockers are fully bounded;
   allow one batch repair and exactly one `final_verification`. Record exit
   conditions and use the existing
   `needs-review -> in-progress -> needs-review` handoff.
3. `triage`: the contract is ambiguous or needs a material scope change; follow
   the Issue scope-change workflow.
4. `fix_base`: a systemic base risk requires a linked blocking Issue/PR and an
   exact unblock condition before rebase.
5. `split_or_replace_or_redesign`: the current surface cannot be bounded and
   must be split, replaced, or redesigned.

A split or replacement starts a new round sequence only when the adjudicator
explicitly authorizes it, both PRs link to each other and the stable
`adjudication_id`, the new PR inherits every open ledger item and disposition
history, and the changed review surface is recorded. Closing and reopening,
changing Reviewer, or recreating the same content does not qualify.

### One-Time Final Verification

`final_verification` is an adjudication outcome, not a hidden R4. It is bound to
the `adjudication_id` and the new `head_sha`. After the batch repair it must
rerun and record:

- every current acceptance criterion;
- every non-waivable merge/release gate;
- the full ledger and all exit conditions;
- the complete repair delta and interactions; and
- all required regression checks.

Approval and failure records must both prove that complete rerun. The
`blockers_present` field is true exactly when the ledger contains at least one
finding with `blocks_current_pr=true`; neither an empty-ledger blocker claim nor
an unreported blocking finding is valid.

Any remaining blocker, repair-introduced blocker, late blocker, or other finding
that still meets blocker criteria fails final verification. The PR must not
merge and no further repair round is allowed under that adjudication. The
recorded failure path must be `triage`, `fix_base`, `split`, `replace`, or
`redesign`.
