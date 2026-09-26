#!/usr/bin/env python3
"""Bounded PostgreSQL 17 integration tests; never connect to an existing service."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from database.fixture import statements  # noqa: E402
from database.migration import verify_migration  # noqa: E402


def run(args, env=None, sql=None, ok=True):
    result = subprocess.run([str(v) for v in args], input=sql, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=60)
    if ok and result.returncode:
        raise RuntimeError(result.stderr + result.stdout)
    if not ok and not result.returncode:
        raise AssertionError("expected command failure")
    return result


@contextlib.contextmanager
def instance(pg_bin):
    # One private directory owns both the data directory and socket. TCP disabled.
    directory = Path(tempfile.mkdtemp(prefix="sql-apm-pg-", dir="/tmp"))
    data, sock = directory / "data", directory / "socket"
    sock.mkdir(mode=0o700)
    started = False
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith("PG"):
            env.pop(key)
    env["PGCONNECT_TIMEOUT"] = "5"
    try:
        run([pg_bin / "initdb", "-D", data, "-U", "apm_test_admin", "--auth-local=trust",
             "--auth-host=reject", "--encoding=UTF8", "--locale=C"], env)
        with (data / "postgresql.conf").open("a") as file:
            file.write("\nlisten_addresses = ''\nunix_socket_directories = '" + str(sock) +
                       "'\nport = 55473\nlog_statement = 'none'\nlog_min_error_statement = 'panic'\n")
        run([pg_bin / "pg_ctl", "-D", data, "-l", directory / "server.log", "-w", "start"], env)
        started = True
        yield directory, env
    finally:
        # Startup may have succeeded even when the client was interrupted.
        if started or (data / "postmaster.pid").exists():
            run([pg_bin / "pg_ctl", "-D", data, "-m", "immediate", "-w", "stop"], env, ok=True)
        # Delete only the directory generated above, and only after confirmed stop.
        shutil.rmtree(directory)
        print("CLEANUP: stopped and removed " + str(directory), flush=True)


class Verification:
    def __init__(self, pg_bin, directory, env):
        self.pg_bin, self.directory, self.env = pg_bin, directory, env
        self.args = ["--host", directory / "socket", "--port", "55473", "--pg-bin", pg_bin]
        self.completed = 0

    def init(self, mode="all", names=None, ok=True, root=ROOT):
        args = [root / "scripts/db/initialize.sh", mode] + self.args
        if mode in ("all", "bootstrap"):
            args += ["--admin-user", "apm_test_admin", "--admin-database", "postgres"]
        if names:
            args += ["--database", names[0], "--schema", names[1], "--role", names[2]]
        return run(args, self.env, ok=ok)

    def sql(self, sql, admin=False, database=None, ok=True):
        return run([self.pg_bin / "psql", "-X", "-w", "-Atq", "-v", "ON_ERROR_STOP=1",
                    "-h", self.directory / "socket", "-p", "55473",
                    "-U", "apm_test_admin" if admin else "sql_apm",
                    "-d", database or ("postgres" if admin else "sql_apm")],
                   self.env, "SET search_path=sql_apm,pg_catalog;\n" + sql, ok=ok).stdout.strip()

    def require(self, condition, label):
        if not condition:
            raise AssertionError(label)
        self.completed += 1
        print("PASS: " + label, flush=True)

    def rejects(self, sql, label):
        self.sql("BEGIN; " + sql + "; ROLLBACK;", ok=False)
        self.require(True, label)

    def tests(self):
        self.init()
        self.require(self.sql("SELECT current_user || ':' || current_setting('server_version')").startswith("sql_apm:17."),
                     "empty initialization and actual PostgreSQL 17 project login")
        self.require(self.sql("SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls) FROM pg_roles WHERE rolname=current_user") == "t",
                     "ordinary project role attributes")
        self.require(self.sql("SELECT bool_and(c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='sql_apm'") == "t",
                     "project owns every table and index")
        self.sql("CREATE TABLE owner_probe (id integer); ALTER TABLE owner_probe ADD COLUMN value text; INSERT INTO owner_probe VALUES (1,'ok'); UPDATE owner_probe SET value='changed'; DELETE FROM owner_probe; DROP TABLE owner_probe")
        self.require(True, "project account DDL and read/write access")
        self.sql("BEGIN;\n" + statements() + "\nCOMMIT;")
        self.require(self.sql("SELECT count(*) FROM mpp_statistic") == "5", "Issue #3 base fixture, five layers and 17 metric columns stored")
        self.require(self.sql("SELECT sum(jsonb_array_length(computed_keys)+jsonb_array_length(empty_keys)) FROM mpp_build_coverage") == "67", "sparse coverage expands to 67 logical buckets")
        self.require(self.sql("SELECT sum(included_count) FROM mpp_build_timing_coverage") == "1", "five timing summaries preserve one actual request")
        # A second actual event reuses the same complete original SQL.
        self.sql("INSERT INTO evidence_record SELECT 'R2',file_id,source_id,scope_id,2,3,3,decode_state,observed FROM evidence_record WHERE record_id='R1'; "
                 "INSERT INTO mpp_occurrence SELECT (jsonb_populate_record(NULL::mpp_occurrence,to_jsonb(o)||'{\"occurrence_id\":\"O2\",\"anchor_ref\":\"R2\"}'::jsonb)).* FROM mpp_occurrence o WHERE occurrence_id='O1'")
        self.require(self.sql("SELECT (SELECT count(*) FROM mpp_sql_text)||':'||(SELECT count(*) FROM mpp_occurrence)") == "1:2",
                     "original SQL reused; actual executions retained separately")
        self.sql("UPDATE mpp_occurrence SET duration_ms=0,estimated_start_at=end_at WHERE occurrence_id='O2'")
        self.require(self.sql("SELECT duration_ms=0 AND estimated_start_at=end_at FROM mpp_occurrence WHERE occurrence_id='O2'") == "t", "real zero duration")
        self.sql("UPDATE mpp_occurrence SET duration_ms=NULL,estimated_start_at=NULL,start_basis=NULL,value_reasons='{\"duration_ms\":\"unknown\",\"estimated_start_at\":\"duration unknown\"}' WHERE occurrence_id='O2'")
        self.require(self.sql("SELECT duration_ms IS NULL FROM mpp_occurrence WHERE occurrence_id='O2'") == "t", "unknown duration remains NULL with reasons")
        self.sql("UPDATE mpp_occurrence SET duration_ms=0.001,end_at='2026-09-21 00:00:00.000001+08',estimated_start_at='2026-09-21 00:00:00+08',start_basis='end_minus_duration',value_reasons='{}' WHERE occurrence_id='O2'")
        self.require(self.sql("SELECT extract(epoch FROM end_at-estimated_start_at)*1000=duration_ms FROM mpp_occurrence WHERE occurrence_id='O2'") == "t",
                     "decimal milliseconds and microsecond timestamps round-trip")
        self.rejects("UPDATE mpp_occurrence SET duration_ms=-1 WHERE occurrence_id='O2'", "negative duration rejected")
        for bad in ("NaN", "Infinity", "-Infinity"):
            self.rejects("UPDATE mpp_occurrence SET duration_ms='" + bad + "' WHERE occurrence_id='O2'", bad + " duration rejected")
        self.rejects("UPDATE mpp_occurrence SET duration_ms=NULL WHERE occurrence_id='O2'", "unknown duration without explanation rejected")
        self.rejects("UPDATE mpp_occurrence SET sql_id=NULL WHERE occurrence_id='O1'", "complete SQL cannot lose its reference")
        self.rejects("UPDATE mpp_occurrence SET sql_id='missing' WHERE occurrence_id='O1'", "dangling original SQL reference rejected")
        self.rejects("UPDATE mpp_occurrence SET timing_type='execute_first' WHERE occurrence_id='O1'", "request/call mismatch rejected")
        self.rejects("UPDATE mpp_occurrence SET association_state='unpaired',association_reason='unknown',unit='call',timing_type='execute_first' WHERE occurrence_id='O1'", "unpaired execute classification rejected")
        self.rejects("UPDATE evidence_record SET record_no=1 WHERE record_id='R2'", "file logical record uniqueness")
        self.rejects("UPDATE mpp_statistic SET bucket_number=24 WHERE layer='hour'", "invalid hour bucket rejected")
        self.rejects("UPDATE mpp_statistic SET p95_ms=NULL WHERE statistic_id='ST1'", "missing metric without reason rejected")
        self.rejects("UPDATE mpp_statistic SET cv=NULL,metric_null_reasons='{\"cv\":\"zero_denominator\"}' WHERE statistic_id='ST1'", "zero denominator reason must match actual denominator")
        self.rejects("UPDATE mpp_statistic SET p95_ms=1 WHERE statistic_id='ST1'", "quantile ordering rejected")
        self.rejects("INSERT INTO mpp_statistic SELECT (jsonb_populate_record(NULL::mpp_statistic,to_jsonb(s)||'{\"statistic_id\":\"duplicate\"}'::jsonb)).* FROM mpp_statistic s WHERE statistic_id='ST1'", "overall NULL bucket uniqueness")
        self.rejects("DELETE FROM mpp_normalization WHERE normalization_id='N1'", "historical rule references prevent deletion")
        self.rejects("UPDATE current_version SET last_success_at=last_success_at+interval '1 second'", "current pointer must match successful publication time")
        self.sql("INSERT INTO publication VALUES ('PUB_FAIL','CL1','V1','V1','publish_failed','synthetic failure','2026-10-02 00:00:00+08')")
        self.rejects("UPDATE current_version SET publication_id='PUB_FAIL',last_success_at='2026-10-02 00:00:00+08'", "failed publication cannot become current")
        self.require(self.sql("SELECT build_id||':'||publication_id FROM current_version") == "V1:PUB1", "failed writes preserve current version")
        self.sql("INSERT INTO scope VALUES ('CL2','hashdata','hashdata-csv/1','1.0.0')")
        self.rejects("UPDATE mpp_decision SET scope_id='CL2'", "cross-cluster build references rejected")

        self.rejects("UPDATE mpp_statistic SET sufficiency=jsonb_set(sufficiency,'{basic,met}','true') WHERE statistic_id='ST1'", "false sufficiency claim rejected")
        self.rejects("UPDATE mpp_statistic SET sufficiency=jsonb_set(sufficiency,'{p95,actual_count}','99') WHERE statistic_id='ST1'", "threshold sample count must match mpp_statistic")
        self.rejects("UPDATE mpp_decision SET rule_evaluations=jsonb_set(rule_evaluations,'{window}','null')", "unknown rule evaluation rejected")
        self.rejects("UPDATE mpp_sql_text SET content_sha256=decode(repeat('00',32),'hex')", "SQL content checksum mismatch rejected")
        stat = json.loads(self.sql("SELECT row_to_json(s) FROM mpp_statistic s WHERE statistic_id='ST2'"))
        metric_names = list(json.loads((ROOT / "docs/design/offline-data-contract/examples.json").read_text())["metric_names"])
        for metric in metric_names:
            stat[metric] = None
        stat.update(statistic_id="EMPTY", bucket_date="2026-09-22", range_start="2026-09-22T00:00:00+08:00",
                    range_end="2026-09-23T00:00:00+08:00", included_count=0, excluded_count=1,
                    exclusions_by_reason={"duration_unknown": 1}, active_dates=[], active_week_starts=[],
                    first_sample_at=None, last_sample_at=None,
                    metric_null_reasons={m: "no_samples" for m in metric_names})
        for result in stat["sufficiency"].values():
            result["actual_count"] = 0
            result["actual_coverage"] = 0
        payload = json.dumps(stat).replace("'", "''")
        self.require(self.sql("BEGIN; INSERT INTO mpp_statistic SELECT (jsonb_populate_record(NULL::mpp_statistic,'" + payload + "'::jsonb)).*; SELECT included_count=0 AND excluded_count=1 AND mean_ms IS NULL FROM mpp_statistic WHERE statistic_id='EMPTY'; ROLLBACK") == "t",
                     "zero samples with exclusions stored explicitly; all 17 metrics carry no_samples")
        stat.update(statistic_id="ZERO", included_count=1, excluded_count=0, exclusions_by_reason={},
                    active_dates=["2026-09-22"], active_week_starts=["2026-09-21"],
                    first_sample_at="2026-09-22T08:00:00+08:00", last_sample_at="2026-09-22T08:00:00+08:00",
                    metric_null_reasons={m: "zero_denominator" for m in ("cv","p95_p50","p99_p50")})
        for metric in metric_names:
            stat[metric] = None if metric in stat["metric_null_reasons"] else 0
        for result in stat["sufficiency"].values():
            result["actual_count"] = 1
        payload = json.dumps(stat).replace("'", "''")
        self.require(self.sql("BEGIN; INSERT INTO mpp_statistic SELECT (jsonb_populate_record(NULL::mpp_statistic,'" + payload + "'::jsonb)).*; SELECT mean_ms=0 AND cv IS NULL FROM mpp_statistic WHERE statistic_id='ZERO'; ROLLBACK") == "t",
                     "zero duration metrics and zero denominator reasons remain distinct")
        self.sql("INSERT INTO mpp_normalization SELECT 'N2',algorithm_version,parser_version,dictionary_schema_version,'synthetic-next',dictionary_digest_algorithm,'synthetic-next',rules_ref FROM mpp_normalization WHERE normalization_id='N1'")
        self.rejects("UPDATE mpp_decision SET normalization_id='N2'", "cross-mpp_normalization build/mpp_decision reference rejected")
        self.sql("INSERT INTO scope VALUES ('JOB','batch-job-example','batch-job-example/1','1.0.0'); "
                 "INSERT INTO input_snapshot VALUES ('JOB_IN','JOB','immutable_manifest','synthetic:job-runs','2026-10-01 00:00:00+00'); "
                 "INSERT INTO config_snapshot SELECT 'JOB_CFG','JOB',NULL,'batch-job-example/1','[]',cutoff_date,window_days,window_start,window_end,'{}','[]','{}','synthetic:job-formulas' FROM config_snapshot WHERE config_id='CFG1'; "
                 "INSERT INTO build VALUES ('JOB_BUILD','JOB','JOB_IN','JOB_CFG',NULL,'batch-job-example/1',NULL,'running','2026-10-01 00:00:00+00',NULL,false)")
        self.require(self.sql("SELECT normalization_id IS NULL FROM build WHERE build_id='JOB_BUILD'") == "t",
                     "generic build references do not require synthetic SQL for non-SQL extension example")

        # Install real SCRAM authentication; bootstrap must preserve it on rerun.
        secret = secrets.token_hex(24)
        self.sql("SET password_encryption='scram-sha-256'; ALTER ROLE sql_apm PASSWORD '" + secret + "'", admin=True)
        password_before = self.sql("SELECT rolpassword FROM pg_authid WHERE rolname='sql_apm'", admin=True)
        (self.directory / "data/pg_hba.conf").write_text("local all apm_test_admin trust\nlocal all all scram-sha-256\n")
        run([self.pg_bin / "pg_ctl", "-D", self.directory / "data", "reload"], self.env)
        passfile = self.directory / "pgpass"
        passfile.write_text("*:55473:*:sql_apm:" + secret + "\n")
        passfile.chmod(0o600)
        self.env["PGPASSFILE"] = str(passfile)
        before = self.sql("SELECT row_to_json(v) FROM schema_version v")
        self.init()
        self.init("check")
        self.require(self.sql("SELECT count(*) FROM mpp_occurrence") == "2" and self.sql("SELECT row_to_json(v) FROM schema_version v") == before,
                     "complete rerun preserves data and original version timestamp")
        self.require(password_before == self.sql("SELECT rolpassword FROM pg_authid WHERE rolname='sql_apm'", admin=True),
                     "rerun preserves existing SCRAM password")

        self.sql("ALTER ROLE sql_apm CREATEDB", admin=True)
        self.require("incompatible project role" in self.init(ok=False).stderr, "incompatible existing role rejected without resetting privileges")
        self.sql("ALTER ROLE sql_apm NOCREATEDB", admin=True)
        self.sql("ALTER SCHEMA sql_apm OWNER TO apm_test_admin", admin=True, database="sql_apm")
        self.require("incompatible schema owner" in self.init(ok=False).stderr, "incompatible schema owner rejected")
        self.sql("ALTER SCHEMA sql_apm OWNER TO sql_apm", admin=True, database="sql_apm")
        self.sql("ALTER TABLE sql_apm.mpp_statistic OWNER TO apm_test_admin", admin=True, database="sql_apm")
        self.require("incompatible object: mpp_statistic" in self.init(ok=False).stderr, "incompatible table owner rejected")
        self.sql("ALTER TABLE sql_apm.mpp_statistic OWNER TO sql_apm", admin=True, database="sql_apm")
        self.sql("DROP INDEX mpp_statistic_group_build_idx; CREATE INDEX mpp_statistic_group_build_idx ON mpp_statistic (layer)")
        self.require("indexes" in self.init(ok=False).stderr, "same-name index with wrong definition rejected")
        self.sql("DROP INDEX mpp_statistic_group_build_idx; CREATE INDEX mpp_statistic_group_build_idx ON mpp_statistic (group_id,build_id,layer)")
        self.sql("ALTER TABLE source ADD CHECK (mapping_ref <> 'conflict')")
        self.require("constraints" in self.init(ok=False).stderr, "unexpected same-table constraint rejected")
        self.sql("ALTER TABLE source DROP CONSTRAINT source_mapping_ref_check1")

        self.sql("ALTER TABLE mpp_statistic ADD COLUMN accidental integer")
        failure = self.init(ok=False)
        self.require("incompatible object: mpp_statistic" in failure.stderr, "catalog drift rejected with object diagnostic")
        self.sql("ALTER TABLE mpp_statistic DROP COLUMN accidental")
        self.init("check")
        self.sql("UPDATE schema_version SET script_sha256=repeat('0',64)")
        self.require("checksum" in self.init(ok=False).stderr, "incorrect version checksum rejected")
        self.sql("UPDATE schema_version SET script_sha256='" + hashlib.sha256((ROOT / "sql_apm/storage/schema.sql").read_bytes()).hexdigest() + "'")
        # Further independent fixtures use trust on this isolated private socket.
        (self.directory / "data/pg_hba.conf").write_text("local all all trust\n")
        run([self.pg_bin / "pg_ctl", "-D", self.directory / "data", "reload"], self.env)
        custom = ("apm_custom_db", "apm_custom_schema", "apm_custom_user")
        self.init(names=custom)
        self.init("check", names=custom)
        self.require(True, "independently configurable database, schema and role")

        partial = ("apm_partial", "apm_partial", "apm_partial")
        self.init("bootstrap", names=partial)
        self.sql("SET ROLE apm_partial; CREATE SCHEMA apm_partial; SET search_path=apm_partial,pg_catalog; "
                 "CREATE TABLE scope (scope_id text PRIMARY KEY CHECK (scope_id <> ''), system_kind text NOT NULL CHECK (system_kind <> ''), profile text NOT NULL CHECK (profile <> ''), contract_version text NOT NULL CHECK (contract_version <> ''), UNIQUE(scope_id,profile)); "
                 "INSERT INTO scope VALUES ('retained','hashdata','hashdata-csv/1','1.0.0')",
                 admin=True, database="apm_partial")
        self.init(names=partial)
        self.require(self.sql("SELECT scope_id FROM apm_partial.scope", admin=True, database="apm_partial") == "retained",
                     "compatible partial schema reused and existing data retained")

        bad_owner = ("apm_conflict", "sql_apm", "apm_conflict_role")
        self.sql("CREATE DATABASE apm_conflict", admin=True)
        self.require("incompatible project database" in self.init(names=bad_owner, ok=False).stderr, "pre-existing database owner conflict")
        self.require(self.sql("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='apm_conflict'", admin=True) == "apm_test_admin", "conflicting database not taken over")
        # A checked copy injects a DDL failure, exercising the real phase boundary.
        copy_root = self.directory / "failure_case"
        shutil.copytree(ROOT / "sql_apm/storage", copy_root / "sql_apm/storage")
        (copy_root / "scripts/db").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/db/initialize.sh", copy_root / "scripts/db/initialize.sh")
        initializer = copy_root / "sql_apm/storage/initialize.sql"
        original = initializer.read_text()
        initializer.write_text(original.replace("    COMMIT;", "    SELECT 1/0;\n    COMMIT;"))
        recover = ("apm_recover", "apm_recover", "apm_recover")
        self.require("division by zero" in self.init(names=recover, root=copy_root, ok=False).stderr, "failure after project DDL rolls back schema phase")
        self.require(self.sql("SELECT count(*) FROM pg_namespace WHERE nspname='apm_recover'", admin=True, database="apm_recover") == "0", "failed transaction leaves no project schema")
        self.require(self.sql("SELECT count(*) FROM pg_roles WHERE rolname='apm_recover'", admin=True) == "1", "bootstrap role retained for retry")
        initializer.write_text(original)
        self.init(names=recover, root=copy_root)
        self.init("check", names=recover)
        self.require(True, "corrected rerun reuses database/role and completes")
        self.require(self.sql("SELECT count(*) FROM pg_namespace WHERE nspname LIKE '_apm_expected_%'") == "0", "no scratch schema remains")
        # PostgreSQL itself is restarted to verify on-disk persistence.
        run([self.pg_bin / "pg_ctl", "-D", self.directory / "data", "-m", "fast", "-w", "restart", "-l", self.directory / "server.log"], self.env)
        self.require(self.sql("SELECT count(*) FROM mpp_statistic") == "5", "restart persistence")
        print("RESULT: " + str(self.completed) + " storage checks passed", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", type=Path, default=Path("/usr/pgsql-17/bin"))
    args = parser.parse_args()
    for name in ("initdb", "pg_ctl", "psql"):
        if not (args.pg_bin / name).is_file():
            parser.error("missing PostgreSQL executable: " + name)
    version = run([args.pg_bin / "psql", "--version"]).stdout.strip()
    if " 17." not in version:
        parser.error("requires PostgreSQL 17")
    print("VERSION: " + version, flush=True)
    def interrupted(signum, frame):
        raise RuntimeError("interrupted by signal " + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    with instance(args.pg_bin) as (directory, env):
        Verification(args.pg_bin, directory, env).tests()
    with instance(args.pg_bin) as (directory, env):
        verify_migration(Verification(args.pg_bin, directory, env), ROOT, run)
    # Exercise exceptional cleanup through exactly the same owner/context manager.
    failure_directory = None
    try:
        with instance(args.pg_bin) as (failure_directory, env):
            raise ValueError("synthetic failure after startup")
    except ValueError:
        if failure_directory.exists():
            raise AssertionError("exception cleanup failed")
    print("PASS: exceptional instance cleanup", flush=True)


if __name__ == "__main__":
    main()
