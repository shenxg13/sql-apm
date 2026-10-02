#!/usr/bin/env python3
"""Build one offline handbook from the maintained Markdown pages (build only)."""
from html import escape
from html.parser import HTMLParser
import posixpath
import re
from urllib.parse import unquote, urlsplit

import markdown
from markdown.extensions.toc import slugify_unicode

PAGES = (
    'docs/runbooks/kylin-offline-deployment.md',
    'docs/runbooks/kylin-validation-record.md',
    'docs/runbooks/database-initialization.md',
    'docs/runbooks/build-publication.md',
    'docs/runbooks/log-ingestion.md',
    'docs/runbooks/training-decisions.md',
    'docs/runbooks/baseline-statistics.md',
)
CSS = '''
body{font:16px/1.65 system-ui,sans-serif;color:#18232d;background:#fff;
max-width:1080px;margin:2rem auto;padding:0 1.5rem}
nav{padding:1rem;background:#f2f5f7;border-radius:8px}a{color:#145b91}
pre{overflow:auto;padding:1rem;background:#f2f5f7;border:1px solid #d7dfe5;
white-space:pre;font-size:13px}code{font-family:monospace}
table{border-collapse:collapse;display:block;overflow:auto;margin:1rem 0}
th,td{border:1px solid #cbd5df;padding:.5rem;text-align:left}
section{margin:3rem 0}h1,h2,h3{scroll-margin-top:1rem}h2{border-bottom:1px solid #ccd}
@media print{body{max-width:none;margin:0;font-size:10pt}nav{display:none}
pre{white-space:pre-wrap;overflow-wrap:anywhere}table{display:table;font-size:9pt}
h1,h2,h3{break-after:avoid}a{color:inherit;text-decoration:none}}
'''


def code_blocks(source):
    """The repository handbook uses top-level backtick/tilde fenced blocks."""
    result, fence, block = [], None, []
    for line in source.splitlines(keepends=True):
        if fence is None:
            match = re.fullmatch(r'(`{3,}|~{3,})[^\n]*\n?', line)
            if match:
                fence, block = match[1], []
        elif line.rstrip('\r\n') == fence:
            result.append(''.join(block))
            fence = None
        else:
            block.append(line)
    if fence:
        raise ValueError('unclosed Markdown code fence')
    return result


class Inspection(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids, self.hrefs, self.blocks = set(), [], []
        self.in_pre = False
        self.styles = []
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'link', 'iframe', 'object', 'embed', 'base', 'form'):
            raise ValueError('active or external HTML resource: ' + tag)
        if any(k.startswith('on') for k in attrs):
            raise ValueError('HTML event handler')
        if 'srcset' in attrs or ('src' in attrs and not attrs['src'].startswith('data:')):
            raise ValueError('external HTML resource')
        if tag == 'meta' and attrs.get('http-equiv', '').lower() == 'refresh':
            raise ValueError('HTML redirect')
        if 'style' in attrs:
            self.styles.append(attrs['style'])
        if 'id' in attrs:
            if attrs['id'] in self.ids:
                raise ValueError('duplicate HTML id: ' + attrs['id'])
            self.ids.add(attrs['id'])
        if tag == 'a' and 'href' in attrs:
            self.hrefs.append(attrs['href'])
        if tag == 'pre':
            self.in_pre = True
            self.blocks.append('')
        if tag == 'style':
            self.in_style = True

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.in_pre = False
        if tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if self.in_pre:
            self.blocks[-1] += data
        if self.in_style:
            self.styles.append(data)


def inspect(document, expected_blocks=None):
    result = Inspection()
    result.feed(document)
    for href in result.hrefs:
        if href.startswith('#'):
            if unquote(href[1:]) not in result.ids:
                raise ValueError('unreachable HTML anchor: ' + href)
        elif urlsplit(href).scheme not in ('http', 'https'):
            raise ValueError('unresolved package link: ' + href)
    if re.search(r'@import|url\s*\(', ''.join(result.styles), re.I):
        raise ValueError('CSS resource request')
    if expected_blocks is not None and result.blocks != expected_blocks:
        raise ValueError('HTML code blocks differ from Markdown source')
    return dict(anchors=len(result.ids), links=len(result.hrefs), code_blocks=len(result.blocks),
                external_resource_requests=0)


def render(root, commit, version):
    if markdown.__version__ != '3.8.2':
        raise ValueError('install the locked build-requirements.txt in a separate build venv')
    prefixes = {path: 'manual-' + str(i) for i, path in enumerate(PAGES)}
    sections, expected, contents = [], [], []
    for path in PAGES:
        source = (root / path).read_text(encoding='utf-8')
        expected.extend(code_blocks(source))
        prefix = prefixes[path]
        converter = markdown.Markdown(
            extensions=['fenced_code', 'tables', 'toc'],
            extension_configs={'toc': {'slugify': lambda v, sep, p=prefix:
                                      p + '-' + slugify_unicode(v, sep), 'toc_depth': '2-3'}},
            tab_length=4)
        fragment = converter.convert(source)

        def rewrite(match):
            href = unquote(match[1].replace('&amp;', '&'))
            parts = urlsplit(href)
            if parts.scheme or parts.netloc:
                return match[0]
            target = posixpath.normpath(posixpath.join(posixpath.dirname(path), parts.path)) if parts.path else path
            if target in prefixes:
                anchor = prefixes[target]
                if parts.fragment:
                    anchor += '-' + slugify_unicode(parts.fragment, '-')
                url = '#' + anchor
            else:
                if target.startswith('../') or not (root / target).is_file():
                    raise ValueError('missing documentation reference: ' + target)
                url = 'https://github.com/shenxg13/sql-apm/blob/' + commit + '/' + target
                if parts.fragment:
                    url += '#' + parts.fragment
            return 'href="' + escape(url, quote=True) + '"'

        fragment = re.sub(r'href="([^"]+)"', rewrite, fragment)
        title = source.splitlines()[0].lstrip('# ')
        contents.append('<li><a href="#' + prefix + '">' + escape(title) + '</a></li>')
        context = ('' if path in PAGES[:2] else
                   '<p><strong>通用模块参考：</strong>以下保留模块原稿供查询配置、恢复和升级。'
                   '其中标明的开发验收、性能探针、tests 和 *_full 命令仅供开发仓库使用，'
                   '不属于精简包的目标机操作；Kylin 安装与自检请按本手册第一部分执行。'
                   '示例 var 路径须替换为实际配置路径，九任务保持 --workers 1。</p>')
        sections.append('<section id="' + prefix + '">' + context + converter.toc + fragment + '</section>')
    document = ('<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>SQL APM 安装与操作手册</title><style>' + CSS + '</style></head><body>'
                '<header><h1>SQL APM 安装与操作手册</h1><p>' + escape(version) +
                ' · 预发布，不用于生产</p><p>离线文档；参考资料的 GitHub 链接需要联网。'
                '使用浏览器搜索、复制与打印。程序提交：<code>' + escape(commit) +
                '</code></p></header><nav aria-label="文档目录"><ol>' + ''.join(contents) +
                '</ol></nav>' + ''.join(sections) + '</body></html>\n')
    return document, inspect(document, expected)
