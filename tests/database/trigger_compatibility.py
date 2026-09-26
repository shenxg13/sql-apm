"""FK trigger drift checks against disposable databases, including both FK sides."""


def verify_trigger_modes(v, names, runner, state, legacy=False):
    database, schema, role = names

    def sql(statement, admin=False, ok=True):
        return runner([v.pg_bin / "psql", "-X", "-w", "-Atq", "-v", "ON_ERROR_STOP=1",
                       "-h", v.directory / "socket", "-p", "55473",
                       "-U", "apm_test_admin" if admin else role, "-d", database],
                      v.env, 'SET search_path="' + schema + '",pg_catalog;\n' + statement, ok=ok)

    def snapshot():
        triggers = sql("SELECT t.oid,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                       "WHERE c.relnamespace='" + schema + "'::regnamespace ORDER BY t.oid").stdout
        receipt = sql("SELECT to_jsonb(v) FROM schema_version v ORDER BY version").stdout
        return state(), triggers, receipt

    child, parent = ("occurrence", "sql_text") if legacy else ("source", "scope")
    modes = ("upgrade",) if legacy else ("all", "schema", "check", "upgrade")
    # DISABLE ALL reproduces R1. Single-trigger overrides also exercise a mixed
    # O/R (or O/D) table and the action triggers on the referenced side of a FK.
    cases = ((child, "D", None), (child, "R", "RI_FKey_check_ins"),
             (child, "A", "RI_FKey_check_ins"),
             (parent, "D", "RI_FKey_noaction_del"), (parent, "R", "RI_FKey_noaction_del"))
    before = snapshot()
    for table, enabled, function in cases:
        trigger = "ALL" if function is None else sql(
            "SELECT quote_ident(t.tgname) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid "
            "WHERE t.tgisinternal AND t.tgrelid='" + table + "'::regclass AND p.proname='" + function +
            "' ORDER BY t.tgname LIMIT 1").stdout.strip()
        if not trigger:
            raise AssertionError("missing FK trigger for " + table + ":" + str(function))
        action = {"D": "DISABLE TRIGGER ", "R": "ENABLE REPLICA TRIGGER ", "A": "ENABLE ALWAYS TRIGGER "}[enabled]
        sql('ALTER TABLE "' + table + '" ' + action + trigger, admin=True)
        drifted = snapshot()
        if drifted == before:
            raise AssertionError("trigger fault was not installed")
        for mode in modes:
            failure = v.init(mode, names=names, ok=False)
            v.require("incompatible object: " + table in failure.stderr
                      and "foreign_key_trigger_overrides" in failure.stderr
                      and snapshot() == drifted
                      and sql("SELECT count(*) FROM pg_namespace WHERE nspname LIKE '_apm_expected_%' "
                              "OR nspname LIKE '_apm_legacy_%'").stdout.strip() == "0",
                      schema + ": " + mode + " rejects " + table + " FK mode " + enabled +
                      " without changing rows, objects, receipts or triggers")
        sql('ALTER TABLE "' + table + '" ENABLE TRIGGER ' + trigger, admin=True)
        v.require(snapshot() == before, schema + ": explicit trigger restoration preserves original state")

    if not legacy:
        for mode in modes:
            v.init(mode, names=names)
        v.require(snapshot() == before, schema + ": corrected all/schema/check/upgrade retry preserves state")
    # A failed statement must be the FK rejection, not an unrelated SQL error.
    # Each probe rolls back and runs as the actual ordinary project account.
    probes = ("UPDATE occurrence SET sql_id='MISSING'", "DELETE FROM sql_text") if legacy else (
        "INSERT INTO source VALUES ('orphan','MISSING','map','build','UTC+08:00','synthetic')",
        "DELETE FROM scope WHERE scope_id='TRIGGER_SCOPE'")
    for probe in probes:
        failure = sql("BEGIN; " + probe + "; ROLLBACK;", ok=False)
        v.require("foreign key constraint" in failure.stderr and snapshot() == before,
                  schema + ": restored FK rejects ordinary-role " + probe.split()[0])
    # Legacy upgrade/retry and data/OID preservation are checked by the caller.


def verify_current_triggers(v, runner):
    for names in (("sql_apm",) * 3, ("apm_trigger_db", "apm_trigger_store", "apm_trigger_role")):
        database, schema, role = names

        def sql(statement):
            return runner([v.pg_bin / "psql", "-X", "-w", "-Atq", "-v", "ON_ERROR_STOP=1",
                           "-h", v.directory / "socket", "-p", "55473", "-U", role, "-d", database],
                          v.env, 'SET search_path="' + schema + '",pg_catalog;\n' + statement).stdout

        def state():
            return sql("SELECT to_jsonb(s) FROM scope s ORDER BY scope_id; "
                       "SELECT to_jsonb(s) FROM source s ORDER BY source_id; "
                       "SELECT oid,relname,relfilenode FROM pg_class WHERE relnamespace='" + schema +
                       "'::regnamespace ORDER BY oid; "
                       "SELECT oid,conname FROM pg_constraint WHERE connamespace='" + schema +
                       "'::regnamespace ORDER BY oid")

        v.init(names=names)
        sql("INSERT INTO scope VALUES ('TRIGGER_SCOPE','hashdata','hashdata-csv/1','1.0.0'); "
            "INSERT INTO source VALUES ('TRIGGER_SOURCE','TRIGGER_SCOPE','map','build','UTC+08:00','synthetic')")
        verify_trigger_modes(v, names, runner, state)
    print("RESULT: " + str(v.completed) + " FK trigger compatibility checks passed", flush=True)
