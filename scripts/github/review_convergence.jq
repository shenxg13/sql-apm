def nonempty_string:
    type == "string" and (gsub("[[:space:]]"; "") | length > 0);

def positive_integer:
    type == "number" and floor == . and . > 0;

def sha:
    type == "string" and test("^[0-9a-f]{40}$");

def github_url:
    type == "string" and test("^https://github\\.com/[^/]+/[^/]+/");

def nonempty_string_array:
    type == "array" and length > 0 and all(.[]; nonempty_string);

def require($condition; $message):
    if $condition then . else error($message) end;

def one_of($value; $values):
    ($values | index($value)) != null;

def derived_blocker:
    .introduced_by_pr or .worsened_by_pr or
        .reachability_expanded_by_pr or .violates_current_contract or
        .violates_non_waivable_gate or .stop_release_policy_applies;

def validate_finding($stage):
    . as $finding
    | require(.finding_id | nonempty_string;
        "finding_id must be non-empty")
    | require(one_of(.severity; ["P0", "P1", "P2", "P3"]);
        "finding severity must be P0, P1, P2, or P3")
    | require(.category | nonempty_string;
        "finding category must be non-empty")
    | require(.evidence | nonempty_string;
        "finding evidence must be non-empty")
    | require(.affected_requirement_or_gate | nonempty_string;
        "finding must identify the affected requirement or gate")
    | require(.sha_provenance | nonempty_string;
        "finding must identify fixed-SHA provenance")
    | require(.affected_surface | nonempty_string;
        "finding affected_surface must be non-empty")
    | require(.introduced_by_pr | type == "boolean";
        "introduced_by_pr must be boolean")
    | require(.worsened_by_pr | type == "boolean";
        "worsened_by_pr must be boolean")
    | require(.reachability_expanded_by_pr | type == "boolean";
        "reachability_expanded_by_pr must be boolean")
    | require(.violates_current_contract | type == "boolean";
        "violates_current_contract must be boolean")
    | require(.violates_non_waivable_gate | type == "boolean";
        "violates_non_waivable_gate must be boolean")
    | require(.stop_release_policy_applies | type == "boolean";
        "stop_release_policy_applies must be boolean")
    | require(one_of(.finding_origin; [
        "preexisting_in_base", "initial_pr_head", "repair_delta"
      ]); "finding_origin is invalid")
    | require(one_of(.evidence_timing; ["on_time", "late"]);
        "evidence_timing must be on_time or late")
    | require(
        if .evidence_timing == "late" then
            (.late_reason | nonempty_string) and
                (.prior_miss_reason | nonempty_string) and
                (.sibling_sweep_evidence | nonempty_string)
        else true end;
        "late finding requires reason, prior miss reason, and sweep evidence")
    | require(.exit_condition | nonempty_string;
        "finding exit_condition must be non-empty")
    | require(.regression_checks | nonempty_string_array;
        "finding regression_checks must be a non-empty string array")
    | require(.disposition | nonempty_string;
        "finding disposition must be non-empty")
    | require(.disposition_evidence | nonempty_string;
        "finding disposition_evidence must be non-empty")
    | require(.blocks_current_pr | type == "boolean";
        "blocks_current_pr must be boolean")
    | require(
        if derived_blocker then
            if .disposition == "accepted_risk" then
                (.violates_non_waivable_gate == false) and
                    (.stop_release_policy_applies == false) and
                    (.risk_authorized_by | nonempty_string) and
                    (.risk_authorization_evidence | nonempty_string) and
                    (.blocks_current_pr == false)
            elif .disposition == "fixed" then
                .blocks_current_pr == false
            else .blocks_current_pr == true end
        else
            .finding_origin == "preexisting_in_base" and
                .blocks_current_pr == false and
                .disposition == "linked_urgent_issue" and
                (.linked_issue_url | github_url)
        end;
        "finding blocker routing or accepted-risk authorization is invalid")
    | require(
        if .violates_non_waivable_gate then
            .disposition != "accepted_risk"
        else true end;
        "a non-waivable gate cannot be accepted or unblocked")
    | require(
        if one_of($stage; ["R2", "R3", "final_verification"]) then
            (.finding_origin | nonempty_string) and
                (.evidence_timing | nonempty_string) and
                (.sibling_sweep_evidence | nonempty_string)
        else true end;
        "R2/R3/final findings require origin, timing, and sweep evidence")
    | require(
        if .stop_release_policy_applies then
            .disposition == "blocking_issue" and .blocks_current_pr and
                (.linked_issue_url | github_url) and
                (.unblock_condition | nonempty_string)
        else true end;
        "stop-release policy requires a blocking Issue and unblock condition")
    | $finding;

def validate_findings($stage):
    require(.findings | type == "array"; "findings must be an array")
    | .findings[]? |= validate_finding($stage);

def validate_round_common:
    require(.review_id | nonempty_string; "review_id must be non-empty")
    | require(.issue_number | positive_integer;
        "issue_number must be a positive integer")
    | require(.pr_number | positive_integer;
        "pr_number must be a positive integer")
    | require(.issue_status == "status:needs-review";
        "formal review requires status:needs-review")
    | require(.issue_comment_url | github_url;
        "issue_comment_url must be a GitHub URL")
    | require(.pr_evidence_url | github_url;
        "pr_evidence_url must be a GitHub URL")
    | require(.owner | nonempty_string; "owner must be non-empty")
    | require(.reviewers | nonempty_string_array;
        "reviewers must be a non-empty string array")
    | require(.target_tip_sha | sha; "target_tip_sha must be a 40-char SHA")
    | require(.merge_base_sha | sha; "merge_base_sha must be a 40-char SHA")
    | require(.head_sha | sha; "head_sha must be a 40-char SHA")
    | require(.initial_head_sha | sha;
        "initial_head_sha must be a 40-char SHA")
    | require(.review_surface | nonempty_string_array;
        "review_surface must be a non-empty string array")
    | require(.coverage | type == "array"; "coverage must be an array")
    | require(.blind_spots | type == "array";
        "blind_spots must be an array")
    | require(.sha_changed_during_round | type == "boolean";
        "sha_changed_during_round must be boolean")
    | require(.effective_diff_recomputed | type == "boolean";
        "effective_diff_recomputed must be boolean")
    | require(.effective_diff_changed | type == "boolean";
        "effective_diff_changed must be boolean")
    | require(.round_completion_possible | type == "boolean";
        "round_completion_possible must be boolean")
    | require(.aborted | type == "boolean"; "aborted must be boolean")
    | require(
        if .sha_changed_during_round then .effective_diff_recomputed else true end;
        "SHA changes require effective-diff recomputation")
    | require(
        if .effective_diff_changed and (.round_completion_possible | not) then
            .aborted and .verdict == "aborted"
        else true end;
        "incomplete review of a changed effective diff must be aborted")
    | require(
        if .aborted then
            .verdict == "aborted" and
                (.unfinished_responsibilities | nonempty_string_array)
        else true end;
        "aborted handoff must preserve unfinished responsibilities")
    | require(
        one_of(.incomplete_stage_inherited_from; [null, "R1", "R2"]);
        "incomplete_stage_inherited_from is invalid")
    | require(.inherited_responsibilities | type == "array";
        "inherited_responsibilities must be an array")
    | require(
        if .incomplete_stage_inherited_from == "R1" then
            (.inherited_responsibilities | index("comprehensive_discovery")) !=
                null and .inherited_stage_completed
        elif .incomplete_stage_inherited_from == "R2" then
            (.inherited_responsibilities | index("repair_verification")) !=
                null and .inherited_stage_completed
        else true end;
        "the next round must inherit and complete the unfinished stage")
    | require(.inherited_stage_completed | type == "boolean";
        "inherited_stage_completed must be boolean");

def validate_r0:
    require(.formal_rounds_consumed == 0; "R0 consumes no formal round")
    | require(.issue_contract_frozen == true;
        "R0 must freeze the Issue contract")
    | require(.review_surface_frozen == true;
        "R0 must freeze the review surface")
    | require(.acceptance_mapping | nonempty_string_array;
        "R0 requires acceptance mapping")
    | require(.planned_checks | nonempty_string_array;
        "R0 requires planned checks")
    | require(.known_risks | type == "array";
        "R0 known_risks must be an array")
    | require(.reviewer_domains | nonempty_string_array;
        "R0 requires reviewer domains")
    | require(.verdict == "admitted"; "R0 verdict must be admitted")
    | require(.aborted == false; "R0 cannot be an aborted formal handoff");

def validate_r1:
    require(.formal_rounds_consumed == 1; "R1 must consume round one")
    | require(.safe_stop | type == "boolean"; "safe_stop must be boolean")
    | require(.r1_scope_complete | type == "boolean";
        "r1_scope_complete must be boolean")
    | require(
        if .safe_stop then
            one_of(.safe_stop_reason; [
                "secret_disclosure", "destructive_test",
                "production_incident", "active_safety_risk"
            ]) and (.r1_scope_complete == false) and
                .verdict == "escalate" and
                ((.unfinished_responsibilities |
                    index("comprehensive_discovery")) != null)
        elif .aborted then
            ((.unfinished_responsibilities |
                index("comprehensive_discovery")) != null)
        else
            .r1_scope_complete and (.coverage | nonempty_string_array) and
                one_of(.verdict; ["approve", "changes_requested", "escalate"])
        end;
        "R1 cannot stop early except for an audited safe stop or abort")
    | require(
        if .verdict == "approve" then
            all(.findings[]?; .blocks_current_pr == false)
        else true end;
        "R1 cannot approve with a current blocker");

def validate_r2:
    require(.formal_rounds_consumed == 2; "R2 must consume round two")
    | require(.complete_r1_ledger == true;
        "R2 requires a complete R1 ledger")
    | require(.ledger_exit_conditions_verified | type == "boolean";
        "R2 ledger verification must be boolean")
    | require(.repair_delta_reviewed | type == "boolean";
        "R2 repair_delta_reviewed must be boolean")
    | require(.repair_interactions_reviewed | type == "boolean";
        "R2 repair_interactions_reviewed must be boolean")
    | require(.regressions_reviewed | type == "boolean";
        "R2 regressions_reviewed must be boolean")
    | require(
        if .aborted then true else
            .ledger_exit_conditions_verified and .repair_delta_reviewed and
                .repair_interactions_reviewed and .regressions_reviewed and
                one_of(.verdict; [
                    "pass_to_R3", "changes_requested", "escalate"
                ])
        end;
        "completed R2 must verify ledger, delta, interactions, and regressions")
    | require(
        if .aborted then true
        elif any(.findings[]?; .blocks_current_pr) then
            one_of(.verdict; ["changes_requested", "escalate"])
        else true end;
        "completed R2 with a current blocker must request changes or escalate")
    | require(
        if .verdict == "pass_to_R3" then
            all(.findings[]?; .blocks_current_pr == false)
        else true end;
        "R2 cannot pass a current blocker to R3");

def validate_r3:
    require(.formal_rounds_consumed == 3; "R3 must consume round three")
    | require(.remaining_blockers_verified | type == "boolean";
        "R3 remaining_blockers_verified must be boolean")
    | require(.latest_delta_reviewed | type == "boolean";
        "R3 latest_delta_reviewed must be boolean")
    | require(.acceptance_criteria_verified | type == "boolean";
        "R3 acceptance_criteria_verified must be boolean")
    | require(.non_waivable_gates_verified | type == "boolean";
        "R3 non_waivable_gates_verified must be boolean")
    | require(.regressions_verified | type == "boolean";
        "R3 regressions_verified must be boolean")
    | require(
        if .aborted then true else
            .remaining_blockers_verified and .latest_delta_reviewed and
                .acceptance_criteria_verified and
                .non_waivable_gates_verified and .regressions_verified and
                one_of(.verdict; ["approve", "escalate"])
        end;
        "completed R3 must run final duties and approve or escalate")
    | require(
        if .verdict == "approve" then
            all(.findings[]?; .blocks_current_pr == false)
        else true end;
        "R3 cannot approve with a current blocker");

def validate_review_round:
    validate_round_common
    | require(one_of(.review_round; ["R0", "R1", "R2", "R3"]);
        "ordinary review_round must be R0, R1, R2, or R3")
    | validate_findings(.review_round)
    | if .review_round == "R0" then validate_r0
      elif .review_round == "R1" then validate_r1
      elif .review_round == "R2" then validate_r2
      else validate_r3 end;

def validate_adjudication:
    require(.adjudication_id | nonempty_string;
        "adjudication_id must be non-empty")
    | require(.issue_number | positive_integer;
        "issue_number must be a positive integer")
    | require(.pr_number | positive_integer;
        "pr_number must be a positive integer")
    | require(.adjudicator | nonempty_string;
        "adjudicator must be non-empty")
    | require(.authorization_evidence | nonempty_string;
        "adjudicator authorization evidence is required")
    | require(.formal_rounds_consumed == 3;
        "adjudication requires three consumed rounds")
    | require(.target_tip_sha | sha; "target_tip_sha must be a 40-char SHA")
    | require(.merge_base_sha | sha; "merge_base_sha must be a 40-char SHA")
    | require(.head_sha | sha; "head_sha must be a 40-char SHA")
    | require(.frozen_ledger_ids | type == "array";
        "frozen_ledger_ids must be an array")
    | require(.starts_new_round_sequence | type == "boolean";
        "starts_new_round_sequence must be boolean")
    | require(one_of(.decision; [
        "approve", "batch_repair_final_verification", "triage", "fix_base",
        "split_or_replace_or_redesign"
      ]); "adjudication decision is invalid")
    | require(
        if .decision == "approve" then
            .blockers_remaining == false
        elif .decision == "batch_repair_final_verification" then
            (.batch_exit_conditions | nonempty_string_array) and
                .final_verification_required == true and
                .status_transition == [
                    "status:needs-review", "status:in-progress",
                    "status:needs-review"
                ]
        elif .decision == "triage" then
            .target_status == "status:triage"
        elif .decision == "fix_base" then
            (.blocking_issue_url | github_url) and
                (.unblock_condition | nonempty_string)
        else
            (.surface_decision | nonempty_string)
        end;
        "adjudication decision evidence is incomplete")
    | require(
        if .starts_new_round_sequence then
            .decision == "split_or_replace_or_redesign" and
                .round_reset_authorized == true and
                (.new_pr_url | github_url) and
                .reciprocal_pr_links == true and
                .open_ledger_inherited == true and
                .disposition_history_inherited == true and
                .review_surface_changed == true
        else true end;
        "new PR round reset lacks adjudication, links, ledger, or surface change");

def validate_final_verification:
    require(.adjudication_id | nonempty_string;
        "final verification requires adjudication_id")
    | require(.issue_number | positive_integer;
        "issue_number must be a positive integer")
    | require(.pr_number | positive_integer;
        "pr_number must be a positive integer")
    | require(.head_sha | sha; "head_sha must be a 40-char SHA")
    | require(.status_transition == [
        "status:needs-review", "status:in-progress", "status:needs-review"
      ]; "final verification status transition is invalid")
    | require(.acceptance_criteria_rerun | type == "boolean";
        "acceptance_criteria_rerun must be boolean")
    | require(.non_waivable_gates_rerun | type == "boolean";
        "non_waivable_gates_rerun must be boolean")
    | require(.full_ledger_verified | type == "boolean";
        "full_ledger_verified must be boolean")
    | require(.repair_delta_verified | type == "boolean";
        "repair_delta_verified must be boolean")
    | require(.repair_interactions_verified | type == "boolean";
        "repair_interactions_verified must be boolean")
    | require(.regressions_rerun | type == "boolean";
        "regressions_rerun must be boolean")
    | require(
        .acceptance_criteria_rerun and .non_waivable_gates_rerun and
            .full_ledger_verified and .repair_delta_verified and
            .repair_interactions_verified and .regressions_rerun;
        "every final-verification outcome requires the complete rerun")
    | require(.another_repair_allowed == false;
        "final verification never permits another repair round")
    | validate_findings("final_verification")
    | require(.blockers_present | type == "boolean";
        "blockers_present must be boolean")
    | require(
        .blockers_present == any(.findings[]?; .blocks_current_pr);
        "blockers_present must exactly match the blocking finding ledger")
    | require(
        if .blockers_present then
            .verdict == "fail" and one_of(.failure_path; [
                "triage", "fix_base", "split", "replace", "redesign"
            ])
        else
            .verdict == "approve"
        end;
        "final verification verdict or terminal path is invalid");

require(.schema_version == 1; "schema_version must be 1")
| require(one_of(.record_type; [
    "review_round", "adjudication", "final_verification"
  ]); "record_type is invalid")
| if .record_type == "review_round" then validate_review_round
  elif .record_type == "adjudication" then validate_adjudication
  else validate_final_verification end
