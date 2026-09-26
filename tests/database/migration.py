"""Behavior checks for the fixed MPP rename migration, using private instances."""
import hashlib
import json
import re
import select
import subprocess
import shutil

from database.fixture import statements

MPP_TABLES = (
    "sql_text", "sql_text_evidence", "occurrence", "occurrence_evidence",
    "normalization", "fingerprint", "baseline_group", "input_occurrence",
    "decision", "decision_reason", "decision_reason_evidence",
    "build_timing_coverage", "build_coverage", "statistic",
)
LEGACY_SHA = "df6b4cec6abac9742c56afc3c238d2f16fadd2895da1d61c04fb6255336c3c28"


def verify_migration(v, root, runner):
    storage = root / "sql_apm/storage"
    legacy = (storage / "versions/1.0.0.sql").read_text()
    v.require(hashlib.sha256(legacy.encode()).hexdigest() == LEGACY_SHA,
              "published 1.0.0 DDL is byte-for-byte preserved")
    fixture = statements()
    for name in MPP_TABLES:
        fixture = re.sub(r"\bmpp_" + name + r"\b", name, fixture)

    for custom in (False, True):
        names = ("apm_upgrade_db", "apm_upgrade_store", "apm_upgrade_role") if custom else ("sql_apm",) * 3
        database, schema, role = names

        def sql(statement, ok=True):
            return runner([v.pg_bin / "psql", "-X", "-w", "-Atq", "-v", "ON_ERROR_STOP=1",
                           "-h", v.directory / "socket", "-p", "55473", "-U", role, "-d", database],
                          v.env, 'SET search_path="' + schema + '",pg_catalog;\n' + statement, ok=ok).stdout.strip()

        def tables():
            return sql("SELECT relname FROM pg_class WHERE relnamespace='" + schema +
                       "'::regnamespace AND relkind='r' ORDER BY relname").splitlines()

        def state():
            data = {}
            for table in tables():
                if table != "schema_version":
                    key = table[4:] if table.startswith("mpp_") else table
                    data[key] = sql('SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\') FROM "' + table + '" t')
            objects = sql("SELECT oid::text||':'||relfilenode::text FROM pg_class WHERE relnamespace='" + schema +
                          "'::regnamespace ORDER BY oid")
            constraints = sql("SELECT oid FROM pg_constraint WHERE connamespace='" + schema +
                              "'::regnamespace ORDER BY oid")
            return data, objects, constraints

        def versions():
            return json.loads(sql("SELECT jsonb_object_agg(version,to_jsonb(v)) FROM schema_version v"))

        v.init("bootstrap", names=names)
        sql('CREATE SCHEMA "' + schema + '";\n' + legacy +
            "\nINSERT INTO schema_version(version,script_sha256) VALUES ('1.0.0','" + LEGACY_SHA + "');\n" + fixture)
        # Exercise the public problem -> MPP occurrence foreign key with data.
        sql("INSERT INTO problem SELECT 'MIG_P','record',b.batch_id,f.file_id,NULL,o.analysis_id,o.occurrence_id,"
            "'synthetic','migration provenance','informational','log_record',1,'open',NULL "
            "FROM occurrence o CROSS JOIN import_batch b CROSS JOIN source_file f LIMIT 1")
        before, receipt = state(), versions()
        v.require(len(tables()) == 41 and sql("SELECT count(*) FROM statistic") == "5", "legacy populated schema prepared: " + schema)
        v.init("schema", names=names, ok=False)
        v.require(state() == before and versions() == receipt, "initializer rejects implicit legacy upgrade: " + schema)

        if not custom:
            sql("ALTER TABLE statistic ADD COLUMN drift integer")
            v.require("incompatible object" in v.init("upgrade", names=names, ok=False).stderr,
                      "legacy column drift blocks migration")
            sql("ALTER TABLE statistic DROP COLUMN drift")
            sql("UPDATE schema_version SET script_sha256=repeat('0',64)")
            v.require("checksum" in v.init("upgrade", names=names, ok=False).stderr,
                      "legacy checksum drift blocks migration")
            sql("UPDATE schema_version SET script_sha256='" + LEGACY_SHA + "'")
            sql("INSERT INTO schema_version(version,script_sha256) VALUES ('9.0.0',repeat('0',64))")
            v.require("legacy version" in v.init("upgrade", names=names, ok=False).stderr,
                      "unknown additional source version blocks migration")
            sql("DELETE FROM schema_version WHERE version='9.0.0'")
            sql("CREATE TABLE mpp_statistic (id integer)")
            v.require("unexpected object" in v.init("upgrade", names=names, ok=False).stderr,
                      "target table conflict rejected before any rename")
            sql("DROP TABLE mpp_statistic")
            v.require(state() == before and versions() == receipt, "rejected migrations preserve all legacy rows and objects")

            # A real reader holds the first renamed table; lock_timeout must
            # abort this migration without waiting indefinitely or changing data.
            with subprocess.Popen([str(v.pg_bin / "psql"), "-X", "-w", "-Atq", "-v", "ON_ERROR_STOP=1",
                                   "-h", str(v.directory / "socket"), "-p", "55473", "-U", role, "-d", database],
                                  env=v.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) as holder:
                try:
                    holder.stdin.write('BEGIN; LOCK TABLE "' + schema + '".sql_text IN ACCESS SHARE MODE; SELECT \'ready\';\n')
                    holder.stdin.flush()
                    if not select.select([holder.stdout], [], [], 5)[0] or holder.stdout.readline().strip() != "ready":
                        raise AssertionError("lock-holder did not become ready")
                    v.require("lock timeout" in v.init("upgrade", names=names, ok=False).stderr,
                              "active reader triggers bounded DDL lock timeout")
                finally:
                    holder.communicate(input="ROLLBACK;\n", timeout=5)
            v.require(state() == before and versions() == receipt,
                      "lock timeout rolls back migration and preserves legacy state")

            copy_root = v.directory / "migration_failure"
            shutil.copytree(storage, copy_root / "sql_apm/storage")
            (copy_root / "scripts/db").mkdir(parents=True)
            shutil.copy2(root / "scripts/db/initialize.sh", copy_root / "scripts/db/initialize.sh")
            migration = copy_root / "sql_apm/storage/migrations/1.0.0-to-1.1.0.sql"
            original = migration.read_text()
            first_rename = 'ALTER TABLE :"project_schema".sql_text RENAME TO mpp_sql_text;'
            migration.write_text(original.replace(first_rename, first_rename + "\nSELECT 1/0;"))
            v.require("division by zero" in v.init("upgrade", names=names, root=copy_root, ok=False).stderr,
                      "injected failure after first rename reaches migration transaction")
            v.require(state() == before and versions() == receipt and "sql_text" in tables(),
                      "partial rename rollback preserves rows, OIDs, files, constraints and receipt")
            migration.write_text(original)
            entry = copy_root / "sql_apm/storage/migrate.sql"
            entry.write_text(entry.read_text().replace("COMMIT;", "SELECT 1/0;\nCOMMIT;"))
            v.require("division by zero" in v.init("upgrade", names=names, root=copy_root, ok=False).stderr,
                      "injected failure after target verification and version insert")
            v.require(state() == before and versions() == receipt, "version registration rolls back with all renames")
            v.require(sql("SELECT count(*) FROM pg_namespace WHERE nspname LIKE '_apm_expected_%' OR nspname LIKE '_apm_legacy_%'") == "0",
                      "failed migrations leave no expected/legacy scratch schemas")

        v.init("upgrade", names=names)
        v.init("check", names=names)
        v.require(state() == before, "upgrade preserves every business row, relation OID/file and constraint OID: " + schema)
        after = versions()
        v.require(set(after) == {"1.0.0", "1.1.0"} and after["1.0.0"] == receipt["1.0.0"],
                  "upgrade retains original receipt and adds one target receipt: " + schema)
        v.require(len(tables()) == 41 and {t for t in tables() if t.startswith("mpp_")} == {"mpp_" + n for n in MPP_TABLES}
                  and not set(tables()).intersection(MPP_TABLES), "exact 14 MPP names, 27 common tables and no legacy names: " + schema)
        v.require(sql("SELECT count(*) FROM pg_constraint k JOIN pg_class t ON t.oid=k.conrelid WHERE t.relnamespace='" + schema +
                      "'::regnamespace AND t.relname LIKE 'mpp\\_%' ESCAPE '\\' AND k.conname NOT LIKE 'mpp\\_%' ESCAPE '\\'") == "0",
                  "MPP constraint names consistently prefixed: " + schema)
        v.require(sql("SELECT count(*) FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_class x ON x.oid=i.indexrelid "
                      "WHERE t.relnamespace='" + schema + "'::regnamespace AND t.relname LIKE 'mpp\\_%' ESCAPE '\\' "
                      "AND x.relname NOT LIKE 'mpp\\_%' ESCAPE '\\'") == "0", "MPP index names consistently prefixed: " + schema)
        v.require(sql("SELECT count(*) FROM config_snapshot c JOIN mpp_normalization n USING (normalization_id)") == "1"
                  and sql("SELECT count(*) FROM problem p JOIN mpp_occurrence o USING (analysis_id,occurrence_id)") == "1",
                  "both public-table references retain their MPP targets: " + schema)
        sql("BEGIN; UPDATE problem SET occurrence_id='missing'; ROLLBACK;", ok=False)
        sql("BEGIN; UPDATE config_snapshot SET normalization_id='missing'; ROLLBACK;", ok=False)
        v.require(True, "public foreign keys still reject dangling references: " + schema)
        v.require(sql("SELECT build_id||':'||publication_id FROM current_version") == "V1:PUB1",
                  "published baseline pointer retained: " + schema)
        if not custom:
            sql("UPDATE schema_version SET script_sha256=repeat('0',64) WHERE version='1.1.0'")
            v.require("checksum" in v.init("upgrade", names=names, ok=False).stderr,
                      "repeat upgrade still rejects target version checksum drift")
            sql("UPDATE schema_version SET script_sha256='" + after["1.1.0"]["script_sha256"] + "' WHERE version='1.1.0'")
        v.init("upgrade", names=names)
        v.init("schema", names=names)
        v.require(state() == before and versions() == after, "repeat upgrade and initialization preserve rows and both timestamps: " + schema)
        v.require(sql("SELECT count(*) FROM pg_namespace WHERE nspname LIKE '_apm_expected_%' OR nspname LIKE '_apm_legacy_%'") == "0",
                  "successful migrations leave no scratch schemas: " + schema)

    v.require(True, "new installation and migrated databases pass the same full target catalog check")
    print("RESULT: " + str(v.completed) + " migration checks passed", flush=True)
