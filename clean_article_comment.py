#!/usr/bin/env python3
"""article_comment_dataset 清洗：两个功能，两个函数。

1) strip_truncated_titles(content)
   删除每个 <|start_of_articleid=N|> 后紧跟、且以 … 结尾的截断标题行（含换行）。

2) tag_comment_quotes(content)
   评论里复述了本评论块内前面某条评论的段落时，包成 <ref id=N>…</ref>，
   N = 被引用评论在 <|start_of_comment...|>/<|end_of_comment...|> 之间排第几条(1-based)。
"""
import argparse, difflib, glob, json, os, re, sys
from multiprocessing import Pool


# ---------------- 功能 1：删除截断标题行 ----------------
_TITLE_ELLIPSIS = re.compile(r'(<\|start_of_articleid=\d+\|>)[^\n]*?…[^\S\n]*\n')

def strip_truncated_titles(content: str) -> str:
    return _TITLE_ELLIPSIS.sub(r'\1', content)


# ---------------- 功能 2：评论间引用打标签 ----------------
_BLOCK = re.compile(
    r'(<\|start_of_comment_articleid=\d+\|>)(.*?)(<\|end_of_comment_articleid=\d+\|>)', re.S)
_SAID  = re.compile(r'^.{1,60}?\b(?:said|wrote):\s*$')            # 论坛引用抬头
_URL   = re.compile(r'^\s*(?:https?://|www\.)\S+\s*$', re.I)       # 纯 URL 行
_BOILER = re.compile(                                             # 论坛系统/编辑行
    r'(?i)\b(?:this (?:comment|post) (?:was|has been) edited|last edited by|'
    r'edited by\b.*\bon\b|originally posted by)\b')
_MIN_CHARS, _MIN_WORDS, _SIM = 40, 8, 0.85

def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip().lower()

def _quotable(pn: str, pr: str) -> bool:
    return (len(pn) >= _MIN_CHARS and len(pn.split()) >= _MIN_WORDS
            and not _URL.match(pr.strip()) and not _BOILER.search(pr))

def tag_comment_quotes(content: str) -> str:
    def _tag_block(m):
        comments = m.group(2).split('\n\n')
        prior, out, no = [], [], 0   
        for cm in comments:
            if not cm.strip():
                out.append(cm); continue
            no += 1
            paras  = cm.split('\n')
            pnorms = [_norm(p) for p in paras]
            src = [None] * len(paras)
            for i, (pr, pn) in enumerate(zip(paras, pnorms)):
                if not _quotable(pn, pr):
                    continue
                best_no, best_r = None, 0.0
                for (n, jn) in prior:
                    r = difflib.SequenceMatcher(None, pn, jn).ratio()
                    if r > best_r:
                        best_r, best_no = r, n
                if best_r >= _SIM:
                    src[i] = best_no
            # 连续命中的段落合并；紧贴其上的 said: 抬头一并并入
            lines, i = [], 0
            while i < len(paras):
                if src[i] is None:
                    lines.append(paras[i]); i += 1; continue
                s0, sid = i, src[i]
                while i + 1 < len(paras) and src[i + 1] is not None:
                    i += 1
                span = paras[s0:i + 1]
                if lines and _SAID.match(lines[-1].strip()):
                    span = [lines.pop()] + span
                lines.append(f'<ref id={sid}>' + '\n'.join(span) + '</ref>')
                i += 1
            out.append('\n'.join(lines))
            # 本条评论登记进 prior（系统样板行不登记，避免被后文误引）
            for pr, pn in zip(paras, pnorms):
                if len(pn) >= _MIN_CHARS and not _BOILER.search(pr):
                    prior.append((no, pn))
        return m.group(1) + '\n\n'.join(out) + m.group(3)
    return _BLOCK.sub(_tag_block, content)


# ---------------- 驱动 ----------------
def clean(content: str) -> str:
    return tag_comment_quotes(strip_truncated_titles(content))

def _process_file(args):
    src, dst = args
    out, recs = [], 0
    with open(src, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line:
                continue
            r = json.loads(line); recs += 1
            r['content'] = clean(r.get('content', ''))
            out.append(json.dumps(r, ensure_ascii=False))
    tmp = dst + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + ('\n' if out else ''))
    os.replace(tmp, dst)
    return os.path.basename(src), recs

def _demo(src, n):
    for line in open(src, encoding='utf-8'):
        c = json.loads(line)['content']
        after = clean(c)
        if after == c:
            continue
        print('=' * 70)
        i = after.find('<ref id=')
        if i != -1:
            print(repr(after[max(0, i - 120):i + 200]))
        t = _TITLE_ELLIPSIS.search(c)
        if t:
            print('[title-strip] 删除:', repr(t.group(0)[len(t.group(1)):].strip()))
        n -= 1
        if n <= 0:
            break

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True,
                    help='Directory containing the source JSONL files.')
    ap.add_argument('--dst', default=None)
    ap.add_argument('--pattern', default='ars_*.jsonl')
    ap.add_argument('--workers', type=int, default=32)
    ap.add_argument('--demo', type=int, default=5)
    ap.add_argument('--run', action='store_true')
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.src, a.pattern)))
    if not files:
        print('no files matched', file=sys.stderr); sys.exit(1)

    if not a.run:
        _demo(files[0], a.demo)
        print('\n(demo only — 未写盘。加 --run 才处理。)')
        return

    dst = a.dst or (a.src.rstrip('/') + '_cleaned')
    os.makedirs(dst, exist_ok=True)
    tasks = [(s, os.path.join(dst, os.path.basename(s))) for s in files]
    tot = 0
    with Pool(a.workers) as pool:
        for name, recs in pool.imap_unordered(_process_file, tasks):
            tot += recs; print(f'  {name}: recs={recs}')
    print('-' * 60); print(f'TOTAL records={tot} -> {dst}')


if __name__ == '__main__':
    main()
