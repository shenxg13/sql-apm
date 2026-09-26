-- Structure version 1.0.0. Called only after catalog compatibility checks.
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
CREATE TABLE IF NOT EXISTS sql_text (
    sql_id text PRIMARY KEY CHECK (sql_id <> ''),
    text text NOT NULL CHECK (text <> ''),
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32
        AND content_sha256 = sha256(convert_to(text, 'UTF8')))
);
-- Hash narrows candidates only: full content equality decides reuse.
CREATE INDEX IF NOT EXISTS sql_text_content_idx ON sql_text (content_sha256);
CREATE TABLE IF NOT EXISTS sql_text_evidence (
    sql_id text NOT NULL REFERENCES sql_text,
    record_id text NOT NULL REFERENCES evidence_record,
    PRIMARY KEY (sql_id, record_id)
);
CREATE TABLE IF NOT EXISTS occurrence (
    analysis_id text NOT NULL,
    occurrence_id text NOT NULL CHECK (occurrence_id <> ''),
    scope_id text NOT NULL,
    source_id text NOT NULL,
    anchor_ref text NOT NULL,
    unit text NOT NULL CHECK (unit IN ('request','call')),
    request_shape text NOT NULL CHECK (request_shape IN ('single','batch','unknown')),
    database text CHECK (database <> ''),
    execution_user text CHECK (execution_user <> ''),
    sql_id text REFERENCES sql_text,
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
CREATE INDEX IF NOT EXISTS occurrence_scope_time_idx ON occurrence (scope_id, estimated_start_at);
CREATE INDEX IF NOT EXISTS occurrence_sql_time_idx ON occurrence (sql_id, estimated_start_at);
CREATE TABLE IF NOT EXISTS occurrence_evidence (
    analysis_id text NOT NULL,
    occurrence_id text NOT NULL,
    record_id text NOT NULL REFERENCES evidence_record,
    purpose text NOT NULL CHECK (purpose IN ('support','outcome','association')),
    PRIMARY KEY (analysis_id, occurrence_id, record_id, purpose),
    FOREIGN KEY (analysis_id, occurrence_id) REFERENCES occurrence
);
CREATE TABLE IF NOT EXISTS normalization (
    normalization_id text PRIMARY KEY CHECK (normalization_id <> ''),
    algorithm_version text NOT NULL CHECK (algorithm_version <> ''),
    parser_version text NOT NULL CHECK (parser_version <> ''),
    dictionary_schema_version integer NOT NULL CHECK (dictionary_schema_version > 0),
    dictionary_rules_version text NOT NULL CHECK (dictionary_rules_version <> ''),
    dictionary_digest_algorithm text NOT NULL CHECK (dictionary_digest_algorithm <> ''),
    dictionary_digest_value text NOT NULL CHECK (dictionary_digest_value <> ''),
    rules_ref text NOT NULL CHECK (rules_ref <> '')
);
CREATE TABLE IF NOT EXISTS fingerprint (
    fingerprint_id text PRIMARY KEY CHECK (fingerprint_id <> ''),
    sql_id text NOT NULL REFERENCES sql_text,
    normalization_id text NOT NULL REFERENCES normalization,
    profile text NOT NULL CHECK (profile = 'hashdata-csv/1'),
    state text NOT NULL CHECK (state IN ('reliable','unsupported_syntax','normalization_failed')),
    value text CHECK (value <> ''),
    reason text CHECK (reason <> ''),
    CHECK ((state = 'reliable') = (value IS NOT NULL)),
    CHECK ((state = 'reliable') = (reason IS NULL)),
    UNIQUE (sql_id, normalization_id, profile),
    UNIQUE (fingerprint_id, normalization_id, profile),
    UNIQUE (fingerprint_id, normalization_id, profile, value)
);
CREATE INDEX IF NOT EXISTS fingerprint_value_idx ON fingerprint (normalization_id, profile, value);
CREATE TABLE IF NOT EXISTS baseline_group (
    group_id text PRIMARY KEY CHECK (group_id <> ''),
    scope_id text NOT NULL,
    profile text NOT NULL CHECK (profile = 'hashdata-csv/1'),
    normalization_id text NOT NULL,
    database text NOT NULL CHECK (database <> ''),
    execution_user text NOT NULL CHECK (execution_user <> ''),
    fingerprint_id text NOT NULL,
    fingerprint_value text NOT NULL,
    timing_type text NOT NULL CHECK (timing_type IN ('request','execute_first','execute_fetch','parse','bind')),
    FOREIGN KEY (scope_id, profile) REFERENCES scope (scope_id, profile),
    FOREIGN KEY (fingerprint_id, normalization_id, profile, fingerprint_value) REFERENCES fingerprint (fingerprint_id, normalization_id, profile, value),
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
CREATE TABLE IF NOT EXISTS input_occurrence (
    input_id text NOT NULL,
    analysis_id text NOT NULL,
    occurrence_id text NOT NULL,
    scope_id text NOT NULL,
    PRIMARY KEY (input_id, analysis_id, occurrence_id),
    FOREIGN KEY (input_id, analysis_id) REFERENCES input_analysis,
    FOREIGN KEY (input_id, scope_id) REFERENCES input_snapshot (input_id, scope_id),
    FOREIGN KEY (analysis_id, occurrence_id, scope_id) REFERENCES occurrence (analysis_id, occurrence_id, scope_id)
);
CREATE TABLE IF NOT EXISTS config_snapshot (
    config_id text PRIMARY KEY CHECK (config_id <> ''),
    scope_id text NOT NULL,
    normalization_id text REFERENCES normalization,
    profile text NOT NULL,
    source_mapping_refs jsonb NOT NULL CHECK (jsonb_typeof(source_mapping_refs) = 'array'),
    cutoff_date date NOT NULL,
    window_days integer NOT NULL CHECK (window_days > 0),
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    blacklist jsonb NOT NULL CHECK (jsonb_typeof(blacklist) = 'object' AND (profile <> 'hashdata-csv/1' OR blacklist ?& ARRAY['category_ref','template_ref','category_rules','template_rules'])),
    exclusions jsonb NOT NULL CHECK (jsonb_typeof(exclusions) = 'array'),
    thresholds jsonb NOT NULL CHECK (jsonb_typeof(thresholds) = 'object' AND (profile <> 'hashdata-csv/1' OR thresholds ?& ARRAY['overall','day','week','weekday','hour'])),
    statistics_version text NOT NULL CHECK (statistics_version <> ''),
    FOREIGN KEY (scope_id, profile) REFERENCES scope (scope_id, profile),
    CHECK (window_start < window_end),
    CHECK (profile <> 'hashdata-csv/1' OR (
        window_start = (cutoff_date - (window_days - 1))::timestamp AT TIME ZONE INTERVAL '+08:00'
        AND window_end = (cutoff_date + 1)::timestamp AT TIME ZONE INTERVAL '+08:00')),
    CHECK (profile <> 'hashdata-csv/1' OR normalization_id IS NOT NULL),
    UNIQUE (config_id, scope_id, profile),
    UNIQUE (config_id, scope_id, normalization_id, profile)
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
    CHECK (profile <> 'hashdata-csv/1' OR normalization_id IS NOT NULL),
    FOREIGN KEY (config_id, scope_id, profile) REFERENCES config_snapshot (config_id, scope_id, profile),
    FOREIGN KEY (config_id, scope_id, normalization_id, profile) REFERENCES config_snapshot (config_id, scope_id, normalization_id, profile),
    UNIQUE (build_id, scope_id),
    UNIQUE (build_id, scope_id, normalization_id, profile),
    FOREIGN KEY (retry_of, scope_id) REFERENCES build (build_id, scope_id),
    CHECK (retry_of IS DISTINCT FROM build_id),
    CHECK ((state = 'running') = (finished_at IS NULL)),
    CHECK (finished_at >= started_at)
);
CREATE INDEX IF NOT EXISTS build_scope_time_idx ON build (scope_id, started_at);
CREATE TABLE IF NOT EXISTS decision (
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
    FOREIGN KEY (analysis_id, occurrence_id, scope_id) REFERENCES occurrence (analysis_id, occurrence_id, scope_id),
    FOREIGN KEY (fingerprint_id, normalization_id, profile) REFERENCES fingerprint (fingerprint_id, normalization_id, profile),
    FOREIGN KEY (fingerprint_id, normalization_id, profile, fingerprint_value) REFERENCES fingerprint (fingerprint_id, normalization_id, profile, value),
    FOREIGN KEY (group_id, scope_id, normalization_id, profile) REFERENCES baseline_group (group_id, scope_id, normalization_id, profile),
    FOREIGN KEY (group_id, normalization_id, profile, fingerprint_value) REFERENCES baseline_group (group_id, normalization_id, profile, fingerprint_value),
    UNIQUE (build_id, analysis_id, occurrence_id),
    CHECK (coalesce(rule_evaluations->>'outcome' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'duration' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'association' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'sql' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'fingerprint' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'blacklist' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'exclusion_interval' IN ('matched','not_matched','not_evaluated'),false) AND coalesce(rule_evaluations->>'window' IN ('matched','not_matched','not_evaluated'),false)),
    CHECK (group_id IS NULL OR (fingerprint_id IS NOT NULL AND fingerprint_value IS NOT NULL)),
    CHECK (count_scope <> 'group' OR group_id IS NOT NULL),
    CHECK (state <> 'included' OR (in_window IS TRUE AND count_scope = 'group')),
    CHECK ((state = 'outside_window') = (in_window IS FALSE)),
    CHECK ((state = 'outside_window') = (count_scope = 'none'))
);
CREATE INDEX IF NOT EXISTS decision_group_idx ON decision (build_id, group_id);
CREATE TABLE IF NOT EXISTS decision_reason (
    decision_id text NOT NULL REFERENCES decision,
    code text NOT NULL CHECK (code IN ('execution_failed','execution_cancelled','execution_timed_out','outcome_unknown','duration_unknown','association_unreliable','timing_unknown','sql_missing','sql_incomplete','sql_encoding_invalid','sql_uncertain','fingerprint_failed','identity_missing','start_unknown','blacklist_category','blacklist_template','excluded_interval','outside_window')),
    rule_ref text NOT NULL CHECK (rule_ref <> ''),
    PRIMARY KEY (decision_id, code)
);
CREATE TABLE IF NOT EXISTS decision_reason_evidence (
    decision_id text NOT NULL,
    code text NOT NULL,
    record_id text NOT NULL REFERENCES evidence_record,
    PRIMARY KEY (decision_id, code, record_id),
    FOREIGN KEY (decision_id, code) REFERENCES decision_reason
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
    FOREIGN KEY (analysis_id, occurrence_id) REFERENCES occurrence MATCH FULL,
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
CREATE TABLE IF NOT EXISTS build_timing_coverage (
    build_id text NOT NULL REFERENCES build,
    timing_type text NOT NULL CHECK (timing_type IN ('request','execute_first','execute_fetch','parse','bind')),
    included_count bigint NOT NULL CHECK (included_count >= 0),
    excluded_count bigint NOT NULL CHECK (excluded_count >= 0),
    PRIMARY KEY (build_id, timing_type)
);
CREATE TABLE IF NOT EXISTS build_coverage (
    build_id text NOT NULL,
    group_id text NOT NULL,
    scope_id text NOT NULL,
    normalization_id text NOT NULL,
    profile text NOT NULL,
    layer text NOT NULL CHECK (layer IN ('overall','day','week','weekday','hour')),
    computed_keys jsonb NOT NULL CHECK (jsonb_typeof(computed_keys) = 'array'),
    empty_keys jsonb NOT NULL CHECK (jsonb_typeof(empty_keys) = 'array'),
    PRIMARY KEY (build_id, group_id, layer),
    FOREIGN KEY (build_id, scope_id, normalization_id, profile) REFERENCES build (build_id, scope_id, normalization_id, profile),
    FOREIGN KEY (group_id, scope_id, normalization_id, profile) REFERENCES baseline_group (group_id, scope_id, normalization_id, profile)
);

CREATE TABLE IF NOT EXISTS statistic (
    statistic_id text PRIMARY KEY CHECK (statistic_id <> ''),
    build_id text NOT NULL,
    group_id text NOT NULL,
    scope_id text NOT NULL,
    normalization_id text NOT NULL,
    profile text NOT NULL,
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
    sufficiency jsonb NOT NULL CHECK (jsonb_typeof(sufficiency) = 'object' AND sufficiency ?& ARRAY['basic','p95','p99']),
    FOREIGN KEY (build_id, scope_id, normalization_id, profile) REFERENCES build (build_id, scope_id, normalization_id, profile),
    FOREIGN KEY (group_id, scope_id, normalization_id, profile) REFERENCES baseline_group (group_id, scope_id, normalization_id, profile),
    UNIQUE NULLS NOT DISTINCT (build_id, group_id, layer, bucket_date, bucket_number),
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
CONSTRAINT statistic_sufficiency_basic_check CHECK (coalesce(
    jsonb_typeof(sufficiency->'basic') = 'object'
    AND (sufficiency->'basic') ?& ARRAY['required_count','actual_count','coverage_kind','required_coverage','actual_coverage','met','reasons']
    AND jsonb_typeof(sufficiency->'basic'->'required_count') = 'number'
    AND (sufficiency->'basic'->>'required_count')::numeric >= 0
    AND mod((sufficiency->'basic'->>'required_count')::numeric,1) = 0
    AND jsonb_typeof(sufficiency->'basic'->'actual_count') = 'number'
    AND (sufficiency->'basic'->>'actual_count')::numeric = included_count
    AND (sufficiency->'basic'->>'coverage_kind') IN ('none','active_days','active_weeks')
    AND jsonb_typeof(sufficiency->'basic'->'required_coverage') = 'number'
    AND (sufficiency->'basic'->>'required_coverage')::numeric >= 0
    AND mod((sufficiency->'basic'->>'required_coverage')::numeric,1) = 0
    AND jsonb_typeof(sufficiency->'basic'->'actual_coverage') = 'number'
    AND (sufficiency->'basic'->>'actual_coverage')::numeric = CASE sufficiency->'basic'->>'coverage_kind'
        WHEN 'none' THEN 0 WHEN 'active_days' THEN cardinality(active_dates)
        WHEN 'active_weeks' THEN cardinality(active_week_starts) END
    AND ((sufficiency->'basic'->>'coverage_kind') <> 'none' OR (sufficiency->'basic'->>'required_coverage')::numeric = 0)
    AND jsonb_typeof(sufficiency->'basic'->'met') = 'boolean'
    AND (sufficiency->'basic'->>'met')::boolean = (
        included_count >= (sufficiency->'basic'->>'required_count')::numeric
        AND (sufficiency->'basic'->>'actual_coverage')::numeric >= (sufficiency->'basic'->>'required_coverage')::numeric)
    AND jsonb_typeof(sufficiency->'basic'->'reasons') = 'array'
    AND ((sufficiency->'basic'->>'met')::boolean = (sufficiency->'basic'->'reasons' = '[]'::jsonb)), false)),
CONSTRAINT statistic_sufficiency_p95_check CHECK (coalesce(
    jsonb_typeof(sufficiency->'p95') = 'object'
    AND (sufficiency->'p95') ?& ARRAY['required_count','actual_count','coverage_kind','required_coverage','actual_coverage','met','reasons']
    AND jsonb_typeof(sufficiency->'p95'->'required_count') = 'number'
    AND (sufficiency->'p95'->>'required_count')::numeric >= 0
    AND mod((sufficiency->'p95'->>'required_count')::numeric,1) = 0
    AND jsonb_typeof(sufficiency->'p95'->'actual_count') = 'number'
    AND (sufficiency->'p95'->>'actual_count')::numeric = included_count
    AND (sufficiency->'p95'->>'coverage_kind') IN ('none','active_days','active_weeks')
    AND jsonb_typeof(sufficiency->'p95'->'required_coverage') = 'number'
    AND (sufficiency->'p95'->>'required_coverage')::numeric >= 0
    AND mod((sufficiency->'p95'->>'required_coverage')::numeric,1) = 0
    AND jsonb_typeof(sufficiency->'p95'->'actual_coverage') = 'number'
    AND (sufficiency->'p95'->>'actual_coverage')::numeric = CASE sufficiency->'p95'->>'coverage_kind'
        WHEN 'none' THEN 0 WHEN 'active_days' THEN cardinality(active_dates)
        WHEN 'active_weeks' THEN cardinality(active_week_starts) END
    AND ((sufficiency->'p95'->>'coverage_kind') <> 'none' OR (sufficiency->'p95'->>'required_coverage')::numeric = 0)
    AND jsonb_typeof(sufficiency->'p95'->'met') = 'boolean'
    AND (sufficiency->'p95'->>'met')::boolean = (
        included_count >= (sufficiency->'p95'->>'required_count')::numeric
        AND (sufficiency->'p95'->>'actual_coverage')::numeric >= (sufficiency->'p95'->>'required_coverage')::numeric)
    AND jsonb_typeof(sufficiency->'p95'->'reasons') = 'array'
    AND ((sufficiency->'p95'->>'met')::boolean = (sufficiency->'p95'->'reasons' = '[]'::jsonb)), false)),
CONSTRAINT statistic_sufficiency_p99_check CHECK (coalesce(
    jsonb_typeof(sufficiency->'p99') = 'object'
    AND (sufficiency->'p99') ?& ARRAY['required_count','actual_count','coverage_kind','required_coverage','actual_coverage','met','reasons']
    AND jsonb_typeof(sufficiency->'p99'->'required_count') = 'number'
    AND (sufficiency->'p99'->>'required_count')::numeric >= 0
    AND mod((sufficiency->'p99'->>'required_count')::numeric,1) = 0
    AND jsonb_typeof(sufficiency->'p99'->'actual_count') = 'number'
    AND (sufficiency->'p99'->>'actual_count')::numeric = included_count
    AND (sufficiency->'p99'->>'coverage_kind') IN ('none','active_days','active_weeks')
    AND jsonb_typeof(sufficiency->'p99'->'required_coverage') = 'number'
    AND (sufficiency->'p99'->>'required_coverage')::numeric >= 0
    AND mod((sufficiency->'p99'->>'required_coverage')::numeric,1) = 0
    AND jsonb_typeof(sufficiency->'p99'->'actual_coverage') = 'number'
    AND (sufficiency->'p99'->>'actual_coverage')::numeric = CASE sufficiency->'p99'->>'coverage_kind'
        WHEN 'none' THEN 0 WHEN 'active_days' THEN cardinality(active_dates)
        WHEN 'active_weeks' THEN cardinality(active_week_starts) END
    AND ((sufficiency->'p99'->>'coverage_kind') <> 'none' OR (sufficiency->'p99'->>'required_coverage')::numeric = 0)
    AND jsonb_typeof(sufficiency->'p99'->'met') = 'boolean'
    AND (sufficiency->'p99'->>'met')::boolean = (
        included_count >= (sufficiency->'p99'->>'required_count')::numeric
        AND (sufficiency->'p99'->>'actual_coverage')::numeric >= (sufficiency->'p99'->>'required_coverage')::numeric)
    AND jsonb_typeof(sufficiency->'p99'->'reasons') = 'array'
    AND ((sufficiency->'p99'->>'met')::boolean = (sufficiency->'p99'->'reasons' = '[]'::jsonb)), false)),
    CHECK (included_count = 0 OR ((mean_ms = 0) = (cv IS NULL))),
    CHECK (included_count = 0 OR ((p50_ms = 0) = (p95_p50 IS NULL))),
    CHECK (included_count = 0 OR ((p50_ms = 0) = (p99_p50 IS NULL))),
    CHECK (cardinality(active_dates) <= included_count AND cardinality(active_week_starts) <= included_count),
    CHECK (mean_ms BETWEEN min_ms AND max_ms)
);
CREATE INDEX IF NOT EXISTS statistic_group_build_idx ON statistic (group_id, build_id, layer);
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
    mode text NOT NULL CHECK (mode IN ('full','import_only','rebuild')),
    state text NOT NULL CHECK (state IN ('running','succeeded','failed','interrupted','busy_rejected')),
    stage text NOT NULL CHECK (stage IN ('import','build','publish','none')),
    busy_task_id text,
    reason text CHECK (reason <> ''),
    UNIQUE (task_id, scope_id),
    FOREIGN KEY (busy_task_id, scope_id) REFERENCES task (task_id, scope_id),
    CHECK (busy_task_id IS DISTINCT FROM task_id),
    CHECK ((state = 'busy_rejected') = (busy_task_id IS NOT NULL)),
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
