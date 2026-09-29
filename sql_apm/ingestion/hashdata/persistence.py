"""HashData field-to-MPP persistence mapping; no generic-source assumptions."""
from sql_apm.ingestion.config import canonical, identity
from sql_apm.ingestion.hashdata.reader import raw_bytes, valid_text, site
from sql_apm.storage.ingestion import copy_rows

ASSOCIATION = 'execute-file-sequence/1'


def write_records(self, cur, fid, buffer, counts):
    config = self.config
    texts = [raw_bytes(row[24]) for _, _, _, _, row, event, duration in buffer if (event or duration) and row[24].strip()]
    resolved = self.writer.resolve(texts)
    evidence, occurrences, sql_links, support, approx_links, approx_events, problems = [], [], [], [], [], [], []
    for rid, number, begin, end, row, event, duration in buffer:
        result = resolved.get(raw_bytes(row[24])) if (event or duration) and row[24].strip() else None
        # Original files + checksums are immutable evidence. Keep scalar identity/time
        # columns for every record, and original SQL/message on problematic records.
        keep = (0, 1, 2, 3, 7, 9, 10, 11, 16, 17, 27, 28)
        observed = {str(i).zfill(2): row[i] if valid_text(row[i]) else None for i in keep}
        if event or duration or row[16] in ('ERROR', 'FATAL', 'PANIC') or site(row) == 'postgres.c:2764':
            for i in (18, 19, 21, 24):
                if i == 24 and result and result['sql_id']:
                    continue
                observed[str(i).zfill(2)] = row[i] if valid_text(row[i]) else None
        decoded = all(valid_text(x) for x in row)
        evidence.append((rid, fid, config['source_id'], config['scope_id'], number, begin, end,
                         'decoded' if decoded else 'invalid', canonical(observed)))
        if not decoded:
            problems.append((rid, 'record_encoding_invalid', rid if event else None))
        if result:
            if result['sql_id']:
                sql_links.append((result['sql_id'], rid))
            if result['approximate_id']:
                approx_links.append((result['approximate_id'], rid))
            if result['fingerprint']['state'] != 'reliable':
                problems.append((rid, 'fingerprint_' + result['fingerprint']['reason'], rid if event else None))
        if event is None:
            if duration:
                problems.append((rid, 'timing_out_of_scope', None))
            elif row[16] in ('ERROR', 'FATAL', 'PANIC'):
                problems.append((rid, 'error_without_execution', None))
            continue
        counts['occurrences'] += 1
        counts['outcome:' + event['outcome']] += 1
        counts['timing:' + (event['timing'] or 'unknown')] += 1
        reasons = {}
        for name, val in (('end_at', event['end']), ('duration_ms', event['duration']), ('estimated_start_at', event['start'])):
            if val is None:
                reasons[name] = 'unknown_from_evidence'
        occurrences.append((self.analysis_id, rid, config['scope_id'], config['source_id'], rid,
                            event['unit'], result['shape'] if result else 'unknown',
                            row[2] if row[2] and valid_text(row[2]) else None,
                            row[1] if row[1] and valid_text(row[1]) else None,
                            result['sql_id'] if result else None, result['sql_state'] if result else 'missing',
                            event['timing'], event['timing_reason'], event['outcome'], event['association'],
                            ASSOCIATION, event['association_reason'], event['end'], event['duration'], event['start'],
                            'end_minus_duration' if event['start'] is not None else None, canonical(reasons)))
        if event['support']:
            support.append((self.analysis_id, rid, event['support'], 'association'))
        if result and result['approximate_id']:
            approx_events.append((self.analysis_id, rid, config['scope_id'], result['rule_id'], result['approximate_id'], rid, config['source_id']))
        if not result:
            problems.append((rid, 'sql_missing', rid))
        if event['problem']:
            problems.append((rid, event['problem'], rid))
        for name in reasons:
            problems.append((rid, name + '_unknown', rid))
        if not row[1] or not row[2] or not valid_text(row[1]) or not valid_text(row[2]):
            problems.append((rid, 'identity_missing', rid))
    copy_rows(cur, 'evidence_record', 'record_id,file_id,source_id,scope_id,record_no,line_start,line_end,decode_state,observed', evidence)
    copy_rows(cur, 'mpp_sql_text_evidence', 'sql_id,record_id', sql_links)
    copy_rows(cur, 'mpp_approximate_evidence', 'result_id,record_id', approx_links)
    copy_rows(cur, 'mpp_occurrence', 'analysis_id,occurrence_id,scope_id,source_id,anchor_ref,unit,request_shape,database,execution_user,sql_id,sql_state,timing_type,timing_reason,outcome,association_state,association_method,association_reason,end_at,duration_ms,estimated_start_at,start_basis,value_reasons', occurrences)
    copy_rows(cur, 'mpp_occurrence_evidence', 'analysis_id,occurrence_id,record_id,purpose', support)
    copy_rows(cur, 'mpp_occurrence_approximate', 'analysis_id,occurrence_id,scope_id,rule_id,result_id,record_id,source_id', approx_events)
    # Bulk problems avoid a per-record round trip on empty SQL Parse/Bind records.
    problem_rows, problem_refs = [], []
    for rid, code, occurrence in problems:
        pid = 'P:' + identity(self.analysis_id, rid, code)
        problem_rows.append((pid, 'record', config['batch_id'], fid, None, self.analysis_id if occurrence else None,
                             occurrence, code, code, 'isolate_record', 'log_record', 1, 'isolated', None))
        problem_refs.append((pid, rid))
        counts['problem:' + code] += 1
    copy_rows(cur, 'problem', 'problem_id,level,batch_id,file_id,build_id,analysis_id,occurrence_id,code,reason,effect,count_unit,count,resolution,resolution_evidence', problem_rows)
    copy_rows(cur, 'problem_evidence', 'problem_id,record_id', problem_refs)
