"""Synthetic storage checks and verification-only SQL mapping (not an importer)."""
import hashlib
import json

from sql_apm.sql.approximate import fingerprint


def literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    return "'" + value.replace("'", "''") + "'"


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def rule_sql(result):
    values = [result['algorithm_version'] + ':' + result['rules_digest'],
              result['algorithm_version'], result['profile'], result['rules_digest'],
              result['rules_ref'], canonical(result['rules'])]
    return 'INSERT INTO mpp_approximate_rule VALUES (' + ','.join(map(literal, values)) + ') ON CONFLICT DO NOTHING;\n'


def insert_sql(identity, raw, result):
    """Serial test writer; compare exact bytes after narrowing by SHA-256."""
    qid = literal(identity)
    data = "decode(" + literal(raw.hex()) + ",'hex')"
    sha = "decode(" + literal(hashlib.sha256(raw).hexdigest()) + ",'hex')"
    rule = result['algorithm_version'] + ':' + result['rules_digest']
    fields = [rule, result['algorithm_version'], result['kind'], result['state'], result['value'],
              result['reason'], result['structural_reason'], result['observation_only'], 'unverified',
              result['source']['bytes_base64'] is not None,
              canonical(result['normalized']) if result['normalized'] is not None else None]
    tail = ','.join(map(literal, fields))
    diagnostics = 'ARRAY[' + ','.join(map(literal, result['diagnostics'])) + ']::text[]'
    return ("INSERT INTO mpp_approximate_input SELECT " + qid + ',' + data + ',' + str(len(raw)) + ',' + sha +
            ' WHERE NOT EXISTS (SELECT FROM mpp_approximate_input WHERE source_sha256=' + sha + ' AND raw_bytes=' + data + ');\n'
            'INSERT INTO mpp_approximate_result SELECT ' + qid + ',input_id,' + tail + ',' + diagnostics + ',' + str(result['replacements']) +
            ' FROM mpp_approximate_input WHERE source_sha256=' + sha + ' AND raw_bytes=' + data +
            ' ON CONFLICT (input_id,rule_id,structural_reason) DO NOTHING;\n')


READ_SQL = '''SELECT row_to_json(t) FROM (
    SELECT r.*, i.byte_length, encode(i.source_sha256,'hex') AS sha256,
           replace(encode(i.raw_bytes,'base64'),E'\\n','') AS bytes_base64,
           q.profile,q.rules_digest,q.rules_ref,q.rules
    FROM mpp_approximate_result r JOIN mpp_approximate_input i USING(input_id)
    JOIN mpp_approximate_rule q USING(rule_id,algorithm_version)
    ORDER BY result_id) t'''


def unpack(row):
    return dict(kind=row['kind'], state=row['state'], value=row['value'], reason=row['reason'],
                algorithm_version=row['algorithm_version'], profile=row['profile'], rules_digest=row['rules_digest'],
                rules_ref=row['rules_ref'], rules=row['rules'], structural_reason=row['structural_reason'],
                observation_only=row['observation_only'],
                source=dict(byte_length=row['byte_length'], sha256=row['sha256'],
                            bytes_base64=row['bytes_base64'] if row['source_bytes_included'] else None),
                normalized=json.loads(row['normalized']) if row['normalized'] is not None else None,
                diagnostics=row['diagnostics'], replacements=row['replacements'])


def verify_approximate(v):
    cases = [b'SELECT * FROM t WHERE id IN (1,', b'/*', b'SELECT \xff\x00', b'SELECT ' + b'x' * (512 * 1024)]
    results = [fingerprint(raw, structural_reason='synthetic_rejection') for raw in cases]
    failed = dict(results[1], state='failed', reason='approximation_exception')
    # A failed caller envelope needs explicit frozen rules/source context.
    results.append(failed)
    cases.append(cases[1])
    failed['structural_reason'] = 'different_rejection'
    cases.append(b'')
    results.append(fingerprint(b'', structural_reason='sql_missing_or_empty'))
    v.sql(rule_sql(results[0]))
    for index, (raw, result) in enumerate(zip(cases, results)):
        v.sql(insert_sql('A' + str(index), raw, result))
    rows = [json.loads(line) for line in v.sql(READ_SQL).splitlines()]
    v.require([unpack(row) for row in rows] == results, 'all approximate states and invalid bytes round-trip without JSONB loss')
    for index, (raw, result) in enumerate(zip(cases, results)):
        v.sql(insert_sql('REPEAT' + str(index), raw, result))
    v.require(v.sql('SELECT (SELECT count(*) FROM mpp_approximate_input)||\':\'||(SELECT count(*) FROM mpp_approximate_result)') == '5:6',
              'exact bytes and results reused; different structural reasons remain separate')
    for field, value in [('algorithm_version','NULL'), ('state','NULL'), ('structural_reason','NULL'),
                         ('value','NULL'), ('kind',"'reliable'"), ('observation_only','false'),
                         ('completeness',"'complete'"), ('normalized','NULL'), ('normalized',"'[]'"),
                         ('source_bytes_included','false'), ('replacements','-1'),
                         ('value',"'approx:sql-approximate/99:'||repeat('a',64)"),
                         ('value',"'sha256:'||repeat('a',64)")]:
        v.rejects('UPDATE mpp_approximate_result SET ' + field + '=' + value + " WHERE result_id='A0'", 'approximate required/available guard: ' + field + '=' + value)
    for identity in ('A1', 'A4'):
        v.rejects("UPDATE mpp_approximate_result SET reason=NULL WHERE result_id='" + identity + "'", 'unavailable/failed require reason: ' + identity)
        v.rejects("UPDATE mpp_approximate_result SET value='approx:x' WHERE result_id='" + identity + "'", 'unavailable/failed reject value: ' + identity)
    v.rejects("UPDATE mpp_approximate_input SET source_sha256=decode(repeat('00',32),'hex')", 'approximate source digest verified')
    v.rejects('UPDATE mpp_approximate_input SET byte_length=byte_length+1', 'approximate source length verified')
    v.rejects("INSERT INTO mpp_fingerprint SELECT (jsonb_populate_record(NULL::mpp_fingerprint,to_jsonb(f)||'{\"fingerprint_id\":\"bad\",\"normalization_id\":\"N2\",\"value\":\"approx:bad\"}')).* FROM mpp_fingerprint f LIMIT 1", 'reliable fingerprint rejects approximate prefix')
    v.rejects("UPDATE mpp_baseline_group SET fingerprint_id='A0',fingerprint_value=(SELECT value FROM mpp_approximate_result WHERE result_id='A0')", 'Group cannot reference approximate result')
    v.rejects("UPDATE mpp_decision SET fingerprint_id='A0',fingerprint_value=(SELECT value FROM mpp_approximate_result WHERE result_id='A0')", 'Decision cannot reference approximate result')
    rule = literal(results[0]['algorithm_version'] + ':' + results[0]['rules_digest'])
    # Existing two fixture events remain distinct even with one shared near result.
    v.sql("UPDATE mpp_occurrence SET sql_id=NULL,sql_state='incomplete'; "
          "INSERT INTO mpp_approximate_evidence SELECT 'A0',record_id FROM evidence_record; "
          'INSERT INTO mpp_occurrence_approximate SELECT analysis_id,occurrence_id,scope_id,' + rule + ",'A0',anchor_ref,source_id FROM mpp_occurrence")
    v.require(v.sql('SELECT count(*)||\':\'||count(DISTINCT result_id) FROM mpp_occurrence_approximate') == '2:1',
              'two incomplete occurrences reference one result without complete SqlText')
    v.rejects("UPDATE mpp_occurrence_approximate SET result_id='A1'", 'occurrence must reference associated evidence')
    v.rejects("UPDATE mpp_occurrence_approximate SET scope_id='CL2'", 'approximate occurrence cannot cross cluster')
    v.rejects("UPDATE mpp_occurrence_approximate SET rule_id='missing'", 'approximate occurrence cannot cross rule')
    v.init('check')
