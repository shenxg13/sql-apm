#!/usr/bin/env python3
"""Extract signature facts from the unmodified PostgreSQL 9.4.26 source tree.

Offline maintainer tool; never downloads data or assigns normalization actions.
"""
import argparse
import hashlib
import html
import json
import re
import shlex
from pathlib import Path


def functions(text):
    """Read FUNCTION elements including SGML shorthand end tags (</>)."""
    for start in re.finditer(r'<function>', text):
        stack, chunks, cursor = ['function'], [], start.end()
        # Scan only this element; explicit closing tags and short end tags both
        # pop the actual innermost element, not the entire function element.
        last = cursor
        for tag in re.finditer(r'<(/?)([a-zA-Z0-9_-]*)(?:\s[^>]*?)?>', text[cursor:]):
            begin, end = cursor + tag.start(), cursor + tag.end()
            chunks.append(text[last:begin])
            closing, name = tag.group(1), tag.group(2)
            if closing:
                if name and name != stack[-1]:
                    raise ValueError('unbalanced FUNCTION markup: ' + name)
                stack.pop()
                if not stack:
                    yield ' '.join(html.unescape(''.join(chunks)).split())
                    break
            else:
                stack.append(name)
            last = end
        else:
            raise ValueError('unterminated FUNCTION markup')


def extract(root):
    paths = ['doc/src/sgml/func.sgml', 'src/include/catalog/pg_proc.h',
             'src/include/catalog/pg_type.h', 'src/backend/catalog/system_views.sql']
    texts = {p: (root / p).read_text() for p in paths}
    sources = [{'path': p, 'sha256': hashlib.sha256((root / p).read_bytes()).hexdigest()}
               for p in paths]
    types = {}
    for oid, body in re.findall(r'DATA\(insert OID =\s*(\d+)\s*\((.*?)\)\s*\);', texts[paths[2]]):
        types[oid] = shlex.split(body)[0]
    procs = []
    for line, text in enumerate(texts[paths[1]].splitlines(), 1):
        match = re.match(r'DATA\(insert OID =\s*(\d+)\s*\((.*?)\)\s*\);', text)
        if not match:
            continue
        oid, body = match.groups()
        fields = shlex.split(body)
        assert len(fields) == 27, (oid, len(fields))
        argtypes = fields[18].split()
        assert len(argtypes) == int(fields[15])
        procs.append(dict(id='pg94-' + oid, name=fields[0], schema='pg_catalog',
                          types=[types[t] for t in argtypes], defaults=int(fields[16]),
                          variadic=types[fields[6]] if fields[6] != '0' else None,
                          source_path=paths[1], kind='aggregate' if fields[8] == 't' else
                          'window' if fields[9] == 't' else 'function', source_line=line))
    # SQL-installed signatures and default arguments do not all live in pg_proc.h.
    aliases = {'int': 'int4', 'boolean': 'bool', 'double precision': 'float8', 'text[]': '_text'}
    for match in re.finditer(r'CREATE (?:OR REPLACE )?FUNCTION\s+(\w+)\s*\((.*?)\)\s*RETURNS', texts[paths[3]], re.S):
        name, raw = match.groups()
        inputs, defaults, variadic = [], 0, None
        for argument in raw.split(','):
            words = argument.strip().split()
            if words[0] == 'OUT':
                continue
            mode = words.pop(0) if words[0] in ('IN', 'INOUT', 'VARIADIC') else 'IN'
            words.pop(0)  # argument name
            if 'DEFAULT' in words:
                defaults += 1
                words = words[:words.index('DEFAULT')]
            typename = ' '.join(words)
            inputs.append(aliases.get(typename, typename))
            if mode == 'VARIADIC':
                variadic = typename.removesuffix('[]')
        existing = [p for p in procs if p['name'] == name and p['types'] == inputs]
        line = texts[paths[3]][:match.start()].count('\n') + 1
        if existing:
            existing[0]['defaults'] = defaults
            existing[0]['defaults_source_line'] = line
        else:
            procs.append(dict(id='pg94-sql-' + name + '-' + str(len(inputs)), name=name,
                              schema='pg_catalog', types=inputs, defaults=defaults,
                              variadic=variadic, kind='function', source_line=line,
                              source_path=paths[3]))
    # Table signature cells define the document universe; prose references are
    # collected separately so table-less special syntax is not silently lost.
    doc = re.sub(r'<!--.*?-->', '', texts[paths[0]], flags=re.S)
    sections = re.split(r'<sect1 id="([^"]+)">', doc)
    tables, mentions = [], {}
    for i in range(1, len(sections), 2):
        section, content = sections[i:i+2]
        for clean in functions(content):
            name = re.match(r'([A-Za-z_][\w]*)', clean)
            if name:
                mentions.setdefault(name[1].lower(), set()).add(section)
        for row in re.findall(r'<row>(.*?)</row>', content, flags=re.S):
            first = re.search(r'<entry(?:\s[^>]*)?>(.*?)</entry>', row, flags=re.S)
            if not first:
                continue
            clean = ' '.join(html.unescape(re.sub(r'<[^>]*>', '', first[1])).split())
            candidates = set(re.findall(r'\b([A-Za-z_][\w]*)\s*\(', clean))
            candidates.update(re.match(r'([A-Za-z_][\w]*)', t)[1]
                              for t in functions(first[1]) if re.match(r'([A-Za-z_][\w]*)', t))
            for name in sorted(candidates):
                tables.append(dict(name=name.lower(), signature=clean, section=section))
                mentions.setdefault(name.lower(), set()).add(section)
    return dict(source_version='PostgreSQL 9.4.26',
                archive_url='https://ftp.postgresql.org/pub/source/v9.4.26/postgresql-9.4.26.tar.bz2',
                sources=sources, sections=sections[1::2],
                document_signatures=sorted({json.dumps(t, sort_keys=True): t for t in tables}.values(),
                                           key=lambda t: (t['section'], t['signature'])),
                document_mentions={n: sorted(s) for n, s in sorted(mentions.items())},
                catalog=sorted(procs, key=lambda p: (p['name'], p['types'])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(extract(args.source), ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
