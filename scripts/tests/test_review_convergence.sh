#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd -- "${BASH_SOURCE[0]%/*}/../.." && pwd)
test_root=$(mktemp -d "${TMPDIR:-/tmp}/agent-harness-review-convergence.XXXXXX")
command_under_test="$repo_root/scripts/github/review_convergence.sh"
template="$repo_root/.github/review_convergence_comment.md"

cleanup() {
    rm -rf -- "$test_root"
}
trap cleanup EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

expect_pass() {
    local name=$1
    local record=$2
    "$command_under_test" "$record" >"$test_root/$name.stdout" ||
        fail "$name unexpectedly failed"
}

expect_fail() {
    local name=$1
    local record=$2
    if "$command_under_test" "$record" >"$test_root/$name.stdout" \
        2>"$test_root/$name.stderr"; then
        fail "$name unexpectedly passed"
    fi
    grep -Fq 'review convergence record is invalid' \
        "$test_root/$name.stderr" || fail "$name lacked a clear failure"
}

mutate() {
    local source=$1
    local target=$2
    local filter=$3
    jq "$filter" "$source" >"$target"
}

cat >"$test_root/r1.json" <<'EOF'
{
  "schema_version": 1,
  "record_type": "review_round",
  "review_id": "review-18-99-R1",
  "issue_number": 18,
  "pr_number": 99,
  "issue_status": "status:needs-review",
  "issue_comment_url": "https://github.com/example/project/issues/18#issuecomment-1",
  "pr_evidence_url": "https://github.com/example/project/pull/99#issuecomment-2",
  "owner": "implementation-owner",
  "reviewers": ["reviewer:workflow"],
  "review_round": "R1",
  "formal_rounds_consumed": 1,
  "target_tip_sha": "0000000000000000000000000000000000000000",
  "merge_base_sha": "1111111111111111111111111111111111111111",
  "head_sha": "2222222222222222222222222222222222222222",
  "initial_head_sha": "2222222222222222222222222222222222222222",
  "review_surface": ["complete diff", "affected trust boundary"],
  "coverage": ["complete diff", "call and data chains", "current gates"],
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
  "findings": []
}
EOF

cat >"$test_root/finding.json" <<'EOF'
{
  "finding_id": "F-001",
  "severity": "P1",
  "category": "security",
  "evidence": "bounded reproduction",
  "affected_requirement_or_gate": "Issue acceptance item 1",
  "sha_provenance": "review fixed tuple and adjacent repair head",
  "affected_surface": "scripts/github/review_convergence.sh",
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
  "exit_condition": "repair and pass the regression",
  "regression_checks": ["scripts/quality/check.sh"],
  "blocks_current_pr": true,
  "disposition": "open",
  "disposition_evidence": "ledger item remains open",
  "linked_issue_url": null,
  "risk_authorized_by": null,
  "risk_authorization_evidence": null,
  "unblock_condition": null
}
EOF

expect_pass r1_complete "$test_root/r1.json"

mutate "$test_root/r1.json" "$test_root/r0.json" \
    '.review_id = "review-18-99-R0" | .review_round = "R0" |
     .formal_rounds_consumed = 0 | .verdict = "admitted" |
     .coverage = [] | .issue_contract_frozen = true |
     .review_surface_frozen = true |
     .acceptance_mapping = ["acceptance item -> evidence"] |
     .planned_checks = ["scripts/quality/check.sh"] |
     .known_risks = [] | .reviewer_domains = ["workflow"]'
expect_pass r0_complete_admission "$test_root/r0.json"

mutate "$test_root/r0.json" "$test_root/r0_no_acceptance.json" \
    '.acceptance_mapping = []'
expect_fail r0_requires_acceptance_mapping "$test_root/r0_no_acceptance.json"

mutate "$test_root/r0.json" "$test_root/r0_unfixed_head.json" \
    '.head_sha = "2222"'
expect_fail r0_requires_fixed_sha_tuple "$test_root/r0_unfixed_head.json"

mutate "$test_root/r1.json" "$test_root/r1_early_stop.json" \
    '.r1_scope_complete = false | .coverage = ["first blocker only"]'
expect_fail r1_ordinary_first_blocker_stop "$test_root/r1_early_stop.json"

mutate "$test_root/r1.json" "$test_root/r1_safe_stop.json" \
    '.r1_scope_complete = false | .safe_stop = true |
     .safe_stop_reason = "secret_disclosure" | .verdict = "escalate" |
     .unfinished_responsibilities = ["comprehensive_discovery"]'
expect_pass r1_active_safety_stop "$test_root/r1_safe_stop.json"

mutate "$test_root/r1.json" "$test_root/r1_aborted.json" \
    '.r1_scope_complete = false | .sha_changed_during_round = true |
     .effective_diff_recomputed = true | .effective_diff_changed = true |
     .round_completion_possible = false | .aborted = true |
     .verdict = "aborted" |
     .unfinished_responsibilities = ["comprehensive_discovery"]'
expect_pass r1_changed_diff_aborted "$test_root/r1_aborted.json"

mutate "$test_root/r1.json" "$test_root/r2.json" \
    '.review_id = "review-18-99-R2" | .review_round = "R2" |
     .formal_rounds_consumed = 2 | .verdict = "pass_to_R3" |
     .complete_r1_ledger = true | .ledger_exit_conditions_verified = true |
     .repair_delta_reviewed = true | .repair_interactions_reviewed = true |
     .regressions_reviewed = true'
expect_pass r2_complete "$test_root/r2.json"

mutate "$test_root/r2.json" "$test_root/r2_inherits_r1.json" \
    '.incomplete_stage_inherited_from = "R1" |
     .inherited_responsibilities = ["comprehensive_discovery"] |
     .inherited_stage_completed = true'
expect_pass r2_inherits_aborted_r1 "$test_root/r2_inherits_r1.json"

mutate "$test_root/r2_inherits_r1.json" \
    "$test_root/r2_skips_inherited_r1.json" \
    '.inherited_responsibilities = [] | .inherited_stage_completed = false'
expect_fail r2_cannot_skip_aborted_r1 "$test_root/r2_skips_inherited_r1.json"

mutate "$test_root/r2.json" "$test_root/r2_no_r1_ledger.json" \
    '.complete_r1_ledger = false'
expect_fail r2_requires_complete_r1_ledger "$test_root/r2_no_r1_ledger.json"

jq --slurpfile finding "$test_root/finding.json" \
    '.findings = [$finding[0] |
        .finding_origin = "repair_delta" |
        .evidence_timing = "late" |
        .late_reason = "interaction appeared only under combined repair" |
        .prior_miss_reason = "R1 did not contain the repair path" |
        .sibling_sweep_evidence = "all sibling repair paths checked"] |
     .verdict = "changes_requested"' \
    "$test_root/r2.json" >"$test_root/r2_late_repair_finding.json"
expect_pass r2_late_repair_finding "$test_root/r2_late_repair_finding.json"

mutate "$test_root/r2_late_repair_finding.json" \
    "$test_root/r2_late_without_sweep.json" \
    '.findings[0].sibling_sweep_evidence = null'
expect_fail r2_late_requires_sibling_sweep \
    "$test_root/r2_late_without_sweep.json"

mutate "$test_root/r2_late_repair_finding.json" \
    "$test_root/r2_late_without_origin.json" \
    '.findings[0].finding_origin = null'
expect_fail late_timing_does_not_replace_origin \
    "$test_root/r2_late_without_origin.json"

jq --slurpfile finding "$test_root/finding.json" \
    '.findings = [$finding[0] |
        .finding_origin = "repair_delta" |
        .evidence_timing = "on_time" |
        .sibling_sweep_evidence = "all repair-pattern siblings checked"] |
     .verdict = "changes_requested"' \
    "$test_root/r2.json" >"$test_root/r2_repair_delta_finding.json"
expect_pass r2_repair_introduced_finding \
    "$test_root/r2_repair_delta_finding.json"

mutate "$test_root/r2_repair_delta_finding.json" \
    "$test_root/r2_passes_open_blocker.json" \
    '.verdict = "pass_to_R3"'
expect_fail r2_cannot_pass_open_blocker_to_r3 \
    "$test_root/r2_passes_open_blocker.json"

mutate "$test_root/r2_repair_delta_finding.json" \
    "$test_root/r2_escalates_open_blocker.json" \
    '.verdict = "escalate"'
expect_pass r2_can_escalate_open_blocker \
    "$test_root/r2_escalates_open_blocker.json"

jq --slurpfile finding "$test_root/finding.json" \
    '.findings = [$finding[0] |
        .finding_origin = "preexisting_in_base" |
        .sibling_sweep_evidence = "all base-risk siblings classified" |
        .introduced_by_pr = false |
        .violates_current_contract = false |
        .blocks_current_pr = false |
        .disposition = "linked_urgent_issue" |
        .disposition_evidence = "mutually linked urgent issue" |
        .linked_issue_url =
          "https://github.com/example/project/issues/100"]' \
    "$test_root/r2.json" >"$test_root/r2_base_risk.json"
expect_pass preexisting_unworsened_risk_routes_to_issue \
    "$test_root/r2_base_risk.json"

mutate "$test_root/r2_base_risk.json" \
    "$test_root/r2_base_contract_violation.json" \
    '.findings[0].violates_current_contract = true |
     .findings[0].blocks_current_pr = true |
     .findings[0].disposition = "open" |
     .findings[0].disposition_evidence = "current acceptance still fails" |
     .verdict = "changes_requested"'
expect_pass preexisting_contract_violation_still_blocks \
    "$test_root/r2_base_contract_violation.json"

mutate "$test_root/r2_base_risk.json" \
    "$test_root/r2_base_risk_silent.json" \
    '.findings[0].disposition = "ignored" |
     .findings[0].linked_issue_url = null'
expect_fail preexisting_risk_cannot_be_silently_ignored \
    "$test_root/r2_base_risk_silent.json"

mutate "$test_root/r2_base_risk.json" \
    "$test_root/r2_stop_release_policy.json" \
    '.findings[0].stop_release_policy_applies = true |
     .findings[0].blocks_current_pr = true |
     .findings[0].disposition = "blocking_issue" |
     .findings[0].unblock_condition = "linked base repair merges and passes" |
     .verdict = "changes_requested"'
expect_pass repository_stop_release_policy_blocks \
    "$test_root/r2_stop_release_policy.json"

mutate "$test_root/r2_stop_release_policy.json" \
    "$test_root/r2_stop_release_without_condition.json" \
    '.findings[0].unblock_condition = null'
expect_fail stop_release_policy_requires_unblock_condition \
    "$test_root/r2_stop_release_without_condition.json"

jq --slurpfile finding "$test_root/finding.json" \
    '.findings = [$finding[0] |
        .blocks_current_pr = false |
        .disposition = "accepted_risk" |
        .sibling_sweep_evidence = "all matching risks classified" |
        .disposition_evidence = "authorized live Issue decision" |
        .risk_authorized_by = "maintainer" |
        .risk_authorization_evidence = "Issue comment URL"]' \
    "$test_root/r2.json" >"$test_root/r2_authorized_risk.json"
expect_pass authorized_waivable_risk "$test_root/r2_authorized_risk.json"

mutate "$test_root/r2_authorized_risk.json" \
    "$test_root/r2_nonwaivable_accepted.json" \
    '.findings[0].violates_non_waivable_gate = true'
expect_fail nonwaivable_gate_cannot_be_accepted \
    "$test_root/r2_nonwaivable_accepted.json"

mutate "$test_root/r2_nonwaivable_accepted.json" \
    "$test_root/r2_nonwaivable_fixed.json" \
    '.findings[0].disposition = "fixed" |
     .findings[0].disposition_evidence = "gate passes on repaired head" |
     .findings[0].risk_authorized_by = null |
     .findings[0].risk_authorization_evidence = null'
expect_pass nonwaivable_gate_can_only_be_fixed \
    "$test_root/r2_nonwaivable_fixed.json"

mutate "$test_root/r1.json" "$test_root/r3.json" \
    '.review_id = "review-18-99-R3" | .review_round = "R3" |
     .formal_rounds_consumed = 3 | .verdict = "approve" |
     .remaining_blockers_verified = true | .latest_delta_reviewed = true |
     .acceptance_criteria_verified = true |
     .non_waivable_gates_verified = true | .regressions_verified = true'
expect_pass r3_approve_without_blockers "$test_root/r3.json"

jq --slurpfile finding "$test_root/finding.json" \
    '.findings = [$finding[0] |
        .sibling_sweep_evidence = "all final-stage siblings checked"]' \
    "$test_root/r3.json" \
    >"$test_root/r3_approve_with_blocker.json"
expect_fail r3_cannot_approve_with_blocker \
    "$test_root/r3_approve_with_blocker.json"

mutate "$test_root/r3_approve_with_blocker.json" \
    "$test_root/r3_escalates_blocker.json" '.verdict = "escalate"'
expect_pass r3_failed_verification_escalates \
    "$test_root/r3_escalates_blocker.json"

mutate "$test_root/r3.json" "$test_root/r4.json" \
    '.review_round = "R4" | .formal_rounds_consumed = 4'
expect_fail ordinary_r4_is_forbidden "$test_root/r4.json"

cat >"$test_root/adjudication.json" <<'EOF'
{
  "schema_version": 1,
  "record_type": "adjudication",
  "adjudication_id": "ADJ-18-99-1",
  "issue_number": 18,
  "pr_number": 99,
  "adjudicator": "maintainer",
  "authorization_evidence": "repository ownership configuration",
  "formal_rounds_consumed": 3,
  "target_tip_sha": "0000000000000000000000000000000000000000",
  "merge_base_sha": "1111111111111111111111111111111111111111",
  "head_sha": "3333333333333333333333333333333333333333",
  "frozen_ledger_ids": ["F-001"],
  "decision": "split_or_replace_or_redesign",
  "blockers_remaining": true,
  "surface_decision": "replace the unbounded PR with a narrower PR",
  "starts_new_round_sequence": true,
  "round_reset_authorized": true,
  "new_pr_url": "https://github.com/example/project/pull/101",
  "reciprocal_pr_links": true,
  "open_ledger_inherited": true,
  "disposition_history_inherited": true,
  "review_surface_changed": true
}
EOF
expect_pass authorized_replacement_round_reset "$test_root/adjudication.json"

mutate "$test_root/adjudication.json" \
    "$test_root/adjudication_unauthorized_reset.json" \
    '.round_reset_authorized = false'
expect_fail unauthorized_replacement_cannot_reset_rounds \
    "$test_root/adjudication_unauthorized_reset.json"

mutate "$test_root/adjudication.json" "$test_root/adjudication_batch.json" \
    '.decision = "batch_repair_final_verification" |
     .starts_new_round_sequence = false |
     .batch_exit_conditions = ["all bounded blockers closed"] |
     .final_verification_required = true |
     .status_transition = ["status:needs-review", "status:in-progress",
       "status:needs-review"]'
expect_pass one_batch_repair_adjudication "$test_root/adjudication_batch.json"

cat >"$test_root/final.json" <<'EOF'
{
  "schema_version": 1,
  "record_type": "final_verification",
  "adjudication_id": "ADJ-18-99-1",
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
EOF
expect_pass final_verification_approve "$test_root/final.json"

mutate "$test_root/final.json" \
    "$test_root/final_skips_acceptance.json" \
    '.acceptance_criteria_rerun = false'
expect_fail final_approval_requires_full_acceptance_rerun \
    "$test_root/final_skips_acceptance.json"

mutate "$test_root/final.json" \
    "$test_root/final_skips_interactions.json" \
    '.repair_interactions_verified = false'
expect_fail final_requires_repair_interaction_verification \
    "$test_root/final_skips_interactions.json"

mutate "$test_root/final.json" \
    "$test_root/final_empty_ledger_blocker_claim.json" \
    '.acceptance_criteria_rerun = false |
     .non_waivable_gates_rerun = false |
     .full_ledger_verified = false |
     .repair_delta_verified = false |
     .repair_interactions_verified = false |
     .regressions_rerun = false |
     .blockers_present = true | .verdict = "fail" |
     .failure_path = "triage"'
expect_fail final_failure_requires_full_rerun_and_blocking_finding \
    "$test_root/final_empty_ledger_blocker_claim.json"

jq --slurpfile finding "$test_root/finding.json" \
    '.findings = [$finding[0] |
        .finding_origin = "repair_delta" |
        .evidence_timing = "late" |
        .late_reason = "combined final replay exposed the finding" |
        .prior_miss_reason = "not reachable before batch repair" |
        .sibling_sweep_evidence = "all final repair siblings checked"] |
     .blockers_present = true | .verdict = "fail" |
     .failure_path = "replace"' "$test_root/final.json" \
    >"$test_root/final_blocker.json"
expect_pass final_blocker_uses_terminal_path "$test_root/final_blocker.json"

mutate "$test_root/final_blocker.json" \
    "$test_root/final_failure_skips_rerun.json" \
    '.acceptance_criteria_rerun = false |
     .non_waivable_gates_rerun = false |
     .full_ledger_verified = false |
     .repair_delta_verified = false |
     .repair_interactions_verified = false |
     .regressions_rerun = false'
expect_fail final_failure_also_requires_complete_rerun \
    "$test_root/final_failure_skips_rerun.json"

mutate "$test_root/final_blocker.json" \
    "$test_root/final_hides_blocker.json" \
    '.blockers_present = false'
expect_fail final_cannot_hide_blocking_finding \
    "$test_root/final_hides_blocker.json"

mutate "$test_root/final_blocker.json" \
    "$test_root/final_blocker_approve.json" \
    '.verdict = "approve" | .failure_path = null'
expect_fail final_blocker_cannot_approve "$test_root/final_blocker_approve.json"

mutate "$test_root/final_blocker.json" \
    "$test_root/final_extra_repair.json" '.another_repair_allowed = true'
expect_fail final_failure_cannot_open_another_repair \
    "$test_root/final_extra_repair.json"

for field in \
    review_round target_tip_sha merge_base_sha head_sha review_surface reviewers \
    finding_id severity category evidence affected_requirement_or_gate \
    introduced_by_pr worsened_by_pr reachability_expanded_by_pr \
    stop_release_policy_applies \
    finding_origin evidence_timing late_reason exit_condition \
    regression_checks disposition adjudication_id repair_interactions_verified \
    another_repair_allowed; do
    grep -Fq "$field" "$template" ||
        fail "comment template is missing field: $field"
done

printf '%s\n' 'review convergence tests passed'
