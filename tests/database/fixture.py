"""Translate only the committed synthetic base fixture into storage inserts."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return "'" + value.replace("'", "''") + "'"


def insert(table, values):
    return 'INSERT INTO "' + table + '" (' + ",".join(
        '"' + k + '"' for k in values
    ) + ") VALUES (" + ",".join(literal(v) for v in values.values()) + ");"


def statements():
    doc = json.loads((ROOT / "docs/design/offline-data-contract/examples.json").read_text())
    b = doc["base"]
    sql = []
    def add(table, values):
        sql.append(insert(table, values))
    def fields(obj, omitted=(), **extra):
        return dict({k: v for k, v in obj.items() if k not in omitted}, **extra)
    context = dict(scope_id="CL1", normalization_id="N1", profile=doc["profile"])
    add("scope", dict(scope_id="CL1", system_kind="hashdata",
                      profile=doc["profile"], contract_version=doc["contract_version"]))
    for o in b["sources"].values():
        add("source", fields(o, ("system_kind",)))
    for o in b["files"].values():
        add("source_file", fields(o, ("checksum",), scope_id="CL1",
            checksum_algorithm=o["checksum"]["algorithm"], checksum_value=o["checksum"]["value"]))
    for o in b["batches"].values():
        add("import_batch", fields(o, ("declared_dates", "entries", "problem_ids")))
        for date in o["declared_dates"]:
            add("batch_date", dict(batch_id=o["batch_id"], declared_date=date))
    for o in b["import_attempts"].values():
        add("import_attempt", fields(o, ("problem_ids",), scope_id="CL1"))
    for o in b["batches"].values():
        for entry in o["entries"]:
            add("batch_entry", dict(entry, batch_id=o["batch_id"], scope_id="CL1"))
    for o in b["evidence_records"].values():
        add("evidence_record", fields(o, ("problem_ids",), source_id="S1", scope_id="CL1"))
    for o in b["analyses"].values():
        add("analysis", fields(o, ("file_ids",), scope_id="CL1"))
        for file_id in o["file_ids"]:
            add("analysis_file", dict(analysis_id=o["analysis_id"], file_id=file_id, scope_id="CL1"))
    for o in b["sql_texts"].values():
        sql.append("INSERT INTO sql_text (sql_id,text,content_sha256) VALUES (" + literal(o["sql_id"]) + "," + literal(o["text"]) + ",sha256(convert_to(" + literal(o["text"]) + ",'UTF8')));")
        for record in o["evidence_refs"]:
            add("sql_text_evidence", dict(sql_id=o["sql_id"], record_id=record))
    for o in b["occurrences"].values():
        add("occurrence", fields(o, ("evidence_refs", "outcome_evidence_refs", "association"),
            association_state=o["association"]["state"], association_method=o["association"]["method"],
            association_reason=o["association"]["reason"]))
        for purpose, records in (("support", o["evidence_refs"]), ("outcome", o["outcome_evidence_refs"]),
                                 ("association", o["association"]["evidence_refs"])):
            for record in records:
                add("occurrence_evidence", dict(analysis_id=o["analysis_id"], occurrence_id=o["occurrence_id"],
                    record_id=record, purpose=purpose))
    for o in b["normalizations"].values():
        add("normalization", fields(o, ("dictionary_digest",),
            dictionary_digest_algorithm=o["dictionary_digest"]["algorithm"],
            dictionary_digest_value=o["dictionary_digest"]["value"]))
    for o in b["fingerprints"].values():
        add("fingerprint", dict(o, profile=doc["profile"]))
    for o in b["groups"].values():
        add("baseline_group", fields(o, ("cluster_id",), scope_id=o["cluster_id"],
            fingerprint_value=b["fingerprints"][o["fingerprint_id"]]["value"]))
    for o in b["input_snapshots"].values():
        add("input_snapshot", fields(o, ("batch_ids", "file_ids", "analysis_ids", "selection"),
            selection_kind=o["selection"]["kind"], manifest_ref=o["selection"]["manifest_ref"]))
        for key, table, id_key in (("batch_ids", "input_batch", "batch_id"),
                                   ("file_ids", "input_file", "file_id"), ("analysis_ids", "input_analysis", "analysis_id")):
            for val in o[key]:
                add(table, dict(input_id=o["input_id"], scope_id="CL1", **{id_key: val}))
        for ref in o["selection"]["refs"]:
            add("input_occurrence", dict(ref, input_id=o["input_id"], scope_id="CL1"))
    for o in b["config_snapshots"].values():
        add("config_snapshot", fields(o, ("window",), profile=doc["profile"],
            cutoff_date=o["window"]["cutoff_date"], window_days=o["window"]["days"],
            window_start=o["window"]["start"], window_end=o["window"]["end"]))
    for o in b["builds"].values():
        add("build", fields(o, ("checks", "timing_coverage", "statistic_ids", "coverage_index", "problem_ids"),
            normalization_id="N1", profile=doc["profile"]))
        for name, check in o["checks"].items():
            add("build_check", dict(check, build_id=o["build_id"], name=name))
        for timing, counts in o["timing_coverage"].items():
            add("build_timing_coverage", fields(counts, ("group_ids",), build_id=o["build_id"], timing_type=timing))
        for coverage in o["coverage_index"]:
            add("build_coverage", dict(coverage, build_id=o["build_id"], **context))
    for o in b["decisions"].values():
        add("decision", fields(o, ("occurrence_ref", "reasons"),
            fingerprint_value=b["fingerprints"][o["fingerprint_id"]]["value"], **o["occurrence_ref"], **context))
    for o in b["statistics"].values():
        bucket = o["bucket"]
        vals = fields(o, ("bucket", "metrics"), **context, layer=bucket["layer"],
            bucket_date=bucket["key"] if bucket["layer"] in ("day", "week") else None,
            bucket_number=bucket["key"] if bucket["layer"] in ("weekday", "hour") else None,
            range_start=bucket["range_start"], range_end=bucket["range_end"], partial_week=bucket["partial_week"],
            metric_null_reasons={k: v["reason"] for k, v in o["metrics"].items() if v["reason"] is not None})
        vals.update({k: v["value"] for k, v in o["metrics"].items()})
        for key in ("active_dates", "active_week_starts"):
            vals[key] = "{" + ",".join(o[key]) + "}"
        add("statistic", vals)
    for o in b["publications"].values():
        add("publication", o)
    for o in b["current_versions"].values():
        add("current_version", o)
    for o in b["tasks"].values():
        add("task", fields(o, ("batch_ids", "build_ids", "publication_ids")))
        for key, table, field in (("batch_ids","task_batch","batch_id"), ("build_ids","task_build","build_id"),
                                  ("publication_ids","task_publication","publication_id")):
            for val in o[key]:
                row = dict(task_id=o["task_id"], **{field: val})
                row["scope_id"] = o["scope_id"]
                add(table, row)
    return "\n".join(sql)
