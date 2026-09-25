#!/usr/bin/env python3
"""Audit the document universe, signature rules, review policies and examples."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from sql_apm.sql.function_dictionary import FunctionDictionary, read_json
from scripts.functions.build_dictionary import build


def coverage():
    base=ROOT/'rules/functions'
    inventory=read_json(base/'postgres-9.4.26-inventory.json')
    data=read_json(base/'v1.0.1.json')
    dictionary=FunctionDictionary(data)
    policies=read_json(base/'review-policies.json')
    if build(inventory,policies)!=data:
        raise ValueError('v1.0.1.json differs from reviewed policies; regenerate and review changes')
    rules={r['id']:r for r in data['rules']}
    documented=[p for p in inventory['catalog'] if p['name'] in inventory['document_mentions']]
    if any(p['id'] not in rules for p in documented):
        raise ValueError('documented catalog signature missing from dictionary')
    boundaries=read_json(base/'special-syntax.json')
    boundary_names={b['name'] for b in boundaries}
    names={p['name'] for p in inventory['catalog']}
    if boundary_names!=set(inventory['document_mentions'])-names:
        raise ValueError('non-catalog document mentions lack explicit boundary dispositions')
    if len(boundary_names)!=len(boundaries):
        raise ValueError('duplicate special syntax entry')
    for example in read_json(base/'examples.json'):
        actual=dictionary.preview(example['left'],example['context'])==dictionary.preview(example['right'],example['context'])
        if actual!=example['expected_same']:
            raise ValueError('example failed: '+example['id'])
    categories=[]
    for section in inventory['sections']:
        relevant=[p for p in documented if section in inventory['document_mentions'][p['name']]]
        categories.append(dict(section=section, signatures=len(relevant),
                               decisions=dict(Counter(rules[p['id']]['decision'] for p in relevant))))
    return dict(rules_version=dictionary.rules_version, dictionary_sha256=dictionary.sha256,
                source_scope='PostgreSQL 9.4.26 func.sgml chapter 9 tables and function mentions; catalog overload expansion',
                catalog_signatures=len(inventory['catalog']),
                documented_function_names=len({p['name'] for p in documented}),
                documented_catalog_signatures=len(documented),
                documented_reviewed=sum(rules[p['id']]['decision']!='pending' for p in documented),
                documented_decisions=dict(Counter(rules[p['id']]['decision'] for p in documented)),
                catalog_outside_document_scope=len(inventory['catalog'])-len(documented),
                outside_scope_note='catalog facts retained for audit; not claimed reviewed or normalized',
                document_signature_mentions=len(inventory['document_signatures']),
                document_boundary_counts=dict(Counter(b['kind'] for b in boundaries)),
                extension_decisions=dict(Counter(r['decision'] for r in data['rules'] if r['schema']!='pg_catalog')),
                pending_rules=[dict(id=r['id'],reason=r['rationale']) for r in data['rules'] if r['decision']=='pending'],
                unavailable_evidence=['HashData 3.13.13 field function catalog and custom-function semantic definitions'],
                artificial_examples=len(read_json(base/'examples.json')), categories=categories)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        result=coverage()
    except (ValueError,KeyError) as error:
        parser.exit(1,'ERROR: '+str(error)+'\n')
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(text)
    else:print(text,end='')


if __name__=='__main__':main()
