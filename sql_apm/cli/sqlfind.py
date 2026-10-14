"""Human SQL search entry; never evaluates SQL supplied by the operator."""
import argparse
import codecs
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import termios
import unicodedata

import psycopg2
from psycopg2.extensions import get_wait_callback, make_dsn, set_wait_callback
from psycopg2.extras import wait_select

from sql_apm.cli.search import BLANKS, TEXT_MAX_BYTES
from sql_apm.cli.sqlfind_output import BEIJING, Output, TEXT_LIMIT, stamp
from sql_apm.ingestion.config import IngestionError
from sql_apm.sql.normalization import MAX_BYTES
from sql_apm.storage.sqlfind import Finder


class InputError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError('参数错误，请运行 sqlfind --help 查看用法。')

    def format_help(self):
        return super().format_help().replace('usage:', '用法:', 1)


def parser():
    result = Parser(prog='sqlfind', add_help=False, formatter_class=argparse.RawDescriptionHelpFormatter,
        description='SQL 检索：给出输入后打印一次；不带输入且输入输出都是终端时进入交互。',
        epilog='''输入来源三选一：命令行参数、--file 文件、标准输入（管道或重定向）。
文件和标准输入按 UTF-8 原始字节读取，保留首尾空白及换行。
交互按词输入一行后回车；整段和完整 SQL 粘贴后回车、Ctrl-D 结束。
交互详情输入 h 设置历史条数（1～1000）与起止时间；设置仅在本次会话保留。
历史设置中 r 恢复20条及默认7天，q 或 Ctrl-C 取消；不改变当前基线。
Ctrl-C 取消当前步骤或查询；操作提示中 q 退出，不分屏，可用鼠标滚轮回看。
时间均为北京时间；--start/--end 不带时区时按北京时间，结束时间不包含。

例子：
  sqlfind 'orders status'
  sqlfind --mode passage --file query.sql
  sqlfind --mode exact --show 1 --full-text <<'SQL'
SELECT id, name
FROM orders WHERE status = 'ready';
SQL
  sqlfind --mode exact

先由登录账号执行 scripts/sqlfind/configure.py install 配置只读连接与登录入口。
--show 会重新检索；标记可能随新导入变化，可直接输入结构指纹来固定对象。
''')
    result._positionals.title = '输入'
    result._optionals.title = '选项'
    result.add_argument('-h', '--help', action='help', help='显示中文帮助')
    result.add_argument('input', nargs='?', help='要检索的内容，可含多行')
    result.add_argument('--file', type=Path, help='从文件读取内容')
    result.add_argument('--mode', choices=['words', 'passage', 'exact'],
        help='words 按空白切词且全部出现（默认，最多20词）；passage 整段连续出现；exact 完整SQL结构相同')
    result.add_argument('--cluster', help='集群筛选')
    result.add_argument('--database', help='数据库筛选')
    result.add_argument('--user', help='执行用户筛选')
    result.add_argument('--start', help='候选检索开始时间，包含；交互历史时间用 h 设置')
    result.add_argument('--end', help='候选检索结束时间，不包含；交互历史时间用 h 设置')
    result.add_argument('--order', choices=['count', 'recent'], default='count', help='count 记录数最多；recent 最近执行')
    result.add_argument('--schema', help='数据库 schema，默认配置值或 sql_apm')
    result.add_argument('--show', help='只显示标记的详情，如 1 或 s1')
    result.add_argument('--identity', type=int, help='详情身份序号，仅与 --show 一起使用')
    result.add_argument('--timing', choices=['request', 'execute_first', 'execute_fetch', 'parse', 'bind', 'unknown'],
        help='详情计时类别：请求整体、Execute首次、Execute续取、Parse、Bind、无计时；仅与 --show 一起使用')
    result.add_argument('--limit', type=int, help='最近执行行数，默认20，范围1到1000；仅与 --show 一起使用')
    result.add_argument('--full-text', action='store_true', help=f'打印原文全文，默认最多{TEXT_LIMIT}字符；仅与 --show 一起使用')
    result.add_argument('--width', type=int, help='输出到管道或文件的宽度，默认100，最小20；终端随窗口')
    return result


def time_value(value):
    if value is None:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        raise InputError('时间格式错误，请使用 YYYY-MM-DD HH:MM:SS，可附带时区。') from None
    return stamp.replace(tzinfo=BEIJING) if stamp.tzinfo is None else stamp


def arguments(argv):
    args = parser().parse_args(argv)
    args.mode_given = args.mode is not None
    args.mode = args.mode or 'words'
    if not args.show and (args.identity is not None or args.timing is not None or args.limit is not None or args.full_text):
        raise InputError('--identity、--timing、--limit、--full-text 只能与 --show 一起使用。')
    if args.identity is not None and args.identity < 1:
        raise InputError('身份序号必须从 1 起。')
    if args.limit is not None and not 1 <= args.limit <= 1000:
        raise InputError('--limit 必须在 1 到 1000 之间。')
    if args.width is not None and args.width < 20:
        raise InputError('--width 必须至少为 20。')
    args.start, args.end = time_value(args.start), time_value(args.end)
    if args.start and args.end and args.start >= args.end:
        raise InputError('开始时间必须早于结束时间。')
    return args


def connection(args):
    path = Path.home() / '.sqlfind' / 'connection.json'
    saved = {}
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                raise ValueError
        except (OSError, ValueError):
            raise InputError('无法读取 sqlfind 配置，请重新执行配置步骤。') from None
    if 'SQL_APM_DSN' in os.environ:
        dsn = os.environ['SQL_APM_DSN']
    elif saved:
        try:
            dsn = make_dsn(**{key: saved[key] for key in ('host', 'port', 'dbname', 'user')})
        except (KeyError, TypeError, psycopg2.Error):
            raise InputError('sqlfind 连接配置无效，请重新执行配置步骤。') from None
    else:
        raise InputError('请先执行 sqlfind 配置步骤，或设置 SQL_APM_DSN 只读连接。')
    passfile = os.environ.get('PGPASSFILE', saved.get('passfile'))
    if passfile:
        dsn = make_dsn(dsn, passfile=passfile)
    return dsn, args.schema or os.environ.get('SQL_APM_SCHEMA') or saved.get('schema', 'sql_apm')


def input_source(args, stream):
    piped = not stream.isatty()
    if sum((args.input is not None, args.file is not None, piped)) > 1:
        raise InputError('输入来源只能选一种：命令行参数、--file 或标准输入。')
    maximum = MAX_BYTES if args.mode == 'exact' else TEXT_MAX_BYTES
    if args.input is not None:
        return args.input.encode('utf-8', 'surrogateescape')
    if args.file is not None:
        with args.file.open('rb') as source:
            return source.read(maximum + 1)
    if piped:
        return stream.buffer.read(maximum + 1)
    return None


def validate(raw, mode):
    limit = MAX_BYTES if mode == 'exact' else TEXT_MAX_BYTES
    if len(raw) > limit:
        raise InputError('输入超过 ' + ('512' if mode == 'exact' else '256') + ' KB 的上限。')
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        raise InputError('输入不是有效的 UTF-8。') from None
    if not text.strip(BLANKS):
        raise InputError('输入为空，请重新输入。')
    if '\x00' in text:
        raise InputError('输入含有空字节，无法检索。')
    # Database owns word splitting and the 20-word rejection (in search_note).


def search(finder, raw, args):
    validate(raw, args.mode)
    result = finder.search(raw, args)
    refused = next((n['content'] for n in result['notes'] if n['item'] == '没有检索'), None)
    if refused:
        raise InputError(refused + '。')
    return result


def failure(error):
    if isinstance(error, InputError):
        return str(error)
    if isinstance(error, psycopg2.errors.QueryCanceled):
        return '查询超时或已取消，请缩小检索范围后重试。'
    if isinstance(error, psycopg2.OperationalError):
        return '连接或查询失败，请检查数据库连接设置。'
    if isinstance(error, psycopg2.Error):
        reason = getattr(error.diag, 'message_primary', None)
        return {'too_many_search_terms': '输入超过 20 个词，请改用整段方式。',
                'invalid_time_range': '时间范围无效，请检查开始和结束时间。'}.get(reason, '数据库查询失败，请检查连接与规则版本后重试。')
    if isinstance(error, OSError):
        return '无法读取输入或配置文件，请检查路径和权限。'
    return {'invalid_identity': '身份序号不存在，请查看身份表中的可用序号。',
            'invalid_timing': '该身份没有所选计时类别，请查看可用计时类别。',
            'records_changed': '检索结果已变化，请重新检索。'}.get(str(error), '输入或配置无效，请检查后重试。')


ERRORS = (InputError, IngestionError, psycopg2.Error, OSError, ValueError, TypeError)


def show(finder, found, mark, args, output, identity=None, timing=None, full=False, history=None, history_only=False):
    valid = [str(i) for i in range(1, len(found['candidates']) + 1)] + ['s' + str(h['statement']) for h in found['hints']]
    if mark not in valid:
        raise InputError('标记不存在，可用标记: ' + (', '.join(valid) if valid else '无') + '。')
    settings = history or dict(limit=args.limit or 20, window=None)
    detail = finder.detail(found, mark, args, identity, timing, **settings)
    if history_only and 'hint' not in detail:
        output.history(detail)
    else:
        output.detail(detail, full)
    return detail


class InputEcho:
    """Track input cells so erase also works for wide characters, tabs and wraps."""
    def __init__(self, output):
        self.output, self.width = output, output.width
        self.row = self.column = 0
        self.marks, self.pending = [], []

    def add(self, text, end):
        for char in text:
            self.marks.append((end, self.row, self.column))
            if char in '\r\n':
                self.pending.append('\n')
                self.row, self.column = self.row + 1, 0
                continue
            encoding = getattr(self.output.stream, 'encoding', None) or 'utf-8'
            rendered = char.encode(encoding, 'replace').decode(encoding)
            if rendered != char:
                self.output.replaced = True
            if char == '\t':
                rendered = ' ' * (8 - self.column % 8)
            elif unicodedata.category(char).startswith('C'):
                rendered = ''
            for glyph in rendered:
                width = 0 if unicodedata.combining(glyph) else 2 if unicodedata.east_asian_width(glyph) in ('W', 'F') else 1
                if self.column + width > self.width:
                    self.pending.append('\n')
                    self.row, self.column = self.row + 1, 0
                self.pending.append(glyph)
                self.column += width
                if self.column == self.width:
                    # Resolve the terminal's delayed wrap before a possible erase.
                    self.pending.append('\n')
                    self.row, self.column = self.row + 1, 0

    def erase(self, end):
        target = None
        while self.marks and self.marks[-1][0] > end:
            _, row, column = self.marks.pop()
            target = row, column
        if target is None:
            return
        row, column = target
        if self.row > row:
            self.pending.append(f'\x1b[{self.row - row}A')
        self.pending.append('\r')
        if column:
            self.pending.append(f'\x1b[{column}C')
        self.pending.append('\x1b[J')
        self.row, self.column = row, column

    def flush(self):
        self.output.write(''.join(self.pending))
        self.pending.clear()


def terminal_input(output, prompt, multiline=False, maximum=MAX_BYTES):
    """Read noncanonical input: canonical PTYs silently truncate lines at 4096 bytes.

    Drain paste remainder before returning so SQL can never become a menu command.
    No history, readline, full-screen mode or alternate screen is used.
    """
    fd = sys.stdin.fileno()
    termios.tcflush(fd, termios.TCIFLUSH)
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[0] &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR)
    new[3] &= ~(termios.ICANON | termios.ECHO | termios.IEXTEN)
    new[6][termios.VMIN], new[6][termios.VTIME] = 1, 0
    data, overflow = bytearray(), False
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    echo, escape = InputEcho(output), ''
    try:
        termios.tcsetattr(fd, termios.TCSANOW, new)
        output.line(prompt)
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            end = False
            for byte in chunk:
                if byte == 4 or (not multiline and byte in (10, 13)):
                    end = True
                    break
                if byte == 27:
                    escape = 'start'
                    continue
                if escape:
                    if escape == 'start' and byte in (ord('['), ord('O')):
                        escape = 'sequence'
                        continue
                    if escape == 'sequence' and 32 <= byte <= 126:
                        if byte >= 64:
                            escape = ''
                        continue
                    escape = ''
                if byte in (8, 127):
                    if data:
                        while data and data[-1] & 0xc0 == 0x80:
                            data.pop()
                        if data:
                            data.pop()
                    decoder.reset()
                    echo.erase(len(data))
                    continue
                if len(data) <= maximum:
                    data.append(byte)
                else:
                    overflow = True
                    continue
                text = decoder.decode(bytes([byte]))
                if byte != 10 or len(data) < 2 or data[-2] != 13:
                    echo.add(text, len(data))
            echo.flush()
            if end:
                break
    finally:
        termios.tcflush(fd, termios.TCIFLUSH)
        termios.tcsetattr(fd, termios.TCSANOW, old)
        output.write('\n')
    if overflow:
        return b'x' * (maximum + 1)
    raw = bytes(data).replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    if multiline and raw.endswith(b'\n'):
        raw = raw[:-1]  # The one Return pressed to finish input is not SQL.
    return raw


def history_settings(output, current, detail):
    """Collect settings locally; the caller commits them after a successful query."""
    limit, window = current['limit'], current['window']
    start, end = window or (detail['start'], detail['end'])
    step = 'limit'
    output.line('历史设置：北京时间；回车保留；r 恢复默认；q 或 Ctrl-C 取消。')
    try:
        while True:
            prompts = {
                'limit': f'显示条数 [{limit}]，可选 1-1000：',
                'mode': f'时间范围：1 默认7天 / 2 指定起止时间 [{2 if window else 1}]：',
                'start': f'开始时间（包含）[{stamp(start)}]：',
                'end': f'结束时间（不包含）[{stamp(end)}]：',
            }
            raw = terminal_input(output, prompts[step], maximum=1024)
            try:
                if len(raw) > 1024:
                    raise InputError('设置内容过长，请重新输入。')
                value = raw.decode('utf-8').strip()
                if value == 'q':
                    return None
                if value == 'r':
                    return dict(limit=20, window=None)
                if step == 'limit':
                    try:
                        chosen = int(value) if value else limit
                    except ValueError:
                        raise InputError('显示条数必须是 1 到 1000 的整数。') from None
                    if not 1 <= chosen <= 1000:
                        raise InputError('显示条数必须是 1 到 1000 的整数。')
                    limit, step = chosen, 'mode'
                elif step == 'mode':
                    value = value or ('2' if window else '1')
                    if value == '1':
                        return dict(limit=limit, window=None)
                    if value != '2':
                        raise InputError('请选择 1 或 2。')
                    step = 'start'
                elif step == 'start':
                    start = time_value(value) if value else start
                    step = 'end'
                else:
                    chosen_end = time_value(value) if value else end
                    if start >= chosen_end:
                        raise InputError('结束时间必须晚于开始时间，请重新输入结束时间。')
                    return dict(limit=limit, window=(start, chosen_end))
            except UnicodeDecodeError:
                output.line('输入不是有效的 UTF-8，请重新输入。')
            except InputError as error:
                output.line(str(error))
    except KeyboardInterrupt:
        return None


def interactive(finder, args, output):
    state = 'input' if args.mode_given else 'mode'
    found = detail = None
    mark, chosen_identity, chosen_timing = None, None, None
    history = dict(limit=20, window=None)
    output.line('sqlfind 交互检索: Ctrl-C 取消当前步骤或查询；操作提示中 q 退出。')
    while True:
        try:
            if state == 'mode':
                command = terminal_input(output,
                    '方式: 1 按词(每词都出现，默认) / 2 整段(连续出现) / 3 完整SQL(结构相同)；q 退出').decode().strip()
                if command == 'q':
                    return 0
                if command not in ('', '1', '2', '3', 'words', 'passage', 'exact'):
                    raise InputError('请选择 1、2 或 3。')
                args.mode = {'': 'words', '1': 'words', '2': 'passage', '3': 'exact'}.get(command, command)
                state = 'input'
            elif state == 'input':
                multi = args.mode != 'words'
                raw = terminal_input(output, ('粘贴多行内容，完成后回车再按 Ctrl-D；Ctrl-C 返回方式选择，之后 q 退出。'
                    if multi else '输入一行检索词，回车结束；Ctrl-C 返回方式选择，之后 q 退出。'), multi,
                    MAX_BYTES if args.mode == 'exact' else TEXT_MAX_BYTES)
                found = search(finder, raw, args)
                output.candidates(found, args)
                state = 'list'
            elif state == 'list':
                command = terminal_input(output, '输入标记看详情；l 重看列表；n 新检索；q 退出；Ctrl-C 返回输入。').decode().strip()
                if command in ('', 'q'):
                    return 0
                if command == 'n':
                    state = 'mode'
                elif command == 'l':
                    output.candidates(found, args)
                else:
                    detail = show(finder, found, command, args, output, history=history)
                    mark, chosen_identity, chosen_timing = command, None, None
                    state = 'detail'
            else:
                if 'timings' in detail:
                    output.line('可用计时类别: ' + '; '.join(t['timing'] + '=' + t['label'] for t in detail['timings']))
                output.line('i 序号 换身份；t 类别 换计时；f 查看原文全文。')
                if 'timings' in detail:
                    output.line('h 历史设置（条数、时间范围、恢复默认）。')
                command = terminal_input(output, 'b 返回列表；n 新检索；q 退出；Ctrl-C 返回列表。').decode().strip()
                if command in ('', 'q'):
                    return 0
                if command == 'b':
                    output.candidates(found, args)
                    state = 'list'
                elif command == 'n':
                    state = 'mode'
                elif command == 'h':
                    if 'timings' not in detail:
                        raise InputError('该提示没有可查看的执行历史。')
                    next_history = history_settings(output, history, detail)
                    if next_history is None:
                        output.line('已取消历史设置，保留原设置。')
                        continue
                    try:
                        updated = show(finder, found, mark, args, output, chosen_identity, chosen_timing,
                                       history=next_history, history_only=True)
                    except KeyboardInterrupt:
                        output.line('已取消历史查询，保留原设置。')
                        continue
                    except ERRORS as error:
                        output.line(failure(error))
                        output.line('历史设置未应用，保留原设置。')
                        continue
                    detail, history = updated, next_history
                elif command == 'f' or command.startswith(('i ', 't ')):
                    next_identity, next_timing = chosen_identity, chosen_timing
                    if command.startswith('i '):
                        try:
                            next_identity = int(command[2:])
                        except ValueError:
                            raise InputError('身份序号必须是整数。') from None
                        next_timing = None
                    elif command.startswith('t '):
                        next_timing = command[2:].strip()
                    detail = show(finder, found, mark, args, output, next_identity, next_timing, command == 'f', history)
                    chosen_identity, chosen_timing = next_identity, next_timing
                else:
                    raise InputError('操作无效，请使用提示中的操作。')
        except KeyboardInterrupt:
            output.line('已取消，q 可在操作提示中退出。')
            state = {'mode': 'mode', 'input': 'mode', 'list': 'input', 'detail': 'list'}[state]
        except ERRORS as error:
            output.line(failure(error))
            if isinstance(error, psycopg2.Error):
                state = 'input'


def main(argv=None):
    output = Output()
    previous_wait = get_wait_callback()
    try:
        args = arguments(argv)
        output = Output(args.width)
        raw = input_source(args, sys.stdin)
        if raw is None and (not sys.stdout.isatty() or args.show):
            raise InputError('没有检索输入，请使用参数、--file 或标准输入。')
        if raw is not None:
            validate(raw, args.mode)
        dsn, schema = connection(args)
        set_wait_callback(wait_select)  # psycopg2 cancels on Ctrl-C while waiting for the server.
        finder = Finder(dsn, schema)
        if raw is None:
            return interactive(finder, args, output)
        found = search(finder, raw, args)
        if args.show:
            show(finder, found, args.show, args, output, args.identity, args.timing, args.full_text)
        else:
            output.candidates(found, args)
        return 0
    except KeyboardInterrupt:
        output.line('查询已取消。')
        return 1
    except BrokenPipeError:
        return 1
    except ERRORS as error:
        output.line(failure(error))
        return 1
    finally:
        set_wait_callback(previous_wait)


if __name__ == '__main__':
    raise SystemExit(main())
