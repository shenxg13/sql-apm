-- Structure version 1.9.0. Called only after catalog compatibility checks.
-- The caller sets the verified project schema as search_path.
CREATE TABLE IF NOT EXISTS schema_version (
    version text PRIMARY KEY,
    script_sha256 text NOT NULL CHECK (script_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS scope (
    scope_id text PRIMARY KEY CHECK (scope_id <> ''),
    system_kind text NOT NULL CHECK (system_kind <> ''),
    profile text NOT NULL CHECK (profile <> ''),
    contract_version text NOT NULL CHECK (contract_version <> ''),
    UNIQUE (scope_id, profile)
);
CREATE TABLE IF NOT EXISTS source (
    source_id text PRIMARY KEY CHECK (source_id <> ''),
    scope_id text NOT NULL REFERENCES scope,
    mapping_ref text NOT NULL CHECK (mapping_ref <> ''),
    declared_build text NOT NULL CHECK (declared_build <> ''),
    timezone text NOT NULL CHECK (timezone <> ''),
    declaration_evidence text NOT NULL CHECK (declaration_evidence <> ''),
    UNIQUE (source_id, scope_id)
);
CREATE TABLE IF NOT EXISTS source_file (
    file_id text PRIMARY KEY CHECK (file_id <> ''),
    source_id text NOT NULL,
    scope_id text NOT NULL,
    content_identity text NOT NULL CHECK (content_identity <> ''),
    checksum_algorithm text NOT NULL CHECK (checksum_algorithm <> ''),
    checksum_value text NOT NULL CHECK (checksum_value <> ''),
    byte_count bigint NOT NULL CHECK (byte_count >= 0),
    locator text NOT NULL CHECK (locator <> ''),
    closed_and_copied boolean NOT NULL,
    declaration_evidence text NOT NULL CHECK (declaration_evidence <> ''),
    first_log_at timestamptz,
    last_log_at timestamptz,
    CONSTRAINT source_file_log_time_bounds CHECK (
        (first_log_at IS NULL AND last_log_at IS NULL) OR
        (first_log_at IS NOT NULL AND last_log_at IS NOT NULL AND first_log_at <= last_log_at)),
    FOREIGN KEY (source_id, scope_id) REFERENCES source (source_id, scope_id),
    UNIQUE (source_id, content_identity),
    UNIQUE (file_id, scope_id),
    UNIQUE (file_id, source_id, scope_id)
);
CREATE INDEX IF NOT EXISTS source_file_checksum_idx ON source_file (source_id, checksum_algorithm, checksum_value);
CREATE TABLE IF NOT EXISTS import_batch (
    batch_id text PRIMARY KEY CHECK (batch_id <> ''),
    scope_id text NOT NULL REFERENCES scope,
    files_confirmed_complete boolean NOT NULL,
    state text NOT NULL CHECK (state IN ('pending','processing','complete','failed','conflict')),
    CHECK (state <> 'complete' OR files_confirmed_complete),
    UNIQUE (batch_id, scope_id)
);
CREATE TABLE IF NOT EXISTS batch_date (
    batch_id text NOT NULL REFERENCES import_batch,
    declared_date date NOT NULL,
    PRIMARY KEY (batch_id, declared_date)
);
CREATE TABLE IF NOT EXISTS import_attempt (
    attempt_id text PRIMARY KEY CHECK (attempt_id <> ''),
    batch_id text NOT NULL,
    file_id text NOT NULL,
    scope_id text NOT NULL,
    retry_of text,
    duplicate_of text,
    state text NOT NULL CHECK (state IN ('pending','running','succeeded','duplicate_skipped','failed','interrupted','conflict')),
    started_at timestamptz,
    finished_at timestamptz,
    reliable_record_count bigint CHECK (reliable_record_count >= 0),
    FOREIGN KEY (batch_id, scope_id) REFERENCES import_batch (batch_id, scope_id),
    FOREIGN KEY (file_id, scope_id) REFERENCES source_file (file_id, scope_id),
    UNIQUE (attempt_id, file_id),
    UNIQUE (attempt_id, batch_id, file_id),
    FOREIGN KEY (retry_of, file_id) REFERENCES import_attempt (attempt_id, file_id),
    FOREIGN KEY (duplicate_of, file_id) REFERENCES import_attempt (attempt_id, file_id),
    CHECK ((state = 'duplicate_skipped') = (duplicate_of IS NOT NULL)),
    CHECK (retry_of IS DISTINCT FROM attempt_id AND duplicate_of IS DISTINCT FROM attempt_id),
    CHECK ((state IN ('pending','running')) = (finished_at IS NULL)),
    CHECK (state <> 'running' OR started_at IS NOT NULL),
    CHECK (finished_at >= started_at)
);
CREATE INDEX IF NOT EXISTS import_attempt_file_idx ON import_attempt (file_id);
CREATE TABLE IF NOT EXISTS batch_entry (
    batch_id text NOT NULL,
    file_id text NOT NULL,
    scope_id text NOT NULL,
    final_attempt_id text,
    PRIMARY KEY (batch_id, file_id),
    FOREIGN KEY (batch_id, scope_id) REFERENCES import_batch (batch_id, scope_id),
    FOREIGN KEY (file_id, scope_id) REFERENCES source_file (file_id, scope_id),
    FOREIGN KEY (final_attempt_id, batch_id, file_id) REFERENCES import_attempt (attempt_id, batch_id, file_id)
);
CREATE TABLE IF NOT EXISTS evidence_record (
    record_id text PRIMARY KEY CHECK (record_id <> ''),
    file_id text NOT NULL,
    source_id text NOT NULL,
    scope_id text NOT NULL,
    record_no bigint NOT NULL CHECK (record_no > 0),
    line_start bigint NOT NULL CHECK (line_start > 0),
    line_end bigint NOT NULL CHECK (line_end >= line_start),
    decode_state text NOT NULL CHECK (decode_state IN ('decoded','invalid')),
    observed jsonb NOT NULL CHECK (jsonb_typeof(observed) = 'object'),
    FOREIGN KEY (file_id, source_id, scope_id) REFERENCES source_file (file_id, source_id, scope_id),
    UNIQUE (file_id, record_no),
    UNIQUE (record_id, source_id, scope_id)
);
CREATE TABLE IF NOT EXISTS analysis (
    analysis_id text PRIMARY KEY CHECK (analysis_id <> ''),
    scope_id text NOT NULL,
    profile text NOT NULL,
    mapping_version text NOT NULL CHECK (mapping_version <> ''),
    parser_version text NOT NULL CHECK (parser_version <> ''),
    association_version text NOT NULL CHECK (association_version <> ''),
    supersedes text,
    evidence_manifest text NOT NULL CHECK (evidence_manifest <> ''),
    FOREIGN KEY (scope_id, profile) REFERENCES scope (scope_id, profile),
    UNIQUE (analysis_id, scope_id),
    FOREIGN KEY (supersedes, scope_id) REFERENCES analysis (analysis_id, scope_id),
    CHECK (supersedes IS DISTINCT FROM analysis_id)
);
CREATE TABLE IF NOT EXISTS analysis_file (
    analysis_id text NOT NULL,
    file_id text NOT NULL,
    scope_id text NOT NULL,
    PRIMARY KEY (analysis_id, file_id),
    FOREIGN KEY (analysis_id, scope_id) REFERENCES analysis (analysis_id, scope_id),
    FOREIGN KEY (file_id, scope_id) REFERENCES source_file (file_id, scope_id)
);
CREATE TABLE IF NOT EXISTS mpp_sql_text (
    sql_id text PRIMARY KEY CHECK (sql_id <> ''),
    text text NOT NULL CHECK (text <> ''),
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32
        AND content_sha256 = sha256(convert_to(text, 'UTF8'))),
    search_text text GENERATED ALWAYS AS (translate(text,E'ABCDEFGHIJKLMNOPQRSTUVWXYZ \t\n\r\f\013','abcdefghijklmnopqrstuvwxyz')) STORED
);
-- Hash narrows candidates only: full content equality decides reuse.
CREATE INDEX IF NOT EXISTS mpp_sql_text_content_idx ON mpp_sql_text (content_sha256);
CREATE TABLE IF NOT EXISTS mpp_sql_text_evidence (
    sql_id text NOT NULL REFERENCES mpp_sql_text,
    record_id text NOT NULL REFERENCES evidence_record,
    PRIMARY KEY (sql_id, record_id)
);
CREATE TABLE IF NOT EXISTS mpp_occurrence (
    analysis_id text NOT NULL,
    occurrence_id text NOT NULL CHECK (occurrence_id <> ''),
    scope_id text NOT NULL,
    source_id text NOT NULL,
    anchor_ref text NOT NULL,
    unit text NOT NULL CHECK (unit IN ('request','call')),
    request_shape text NOT NULL CHECK (request_shape IN ('single','batch','unknown')),
    database text CHECK (database <> ''),
    execution_user text CHECK (execution_user <> ''),
    sql_id text REFERENCES mpp_sql_text,
    sql_state text NOT NULL CHECK (sql_state IN ('complete','missing','incomplete','invalid_encoding','uncertain')),
    timing_type text CHECK (timing_type IN ('request','execute_first','execute_fetch','parse','bind')),
    timing_reason text CHECK (timing_reason <> ''),
    outcome text NOT NULL CHECK (outcome IN ('success','failed','cancelled','timed_out','unknown')),
    association_state text NOT NULL CHECK (association_state IN ('reliable','unpaired','ambiguous')),
    association_method text NOT NULL CHECK (association_method <> ''),
    association_reason text CHECK (association_reason <> ''),
    end_at timestamptz,
    duration_ms numeric CHECK (duration_ms >= 0 AND duration_ms NOT IN ('NaN','Infinity','-Infinity')),
    estimated_start_at timestamptz,
    start_basis text CHECK (start_basis = 'end_minus_duration'),
    value_reasons jsonb NOT NULL CHECK (jsonb_typeof(value_reasons) = 'object'),
    PRIMARY KEY (analysis_id, occurrence_id),
    UNIQUE (analysis_id, occurrence_id, scope_id),
    UNIQUE (analysis_id, anchor_ref, unit),
    FOREIGN KEY (analysis_id, scope_id) REFERENCES analysis (analysis_id, scope_id),
    FOREIGN KEY (source_id, scope_id) REFERENCES source (source_id, scope_id),
    FOREIGN KEY (anchor_ref, source_id, scope_id) REFERENCES evidence_record (record_id, source_id, scope_id),
    CHECK ((sql_state = 'complete') = (sql_id IS NOT NULL)),
    CHECK ((timing_type IS NULL) = (timing_reason IS NOT NULL)),
    CHECK (timing_type IS NULL OR (unit = 'request') = (timing_type = 'request')),
    CHECK (timing_type NOT IN ('execute_first','execute_fetch') OR association_state = 'reliable'),
    CHECK ((association_state = 'reliable') = (association_reason IS NULL)),
    CHECK ((estimated_start_at IS NOT NULL) = (end_at IS NOT NULL AND duration_ms IS NOT NULL)),
    CHECK ((estimated_start_at IS NULL) = (start_basis IS NULL)),
    CHECK (estimated_start_at <= end_at),
    CHECK (end_at IS NOT NULL OR coalesce(length(value_reasons->>'end_at'),0) > 0),
    CHECK (duration_ms IS NOT NULL OR coalesce(length(value_reasons->>'duration_ms'),0) > 0),
    CHECK (estimated_start_at IS NOT NULL OR coalesce(length(value_reasons->>'estimated_start_at'),0) > 0)
);
CREATE INDEX IF NOT EXISTS mpp_occurrence_scope_time_idx ON mpp_occurrence (scope_id, estimated_start_at);
CREATE INDEX IF NOT EXISTS mpp_occurrence_sql_time_idx ON mpp_occurrence (sql_id, estimated_start_at);
CREATE TABLE IF NOT EXISTS mpp_occurrence_evidence (
    analysis_id text NOT NULL,
    occurrence_id text NOT NULL,
    record_id text NOT NULL REFERENCES evidence_record,
    purpose text NOT NULL CHECK (purpose IN ('support','outcome','association')),
    PRIMARY KEY (analysis_id, occurrence_id, record_id, purpose),
    FOREIGN KEY (analysis_id, occurrence_id) REFERENCES mpp_occurrence
);
CREATE TABLE IF NOT EXISTS mpp_normalization (
    normalization_id text PRIMARY KEY CHECK (normalization_id <> ''),
    algorithm_version text NOT NULL CHECK (algorithm_version <> ''),
    parser_version text NOT NULL CHECK (parser_version <> ''),
    dictionary_schema_version integer NOT NULL CHECK (dictionary_schema_version > 0),
    dictionary_rules_version text NOT NULL CHECK (dictionary_rules_version <> ''),
    dictionary_digest_algorithm text NOT NULL CHECK (dictionary_digest_algorithm <> ''),
    dictionary_digest_value text NOT NULL CHECK (dictionary_digest_value <> ''),
    rules_ref text NOT NULL CHECK (rules_ref <> '')
);
CREATE TABLE IF NOT EXISTS mpp_fingerprint (
    fingerprint_id text PRIMARY KEY CHECK (fingerprint_id <> ''),
    sql_id text NOT NULL REFERENCES mpp_sql_text,
    normalization_id text NOT NULL REFERENCES mpp_normalization,
    profile text NOT NULL CHECK (profile = 'mpp-csv/1'),
    state text NOT NULL CHECK (state IN ('reliable','unsupported_syntax','normalization_failed')),
    value text CHECK (value <> ''),
    CONSTRAINT mpp_fingerprint_not_approximate CHECK (value NOT LIKE 'approx:%'),
    reason text CHECK (reason <> ''),
    CHECK ((state = 'reliable') = (value IS NOT NULL)),
    CHECK ((state = 'reliable') = (reason IS NULL)),
    UNIQUE (sql_id, normalization_id, profile),
    UNIQUE (fingerprint_id, normalization_id, sql_id),
    UNIQUE (fingerprint_id, normalization_id, profile),
    UNIQUE (fingerprint_id, normalization_id, profile, value)
);
CREATE INDEX IF NOT EXISTS mpp_fingerprint_value_idx ON mpp_fingerprint (normalization_id, profile, value);
CREATE TABLE IF NOT EXISTS mpp_baseline_group (
    group_id text PRIMARY KEY CHECK (group_id <> ''),
    scope_id text NOT NULL,
    profile text NOT NULL CHECK (profile = 'mpp-csv/1'),
    normalization_id text NOT NULL,
    database text NOT NULL CHECK (database <> ''),
    execution_user text NOT NULL CHECK (execution_user <> ''),
    fingerprint_id text NOT NULL,
    fingerprint_value text NOT NULL,
    timing_type text NOT NULL CHECK (timing_type IN ('request','execute_first','execute_fetch','parse','bind')),
    FOREIGN KEY (scope_id, profile) REFERENCES scope (scope_id, profile),
    FOREIGN KEY (fingerprint_id, normalization_id, profile, fingerprint_value) REFERENCES mpp_fingerprint (fingerprint_id, normalization_id, profile, value),
    UNIQUE (scope_id, profile, normalization_id, database, execution_user, fingerprint_value, timing_type),
    UNIQUE (group_id, scope_id, normalization_id, profile),
    UNIQUE (group_id, normalization_id, profile, fingerprint_value)
);
CREATE TABLE IF NOT EXISTS input_snapshot (
    input_id text PRIMARY KEY CHECK (input_id <> ''),
    scope_id text NOT NULL REFERENCES scope,
    selection_kind text NOT NULL CHECK (selection_kind IN ('explicit','immutable_manifest')),
    manifest_ref text CHECK (manifest_ref <> ''),
    frozen_at timestamptz NOT NULL,
    CHECK ((selection_kind = 'immutable_manifest') = (manifest_ref IS NOT NULL)),
    UNIQUE (input_id, scope_id)
);
CREATE TABLE IF NOT EXISTS input_batch (
    input_id text NOT NULL,
    batch_id text NOT NULL,
    scope_id text NOT NULL,
    PRIMARY KEY (input_id, batch_id),
    FOREIGN KEY (input_id, scope_id) REFERENCES input_snapshot (input_id, scope_id),
    FOREIGN KEY (batch_id, scope_id) REFERENCES import_batch (batch_id, scope_id)
);
CREATE TABLE IF NOT EXISTS input_file (
    input_id text NOT NULL,
    file_id text NOT NULL,
    scope_id text NOT NULL,
    PRIMARY KEY (input_id, file_id),
    FOREIGN KEY (input_id, scope_id) REFERENCES input_snapshot (input_id, scope_id),
    FOREIGN KEY (file_id, scope_id) REFERENCES source_file (file_id, scope_id)
);
CREATE TABLE IF NOT EXISTS input_analysis (
    input_id text NOT NULL,
    analysis_id text NOT NULL,
    scope_id text NOT NULL,
    PRIMARY KEY (input_id, analysis_id),
    FOREIGN KEY (input_id, scope_id) REFERENCES input_snapshot (input_id, scope_id),
    FOREIGN KEY (analysis_id, scope_id) REFERENCES analysis (analysis_id, scope_id)
);
CREATE TABLE IF NOT EXISTS mpp_input_occurrence (
    input_id text NOT NULL,
    analysis_id text NOT NULL,
    occurrence_id text NOT NULL,
    scope_id text NOT NULL,
    PRIMARY KEY (input_id, analysis_id, occurrence_id),
    FOREIGN KEY (input_id, analysis_id) REFERENCES input_analysis,
    FOREIGN KEY (input_id, scope_id) REFERENCES input_snapshot (input_id, scope_id),
    FOREIGN KEY (analysis_id, occurrence_id, scope_id) REFERENCES mpp_occurrence (analysis_id, occurrence_id, scope_id)
);
CREATE TABLE IF NOT EXISTS config_snapshot (
    config_id text PRIMARY KEY CHECK (config_id <> ''),
    scope_id text NOT NULL,
    normalization_id text REFERENCES mpp_normalization,
    profile text NOT NULL,
    source_mapping_refs jsonb NOT NULL CHECK (jsonb_typeof(source_mapping_refs) = 'array'),
    cutoff_date date NOT NULL,
    window_days integer NOT NULL CHECK (window_days > 0),
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    blacklist jsonb NOT NULL CHECK (jsonb_typeof(blacklist) = 'object' AND (profile <> 'mpp-csv/1' OR blacklist ?& ARRAY['category_ref','template_ref','category_rules','template_rules'])),
    exclusions jsonb NOT NULL CHECK (jsonb_typeof(exclusions) = 'array'),
    thresholds jsonb NOT NULL CHECK (jsonb_typeof(thresholds) = 'object' AND (profile <> 'mpp-csv/1' OR thresholds ?& ARRAY['overall','day','week','weekday','hour'])),
    statistics_version text NOT NULL CHECK (statistics_version <> ''),
    FOREIGN KEY (scope_id, profile) REFERENCES scope (scope_id, profile),
    CHECK (window_start < window_end),
    CHECK (profile <> 'mpp-csv/1' OR (
        window_start = (cutoff_date - (window_days - 1))::timestamp AT TIME ZONE INTERVAL '+08:00'
        AND window_end = (cutoff_date + 1)::timestamp AT TIME ZONE INTERVAL '+08:00')),
    CHECK (profile <> 'mpp-csv/1' OR normalization_id IS NOT NULL),
    UNIQUE (config_id, scope_id, profile),
    UNIQUE (config_id, scope_id, normalization_id, profile)
);
CREATE TABLE IF NOT EXISTS mpp_result_partition (
    partition_id bigint PRIMARY KEY,
    scope_id text NOT NULL REFERENCES scope,
    build_month date NOT NULL CHECK (extract(day FROM build_month) = 1),
    cleaned_at timestamptz,
    groups_cleaned_at timestamptz,
    CHECK (groups_cleaned_at IS NULL OR (cleaned_at IS NOT NULL AND groups_cleaned_at >= cleaned_at)),
    UNIQUE (scope_id, build_month),
    UNIQUE (partition_id, scope_id)
);
CREATE TABLE IF NOT EXISTS build (
    build_id text PRIMARY KEY CHECK (build_id <> ''),
    scope_id text NOT NULL,
    input_id text NOT NULL,
    config_id text NOT NULL,
    normalization_id text,
    profile text NOT NULL,
    retry_of text,
    state text NOT NULL CHECK (state IN ('running','calculated','failed','interrupted')),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    results_saved boolean NOT NULL,
    FOREIGN KEY (input_id, scope_id) REFERENCES input_snapshot (input_id, scope_id),
    CHECK (profile <> 'mpp-csv/1' OR normalization_id IS NOT NULL),
    FOREIGN KEY (config_id, scope_id, profile) REFERENCES config_snapshot (config_id, scope_id, profile),
    FOREIGN KEY (config_id, scope_id, normalization_id, profile) REFERENCES config_snapshot (config_id, scope_id, normalization_id, profile),
    UNIQUE (build_id, scope_id),
    UNIQUE (build_id, scope_id, normalization_id, profile),
    FOREIGN KEY (retry_of, scope_id) REFERENCES build (build_id, scope_id),
    CHECK (retry_of IS DISTINCT FROM build_id),
    CHECK ((state = 'running') = (finished_at IS NULL)),
    CHECK (finished_at >= started_at),
    partition_id bigint,
    FOREIGN KEY (partition_id, scope_id) REFERENCES mpp_result_partition (partition_id, scope_id),
    UNIQUE (partition_id, build_id),
    diagnostics jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(diagnostics) = 'object')
);
CREATE INDEX IF NOT EXISTS build_scope_time_idx ON build (scope_id, started_at);
CREATE TABLE IF NOT EXISTS mpp_decision (
    decision_id text PRIMARY KEY CHECK (decision_id <> ''),
    build_id text NOT NULL,
    scope_id text NOT NULL,
    normalization_id text NOT NULL,
    profile text NOT NULL,
    analysis_id text NOT NULL,
    occurrence_id text NOT NULL,
    fingerprint_id text,
    fingerprint_value text,
    group_id text,
    state text NOT NULL CHECK (state IN ('included','excluded','unresolved','outside_window')),
    in_window boolean,
    count_scope text NOT NULL CHECK (count_scope IN ('group','batch','none')),
    rule_evaluations jsonb NOT NULL CHECK (jsonb_typeof(rule_evaluations) = 'object' AND rule_evaluations ?& ARRAY['outcome','duration','association','sql','fingerprint','blacklist','exclusion_interval','window']),
    FOREIGN KEY (build_id, scope_id, normalization_id, profile) REFERENCES build (build_id, scope_id, normalization_id, profile),
    FOREIGN KEY (analysis_id, occurrence_id, scope_id) REFERENCES mpp_occurrence (analysis_id, occurrence_id, scope_id),
    FOREIGN KEY (fingerprint_id, normalization_id, profile) REFERENCES mpp_fingerprint (fingerprint_id, normalization_id, profile),
    FOREIGN KEY (fingerprint_id, normalization_id, profile, fingerprint_value) REFERENCES mpp_fingerprint (fingerprint_id, normalization_id, profile, value),
    FOREIGN KEY (group_id, scope_id, normalization_id, profile) REFERENCES mpp_baseline_group (group_id, scope_id, normalization_id, profile),
    FOREIGN KEY (group_id, normalization_id, profile, fingerprint_value) REFERENCES mpp_baseline_group (group_id, normalization_id, profile, fingerprint_value),
    UNIQUE (build_id, analysis_id, occurrence_id),
    CHECK (coalesce(rule_evaluations->>'outcome' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'duration' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'association' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'sql' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'fingerprint' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'blacklist' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'exclusion_interval' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'window' IN ('matched','not_matched','not_evaluated'),false)),
    CHECK (group_id IS NULL OR (fingerprint_id IS NOT NULL AND fingerprint_value IS NOT NULL)),
    CHECK (count_scope <> 'group' OR group_id IS NOT NULL),
    CHECK (state <> 'included' OR (in_window IS TRUE AND count_scope = 'group')),
    CHECK ((state = 'outside_window') = (in_window IS FALSE)),
    CHECK ((state = 'outside_window') = (count_scope = 'none'))
);
CREATE INDEX IF NOT EXISTS mpp_decision_group_idx ON mpp_decision (build_id, group_id);
CREATE TABLE IF NOT EXISTS mpp_decision_reason (
    decision_id text NOT NULL REFERENCES mpp_decision,
    code text NOT NULL CHECK (code IN ('execution_failed','execution_cancelled','execution_timed_out','outcome_unknown','duration_unknown','association_unreliable','timing_unknown','sql_missing','sql_incomplete','sql_encoding_invalid','sql_uncertain','fingerprint_failed','identity_missing','start_unknown','blacklist_category','blacklist_template','excluded_interval','outside_window')),
    rule_ref text NOT NULL CHECK (rule_ref <> ''),
    PRIMARY KEY (decision_id, code)
);
CREATE TABLE IF NOT EXISTS mpp_decision_reason_evidence (
    decision_id text NOT NULL,
    code text NOT NULL,
    record_id text NOT NULL REFERENCES evidence_record,
    PRIMARY KEY (decision_id, code, record_id),
    FOREIGN KEY (decision_id, code) REFERENCES mpp_decision_reason
);
CREATE TABLE IF NOT EXISTS problem (
    problem_id text PRIMARY KEY CHECK (problem_id <> ''),
    level text NOT NULL CHECK (level IN ('record','file','batch','build')),
    batch_id text REFERENCES import_batch,
    file_id text REFERENCES source_file,
    build_id text REFERENCES build,
    analysis_id text,
    occurrence_id text,
    code text NOT NULL CHECK (code <> ''),
    reason text NOT NULL CHECK (reason <> ''),
    effect text NOT NULL CHECK (effect IN ('isolate_record','fail_file','hold_batch','block_publication','informational')),
    count_unit text NOT NULL CHECK (count_unit IN ('log_record','file','problem')),
    count bigint NOT NULL CHECK (count >= 0),
    resolution text NOT NULL CHECK (resolution IN ('open','isolated','resolved')),
    resolution_evidence text CHECK (resolution_evidence <> ''),
    FOREIGN KEY (analysis_id, occurrence_id) REFERENCES mpp_occurrence MATCH FULL,
    CHECK ((level = 'record' AND batch_id IS NOT NULL AND file_id IS NOT NULL)
        OR (level = 'file' AND file_id IS NOT NULL)
        OR (level = 'batch' AND batch_id IS NOT NULL)
        OR (level = 'build' AND build_id IS NOT NULL)),
    CHECK (resolution <> 'isolated' OR level = 'record'),
    CHECK (resolution <> 'resolved' OR resolution_evidence IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS problem_evidence (
    problem_id text NOT NULL REFERENCES problem,
    record_id text NOT NULL REFERENCES evidence_record,
    PRIMARY KEY (problem_id, record_id)
);
CREATE TABLE IF NOT EXISTS attempt_problem (
    attempt_id text NOT NULL REFERENCES import_attempt,
    problem_id text NOT NULL REFERENCES problem,
    PRIMARY KEY (attempt_id, problem_id)
);
CREATE TABLE IF NOT EXISTS build_check (
    build_id text NOT NULL REFERENCES build,
    name text NOT NULL CHECK (name IN ('batch_complete','rules_consistent','results_complete','results_saved','counts_consistent','values_consistent')),
    state text NOT NULL CHECK (state IN ('passed','failed','not_run')),
    reason text CHECK (reason <> ''),
    PRIMARY KEY (build_id, name),
    CHECK ((state = 'passed') = (reason IS NULL))
);
CREATE TABLE IF NOT EXISTS mpp_build_timing_coverage (
    build_id text NOT NULL REFERENCES build,
    timing_type text NOT NULL CHECK (timing_type IN ('request','execute_first','execute_fetch','parse','bind')),
    included_count bigint NOT NULL CHECK (included_count >= 0),
    excluded_count bigint NOT NULL CHECK (excluded_count >= 0),
    PRIMARY KEY (build_id, timing_type)
);
CREATE TABLE IF NOT EXISTS mpp_build_group (
    partition_id bigint NOT NULL,
    build_id text NOT NULL,
    group_id text NOT NULL REFERENCES mpp_baseline_group,
    PRIMARY KEY (partition_id, build_id, group_id),
    FOREIGN KEY (partition_id, build_id) REFERENCES build (partition_id, build_id)
);
CREATE OR REPLACE FUNCTION mpp_check_build_groups() RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF EXISTS (SELECT FROM added_groups a JOIN mpp_result_partition p USING(partition_id)
        WHERE p.cleaned_at IS NOT NULL) THEN RAISE EXCEPTION 'results_cleaned'; END IF;
    IF EXISTS (SELECT FROM added_groups a JOIN build b USING(build_id)
        JOIN mpp_baseline_group g USING(group_id)
        WHERE (b.scope_id,b.normalization_id,b.profile) IS DISTINCT FROM
              (g.scope_id,g.normalization_id,g.profile)) THEN
        RAISE EXCEPTION 'build_group_context_mismatch';
    END IF;
    RETURN NULL;
END $function$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_build_group'::regclass AND tgname='mpp_build_group_context') THEN
        CREATE TRIGGER mpp_build_group_context AFTER INSERT ON mpp_build_group
            REFERENCING NEW TABLE AS added_groups FOR EACH STATEMENT EXECUTE FUNCTION mpp_check_build_groups();
        CREATE TRIGGER mpp_build_group_context_update AFTER UPDATE ON mpp_build_group
            REFERENCING NEW TABLE AS added_groups FOR EACH STATEMENT EXECUTE FUNCTION mpp_check_build_groups();
    END IF;
END $block$;
CREATE TABLE IF NOT EXISTS mpp_statistic (
    build_id text NOT NULL,
    group_id text NOT NULL,
    partition_id bigint NOT NULL,
    layer text NOT NULL CHECK (layer IN ('overall','day','week','weekday','hour')),
    bucket_date date,
    bucket_number smallint,
    range_start timestamptz NOT NULL,
    range_end timestamptz NOT NULL,
    partial_week boolean,
    included_count bigint NOT NULL CHECK (included_count >= 0),
    excluded_count bigint NOT NULL CHECK (excluded_count >= 0),
    exclusions_by_reason jsonb NOT NULL CHECK (jsonb_typeof(exclusions_by_reason) = 'object'),
    active_dates date[] NOT NULL,
    active_week_starts date[] NOT NULL,
    first_sample_at timestamptz,
    last_sample_at timestamptz,
    min_ms numeric CHECK (min_ms >= 0 AND min_ms NOT IN ('NaN','Infinity','-Infinity')),
    max_ms numeric CHECK (max_ms >= 0 AND max_ms NOT IN ('NaN','Infinity','-Infinity')),
    mean_ms numeric CHECK (mean_ms >= 0 AND mean_ms NOT IN ('NaN','Infinity','-Infinity')),
    p25_ms numeric CHECK (p25_ms >= 0 AND p25_ms NOT IN ('NaN','Infinity','-Infinity')),
    p50_ms numeric CHECK (p50_ms >= 0 AND p50_ms NOT IN ('NaN','Infinity','-Infinity')),
    p75_ms numeric CHECK (p75_ms >= 0 AND p75_ms NOT IN ('NaN','Infinity','-Infinity')),
    p90_ms numeric CHECK (p90_ms >= 0 AND p90_ms NOT IN ('NaN','Infinity','-Infinity')),
    p95_ms numeric CHECK (p95_ms >= 0 AND p95_ms NOT IN ('NaN','Infinity','-Infinity')),
    p99_ms numeric CHECK (p99_ms >= 0 AND p99_ms NOT IN ('NaN','Infinity','-Infinity')),
    stddev_ms numeric CHECK (stddev_ms >= 0 AND stddev_ms NOT IN ('NaN','Infinity','-Infinity')),
    cv numeric CHECK (cv >= 0 AND cv NOT IN ('NaN','Infinity','-Infinity')),
    mad_ms numeric CHECK (mad_ms >= 0 AND mad_ms NOT IN ('NaN','Infinity','-Infinity')),
    iqr_ms numeric CHECK (iqr_ms >= 0 AND iqr_ms NOT IN ('NaN','Infinity','-Infinity')),
    log_median numeric CHECK (log_median >= 0 AND log_median NOT IN ('NaN','Infinity','-Infinity')),
    log_mad numeric CHECK (log_mad >= 0 AND log_mad NOT IN ('NaN','Infinity','-Infinity')),
    p95_p50 numeric CHECK (p95_p50 >= 0 AND p95_p50 NOT IN ('NaN','Infinity','-Infinity')),
    p99_p50 numeric CHECK (p99_p50 >= 0 AND p99_p50 NOT IN ('NaN','Infinity','-Infinity')),
    metric_null_reasons jsonb NOT NULL CHECK (jsonb_typeof(metric_null_reasons) = 'object'),
    FOREIGN KEY (partition_id, build_id, group_id) REFERENCES mpp_build_group (partition_id, build_id, group_id),
    UNIQUE NULLS NOT DISTINCT (partition_id, build_id, group_id, layer, bucket_date, bucket_number),
    CHECK (range_start < range_end),
    CHECK (
        (layer = 'overall' AND bucket_date IS NULL AND bucket_number IS NULL)
        OR (layer = 'day' AND bucket_date IS NOT NULL AND bucket_number IS NULL)
        OR (layer = 'week' AND bucket_date IS NOT NULL AND extract(isodow FROM bucket_date) = 1 AND bucket_number IS NULL)
        OR (layer = 'weekday' AND bucket_date IS NULL AND bucket_number IS NOT NULL AND bucket_number BETWEEN 1 AND 7)
        OR (layer = 'hour' AND bucket_date IS NULL AND bucket_number IS NOT NULL AND bucket_number BETWEEN 0 AND 23)),
    CHECK ((layer = 'week') = (partial_week IS NOT NULL)),
    CHECK ((included_count = 0 AND first_sample_at IS NULL AND last_sample_at IS NULL
            AND cardinality(active_dates) = 0 AND cardinality(active_week_starts) = 0)
        OR (included_count > 0 AND first_sample_at IS NOT NULL AND last_sample_at IS NOT NULL
            AND cardinality(active_dates) > 0 AND cardinality(active_week_starts) > 0)),
    CHECK (first_sample_at <= last_sample_at),
    CHECK (array_position(active_dates,NULL) IS NULL AND array_position(active_week_starts,NULL) IS NULL),
    CHECK ((min_ms IS NOT NULL AND NOT metric_null_reasons ? 'min_ms' AND included_count > 0)
        OR (min_ms IS NULL AND coalesce(metric_null_reasons->>'min_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'min_ms' = 'no_samples')))),
    CHECK ((max_ms IS NOT NULL AND NOT metric_null_reasons ? 'max_ms' AND included_count > 0)
        OR (max_ms IS NULL AND coalesce(metric_null_reasons->>'max_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'max_ms' = 'no_samples')))),
    CHECK ((mean_ms IS NOT NULL AND NOT metric_null_reasons ? 'mean_ms' AND included_count > 0)
        OR (mean_ms IS NULL AND coalesce(metric_null_reasons->>'mean_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'mean_ms' = 'no_samples')))),
    CHECK ((p25_ms IS NOT NULL AND NOT metric_null_reasons ? 'p25_ms' AND included_count > 0)
        OR (p25_ms IS NULL AND coalesce(metric_null_reasons->>'p25_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p25_ms' = 'no_samples')))),
    CHECK ((p50_ms IS NOT NULL AND NOT metric_null_reasons ? 'p50_ms' AND included_count > 0)
        OR (p50_ms IS NULL AND coalesce(metric_null_reasons->>'p50_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p50_ms' = 'no_samples')))),
    CHECK ((p75_ms IS NOT NULL AND NOT metric_null_reasons ? 'p75_ms' AND included_count > 0)
        OR (p75_ms IS NULL AND coalesce(metric_null_reasons->>'p75_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p75_ms' = 'no_samples')))),
    CHECK ((p90_ms IS NOT NULL AND NOT metric_null_reasons ? 'p90_ms' AND included_count > 0)
        OR (p90_ms IS NULL AND coalesce(metric_null_reasons->>'p90_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p90_ms' = 'no_samples')))),
    CHECK ((p95_ms IS NOT NULL AND NOT metric_null_reasons ? 'p95_ms' AND included_count > 0)
        OR (p95_ms IS NULL AND coalesce(metric_null_reasons->>'p95_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p95_ms' = 'no_samples')))),
    CHECK ((p99_ms IS NOT NULL AND NOT metric_null_reasons ? 'p99_ms' AND included_count > 0)
        OR (p99_ms IS NULL AND coalesce(metric_null_reasons->>'p99_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p99_ms' = 'no_samples')))),
    CHECK ((stddev_ms IS NOT NULL AND NOT metric_null_reasons ? 'stddev_ms' AND included_count > 0)
        OR (stddev_ms IS NULL AND coalesce(metric_null_reasons->>'stddev_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'stddev_ms' = 'no_samples')))),
    CHECK ((cv IS NOT NULL AND NOT metric_null_reasons ? 'cv' AND included_count > 0)
        OR (cv IS NULL AND coalesce(metric_null_reasons->>'cv' IN ('no_samples','zero_denominator'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'cv' = 'no_samples')))),
    CHECK ((mad_ms IS NOT NULL AND NOT metric_null_reasons ? 'mad_ms' AND included_count > 0)
        OR (mad_ms IS NULL AND coalesce(metric_null_reasons->>'mad_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'mad_ms' = 'no_samples')))),
    CHECK ((iqr_ms IS NOT NULL AND NOT metric_null_reasons ? 'iqr_ms' AND included_count > 0)
        OR (iqr_ms IS NULL AND coalesce(metric_null_reasons->>'iqr_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'iqr_ms' = 'no_samples')))),
    CHECK ((log_median IS NOT NULL AND NOT metric_null_reasons ? 'log_median' AND included_count > 0)
        OR (log_median IS NULL AND coalesce(metric_null_reasons->>'log_median' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'log_median' = 'no_samples')))),
    CHECK ((log_mad IS NOT NULL AND NOT metric_null_reasons ? 'log_mad' AND included_count > 0)
        OR (log_mad IS NULL AND coalesce(metric_null_reasons->>'log_mad' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'log_mad' = 'no_samples')))),
    CHECK ((p95_p50 IS NOT NULL AND NOT metric_null_reasons ? 'p95_p50' AND included_count > 0)
        OR (p95_p50 IS NULL AND coalesce(metric_null_reasons->>'p95_p50' IN ('no_samples','zero_denominator'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p95_p50' = 'no_samples')))),
    CHECK ((p99_p50 IS NOT NULL AND NOT metric_null_reasons ? 'p99_p50' AND included_count > 0)
        OR (p99_p50 IS NULL AND coalesce(metric_null_reasons->>'p99_p50' IN ('no_samples','zero_denominator'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p99_p50' = 'no_samples')))),
    CHECK (min_ms <= p25_ms AND p25_ms <= p50_ms AND p50_ms <= p75_ms
        AND p75_ms <= p90_ms AND p90_ms <= p95_ms AND p95_ms <= p99_ms AND p99_ms <= max_ms),
    CHECK (included_count = 0 OR ((mean_ms = 0) = (cv IS NULL))),
    CHECK (included_count = 0 OR ((p50_ms = 0) = (p95_p50 IS NULL))),
    CHECK (included_count = 0 OR ((p50_ms = 0) = (p99_p50 IS NULL))),
    CHECK (cardinality(active_dates) <= included_count AND cardinality(active_week_starts) <= included_count),
    CHECK (mean_ms BETWEEN min_ms AND max_ms)
) PARTITION BY LIST (partition_id);
CREATE INDEX IF NOT EXISTS mpp_statistic_group_build_idx ON mpp_statistic (group_id, build_id, layer);
-- Pure projection: callers join the statistic's Build to its sealed ConfigSnapshot.
-- Keep the version branch when a future statistics contract is added.
CREATE OR REPLACE FUNCTION mpp_statistic_sufficiency(
    p_statistics_version text, p_thresholds jsonb, p_layer text,
    p_included_count bigint, p_active_dates date[], p_active_week_starts date[]
) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $function$
DECLARE
    threshold jsonb;
    coverage_kind text;
    required_coverage numeric;
    actual_coverage integer;
    required_count numeric;
    label text;
    reasons jsonb;
    result jsonb := '{}';
BEGIN
    IF p_statistics_version IS DISTINCT FROM 'baseline-formulas/1' THEN
        RAISE EXCEPTION 'unsupported_statistics_version';
    END IF;
    IF p_layer IS NULL OR p_layer NOT IN ('overall','day','week','weekday','hour')
        OR p_included_count IS NULL OR p_included_count < 0
        OR p_active_dates IS NULL OR p_active_week_starts IS NULL
        OR array_position(p_active_dates,NULL) IS NOT NULL
        OR array_position(p_active_week_starts,NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'invalid_statistic_coverage';
    END IF;
    threshold := p_thresholds->p_layer;
    coverage_kind := CASE p_layer WHEN 'day' THEN 'none'
        WHEN 'weekday' THEN 'active_weeks' ELSE 'active_days' END;
    IF jsonb_typeof(threshold) IS DISTINCT FROM 'object'
        OR (threshold->>'coverage_kind') IS DISTINCT FROM coverage_kind
        OR jsonb_typeof(threshold->'coverage_min') IS DISTINCT FROM 'number' THEN
        RAISE EXCEPTION 'invalid_statistics_thresholds';
    END IF;
    required_coverage := (threshold->>'coverage_min')::numeric;
    IF required_coverage < 0 OR mod(required_coverage,1) <> 0
        OR (coverage_kind='none' AND required_coverage<>0) THEN
        RAISE EXCEPTION 'invalid_statistics_thresholds';
    END IF;
    actual_coverage := CASE coverage_kind WHEN 'none' THEN 0
        WHEN 'active_days' THEN cardinality(p_active_dates)
        ELSE cardinality(p_active_week_starts) END;
    FOREACH label IN ARRAY ARRAY['basic','p95','p99'] LOOP
        IF jsonb_typeof(threshold->(label||'_count')) IS DISTINCT FROM 'number' THEN
            RAISE EXCEPTION 'invalid_statistics_thresholds';
        END IF;
        required_count := (threshold->>(label||'_count'))::numeric;
        IF required_count < 0 OR mod(required_count,1) <> 0 THEN
            RAISE EXCEPTION 'invalid_statistics_thresholds';
        END IF;
        reasons := '[]';
        IF p_included_count < required_count THEN
            reasons := reasons || jsonb_build_array('sample_count_below_min');
        END IF;
        IF actual_coverage < required_coverage THEN
            reasons := reasons || jsonb_build_array(coverage_kind||'_below_min');
        END IF;
        result := result || jsonb_build_object(label,jsonb_build_object(
            'required_count',required_count,'actual_count',p_included_count,
            'coverage_kind',coverage_kind,'required_coverage',required_coverage,
            'actual_coverage',actual_coverage,'met',reasons='[]'::jsonb,'reasons',reasons));
    END LOOP;
    RETURN result;
END;
$function$;

CREATE OR REPLACE FUNCTION mpp_result_context_guard() RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF TG_TABLE_NAME = 'build' THEN
        IF (TG_OP = 'INSERT' OR NEW.partition_id IS DISTINCT FROM OLD.partition_id)
            AND EXISTS (SELECT FROM mpp_result_partition WHERE partition_id=NEW.partition_id
                AND cleaned_at IS NOT NULL) THEN RAISE EXCEPTION 'results_cleaned'; END IF;
        IF NEW.partition_id IS NOT NULL AND NOT EXISTS (
            SELECT FROM mpp_result_partition WHERE partition_id=NEW.partition_id
                AND scope_id=NEW.scope_id
                AND build_month=date_trunc('month',NEW.started_at AT TIME ZONE 'Asia/Shanghai')::date) THEN
            RAISE EXCEPTION 'build_partition_month_mismatch';
        END IF;
        IF TG_OP = 'UPDATE' AND (NEW.scope_id,NEW.normalization_id,NEW.profile) IS DISTINCT FROM
            (OLD.scope_id,OLD.normalization_id,OLD.profile)
            AND EXISTS (SELECT FROM mpp_build_group WHERE build_id=OLD.build_id) THEN
            RAISE EXCEPTION 'build_result_context_immutable';
        END IF;
    ELSIF (NEW.scope_id,NEW.normalization_id,NEW.profile) IS DISTINCT FROM
            (OLD.scope_id,OLD.normalization_id,OLD.profile)
            AND EXISTS (SELECT FROM mpp_build_group WHERE group_id=OLD.group_id) THEN
        RAISE EXCEPTION 'group_result_context_immutable';
    END IF;
    RETURN NEW;
END $function$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='build'::regclass AND tgname='mpp_result_context') THEN
        CREATE TRIGGER mpp_result_context BEFORE INSERT OR UPDATE ON build
            FOR EACH ROW EXECUTE FUNCTION mpp_result_context_guard();
        CREATE TRIGGER mpp_result_context BEFORE UPDATE ON mpp_baseline_group
            FOR EACH ROW EXECUTE FUNCTION mpp_result_context_guard();
    END IF;
END $block$;
CREATE OR REPLACE FUNCTION mpp_ensure_result_partition(selected_scope text, selected_month date)
RETURNS bigint LANGUAGE plpgsql AS $function$
DECLARE pid bigint; table_name text; child_name text;
BEGIN
    IF extract(day FROM selected_month) <> 1 THEN RAISE EXCEPTION 'invalid_build_month'; END IF;
    pid := ('x'||substr(encode(sha256(convert_to(jsonb_build_array(selected_scope,selected_month)::text,'UTF8')),'hex'),1,15))::bit(60)::bigint;
    -- A brief transaction lock only coordinates DDL for this cluster/month.
    PERFORM pg_advisory_xact_lock(pid);
    INSERT INTO mpp_result_partition (partition_id,scope_id,build_month) VALUES (pid,selected_scope,selected_month) ON CONFLICT DO NOTHING;
    IF NOT EXISTS (SELECT FROM mpp_result_partition WHERE partition_id=pid AND scope_id=selected_scope AND build_month=selected_month) THEN
        RAISE EXCEPTION 'partition_identity_collision';
    END IF;
    IF EXISTS (SELECT FROM mpp_result_partition WHERE partition_id=pid AND cleaned_at IS NOT NULL) THEN
        RAISE EXCEPTION 'results_cleaned';
    END IF;
    FOREACH table_name IN ARRAY ARRAY['mpp_statistic','mpp_observation_statistic'] LOOP
        child_name := table_name||'_p'||pid;
        IF to_regclass(child_name) IS NULL THEN
            EXECUTE format('CREATE TABLE %I (LIKE %I INCLUDING ALL)',child_name,table_name);
            EXECUTE format('ALTER TABLE %I ATTACH PARTITION %I FOR VALUES IN (%s)',table_name,child_name,pid);
        END IF;
    END LOOP;
    RETURN pid;
END $function$;

CREATE TABLE IF NOT EXISTS publication (
    publication_id text PRIMARY KEY CHECK (publication_id <> ''),
    scope_id text NOT NULL,
    build_id text NOT NULL,
    previous_build_id text,
    result text NOT NULL CHECK (result IN ('published','no_samples','check_failed','publish_failed')),
    reason text CHECK (reason <> ''),
    at timestamptz NOT NULL,
    FOREIGN KEY (build_id, scope_id) REFERENCES build (build_id, scope_id),
    FOREIGN KEY (previous_build_id, scope_id) REFERENCES build (build_id, scope_id),
    CHECK ((result = 'published') = (reason IS NULL)),
    UNIQUE (publication_id, scope_id),
    UNIQUE (publication_id, scope_id, build_id, result, at)
);
CREATE INDEX IF NOT EXISTS publication_scope_at_idx ON publication (scope_id, at);
CREATE TABLE IF NOT EXISTS current_version (
    scope_id text PRIMARY KEY REFERENCES scope,
    build_id text,
    last_success_at timestamptz,
    publication_id text,
    publication_result text NOT NULL DEFAULT 'published' CHECK (publication_result = 'published'),
    FOREIGN KEY (build_id, scope_id) REFERENCES build (build_id, scope_id),
    FOREIGN KEY (publication_id, scope_id, build_id, publication_result, last_success_at)
        REFERENCES publication (publication_id, scope_id, build_id, result, at),
    CHECK ((build_id IS NULL AND last_success_at IS NULL AND publication_id IS NULL)
        OR (build_id IS NOT NULL AND last_success_at IS NOT NULL AND publication_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS task (
    task_id text PRIMARY KEY CHECK (task_id <> ''),
    scope_id text NOT NULL REFERENCES scope,
    mode text NOT NULL CHECK (mode IN ('full','import_only','rebuild','snapshot','statistics','cleanup')),
    state text NOT NULL CHECK (state IN ('running','succeeded','failed','interrupted','busy_rejected')),
    stage text NOT NULL CHECK (stage IN ('import','snapshot','build','check','publish','none','cleanup')),
    busy_task_id text,
    reason text CHECK (reason <> ''),
    started_at timestamptz NOT NULL DEFAULT current_timestamp,
    finished_at timestamptz,
    stage_seconds jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(stage_seconds)='object'),
    UNIQUE (task_id, scope_id),
    FOREIGN KEY (busy_task_id, scope_id) REFERENCES task (task_id, scope_id),
    CHECK (busy_task_id IS DISTINCT FROM task_id),
    CHECK (busy_task_id IS NULL OR state = 'busy_rejected'),
    CHECK (state <> 'busy_rejected' OR stage = 'none'),
    CHECK (state NOT IN ('failed','interrupted','busy_rejected') OR reason IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS task_batch (
    task_id text NOT NULL,
    batch_id text NOT NULL,
    scope_id text NOT NULL,
    PRIMARY KEY (task_id, batch_id),
    FOREIGN KEY (task_id, scope_id) REFERENCES task (task_id, scope_id),
    FOREIGN KEY (batch_id, scope_id) REFERENCES import_batch (batch_id, scope_id)
);
CREATE TABLE IF NOT EXISTS task_build (
    task_id text NOT NULL,
    build_id text NOT NULL,
    scope_id text NOT NULL,
    PRIMARY KEY (task_id, build_id),
    FOREIGN KEY (task_id, scope_id) REFERENCES task (task_id, scope_id),
    FOREIGN KEY (build_id, scope_id) REFERENCES build (build_id, scope_id)
);
CREATE TABLE IF NOT EXISTS task_publication (
    task_id text NOT NULL,
    publication_id text NOT NULL,
    scope_id text NOT NULL,
    PRIMARY KEY (task_id, publication_id),
    FOREIGN KEY (task_id, scope_id) REFERENCES task (task_id, scope_id),
    FOREIGN KEY (publication_id, scope_id) REFERENCES publication (publication_id, scope_id)
);

-- Independent observation results; no FK from reliable groups or decisions.
CREATE TABLE IF NOT EXISTS mpp_approximate_rule (
    rule_id text PRIMARY KEY CHECK (rule_id <> ''),
    algorithm_version text NOT NULL CHECK (algorithm_version ~ '^sql-approximate/[1-9][0-9]*$'),
    profile text NOT NULL CHECK (profile = 'mpp-csv/1'),
    rules_digest text NOT NULL CHECK (rules_digest ~ '^[0-9a-f]{64}$'),
    rules_ref text NOT NULL CHECK (rules_ref <> ''),
    rules jsonb NOT NULL CHECK (jsonb_typeof(rules) = 'object'),
    UNIQUE (algorithm_version, profile, rules_digest),
    UNIQUE (rule_id, algorithm_version)
);
CREATE TABLE IF NOT EXISTS mpp_approximate_input (
    input_id text PRIMARY KEY CHECK (input_id <> ''),
    raw_bytes bytea NOT NULL,
    byte_length bigint NOT NULL CHECK (byte_length = octet_length(raw_bytes)),
    source_sha256 bytea NOT NULL CHECK (octet_length(source_sha256) = 32
        AND source_sha256 = sha256(raw_bytes))
);
-- Full byte equality, not a digest alone, decides reuse in the writer.
CREATE INDEX IF NOT EXISTS mpp_approximate_input_sha_idx ON mpp_approximate_input (source_sha256);
CREATE TABLE IF NOT EXISTS mpp_approximate_result (
    result_id text PRIMARY KEY CHECK (result_id <> ''),
    input_id text NOT NULL REFERENCES mpp_approximate_input,
    rule_id text NOT NULL,
    algorithm_version text NOT NULL,
    kind text NOT NULL CHECK (kind = 'approximate'),
    state text NOT NULL CHECK (state IN ('available','unavailable','failed')),
    value text,
    reason text CHECK (reason ~ '^[a-z_]{1,80}$'),
    structural_reason text NOT NULL CHECK (structural_reason ~ '^[a-z_]{1,80}$'),
    observation_only boolean NOT NULL CHECK (observation_only),
    completeness text NOT NULL CHECK (completeness = 'unverified'),
    source_bytes_included boolean NOT NULL,
    -- JSON text preserves escaped NUL/surrogateescape values rejected by JSONB.
    normalized text CHECK (normalized IS JSON OBJECT),
    diagnostics text[] NOT NULL CHECK (array_position(diagnostics,NULL) IS NULL),
    replacements integer NOT NULL CHECK (replacements >= 0),
    FOREIGN KEY (rule_id, algorithm_version) REFERENCES mpp_approximate_rule (rule_id, algorithm_version),
    UNIQUE (input_id, rule_id, structural_reason),
    UNIQUE (result_id, rule_id),
    UNIQUE (result_id, rule_id, value),
    CHECK ((state = 'available' AND value IS NOT NULL AND reason IS NULL
            AND normalized IS NOT NULL AND source_bytes_included
            AND starts_with(value, 'approx:' || algorithm_version || ':')
            AND substring(value FROM length('approx:' || algorithm_version || ':') + 1) ~ '^[0-9a-f]{64}$')
        OR (state IN ('unavailable','failed') AND value IS NULL AND reason IS NOT NULL
            AND normalized IS NULL AND replacements = 0))
);
CREATE INDEX IF NOT EXISTS mpp_approximate_result_value_idx ON mpp_approximate_result (rule_id, value);
CREATE TABLE IF NOT EXISTS mpp_approximate_evidence (
    result_id text NOT NULL REFERENCES mpp_approximate_result,
    record_id text NOT NULL REFERENCES evidence_record,
    PRIMARY KEY (result_id, record_id)
);
CREATE TABLE IF NOT EXISTS mpp_occurrence_approximate (
    analysis_id text NOT NULL,
    occurrence_id text NOT NULL,
    scope_id text NOT NULL,
    rule_id text NOT NULL,
    result_id text NOT NULL,
    record_id text NOT NULL,
    source_id text NOT NULL,
    PRIMARY KEY (analysis_id, occurrence_id, rule_id),
    FOREIGN KEY (analysis_id, occurrence_id, scope_id) REFERENCES mpp_occurrence (analysis_id, occurrence_id, scope_id),
    FOREIGN KEY (result_id, rule_id) REFERENCES mpp_approximate_result (result_id, rule_id),
    FOREIGN KEY (result_id, record_id) REFERENCES mpp_approximate_evidence (result_id, record_id),
    FOREIGN KEY (record_id, source_id, scope_id) REFERENCES evidence_record (record_id, source_id, scope_id)
);
CREATE INDEX IF NOT EXISTS mpp_occurrence_approximate_result_idx ON mpp_occurrence_approximate (result_id);

-- 1.3.0: immutable, original-text-level training cache; no per-build decisions.
CREATE TABLE IF NOT EXISTS mpp_training_rule (
    rule_id text PRIMARY KEY CHECK (rule_id <> ''),
    normalization_id text NOT NULL REFERENCES mpp_normalization,
    category_rules jsonb NOT NULL CHECK (jsonb_typeof(category_rules)='object'),
    template_rules jsonb NOT NULL CHECK (jsonb_typeof(template_rules)='array'),
    normalization_snapshot jsonb NOT NULL CHECK (jsonb_typeof(normalization_snapshot)='object'),
    UNIQUE (rule_id, normalization_id)
);
CREATE TABLE IF NOT EXISTS mpp_training_sql (
    sql_id text NOT NULL REFERENCES mpp_sql_text,
    rule_id text NOT NULL,
    normalization_id text NOT NULL,
    fingerprint_id text NOT NULL,
    category_kind text NOT NULL CHECK (category_kind IN ('pure','mixed','none','unknown')),
    categories text[] NOT NULL,
    template_ids text[] NOT NULL,
    PRIMARY KEY (sql_id, rule_id),
    FOREIGN KEY (rule_id, normalization_id) REFERENCES mpp_training_rule (rule_id, normalization_id),
    FOREIGN KEY (fingerprint_id, normalization_id, sql_id) REFERENCES mpp_fingerprint (fingerprint_id, normalization_id, sql_id)
);
CREATE TABLE IF NOT EXISTS training_config (
    config_id text PRIMARY KEY REFERENCES config_snapshot,
    rule_id text NOT NULL REFERENCES mpp_training_rule,
    decision_version text NOT NULL CHECK (decision_version <> ''),
    source_mappings jsonb NOT NULL CHECK (jsonb_typeof(source_mappings)='array')
);
CREATE TABLE IF NOT EXISTS input_file_analysis (
    input_id text NOT NULL,
    file_id text NOT NULL,
    analysis_id text NOT NULL,
    PRIMARY KEY (input_id, file_id),
    FOREIGN KEY (input_id, file_id) REFERENCES input_file,
    FOREIGN KEY (input_id, analysis_id) REFERENCES input_analysis,
    FOREIGN KEY (analysis_id, file_id) REFERENCES analysis_file
);
CREATE TABLE IF NOT EXISTS input_manifest (
    input_id text PRIMARY KEY REFERENCES input_snapshot,
    manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest)='object')
);

CREATE OR REPLACE FUNCTION training_immutable() RETURNS trigger LANGUAGE plpgsql AS $function$
DECLARE sealed boolean; ref text;
BEGIN
    IF TG_NARGS=0 THEN RAISE EXCEPTION 'immutable_training_data'; END IF;
    IF TG_ARGV[0]='input' THEN
        ref := CASE WHEN TG_OP='INSERT' THEN NEW.input_id ELSE OLD.input_id END;
        EXECUTE format('SELECT EXISTS (SELECT FROM %I.input_manifest WHERE input_id=$1)', TG_TABLE_SCHEMA)
            INTO sealed USING ref;
        IF TG_OP='UPDATE' AND NEW.input_id IS DISTINCT FROM OLD.input_id THEN
            RAISE EXCEPTION 'immutable_training_data';
        END IF;
    ELSE
        EXECUTE format('SELECT EXISTS (SELECT FROM %I.training_config WHERE config_id=$1)', TG_TABLE_SCHEMA)
            INTO sealed USING OLD.config_id;
    END IF;
    IF sealed THEN RAISE EXCEPTION 'immutable_training_data'; END IF;
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $function$;
DO $block$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['mpp_training_rule','mpp_training_sql','training_config','input_manifest'] LOOP
        IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid=t::regclass AND tgname='training_immutable') THEN
            EXECUTE format('CREATE TRIGGER training_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION training_immutable()',t);
        END IF;
    END LOOP;
    FOREACH t IN ARRAY ARRAY['input_snapshot','input_batch','input_file','input_analysis','mpp_input_occurrence','input_file_analysis'] LOOP
        IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid=t::regclass AND tgname='training_immutable') THEN
            EXECUTE format('CREATE TRIGGER training_immutable BEFORE INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION training_immutable(''input'')',t);
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='config_snapshot'::regclass AND tgname='training_immutable') THEN
        CREATE TRIGGER training_immutable BEFORE UPDATE OR DELETE ON config_snapshot
            FOR EACH ROW EXECUTE FUNCTION training_immutable('config');
    END IF;
END $block$;

CREATE OR REPLACE FUNCTION training_version(version text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $function$
BEGIN
    IF version IS DISTINCT FROM 'training-decision/1' THEN
        RAISE EXCEPTION 'unsupported_decision_version';
    END IF;
    RETURN true;
END $function$;

CREATE OR REPLACE FUNCTION training_cache_required() RETURNS boolean
LANGUAGE plpgsql STABLE AS $function$
BEGIN
    RAISE EXCEPTION 'training_cache_not_prepared';
END $function$;

-- Parsed SQL bodies bind table/function identities at installation, so custom
-- schemas work without relying on a caller-controlled search_path.
CREATE OR REPLACE FUNCTION mpp_training_decisions(selected_input text, selected_config text,
    selected_analysis text DEFAULT NULL, selected_occurrence text DEFAULT NULL)
RETURNS TABLE (analysis_id text, occurrence_id text, scope_id text, timing_type text,
    fingerprint_id text, group_id text, state text, in_window boolean, count_scope text,
    reason_codes text[], reasons jsonb, rule_evaluations jsonb,
    category_kind text, categories text[], template_ids text[], interval_ids text[],
    estimated_start_at timestamptz, duration_ms numeric)
LANGUAGE sql STABLE
BEGIN ATOMIC
WITH context AS MATERIALIZED (
    SELECT c.*,tc.rule_id,tc.decision_version,r.template_rules
    FROM config_snapshot c JOIN training_config tc USING(config_id)
    JOIN mpp_training_rule r USING(rule_id)
    JOIN input_snapshot i ON i.scope_id=c.scope_id AND i.input_id=selected_input
    JOIN input_manifest im ON im.input_id=i.input_id
    WHERE c.config_id=selected_config AND training_version(tc.decision_version)
), facts AS (
    SELECT o.*,c.config_id,c.rule_id,c.normalization_id,c.profile,
        s.category_kind,s.categories,f.fingerprint_id,f.value AS fingerprint_value,
        f.state AS fingerprint_state,
        o.estimated_start_at>=c.window_start AND o.estimated_start_at<c.window_end AS in_window,
        ARRAY(SELECT t->>'id' FROM jsonb_array_elements(c.template_rules) t
              WHERE t->>'id'=ANY(s.template_ids)
              AND (NOT t ? 'cluster' OR t->>'cluster'=o.scope_id)
              AND (NOT t ? 'database' OR t->>'database'=o.database)
              AND (NOT t ? 'execution_user' OR t->>'execution_user'=o.execution_user)
              ORDER BY t->>'id') AS templates,
        EXISTS(SELECT FROM jsonb_array_elements(c.template_rules) t
              WHERE t->>'id'=ANY(s.template_ids)
              AND (NOT t ? 'cluster' OR t->>'cluster'=o.scope_id)
              AND (NOT t ? 'database' OR o.database IS NULL OR t->>'database'=o.database)
              AND (NOT t ? 'execution_user' OR o.execution_user IS NULL OR t->>'execution_user'=o.execution_user)
              AND ((t ? 'database' AND o.database IS NULL) OR (t ? 'execution_user' AND o.execution_user IS NULL))) AS template_unknown,
        ARRAY(SELECT e->>'rule_id' FROM jsonb_array_elements(c.exclusions) e
              WHERE e->>'scope_id'=o.scope_id AND o.estimated_start_at IS NOT NULL
              AND CASE WHEN o.duration_ms=0 THEN
                  o.estimated_start_at>=(e->>'start')::timestamptz AND o.estimated_start_at<(e->>'end')::timestamptz
                  ELSE o.estimated_start_at<(e->>'end')::timestamptz AND o.end_at>(e->>'start')::timestamptz END
              ORDER BY e->>'rule_id') AS intervals
    FROM context c JOIN input_file_analysis m ON m.input_id=selected_input
    JOIN evidence_record e ON e.file_id=m.file_id
    JOIN mpp_occurrence o ON o.analysis_id=m.analysis_id AND o.anchor_ref=e.record_id
    LEFT JOIN mpp_training_sql s ON s.sql_id=o.sql_id AND s.rule_id=c.rule_id
    LEFT JOIN mpp_fingerprint f ON f.fingerprint_id=s.fingerprint_id
    WHERE (selected_analysis IS NULL OR o.analysis_id=selected_analysis)
      AND (selected_occurrence IS NULL OR o.occurrence_id=selected_occurrence)
      AND CASE WHEN o.sql_id IS NULL OR s.sql_id IS NOT NULL THEN true
               ELSE training_cache_required() END
), flags AS (
    SELECT facts.*,
        CASE WHEN database IS NOT NULL AND execution_user IS NOT NULL AND timing_type IS NOT NULL
                  AND fingerprint_state='reliable' AND association_state='reliable'
             THEN 'G:'||encode(sha256(convert_to(jsonb_build_array(scope_id,profile,normalization_id,
                  database,execution_user,fingerprint_value,timing_type)::text,'UTF8')),'hex') END AS group_key,
        array_remove(ARRAY[
            CASE outcome WHEN 'failed' THEN 'execution_failed' WHEN 'cancelled' THEN 'execution_cancelled'
                 WHEN 'timed_out' THEN 'execution_timed_out' WHEN 'unknown' THEN 'outcome_unknown' END,
            CASE WHEN duration_ms IS NULL THEN 'duration_unknown' END,
            CASE WHEN association_state<>'reliable' THEN 'association_unreliable' END,
            CASE WHEN timing_type IS NULL THEN 'timing_unknown' END,
            CASE sql_state WHEN 'missing' THEN 'sql_missing' WHEN 'incomplete' THEN 'sql_incomplete'
                 WHEN 'invalid_encoding' THEN 'sql_encoding_invalid' WHEN 'uncertain' THEN 'sql_uncertain' END,
            CASE WHEN sql_id IS NOT NULL AND fingerprint_state IS DISTINCT FROM 'reliable' THEN 'fingerprint_failed' END,
            CASE WHEN database IS NULL OR execution_user IS NULL THEN 'identity_missing' END,
            CASE WHEN estimated_start_at IS NULL THEN 'start_unknown' END,
            CASE WHEN category_kind='pure' THEN 'blacklist_category' END,
            CASE WHEN cardinality(templates)>0 THEN 'blacklist_template' END,
            CASE WHEN cardinality(intervals)>0 THEN 'excluded_interval' END,
            CASE WHEN in_window IS FALSE THEN 'outside_window' END
        ],NULL) AS codes
    FROM facts
), states AS (
    SELECT flags.*, CASE WHEN in_window IS FALSE THEN 'outside_window'
        WHEN codes && ARRAY['execution_failed','execution_cancelled','execution_timed_out',
                            'blacklist_category','blacklist_template','excluded_interval'] THEN 'excluded'
        WHEN cardinality(codes)>0 THEN 'unresolved' ELSE 'included' END AS decision_state
    FROM flags
)
SELECT analysis_id,occurrence_id,scope_id,timing_type,fingerprint_id,group_key,decision_state,in_window,
    CASE WHEN in_window IS FALSE THEN 'none'
         WHEN group_key IS NOT NULL AND estimated_start_at IS NOT NULL THEN 'group' ELSE 'batch' END,
    codes,
    (SELECT coalesce(jsonb_agg(jsonb_build_object('code',code,'rule_ref',
        CASE code WHEN 'blacklist_category' THEN rule_id||'/categories'
                  WHEN 'blacklist_template' THEN config_id||'/templates'
                  WHEN 'excluded_interval' THEN config_id||'/exclusions'
                  WHEN 'outside_window' THEN config_id||'/window'
                  ELSE 'training-decision/1/'||code END,
        'evidence_refs',jsonb_build_array(anchor_ref)) ORDER BY ord),'[]') FROM unnest(codes) WITH ORDINALITY AS r(code,ord)),
    jsonb_build_object(
        'outcome',CASE WHEN outcome='success' THEN 'not_matched' ELSE 'matched' END,
        'duration',CASE WHEN duration_ms IS NULL THEN 'matched' ELSE 'not_matched' END,
        'association',CASE WHEN association_state<>'reliable' OR timing_type IS NULL THEN 'matched' ELSE 'not_matched' END,
        'sql',CASE WHEN sql_state<>'complete' THEN 'matched' ELSE 'not_matched' END,
        'fingerprint',CASE WHEN sql_id IS NULL THEN 'not_evaluated' WHEN fingerprint_state='reliable' THEN 'not_matched' ELSE 'matched' END,
        'blacklist',CASE WHEN category_kind='pure' OR cardinality(templates)>0 THEN 'matched'
                        WHEN template_unknown OR category_kind IS NULL OR category_kind='unknown' OR fingerprint_state IS DISTINCT FROM 'reliable'
                        THEN 'not_evaluated' ELSE 'not_matched' END,
        'exclusion_interval',CASE WHEN estimated_start_at IS NULL OR duration_ms IS NULL OR end_at IS NULL THEN 'not_evaluated'
                                 WHEN cardinality(intervals)>0 THEN 'matched' ELSE 'not_matched' END,
        'window',CASE WHEN in_window IS NULL THEN 'not_evaluated' WHEN in_window THEN 'not_matched' ELSE 'matched' END),
    category_kind,categories,templates,intervals,estimated_start_at,duration_ms
FROM states;
END;


-- Observation identity references an available approximate result, never a reliable fingerprint.
CREATE TABLE IF NOT EXISTS mpp_observation_group (
    group_id text PRIMARY KEY CHECK (starts_with(group_id, 'OG:')),
    scope_id text NOT NULL,
    profile text NOT NULL CHECK (profile = 'mpp-csv/1'),
    database text NOT NULL CHECK (database <> ''),
    execution_user text NOT NULL CHECK (execution_user <> ''),
    rule_id text NOT NULL,
    result_id text NOT NULL,
    approximate_value text NOT NULL,
    timing_type text NOT NULL CHECK (timing_type IN ('request','execute_first','execute_fetch','parse','bind','unknown')),
    FOREIGN KEY (scope_id, profile) REFERENCES scope (scope_id, profile),
    FOREIGN KEY (result_id, rule_id, approximate_value) REFERENCES mpp_approximate_result (result_id, rule_id, value),
    UNIQUE (scope_id, profile, database, execution_user, rule_id, approximate_value, timing_type)
);
CREATE TABLE IF NOT EXISTS mpp_build_observation_group (
    partition_id bigint NOT NULL,
    build_id text NOT NULL,
    group_id text NOT NULL REFERENCES mpp_observation_group,
    PRIMARY KEY (partition_id, build_id, group_id),
    FOREIGN KEY (partition_id, build_id) REFERENCES build (partition_id, build_id)
);
CREATE OR REPLACE FUNCTION mpp_check_build_observation_groups() RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF EXISTS (SELECT FROM added_groups a JOIN mpp_result_partition p USING(partition_id)
        WHERE p.cleaned_at IS NOT NULL) THEN RAISE EXCEPTION 'results_cleaned'; END IF;
    IF EXISTS (SELECT FROM added_groups a JOIN build b USING(build_id)
        JOIN mpp_observation_group g USING(group_id)
        WHERE (b.scope_id,b.profile) IS DISTINCT FROM (g.scope_id,g.profile)) THEN
        RAISE EXCEPTION 'build_observation_group_context_mismatch';
    END IF;
    RETURN NULL;
END $function$;
CREATE OR REPLACE FUNCTION mpp_observation_context_guard() RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF TG_TABLE_NAME = 'build' THEN
        IF (NEW.scope_id,NEW.profile) IS DISTINCT FROM (OLD.scope_id,OLD.profile)
            AND EXISTS (SELECT FROM mpp_build_observation_group WHERE build_id=OLD.build_id) THEN
            RAISE EXCEPTION 'build_observation_context_immutable';
        END IF;
    ELSIF NEW IS DISTINCT FROM OLD
        AND EXISTS (SELECT FROM mpp_build_observation_group WHERE group_id=OLD.group_id) THEN
        RAISE EXCEPTION 'observation_group_context_immutable';
    END IF;
    RETURN NEW;
END $function$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_build_observation_group'::regclass AND tgname='mpp_observation_group_context') THEN
        CREATE TRIGGER mpp_observation_group_context AFTER INSERT ON mpp_build_observation_group
            REFERENCING NEW TABLE AS added_groups FOR EACH STATEMENT EXECUTE FUNCTION mpp_check_build_observation_groups();
        CREATE TRIGGER mpp_observation_group_context_update AFTER UPDATE ON mpp_build_observation_group
            REFERENCING NEW TABLE AS added_groups FOR EACH STATEMENT EXECUTE FUNCTION mpp_check_build_observation_groups();
        CREATE TRIGGER mpp_observation_context BEFORE UPDATE ON build
            FOR EACH ROW EXECUTE FUNCTION mpp_observation_context_guard();
        CREATE TRIGGER mpp_observation_context BEFORE UPDATE ON mpp_observation_group
            FOR EACH ROW EXECUTE FUNCTION mpp_observation_context_guard();
    END IF;
END $block$;
CREATE TABLE IF NOT EXISTS mpp_observation_statistic (
    build_id text NOT NULL,
    group_id text NOT NULL,
    partition_id bigint NOT NULL,
    layer text NOT NULL CHECK (layer IN ('overall','day','week','weekday','hour')),
    bucket_date date,
    bucket_number smallint,
    range_start timestamptz NOT NULL,
    range_end timestamptz NOT NULL,
    partial_week boolean,
    included_count bigint NOT NULL CHECK (included_count >= 0),
    excluded_count bigint NOT NULL CHECK (excluded_count >= 0),
    exclusions_by_reason jsonb NOT NULL CHECK (jsonb_typeof(exclusions_by_reason) = 'object'),
    active_dates date[] NOT NULL,
    active_week_starts date[] NOT NULL,
    first_sample_at timestamptz,
    last_sample_at timestamptz,
    min_ms numeric CHECK (min_ms >= 0 AND min_ms NOT IN ('NaN','Infinity','-Infinity')),
    max_ms numeric CHECK (max_ms >= 0 AND max_ms NOT IN ('NaN','Infinity','-Infinity')),
    mean_ms numeric CHECK (mean_ms >= 0 AND mean_ms NOT IN ('NaN','Infinity','-Infinity')),
    p25_ms numeric CHECK (p25_ms >= 0 AND p25_ms NOT IN ('NaN','Infinity','-Infinity')),
    p50_ms numeric CHECK (p50_ms >= 0 AND p50_ms NOT IN ('NaN','Infinity','-Infinity')),
    p75_ms numeric CHECK (p75_ms >= 0 AND p75_ms NOT IN ('NaN','Infinity','-Infinity')),
    p90_ms numeric CHECK (p90_ms >= 0 AND p90_ms NOT IN ('NaN','Infinity','-Infinity')),
    p95_ms numeric CHECK (p95_ms >= 0 AND p95_ms NOT IN ('NaN','Infinity','-Infinity')),
    p99_ms numeric CHECK (p99_ms >= 0 AND p99_ms NOT IN ('NaN','Infinity','-Infinity')),
    stddev_ms numeric CHECK (stddev_ms >= 0 AND stddev_ms NOT IN ('NaN','Infinity','-Infinity')),
    cv numeric CHECK (cv >= 0 AND cv NOT IN ('NaN','Infinity','-Infinity')),
    mad_ms numeric CHECK (mad_ms >= 0 AND mad_ms NOT IN ('NaN','Infinity','-Infinity')),
    iqr_ms numeric CHECK (iqr_ms >= 0 AND iqr_ms NOT IN ('NaN','Infinity','-Infinity')),
    log_median numeric CHECK (log_median >= 0 AND log_median NOT IN ('NaN','Infinity','-Infinity')),
    log_mad numeric CHECK (log_mad >= 0 AND log_mad NOT IN ('NaN','Infinity','-Infinity')),
    p95_p50 numeric CHECK (p95_p50 >= 0 AND p95_p50 NOT IN ('NaN','Infinity','-Infinity')),
    p99_p50 numeric CHECK (p99_p50 >= 0 AND p99_p50 NOT IN ('NaN','Infinity','-Infinity')),
    metric_null_reasons jsonb NOT NULL CHECK (jsonb_typeof(metric_null_reasons) = 'object'),
    FOREIGN KEY (partition_id, build_id, group_id) REFERENCES mpp_build_observation_group (partition_id, build_id, group_id),
    UNIQUE NULLS NOT DISTINCT (partition_id, build_id, group_id, layer, bucket_date, bucket_number),
    CHECK (range_start < range_end),
    CHECK (
        (layer = 'overall' AND bucket_date IS NULL AND bucket_number IS NULL)
        OR (layer = 'day' AND bucket_date IS NOT NULL AND bucket_number IS NULL)
        OR (layer = 'week' AND bucket_date IS NOT NULL AND extract(isodow FROM bucket_date) = 1 AND bucket_number IS NULL)
        OR (layer = 'weekday' AND bucket_date IS NULL AND bucket_number IS NOT NULL AND bucket_number BETWEEN 1 AND 7)
        OR (layer = 'hour' AND bucket_date IS NULL AND bucket_number IS NOT NULL AND bucket_number BETWEEN 0 AND 23)),
    CHECK ((layer = 'week') = (partial_week IS NOT NULL)),
    CHECK ((included_count = 0 AND first_sample_at IS NULL AND last_sample_at IS NULL
            AND cardinality(active_dates) = 0 AND cardinality(active_week_starts) = 0)
        OR (included_count > 0 AND first_sample_at IS NOT NULL AND last_sample_at IS NOT NULL
            AND cardinality(active_dates) > 0 AND cardinality(active_week_starts) > 0)),
    CHECK (first_sample_at <= last_sample_at),
    CHECK (array_position(active_dates,NULL) IS NULL AND array_position(active_week_starts,NULL) IS NULL),
    CHECK ((min_ms IS NOT NULL AND NOT metric_null_reasons ? 'min_ms' AND included_count > 0)
        OR (min_ms IS NULL AND coalesce(metric_null_reasons->>'min_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'min_ms' = 'no_samples')))),
    CHECK ((max_ms IS NOT NULL AND NOT metric_null_reasons ? 'max_ms' AND included_count > 0)
        OR (max_ms IS NULL AND coalesce(metric_null_reasons->>'max_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'max_ms' = 'no_samples')))),
    CHECK ((mean_ms IS NOT NULL AND NOT metric_null_reasons ? 'mean_ms' AND included_count > 0)
        OR (mean_ms IS NULL AND coalesce(metric_null_reasons->>'mean_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'mean_ms' = 'no_samples')))),
    CHECK ((p25_ms IS NOT NULL AND NOT metric_null_reasons ? 'p25_ms' AND included_count > 0)
        OR (p25_ms IS NULL AND coalesce(metric_null_reasons->>'p25_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p25_ms' = 'no_samples')))),
    CHECK ((p50_ms IS NOT NULL AND NOT metric_null_reasons ? 'p50_ms' AND included_count > 0)
        OR (p50_ms IS NULL AND coalesce(metric_null_reasons->>'p50_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p50_ms' = 'no_samples')))),
    CHECK ((p75_ms IS NOT NULL AND NOT metric_null_reasons ? 'p75_ms' AND included_count > 0)
        OR (p75_ms IS NULL AND coalesce(metric_null_reasons->>'p75_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p75_ms' = 'no_samples')))),
    CHECK ((p90_ms IS NOT NULL AND NOT metric_null_reasons ? 'p90_ms' AND included_count > 0)
        OR (p90_ms IS NULL AND coalesce(metric_null_reasons->>'p90_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p90_ms' = 'no_samples')))),
    CHECK ((p95_ms IS NOT NULL AND NOT metric_null_reasons ? 'p95_ms' AND included_count > 0)
        OR (p95_ms IS NULL AND coalesce(metric_null_reasons->>'p95_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p95_ms' = 'no_samples')))),
    CHECK ((p99_ms IS NOT NULL AND NOT metric_null_reasons ? 'p99_ms' AND included_count > 0)
        OR (p99_ms IS NULL AND coalesce(metric_null_reasons->>'p99_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p99_ms' = 'no_samples')))),
    CHECK ((stddev_ms IS NOT NULL AND NOT metric_null_reasons ? 'stddev_ms' AND included_count > 0)
        OR (stddev_ms IS NULL AND coalesce(metric_null_reasons->>'stddev_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'stddev_ms' = 'no_samples')))),
    CHECK ((cv IS NOT NULL AND NOT metric_null_reasons ? 'cv' AND included_count > 0)
        OR (cv IS NULL AND coalesce(metric_null_reasons->>'cv' IN ('no_samples','zero_denominator'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'cv' = 'no_samples')))),
    CHECK ((mad_ms IS NOT NULL AND NOT metric_null_reasons ? 'mad_ms' AND included_count > 0)
        OR (mad_ms IS NULL AND coalesce(metric_null_reasons->>'mad_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'mad_ms' = 'no_samples')))),
    CHECK ((iqr_ms IS NOT NULL AND NOT metric_null_reasons ? 'iqr_ms' AND included_count > 0)
        OR (iqr_ms IS NULL AND coalesce(metric_null_reasons->>'iqr_ms' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'iqr_ms' = 'no_samples')))),
    CHECK ((log_median IS NOT NULL AND NOT metric_null_reasons ? 'log_median' AND included_count > 0)
        OR (log_median IS NULL AND coalesce(metric_null_reasons->>'log_median' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'log_median' = 'no_samples')))),
    CHECK ((log_mad IS NOT NULL AND NOT metric_null_reasons ? 'log_mad' AND included_count > 0)
        OR (log_mad IS NULL AND coalesce(metric_null_reasons->>'log_mad' IN ('no_samples'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'log_mad' = 'no_samples')))),
    CHECK ((p95_p50 IS NOT NULL AND NOT metric_null_reasons ? 'p95_p50' AND included_count > 0)
        OR (p95_p50 IS NULL AND coalesce(metric_null_reasons->>'p95_p50' IN ('no_samples','zero_denominator'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p95_p50' = 'no_samples')))),
    CHECK ((p99_p50 IS NOT NULL AND NOT metric_null_reasons ? 'p99_p50' AND included_count > 0)
        OR (p99_p50 IS NULL AND coalesce(metric_null_reasons->>'p99_p50' IN ('no_samples','zero_denominator'), false)
            AND ((included_count = 0) = (metric_null_reasons->>'p99_p50' = 'no_samples')))),
    CHECK (min_ms <= p25_ms AND p25_ms <= p50_ms AND p50_ms <= p75_ms
        AND p75_ms <= p90_ms AND p90_ms <= p95_ms AND p95_ms <= p99_ms AND p99_ms <= max_ms),
    CHECK (included_count = 0 OR ((mean_ms = 0) = (cv IS NULL))),
    CHECK (included_count = 0 OR ((p50_ms = 0) = (p95_p50 IS NULL))),
    CHECK (included_count = 0 OR ((p50_ms = 0) = (p99_p50 IS NULL))),
    CHECK (cardinality(active_dates) <= included_count AND cardinality(active_week_starts) <= included_count),
    CHECK (mean_ms BETWEEN min_ms AND max_ms)
) PARTITION BY LIST (partition_id);
CREATE INDEX IF NOT EXISTS mpp_observation_statistic_group_build_idx ON mpp_observation_statistic (group_id, build_id, layer);

-- Ten compact receipts per build; observation receipts are informational only.
CREATE TABLE IF NOT EXISTS mpp_build_layer_count (
    build_id text NOT NULL REFERENCES build,
    kind text NOT NULL CHECK (kind IN ('formal','observation')),
    layer text NOT NULL CHECK (layer IN ('overall','day','week','weekday','hour')),
    row_count bigint NOT NULL CHECK (row_count>=0),
    group_count bigint NOT NULL CHECK (group_count>=0 AND group_count<=row_count),
    PRIMARY KEY (build_id,kind,layer)
);
CREATE INDEX IF NOT EXISTS task_scope_time_idx ON task (scope_id,started_at);
-- Retention status is physical metadata; build.results_saved remains historical.
CREATE OR REPLACE FUNCTION mpp_require_results(selected_build text)
RETURNS bigint LANGUAGE plpgsql AS $function$
DECLARE pid bigint; saved boolean; cleaned timestamptz;
BEGIN
    LOCK TABLE ONLY mpp_statistic,ONLY mpp_observation_statistic IN ACCESS SHARE MODE;
    SELECT b.partition_id,b.results_saved,p.cleaned_at INTO pid,saved,cleaned
        FROM build b LEFT JOIN mpp_result_partition p USING(partition_id) WHERE b.build_id=selected_build;
    IF saved AND cleaned IS NOT NULL THEN RAISE EXCEPTION 'results_cleaned'; END IF;
    RETURN pid;
END $function$;

CREATE OR REPLACE FUNCTION mpp_read_statistics(selected_build text, observation boolean DEFAULT false,
    selected_group text DEFAULT NULL)
RETURNS SETOF mpp_statistic LANGUAGE plpgsql AS $function$
DECLARE pid bigint;
BEGIN
    pid := mpp_require_results(selected_build);
    IF observation THEN
        RETURN QUERY SELECT * FROM mpp_observation_statistic WHERE partition_id=pid AND build_id=selected_build
            AND (selected_group IS NULL OR group_id=selected_group);
    ELSE
        RETURN QUERY SELECT * FROM mpp_statistic WHERE partition_id=pid AND build_id=selected_build
            AND (selected_group IS NULL OR group_id=selected_group);
    END IF;
END $function$;

CREATE TABLE IF NOT EXISTS mpp_cleanup_month (
    task_id text NOT NULL REFERENCES task,
    partition_id bigint NOT NULL REFERENCES mpp_result_partition,
    state text NOT NULL CHECK (state IN ('pending','removing_groups','succeeded','retained','protected','already_cleaned','lock_timeout','failed','interrupted')),
    reason text NOT NULL CHECK (reason <> ''),
    published_builds bigint NOT NULL CHECK (published_builds >= 0),
    unpublished_builds bigint NOT NULL CHECK (unpublished_builds >= 0),
    before_bytes bigint NOT NULL CHECK (before_bytes >= 0),
    after_bytes bigint NOT NULL CHECK (after_bytes >= 0),
    released_bytes bigint NOT NULL DEFAULT 0 CHECK (released_bytes >= 0),
    formal_groups_deleted bigint NOT NULL DEFAULT 0 CHECK (formal_groups_deleted >= 0),
    observation_groups_deleted bigint NOT NULL DEFAULT 0 CHECK (observation_groups_deleted >= 0),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    exclusive_seconds double precision NOT NULL DEFAULT 0 CHECK (exclusive_seconds >= 0),
    group_seconds double precision NOT NULL DEFAULT 0 CHECK (group_seconds >= 0),
    PRIMARY KEY (task_id,partition_id)
);
CREATE OR REPLACE FUNCTION mpp_coverage(selected_build text, observation boolean DEFAULT false,
    selected_group text DEFAULT NULL)
RETURNS TABLE (group_id text,layer text,computed_keys jsonb,empty_keys jsonb)
LANGUAGE plpgsql AS $function$
DECLARE group_table text; statistic_table text;
BEGIN
    PERFORM mpp_require_results(selected_build);
    group_table := CASE WHEN observation THEN 'mpp_build_observation_group' ELSE 'mpp_build_group' END;
    statistic_table := CASE WHEN observation THEN 'mpp_observation_statistic' ELSE 'mpp_statistic' END;
    RETURN QUERY EXECUTE format($query$
        WITH context AS MATERIALIZED (
            SELECT b.partition_id,c.window_start,c.window_end FROM build b
            JOIN config_snapshot c USING(config_id) WHERE b.build_id=$1),
        days AS (SELECT d::date AS sample_day FROM context CROSS JOIN LATERAL generate_series(
            window_start AT TIME ZONE 'Asia/Shanghai',
            (window_end AT TIME ZONE 'Asia/Shanghai')-interval '1 day',interval '1 day') d),
        keys AS MATERIALIZED (
            SELECT 'overall'::text layer,'null'::jsonb key
            UNION ALL SELECT 'day',to_jsonb(sample_day) FROM days
            UNION SELECT 'week',to_jsonb(date_trunc('week',sample_day)::date) FROM days
            UNION ALL SELECT 'weekday',to_jsonb(i) FROM generate_series(1,7) i
            UNION ALL SELECT 'hour',to_jsonb(i) FROM generate_series(0,23) i),
        expected AS (SELECT layer,jsonb_agg(key ORDER BY key) keys FROM keys GROUP BY layer),
        actual AS (
            SELECT s.group_id,s.layer,jsonb_agg(coalesce(to_jsonb(bucket_date),to_jsonb(bucket_number),'null'::jsonb)
                ORDER BY bucket_date,bucket_number) computed
            FROM %I s JOIN context c USING(partition_id)
            WHERE s.build_id=$1 AND ($2 IS NULL OR s.group_id=$2) GROUP BY s.group_id,s.layer)
        SELECT g.group_id,k.layer,coalesce(a.computed,'[]'::jsonb),
            coalesce((SELECT jsonb_agg(key ORDER BY key) FROM jsonb_array_elements(k.keys) key
                      WHERE NOT coalesce(a.computed,'[]'::jsonb) @> jsonb_build_array(key)),'[]'::jsonb)
        FROM %I g JOIN context c USING(partition_id) CROSS JOIN expected k
        LEFT JOIN actual a ON a.group_id=g.group_id AND a.layer=k.layer
        WHERE g.build_id=$1 AND ($2 IS NULL OR g.group_id=$2)
    $query$,statistic_table,group_table) USING selected_build,selected_group;
END $function$;

-- Search/query API v1. ASCII folding is independent of database collation.
CREATE OR REPLACE FUNCTION mpp_search_fold(value text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN translate(value,E'ABCDEFGHIJKLMNOPQRSTUVWXYZ \t\n\r\f\013','abcdefghijklmnopqrstuvwxyz');

CREATE OR REPLACE FUNCTION mpp_search_terms(value text) RETURNS text[]
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $function$
DECLARE terms text[]; unquoted text;
BEGIN
    -- One ordered regexp stream; string_agg builds its buffer without repeatedly
    -- copying an ever-growing token. Fold quoted chunks before splitting so their
    -- spaces never become separators; adjacent chunks still belong to one term.
    SELECT string_agg(CASE WHEN left(part[1],1)='"' AND length(part[1])>1
        THEN mpp_search_fold(substr(part[1],2,length(part[1])-2)) ELSE part[1] END,'')
        INTO unquoted FROM regexp_matches(value,'"[^"]*"|[^"]+|"','g') part;
    terms := ARRAY(SELECT mpp_search_fold(t)
        FROM regexp_split_to_table(unquoted,E'[ \t\n\r\f\013]+') t WHERE t<>'');
    IF cardinality(terms)=0 THEN RAISE EXCEPTION 'empty_search_input'; END IF;
    IF cardinality(terms)>20 THEN RAISE EXCEPTION 'too_many_search_terms'; END IF;
    RETURN terms;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_occurrences(
    p_normalization text, p_fingerprint text DEFAULT NULL, p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL, p_user text DEFAULT NULL,
    p_start timestamptz DEFAULT NULL, p_end timestamptz DEFAULT NULL)
RETURNS TABLE (analysis_id text, occurrence_id text, scope_id text, database text,
    execution_user text, fingerprint text, timing_type text, unit text, request_shape text,
    outcome text, end_at timestamptz, estimated_start_at timestamptz, duration_ms numeric,
    sql_id text, file_id text, record_no bigint, line_start bigint, line_end bigint)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT o.analysis_id,o.occurrence_id,o.scope_id,o.database,o.execution_user,f.value,
        o.timing_type,o.unit,o.request_shape,o.outcome,o.end_at,
        CASE WHEN o.timing_type IS NOT NULL THEN o.estimated_start_at END,
        CASE WHEN o.timing_type IS NOT NULL THEN o.duration_ms END,
        o.sql_id,e.file_id,e.record_no,e.line_start,e.line_end
    FROM mpp_fingerprint f JOIN mpp_occurrence o USING(sql_id)
    JOIN evidence_record e ON e.record_id=o.anchor_ref
    WHERE f.normalization_id=p_normalization AND f.profile='mpp-csv/1' AND f.state='reliable'
        AND (p_fingerprint IS NULL OR f.value=p_fingerprint)
        AND (p_scope IS NULL OR o.scope_id=p_scope)
        AND (p_database IS NULL OR o.database=p_database)
        AND (p_user IS NULL OR o.execution_user=p_user)
        AND (p_start IS NULL OR o.end_at>=p_start) AND (p_end IS NULL OR o.end_at<p_end);
END;

CREATE OR REPLACE FUNCTION mpp_query_versions(p_normalization text,p_scope text DEFAULT NULL)
RETURNS TABLE (scope_id text, build_id text, built_at timestamptz, published_at timestamptz,
    window_start timestamptz, window_end timestamptz, window_days integer,
    normalization_id text, algorithm_version text, parser_version text, dictionary_rules_version text,
    is_current boolean, results_cleaned boolean, cleaned_at timestamptz, rules_match boolean)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT b.scope_id,b.build_id,b.finished_at,max(p.at),c.window_start,c.window_end,c.window_days,
        b.normalization_id,n.algorithm_version,n.parser_version,n.dictionary_rules_version,
        coalesce(v.build_id=b.build_id,false),r.cleaned_at IS NOT NULL,r.cleaned_at,
        b.normalization_id=p_normalization
    FROM build b JOIN publication p ON p.build_id=b.build_id AND p.result='published'
    JOIN config_snapshot c USING(config_id) JOIN mpp_normalization n ON n.normalization_id=b.normalization_id
    LEFT JOIN current_version v ON v.scope_id=b.scope_id
    LEFT JOIN mpp_result_partition r ON r.partition_id=b.partition_id
    WHERE p_scope IS NULL OR b.scope_id=p_scope
    GROUP BY b.scope_id,b.build_id,c.config_id,n.normalization_id,v.build_id,r.partition_id;
END;

CREATE OR REPLACE FUNCTION mpp_query_hits(
    p_normalization text,p_fingerprint text,p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL,p_user text DEFAULT NULL)
RETURNS TABLE (scope_id text,database text,execution_user text,fingerprint text,
    record_count bigint,first_at timestamptz,last_at timestamptz,timing_types text[],
    has_unknown_timing boolean,has_baseline boolean,current_build text,current_rules_match boolean)
LANGUAGE plpgsql STABLE SET plan_cache_mode=force_custom_plan AS $function$
BEGIN
    RETURN QUERY
    WITH hits AS MATERIALIZED (
        SELECT o.scope_id,o.database,o.execution_user,count(*) records,min(o.end_at) first_at,
            max(o.end_at) last_at,array_remove(array_agg(DISTINCT o.timing_type ORDER BY o.timing_type),NULL) timings,
            bool_or(o.timing_type IS NULL) unknown_timing
        FROM mpp_fingerprint f JOIN mpp_occurrence o USING(sql_id)
        WHERE f.normalization_id=p_normalization AND f.profile='mpp-csv/1'
            AND f.state='reliable' AND f.value=p_fingerprint
            AND (p_scope IS NULL OR o.scope_id=p_scope)
            AND (p_database IS NULL OR o.database=p_database)
            AND (p_user IS NULL OR o.execution_user=p_user)
        GROUP BY o.scope_id,o.database,o.execution_user)
    SELECT h.scope_id,h.database,h.execution_user,p_fingerprint,h.records,h.first_at,h.last_at,
        h.timings,h.unknown_timing,EXISTS(
            SELECT FROM mpp_baseline_group g JOIN mpp_statistic s USING(group_id)
            WHERE g.scope_id=h.scope_id AND g.profile='mpp-csv/1'
                AND g.database=h.database AND g.execution_user=h.execution_user
                AND g.normalization_id=p_normalization AND g.fingerprint_value=p_fingerprint
                AND s.build_id=v.build_id AND s.layer='overall'),v.build_id,
        coalesce(b.normalization_id=p_normalization,false)
    FROM hits h LEFT JOIN current_version v ON v.scope_id=h.scope_id LEFT JOIN build b ON b.build_id=v.build_id
    ORDER BY h.records DESC,h.scope_id,h.database,h.execution_user;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_fuzzy(
    p_normalization text,p_input text,p_scope text DEFAULT NULL,p_database text DEFAULT NULL,
    p_user text DEFAULT NULL,p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL,
    p_order text DEFAULT 'count')
RETURNS TABLE (fingerprint text,example_sql_id text,matched_texts bigint,record_count bigint,
    scopes text[],databases text[],execution_users text[],last_at timestamptz,total_structures bigint)
LANGUAGE plpgsql STABLE SET plan_cache_mode=force_custom_plan AS $function$
DECLARE terms text[];
BEGIN
    terms:=mpp_search_terms(p_input);
    IF p_order IS NULL OR p_order NOT IN ('count','recent') THEN RAISE EXCEPTION 'invalid_search_order'; END IF;
    IF p_start>=p_end THEN RAISE EXCEPTION 'invalid_time_range'; END IF;
    RETURN QUERY
    WITH matched AS MATERIALIZED (
        SELECT t.sql_id,f.value FROM mpp_sql_text t JOIN mpp_fingerprint f USING(sql_id)
        WHERE f.normalization_id=p_normalization AND f.profile='mpp-csv/1' AND f.state='reliable'
          AND NOT EXISTS (SELECT FROM unnest(terms) term WHERE strpos(t.search_text,term)=0)
    ), grouped AS MATERIALIZED (
        SELECT m.value,min(m.sql_id) example_sql_id,count(DISTINCT m.sql_id) matched_texts,
            count(*) records,max(o.end_at) last_at,
            array_agg(DISTINCT o.scope_id ORDER BY o.scope_id) scopes,
            array_agg(DISTINCT o.database ORDER BY o.database) databases,
            array_agg(DISTINCT o.execution_user ORDER BY o.execution_user) users
        FROM matched m JOIN mpp_occurrence o ON o.sql_id=m.sql_id
        WHERE (p_scope IS NULL OR o.scope_id=p_scope)
            AND (p_database IS NULL OR o.database=p_database)
            AND (p_user IS NULL OR o.execution_user=p_user)
            AND (p_start IS NULL OR o.end_at>=p_start) AND (p_end IS NULL OR o.end_at<p_end)
        GROUP BY m.value
    )
    SELECT g.value,g.example_sql_id,g.matched_texts,g.records,g.scopes,g.databases,g.users,g.last_at,count(*) OVER ()
    FROM grouped g
    ORDER BY CASE WHEN p_order='count' THEN g.records END DESC,
        CASE WHEN p_order='recent' THEN g.last_at END DESC NULLS LAST,g.value
    LIMIT 50;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_missing_rules(p_normalization text) RETURNS bigint
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT count(*) FROM mpp_sql_text t WHERE NOT EXISTS (
        SELECT FROM mpp_fingerprint f WHERE f.sql_id=t.sql_id AND f.normalization_id=p_normalization
            AND f.profile='mpp-csv/1');
END;

CREATE OR REPLACE FUNCTION mpp_query_text(p_sql_id text) RETURNS TABLE(sql_id text,sql_text text)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT t.sql_id,t.text FROM mpp_sql_text t WHERE t.sql_id=p_sql_id;
END;

CREATE OR REPLACE FUNCTION mpp_query_select_version(p_normalization text,p_scope text,p_build text DEFAULT NULL)
RETURNS text LANGUAGE plpgsql AS $function$
DECLARE chosen record;
BEGIN
    SELECT * INTO chosen FROM mpp_query_versions(p_normalization,p_scope) v
        WHERE CASE WHEN p_build IS NULL THEN v.is_current ELSE v.build_id=p_build END;
    IF NOT FOUND THEN
        IF p_build IS NULL THEN RAISE EXCEPTION 'no_current_baseline';
        ELSE RAISE EXCEPTION 'published_version_not_found'; END IF;
    END IF;
    PERFORM mpp_require_results(chosen.build_id);
    IF NOT chosen.rules_match THEN RAISE EXCEPTION 'normalization_version_mismatch'; END IF;
    RETURN chosen.build_id;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_training(p_build text,p_records jsonb)
RETURNS TABLE(analysis_id text,occurrence_id text,decision text,reason_codes text[],detail jsonb)
LANGUAGE plpgsql STABLE AS $function$
DECLARE b record; r record;
BEGIN
    SELECT x.input_id,x.config_id,t.decision_version INTO b FROM build x
        LEFT JOIN training_config t USING(config_id) WHERE x.build_id=p_build;
    IF NOT FOUND THEN RAISE EXCEPTION 'published_version_not_found'; END IF;
    IF jsonb_typeof(p_records) IS DISTINCT FROM 'array' OR jsonb_array_length(p_records)>1000 THEN
        RAISE EXCEPTION 'invalid_training_records'; END IF;
    FOR r IN SELECT * FROM jsonb_to_recordset(p_records) AS x(analysis_id text,occurrence_id text) LOOP
        IF r.analysis_id IS NULL OR r.occurrence_id IS NULL THEN RAISE EXCEPTION 'invalid_training_records'; END IF;
        IF NOT EXISTS(SELECT FROM mpp_occurrence o WHERE o.analysis_id=r.analysis_id AND o.occurrence_id=r.occurrence_id) THEN
            RAISE EXCEPTION 'occurrence_not_found';
        END IF;
        IF NOT EXISTS(SELECT FROM mpp_occurrence o JOIN evidence_record e ON e.record_id=o.anchor_ref
            JOIN input_file_analysis i ON i.file_id=e.file_id AND i.analysis_id=o.analysis_id
            WHERE i.input_id=b.input_id AND o.analysis_id=r.analysis_id AND o.occurrence_id=r.occurrence_id) THEN
            RETURN QUERY SELECT r.analysis_id,r.occurrence_id,'not_in_version_input'::text,ARRAY[]::text[],'{}'::jsonb;
        ELSIF b.decision_version IS DISTINCT FROM 'training-decision/1' THEN
            RETURN QUERY SELECT r.analysis_id,r.occurrence_id,'decision_unavailable'::text,
                ARRAY['unsupported_decision_version'],jsonb_build_object('decision_version',b.decision_version);
        ELSE
            RETURN QUERY SELECT d.analysis_id,d.occurrence_id,d.state,d.reason_codes,
                to_jsonb(d)-ARRAY['analysis_id','occurrence_id','state','reason_codes']
                FROM mpp_training_decisions(b.input_id,b.config_id,r.analysis_id,r.occurrence_id) d;
        END IF;
    END LOOP;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_time_bounds(p_normalization text,p_fingerprint text,
    p_scope text,p_database text,p_user text,p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL)
RETURNS TABLE(start_at timestamptz,end_at timestamptz)
LANGUAGE plpgsql STABLE SET plan_cache_mode=force_custom_plan AS $function$
DECLARE latest timestamptz;
BEGIN
    IF p_end IS NOT NULL THEN
        RETURN QUERY SELECT coalesce(p_start,p_end-interval '7 days'),p_end;
        RETURN;
    END IF;
    SELECT max(o.end_at) INTO latest FROM mpp_fingerprint f JOIN mpp_occurrence o USING(sql_id)
    WHERE f.normalization_id=p_normalization AND f.profile='mpp-csv/1'
        AND f.state='reliable' AND f.value=p_fingerprint
        AND (p_scope IS NULL OR o.scope_id=p_scope)
        AND (p_database IS NULL OR o.database=p_database)
        AND (p_user IS NULL OR o.execution_user=p_user);
    RETURN QUERY SELECT coalesce(p_start,latest-interval '7 days'),latest+interval '1 microsecond';
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_timeline(p_normalization text,p_fingerprint text,
    p_scope text,p_database text,p_user text,p_start timestamptz,p_end timestamptz,p_bucket text DEFAULT 'hour')
RETURNS TABLE(timing_type text,bucket_at timestamptz,record_count bigint,status_counts jsonb,
    known_duration_count bigint,p50_ms double precision,p95_ms double precision,max_ms numeric)
LANGUAGE plpgsql STABLE SET plan_cache_mode=force_custom_plan AS $function$
BEGIN
    IF p_bucket IS NULL OR p_bucket NOT IN ('hour','day') THEN RAISE EXCEPTION 'invalid_time_bucket'; END IF;
    IF p_start>=p_end THEN RAISE EXCEPTION 'invalid_time_range'; END IF;
    RETURN QUERY WITH facts AS MATERIALIZED (
        SELECT o.timing_type,date_trunc(p_bucket,o.end_at AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai' bucket_at,
            o.outcome,o.duration_ms
        FROM mpp_query_occurrences(p_normalization,p_fingerprint,p_scope,p_database,p_user,p_start,p_end) o
    ), counts AS (
        SELECT f.timing_type,f.bucket_at,jsonb_object_agg(f.outcome,f.n) status_counts
        FROM (SELECT a.timing_type,a.bucket_at,a.outcome,count(*) n FROM facts a
            GROUP BY a.timing_type,a.bucket_at,a.outcome) f GROUP BY f.timing_type,f.bucket_at
    ), metrics AS (
        SELECT a.timing_type,a.bucket_at,count(*) n,count(a.duration_ms) known,
            percentile_cont(0.5) WITHIN GROUP(ORDER BY a.duration_ms::double precision) p50,
            percentile_cont(0.95) WITHIN GROUP(ORDER BY a.duration_ms::double precision) p95,max(a.duration_ms) maximum
        FROM facts a GROUP BY a.timing_type,a.bucket_at
    ) SELECT m.timing_type,m.bucket_at,m.n,c.status_counts,m.known,m.p50,m.p95,m.maximum
        FROM metrics m JOIN counts c ON c.timing_type IS NOT DISTINCT FROM m.timing_type AND c.bucket_at IS NOT DISTINCT FROM m.bucket_at
        ORDER BY m.bucket_at,m.timing_type NULLS LAST;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_history(p_normalization text,p_fingerprint text,
    p_scope text,p_database text,p_user text,p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL,
    p_limit integer DEFAULT 100,p_cursor jsonb DEFAULT NULL,p_build text DEFAULT NULL,p_bucket text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SET plan_cache_mode=force_custom_plan AS $function$
DECLARE bounds record; chosen text; rows jsonb; page jsonb; cursor_value jsonb; missing bigint;
BEGIN
    IF p_limit IS NULL OR p_limit<1 OR p_limit>1000 THEN RAISE EXCEPTION 'invalid_page_size'; END IF;
    IF p_start>=p_end THEN RAISE EXCEPTION 'invalid_time_range'; END IF;
    IF p_bucket IS NOT NULL AND p_bucket NOT IN ('hour','day') THEN RAISE EXCEPTION 'invalid_time_bucket'; END IF;
    IF p_cursor IS NOT NULL AND (jsonb_typeof(p_cursor) IS DISTINCT FROM 'object'
        OR NOT p_cursor ?& ARRAY['end_at','analysis_id','occurrence_id']) THEN RAISE EXCEPTION 'invalid_page_cursor'; END IF;
    SELECT * INTO bounds FROM mpp_query_time_bounds(p_normalization,p_fingerprint,p_scope,p_database,p_user,p_start,p_end);
    IF p_build IS NOT NULL THEN chosen:=mpp_query_select_version(p_normalization,p_scope,p_build); END IF;
    IF bounds.end_at IS NULL OR bounds.start_at>=bounds.end_at THEN
        page:='[]';
    ELSIF p_bucket IS NOT NULL THEN
        SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.bucket_at,t.timing_type),'[]') INTO page
            FROM mpp_query_timeline(p_normalization,p_fingerprint,p_scope,p_database,p_user,bounds.start_at,bounds.end_at,p_bucket) t;
    ELSE
        SELECT coalesce(jsonb_agg(to_jsonb(o) ORDER BY o.end_at DESC,o.analysis_id DESC,o.occurrence_id DESC),'[]') INTO rows
        FROM (SELECT * FROM mpp_query_occurrences(p_normalization,p_fingerprint,p_scope,p_database,p_user,bounds.start_at,bounds.end_at) x
            WHERE p_cursor IS NULL OR (x.end_at,x.analysis_id,x.occurrence_id)<
                ((p_cursor->>'end_at')::timestamptz,p_cursor->>'analysis_id',p_cursor->>'occurrence_id')
            ORDER BY x.end_at DESC,x.analysis_id DESC,x.occurrence_id DESC LIMIT p_limit+1) o;
        IF jsonb_array_length(rows)>p_limit THEN
            cursor_value:=jsonb_build_object('end_at',rows->(p_limit-1)->'end_at',
                'analysis_id',rows->(p_limit-1)->'analysis_id','occurrence_id',rows->(p_limit-1)->'occurrence_id');
        END IF;
        SELECT coalesce(jsonb_agg(x.value ORDER BY x.ordinality),'[]') INTO page
            FROM jsonb_array_elements(rows) WITH ORDINALITY x WHERE x.ordinality<=p_limit;
        IF chosen IS NOT NULL THEN
            SELECT coalesce(jsonb_agg(x.value||jsonb_build_object('training',to_jsonb(d)-ARRAY['analysis_id','occurrence_id']) ORDER BY x.ordinality),'[]') INTO page
            FROM jsonb_array_elements(page) WITH ORDINALITY x JOIN mpp_query_training(chosen,page) d
                ON d.analysis_id=x.value->>'analysis_id' AND d.occurrence_id=x.value->>'occurrence_id';
        END IF;
    END IF;
    missing:=mpp_query_missing_rules(p_normalization);
    RETURN jsonb_build_object('state','ok','start_at',bounds.start_at,'end_at',bounds.end_at,
        'build_id',chosen,'next_cursor',cursor_value,'groups',(
            SELECT coalesce(jsonb_agg(jsonb_build_object('timing_type',t.timing,
                'record_kind',CASE WHEN t.timing='request' THEN 'complete_request' WHEN t.timing IS NULL THEN 'unknown_timing' ELSE 'stage_or_call' END,
                'rows',coalesce((SELECT jsonb_agg(x.value ORDER BY x.ordinality) FROM jsonb_array_elements(page) WITH ORDINALITY x
                    WHERE x.value->>'timing_type' IS NOT DISTINCT FROM t.timing),'[]')) ORDER BY t.ord),'[]')
            FROM unnest(ARRAY['request','execute_first','execute_fetch','parse','bind',NULL]) WITH ORDINALITY t(timing,ord)))
        || CASE WHEN missing>0 THEN jsonb_build_object('warning',jsonb_build_object('reason','history_incomplete_current_rules',
            'missing_sql_texts',missing,'message','这些原文尚未按当前规则生成指纹，其执行记录不在结果中')) ELSE '{}'::jsonb END;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_observations(p_approximate text,p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL,p_user text DEFAULT NULL)
RETURNS TABLE(scope_id text,database text,execution_user text,build_id text,timing_type text,statistic jsonb)
LANGUAGE sql
BEGIN ATOMIC
    SELECT g.scope_id,g.database,g.execution_user,v.build_id,g.timing_type,to_jsonb(s)
    FROM mpp_observation_group g JOIN current_version v USING(scope_id)
    CROSS JOIN LATERAL mpp_read_statistics(v.build_id,true,g.group_id) s
    WHERE g.approximate_value=p_approximate AND (p_scope IS NULL OR g.scope_id=p_scope)
        AND (p_database IS NULL OR g.database=p_database) AND (p_user IS NULL OR g.execution_user=p_user)
        AND s.layer='overall';
END;

CREATE OR REPLACE FUNCTION mpp_query_statistics(p_normalization text,p_scope text,p_database text,
    p_user text,p_fingerprint text,p_build text DEFAULT NULL,p_layer text DEFAULT 'overall')
RETURNS TABLE(build_id text,scope_id text,database text,execution_user text,fingerprint text,
    timing_type text,layer text,bucket_date date,bucket_number smallint,range_start timestamptz,range_end timestamptz,
    sample_state text,included_count bigint,active_days integer,excluded_count bigint,exclusions_by_reason jsonb,
    sufficiency jsonb,metric_null_reasons jsonb,
    min_ms numeric,
    max_ms numeric,
    mean_ms numeric,
    p25_ms numeric,
    p50_ms numeric,
    p75_ms numeric,
    p90_ms numeric,
    p95_ms numeric,
    p99_ms numeric,
    stddev_ms numeric,
    cv numeric,
    mad_ms numeric,
    iqr_ms numeric,
    log_median numeric,
    log_mad numeric,
    p95_p50 numeric,
    p99_p50 numeric)
LANGUAGE plpgsql SET plan_cache_mode=force_custom_plan AS $function$
DECLARE chosen text;
BEGIN
    IF p_layer IS NULL OR p_layer NOT IN ('overall','day','week','weekday','hour') THEN RAISE EXCEPTION 'invalid_baseline_layer'; END IF;
    chosen:=mpp_query_select_version(p_normalization,p_scope,p_build);
    RETURN QUERY
    SELECT chosen,p_scope,p_database,p_user,p_fingerprint,t.timing,p_layer,s.bucket_date,s.bucket_number,
        coalesce(s.range_start,c.window_start),coalesce(s.range_end,c.window_end),
        CASE WHEN coalesce(s.included_count,0)=0 THEN 'no_samples' ELSE 'available' END,
        coalesce(s.included_count,0),coalesce(cardinality(s.active_dates),0),coalesce(s.excluded_count,0),coalesce(s.exclusions_by_reason,'{}'),
        mpp_statistic_sufficiency(c.statistics_version,c.thresholds,p_layer,coalesce(s.included_count,0),
            coalesce(s.active_dates,'{}'),coalesce(s.active_week_starts,'{}')),
        coalesce(s.metric_null_reasons,jsonb_build_object('all','no_samples')),
        s.min_ms,s.max_ms,s.mean_ms,s.p25_ms,s.p50_ms,s.p75_ms,s.p90_ms,s.p95_ms,s.p99_ms,s.stddev_ms,s.cv,s.mad_ms,s.iqr_ms,s.log_median,s.log_mad,s.p95_p50,s.p99_p50
    FROM build b JOIN config_snapshot c USING(config_id)
    CROSS JOIN unnest(ARRAY['request','execute_first','execute_fetch','parse','bind']) WITH ORDINALITY t(timing,ord)
    LEFT JOIN mpp_baseline_group g ON g.scope_id=p_scope AND g.profile='mpp-csv/1'
        AND g.database=p_database AND g.execution_user=p_user
        AND g.normalization_id=p_normalization AND g.fingerprint_value=p_fingerprint AND g.timing_type=t.timing
    LEFT JOIN LATERAL (SELECT r.* FROM mpp_read_statistics(chosen,false,g.group_id) r
        WHERE g.group_id IS NOT NULL AND r.layer=p_layer) s ON true
    WHERE b.build_id=chosen ORDER BY t.ord,s.bucket_date,s.bucket_number;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_exact(p_normalization text,p_fingerprint text,p_approximate text DEFAULT NULL,
    p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL,p_reason text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql
BEGIN ATOMIC
    WITH hits AS MATERIALIZED (SELECT * FROM mpp_query_hits(p_normalization,p_fingerprint,p_scope,p_database,p_user)
        WHERE p_fingerprint IS NOT NULL)
    SELECT jsonb_build_object('match_kind','structural_fingerprint','fingerprint',p_fingerprint,
        'state',CASE WHEN p_fingerprint IS NULL THEN 'unreliable_fingerprint'
            WHEN NOT EXISTS(SELECT FROM hits) THEN 'not_seen'
            WHEN EXISTS(SELECT FROM hits WHERE has_baseline) THEN 'has_baseline' ELSE 'records_without_baseline' END,
        'reason',p_reason,'hits',coalesce((SELECT jsonb_agg(to_jsonb(h) ORDER BY h.record_count DESC,h.scope_id,h.database,h.execution_user) FROM hits h),'[]'),
        'observation_label',CASE WHEN p_fingerprint IS NULL AND p_approximate IS NOT NULL THEN '观察结果，不是正式基线' END,
        'observations',CASE WHEN p_fingerprint IS NULL AND p_approximate IS NOT NULL THEN
            coalesce((SELECT jsonb_agg(to_jsonb(o)) FROM mpp_query_observations(p_approximate,p_scope,p_database,p_user) o),'[]') ELSE '[]'::jsonb END);
END;

CREATE OR REPLACE FUNCTION mpp_query_search(p_normalization text,p_input text,p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL,p_user text DEFAULT NULL,p_start timestamptz DEFAULT NULL,
    p_end timestamptz DEFAULT NULL,p_order text DEFAULT 'count') RETURNS jsonb LANGUAGE plpgsql AS $function$
DECLARE result jsonb; trimmed text;
BEGIN
    trimmed:=btrim(p_input,E' \t\n\r\f\013');
    IF trimmed ~ '^struct:[a-zA-Z0-9_./-]+:[0-9a-f]{64}$' THEN
        RETURN mpp_query_exact(p_normalization,trimmed,NULL,p_scope,p_database,p_user);
    END IF;
    SELECT jsonb_build_object('match_kind','text','state','ok','total_structures',coalesce(max(f.total_structures),0),
        'rows',coalesce(jsonb_agg(to_jsonb(f)-'total_structures' ORDER BY
            CASE WHEN p_order='count' THEN f.record_count END DESC,
            CASE WHEN p_order='recent' THEN f.last_at END DESC NULLS LAST,f.fingerprint),'[]')) INTO result
    FROM mpp_query_fuzzy(p_normalization,p_input,p_scope,p_database,p_user,p_start,p_end,p_order) f;
    RETURN result;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_baseline(p_normalization text,p_scope text,p_database text,p_user text,
    p_fingerprint text,p_build text DEFAULT NULL,p_layer text DEFAULT 'overall') RETURNS jsonb LANGUAGE plpgsql AS $function$
DECLARE chosen text; result jsonb;
BEGIN
    chosen:=mpp_query_select_version(p_normalization,p_scope,p_build);
    SELECT jsonb_build_object('state','ok','version',to_jsonb(v),'layer',p_layer,
        'rows',(SELECT jsonb_agg(to_jsonb(s)) FROM mpp_query_statistics(p_normalization,p_scope,p_database,p_user,p_fingerprint,chosen,p_layer) s))
        INTO result FROM mpp_query_versions(p_normalization,p_scope) v WHERE v.build_id=chosen;
    RETURN result;
END $function$;
