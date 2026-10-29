# -*- coding: utf-8 -*-
"""제목 구분자 잔재 정리 (2026-10-29).

`retitle_store_posts.py` 의 구분자 과다 교체 로직이 예전 버전에서는
CJK 구분자(｜, ：, 、) 를 lstrip 하지 않아 **", ｜" / ", ："** 처럼
두 구분자가 겹친 제목이 생겼다. → 지금 고친 로직으로는 재발 안 하지만
이미 들어간 제목은 정리해야 한다.

규칙:
- ", ｜" → "｜"   ", ：" → "："   ", 、" → "、"
- ", ?"  → "？"  ", !" → "！"  (라틴계는 ", " 를 유지)
- 구분자 앞 공백 정리
"""
import argparse
import glob
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'D:\workbuddy\blog\mycapitalanalyze.com'
CONTENT = os.path.join(ROOT, 'content')

# CJK 계열: 라틴 콤마 + CJK 구분자가 겹친 경우 → CJK 구분자만 남긴다
PAIRS = [
    (re.compile(r'\s*,\s*(?=｜)'), ''),
    (re.compile(r'\s*,\s*(?=：)'), ''),
    (re.compile(r'\s*,\s*(?=、)'), ''),
    (re.compile(r'\s*,\s*(?=？)'), ''),
    (re.compile(r'\s*,\s*(?=！)'), ''),
    (re.compile(r'\s*,\s*(?=：)'), ''),
    # 라틴계: ", ?" → "?"
    (re.compile(r',\s*\?'), '?'),
    (re.compile(r',\s*!'), '!'),
    # 구분자 뒤 공백 정리
    (re.compile(r'[｜：、]\s+(?=\S)'), '｜'.replace('｜', '　') if False else ''),
]


def parse_md(path):
    raw = io.open(path, encoding='utf-8').read()
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', raw, re.S)
    if not m:
        return None, ''
    fm = {}
    for line in m.group(1).splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm, m.group(2).strip()


def write_md(path, fm, body):
    out = ['---']
    for k, v in fm.items():
        out.append('%s: "%s"' % (k, v.replace('"', '\\"')))
    out.append('---')
    out.append('')
    out.append(body)
    out.append('')
    io.open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(out))


def clean_title(t):
    o = t
    # CJK 구분자 앞 라틴 콤마 제거
    t = re.sub(r'\s*,\s*(?=[｜：、？！])', '', t)
    # 라틴계 ", ?" → "?"
    t = re.sub(r',\s*(\?|!)', r'\1', t)
    # 구분자 뒤 불필요한 공백
    t = re.sub(r'([｜：])\s+', r'\1', t)
    # 구분자 앞 공백 제거 (CJK)
    t = re.sub(r'\s+([｜：、])', r'\1', t)
    # 이중 공백
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t, (t != o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    n = 0
    for p in sorted(glob.glob(os.path.join(CONTENT, 'game', '*', '*.md'))):
        fm, body = parse_md(p)
        if not fm:
            continue
        t = fm.get('title') or ''
        nt, ch = clean_title(t)
        if not ch:
            continue
        n += 1
        print('%s' % os.path.basename(p))
        print('   이전: %s' % t)
        print('    →  : %s' % nt)
        if args.apply:
            fm['title'] = nt
            write_md(p, fm, body)
    print('\n정리 대상 %d건%s' % (n, ' (apply)' if args.apply else ' (dry)'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
