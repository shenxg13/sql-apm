-- Mirror only declared cluster/month partitions into the disposable expected
-- schema. The full catalog comparison still validates every leaf and index.
DO $block$
DECLARE row record; expected_id bigint;
BEGIN
    IF to_regclass(format('%I.mpp_result_partition',current_setting('apm.schema'))) IS NOT NULL THEN
        FOR row IN EXECUTE format('SELECT * FROM %I.mpp_result_partition ORDER BY partition_id',current_setting('apm.schema')) LOOP
            INSERT INTO scope VALUES (row.scope_id,'mpp','mpp-csv/1','1.0.0') ON CONFLICT DO NOTHING;
            IF to_jsonb(row)->>'cleaned_at' IS NOT NULL THEN
                INSERT INTO mpp_result_partition (partition_id,scope_id,build_month,cleaned_at,groups_cleaned_at)
                    VALUES (row.partition_id,row.scope_id,row.build_month,row.cleaned_at,row.groups_cleaned_at);
                CONTINUE;
            END IF;
            expected_id := mpp_ensure_result_partition(row.scope_id,row.build_month);
            IF expected_id <> row.partition_id THEN RAISE EXCEPTION 'invalid_partition_identity'; END IF;
        END LOOP;
    END IF;
END $block$;
