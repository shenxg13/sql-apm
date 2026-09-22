# Review Convergence Comment Templates

Copy the relevant section into the live Issue and PR. Replace every `<...>`
placeholder, keep the stable IDs identical across both locations, and do not put
round state in the PR body. The JSON evidence can be saved locally and checked
with `scripts/github/review_convergence.sh RECORD.json` before posting.

## Issue Formal Review Handoff

```markdown
## 正式评审交接：<review_id>

- review_round: <R0|R1|R2|R3>
- formal_rounds_consumed: <0|1|2|3>
- owner: <implementation owner>
- reviewers: <reviewer names and domains>
- target_tip_sha: <40-character SHA>
- merge_base_sha: <40-character SHA>
- head_sha: <40-character SHA>
- initial_head_sha: <R1 initial head SHA>
- review_surface: <fixed diff and directly affected surfaces>
- issue_contract: <live Issue URL and acceptance version>
- pr_evidence_url: <PR Review or top-level comment URL>
- prior_incomplete_stage: <none|R1|R2>
- inherited_responsibilities: <none or explicit responsibilities>
- adjudication_id: <none or stable adjudication ID>
```

Before R1, the R0 handoff must also record acceptance mapping, planned checks,
known risks, blind spots, and Reviewer domains. If any fixed SHA changes, record
whether the effective diff was recomputed and whether the handoff was aborted.

## PR Review And Blocker Ledger

```markdown
## 评审证据：<review_id>

### Coverage

- reviewed: <complete diff, call/data chains, trust boundaries, acceptance,
  non-waivable gates>
- blind_spots: <none or explicit bounded gaps>
- effective_diff_recomputed: <true|false>
- aborted: <true|false and unfinished responsibilities>
- safe_stop: <false or concrete active safety hazard>

### Blocker Ledger

| finding_id | severity | category | evidence | disposition |
| --- | --- | --- | --- | --- |
| <stable ID> | <P0-P3> | <category> | <reproduction/link> | <result> |

For every row, attach the JSON fields below. Do not omit false booleans.

### Verdict

- verdict: <admitted|approve|pass_to_R3|changes_requested|escalate|aborted>
- issue_handoff_url: <Issue audit comment URL>
- remaining_responsibilities: <none or explicit list>
```

The machine-checkable evidence block for a formal round uses this shape. R0,
R2, and R3 add the stage fields listed after the example.

```json
{
  "schema_version": 1,
  "record_type": "review_round",
  "review_id": "review-ISSUE-PR-R1",
  "issue_number": 18,
  "pr_number": 99,
  "issue_status": "status:needs-review",
  "issue_comment_url": "https://github.com/OWNER/REPO/issues/18#issuecomment-ID",
  "pr_evidence_url": "https://github.com/OWNER/REPO/pull/99#issuecomment-ID",
  "owner": "OWNER",
  "reviewers": ["REVIEWER:DOMAIN"],
  "review_round": "R1",
  "formal_rounds_consumed": 1,
  "target_tip_sha": "0000000000000000000000000000000000000000",
  "merge_base_sha": "1111111111111111111111111111111111111111",
  "head_sha": "2222222222222222222222222222222222222222",
  "initial_head_sha": "2222222222222222222222222222222222222222",
  "review_surface": ["complete diff", "affected trust boundary"],
  "coverage": ["complete diff", "current acceptance", "non-waivable gates"],
  "blind_spots": [],
  "sha_changed_during_round": false,
  "effective_diff_recomputed": false,
  "effective_diff_changed": false,
  "round_completion_possible": true,
  "aborted": false,
  "unfinished_responsibilities": [],
  "incomplete_stage_inherited_from": null,
  "inherited_responsibilities": [],
  "inherited_stage_completed": false,
  "safe_stop": false,
  "safe_stop_reason": null,
  "r1_scope_complete": true,
  "verdict": "changes_requested",
  "findings": [
    {
      "finding_id": "F-001",
      "severity": "P1",
      "category": "security",
      "evidence": "reproduction or evidence URL",
      "affected_requirement_or_gate": "Issue acceptance item or gate ID",
      "sha_provenance": "review_id fixed SHA tuple and adjacent repair head",
      "affected_surface": "path, call chain, data chain, or trust boundary",
      "introduced_by_pr": true,
      "worsened_by_pr": false,
      "reachability_expanded_by_pr": false,
      "violates_current_contract": true,
      "violates_non_waivable_gate": false,
      "stop_release_policy_applies": false,
      "finding_origin": "initial_pr_head",
      "evidence_timing": "on_time",
      "late_reason": null,
      "prior_miss_reason": null,
      "sibling_sweep_evidence": null,
      "exit_condition": "bounded testable repair condition",
      "regression_checks": ["command or manual check"],
      "blocks_current_pr": true,
      "disposition": "open",
      "disposition_evidence": "current ledger state",
      "linked_issue_url": null,
      "risk_authorized_by": null,
      "risk_authorization_evidence": null,
      "unblock_condition": null
    }
  ]
}
```

Stage-specific evidence:

- R0: `issue_contract_frozen`, `review_surface_frozen`,
  `acceptance_mapping`, `planned_checks`, `known_risks`, `reviewer_domains`, and
  `verdict=admitted`.
- R1: `safe_stop`, `safe_stop_reason`, and `r1_scope_complete`.
- R2: `complete_r1_ledger`, `ledger_exit_conditions_verified`,
  `repair_delta_reviewed`, `repair_interactions_reviewed`, and
  `regressions_reviewed`; a completed verdict is `pass_to_R3`,
  `changes_requested`, or `escalate`. `pass_to_R3` requires no current blocker;
  otherwise the verdict is `changes_requested` or `escalate`.
- R3: `remaining_blockers_verified`, `latest_delta_reviewed`,
  `acceptance_criteria_verified`, `non_waivable_gates_verified`, and
  `regressions_verified`; a completed verdict is only `approve` or `escalate`.
- A late R2/R3 finding must fill `finding_origin`, `evidence_timing=late`,
  `late_reason`, `prior_miss_reason`, and `sibling_sweep_evidence` independently.

## Convergence Adjudication

```markdown
## 收敛裁决：<adjudication_id>

- adjudicator: <authorized Maintainer/Risk Owner>
- authorization_evidence: <ownership config or live Issue authorization>
- rounds_consumed: 3
- frozen_target_tip_sha: <SHA>
- frozen_merge_base_sha: <SHA>
- frozen_head_sha: <SHA>
- frozen_ledger: <all finding IDs and dispositions>
- decision: <approve|batch_repair_final_verification|triage|fix_base|
  split_or_replace_or_redesign>
- decision_evidence: <why this result is bounded and allowed>
- status_transition: <if applicable>
- replacement_inheritance: <links, open ledger, history, changed surface>
```

For a new PR round sequence, record the stable `adjudication_id`, explicit reset
authorization, reciprocal old/new PR links, inherited open ledger and
disposition history, and the changed review surface.

Save a batch-repair adjudication with this machine-checkable shape. For another
decision, replace the decision-specific fields as described by the protocol.

```json
{
  "schema_version": 1,
  "record_type": "adjudication",
  "adjudication_id": "ADJ-ISSUE-PR-1",
  "issue_number": 18,
  "pr_number": 99,
  "adjudicator": "AUTHORIZED_OWNER",
  "authorization_evidence": "ownership config or live Issue authorization",
  "formal_rounds_consumed": 3,
  "target_tip_sha": "0000000000000000000000000000000000000000",
  "merge_base_sha": "1111111111111111111111111111111111111111",
  "head_sha": "3333333333333333333333333333333333333333",
  "frozen_ledger_ids": ["F-001"],
  "decision": "batch_repair_final_verification",
  "blockers_remaining": true,
  "batch_exit_conditions": ["all bounded blocker exits pass"],
  "final_verification_required": true,
  "status_transition": [
    "status:needs-review",
    "status:in-progress",
    "status:needs-review"
  ],
  "starts_new_round_sequence": false
}
```

## One-Time Final Verification

```markdown
## 单次终验：<adjudication_id>

- head_sha: <new batch-repair head SHA>
- status_transition: needs-review -> in-progress -> needs-review
- current_acceptance_rerun: <evidence>
- non_waivable_gates_rerun: <evidence>
- full_ledger_verified: <evidence>
- repair_delta_and_interactions_verified: <evidence>
- regressions_rerun: <evidence>
- blockers_present: <true|false>
- verdict: <approve|fail>
- failure_path: <none|triage|fix_base|split|replace|redesign>
- another_repair_allowed: false
```

The corresponding JSON record uses this shape and includes full finding objects
from the blocker-ledger template when any finding remains.

```json
{
  "schema_version": 1,
  "record_type": "final_verification",
  "adjudication_id": "ADJ-ISSUE-PR-1",
  "issue_number": 18,
  "pr_number": 99,
  "head_sha": "4444444444444444444444444444444444444444",
  "status_transition": [
    "status:needs-review",
    "status:in-progress",
    "status:needs-review"
  ],
  "acceptance_criteria_rerun": true,
  "non_waivable_gates_rerun": true,
  "full_ledger_verified": true,
  "repair_delta_verified": true,
  "repair_interactions_verified": true,
  "regressions_rerun": true,
  "another_repair_allowed": false,
  "blockers_present": false,
  "verdict": "approve",
  "failure_path": null,
  "findings": []
}
```
