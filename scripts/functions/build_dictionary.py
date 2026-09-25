#!/usr/bin/env python3
"""Materialize reviewed policies onto the fixed inventory; offline and deterministic."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build(inventory, policies, rules_version='1.0.1'):
    rules = []
    for proc in inventory['catalog']:
        if proc['name'] not in inventory['document_mentions']:
            continue
        review = policies['policies'][proc['name']]
        action = review['policy']
        count = len(proc['types'])
        normalized = set(range(count)) if action == 'all' else {0} if action == 'first' else {1} if action == 'second' else set()
        if proc['kind'] != 'function' or proc['variadic'] is not None:
            normalized = set()
            reason = '聚合／窗口或可变参数调用的结构角色保留；首版不执行参数替换。'
        elif any(t not in policies['scalar_types'] for t in proc['types']):
            normalized = set()
            reason = '该重载含非标量、对象身份或结构化类型；显式保留，不套用同名标量规则。'
        else:
            reason = review['rationale']
        # logarithm with two arguments puts the base first, input second.
        if proc['name'] == 'log' and count == 2:
            normalized = {1}
            reason = '双参数 log 的首参为底数，保留；第二参数为业务数值。'
        args = [dict(position=i+1, action='normalize' if i in normalized else 'preserve',
                     role='business_input' if i in normalized else 'preserved_input_or_control') for i in range(count)]
        sources = ['https://www.postgresql.org/docs/9.4/' + s + '.html'
                   for s in inventory['document_mentions'][proc['name']]]
        sources.append('https://github.com/postgres/postgres/blob/REL9_4_26/' + proc['source_path'] + '#L' + str(proc['source_line']))
        rules.append(dict(id=proc['id'], name=proc['name'], schema=proc['schema'], types=proc['types'],
                          defaults=proc['defaults'], variadic=proc['variadic'], kind=proc['kind'],
                          allow_unqualified=True, decision='normalize' if any(a['action']=='normalize' for a in args) else 'preserve',
                          arguments=args, rationale=proc['name'] + ': ' + reason, sources=sources, enabled=True))
    # The official v6 page explicitly documents these signatures. No field
    # deployment or HashData patch-level equivalence is claimed.
    for name, typename in [('__gp_aovisimap_compaction_info','oid'), ('__gp_aoseg_history','oid'),
                           ('__gp_aocsseg','oid'), ('__gp_aocsseg_history','oid')]:
        rules.append(dict(id='gp6-' + name, name=name, schema='gp_toolkit', types=[typename],
                          defaults=0, variadic=None, kind='function', allow_unqualified=False,
                          decision='preserve', arguments=[dict(position=1, action='preserve', role='relation_identity')],
                          rationale='Greenplum v6 文档中的关系 OID 参数，保留对象身份；未现场验证 HashData 3.13.13。',
                          sources=['https://docs-cn.greenplum.org/v6/ref_guide/gp_toolkit.html'], enabled=True))
    # Text names in the v6 guide are examples, not authoritative type signatures.
    # Preserve them as pending entries until a field catalog verifies the type.
    for name in ('gp_param_setting','__gp_aoseg_name','__gp_aoseg_history_name',
                 '__gp_aocsseg_name','__gp_aocsseg_history_name'):
        rules.append(dict(id='gp6-' + name, name=name, schema='gp_toolkit', types=['unknown'],
                          defaults=0, variadic=None, kind='function', allow_unqualified=False,
                          decision='pending', arguments=[dict(position=1, action='preserve', role='configuration_or_relation_identity')],
                          rationale='文档说明配置／表名用途，但未取得现场精确签名；保留且不计已审查签名覆盖。',
                          sources=['https://docs-cn.greenplum.org/v6/ref_guide/gp_toolkit.html'], enabled=True))
    return dict(schema_version=1, rules_version=rules_version, profile='hashdata-pg94', rules=sorted(rules,key=lambda r:r['id']))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'rules/functions/v1.0.1.json')
    parser.add_argument('--rules-version',default='1.0.1')
    args=parser.parse_args()
    inventory=json.loads((ROOT/'rules/functions/postgres-9.4.26-inventory.json').read_text())
    policies=json.loads((ROOT/'rules/functions/review-policies.json').read_text())
    args.output.write_text(json.dumps(build(inventory,policies,args.rules_version),ensure_ascii=False,indent=2)+'\n')


if __name__ == '__main__':
    main()
