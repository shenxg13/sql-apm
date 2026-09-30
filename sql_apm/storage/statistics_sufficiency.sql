-- Read-only, one build per execution. Bind build_id with psycopg2 named parameters.
-- Execute under the selected project schema's search_path, as for other queries.
-- For complete ThresholdResult objects, use mpp_statistic_sufficiency directly.
WITH selected_build AS MATERIALIZED (
    SELECT b.build_id, b.partition_id, c.statistics_version, c.thresholds
    FROM build b JOIN config_snapshot c USING (config_id)
    WHERE b.build_id = %(build_id)s
), validated AS MATERIALIZED (
    -- Exactly five validations, independent of the number of statistic rows.
    -- Reuse the version dispatch and threshold validation of the full function.
    SELECT layer, mpp_statistic_sufficiency(
        b.statistics_version, b.thresholds, layer, 0, '{}'::date[], '{}'::date[]
    ) AS result
    FROM selected_build b
    CROSS JOIN (VALUES ('overall'), ('day'), ('week'), ('weekday'), ('hour')) layers(layer)
), limits AS MATERIALIZED (
    -- numeric preserves legal integer overrides beyond the bigint range.
    SELECT layer,
        (result->'basic'->>'required_count')::numeric AS basic_count,
        (result->'p95'->>'required_count')::numeric AS p95_count,
        (result->'p99'->>'required_count')::numeric AS p99_count,
        result->'basic'->>'coverage_kind' AS coverage_kind,
        (result->'basic'->>'required_coverage')::numeric AS coverage_min
    FROM validated
)
SELECT s.partition_id, s.build_id, s.group_id, s.layer, s.bucket_date, s.bucket_number,
    s.included_count >= t.basic_count AND coverage.actual >= t.coverage_min AS basic_met,
    s.included_count >= t.p95_count AND coverage.actual >= t.coverage_min AS p95_met,
    s.included_count >= t.p99_count AND coverage.actual >= t.coverage_min AS p99_met
FROM selected_build b
JOIN mpp_statistic s ON s.partition_id = b.partition_id AND s.build_id = b.build_id
JOIN limits t ON t.layer = s.layer
CROSS JOIN LATERAL (
    SELECT CASE t.coverage_kind WHEN 'none' THEN 0
        WHEN 'active_days' THEN cardinality(s.active_dates)
        ELSE cardinality(s.active_week_starts) END AS actual
) coverage
