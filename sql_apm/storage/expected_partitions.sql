-- Mirror only declared cluster/month partitions into the disposable expected
-- schema. The full catalog comparison still validates every leaf and index.
DO $block$
DECLARE row record; expected_id bigint;
BEGIN
    IF to_regclass(format('%I.mpp_result_partition',current_setting('apm.schema'))) IS NOT NULL THEN
        FOR row IN EXECUTE format('SELECT * FROM %I.mpp_result_partition ORDER BY partition_id',current_setting('apm.schema')) LOOP
            INSERT INTO scope VALUES (row.scope_id,'mpp','mpp-csv/1','1.0.0') ON CONFLICT DO NOTHING;
            expected_id := mpp_ensure_result_partition(row.scope_id,row.build_month);
            IF expected_id <> row.partition_id THEN RAISE EXCEPTION 'invalid_partition_identity'; END IF;
        END LOOP;
    END IF;
END $block$;
