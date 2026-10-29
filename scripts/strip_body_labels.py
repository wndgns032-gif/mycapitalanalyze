# -*- coding: utf-8 -*-
"""본문 텍스트에 남아있는 스토어 라벨 제거 (2026-10-29).

문제: `clean_store_labels.py` 는 alt 텍스트만 손봤고, 본문 **서술**에 남은
      "앱이름 - Google Play의 앱" 형태(325건 / 1332개)는 그대로였다.
      → 본문 첫 문단이 전부 "AppName - Google Play의 앱" 으로 시작한다.

원칙 (안전 우선):
- 라벨 앞 구분자가 있더라도, 그 바로 앞이 **앱 이름으로 끝나야** 제거한다.
  → "…에서는 Google Play 라벨이 언급된다" 같은 본문 서술은 절대 건드리지 않는다.
- slug / title / description 은 손대지 않는다 (body 만).
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
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import store_label as SL  # noqa: E402

_RX_ALT = re.compile(r'!\[([^\]]*)\]')
_RX_LABEL_ALL = re.compile('|'.join(re.escape(p) for p in SL.LABEL_PHRASES))
_RX_DELIM = re.compile(r'[\-–—:|｜：]\s*$')


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


def _unesc(s):
    """HTML 엔티티 복원 (앱 이름이 alt 에서 &amp; 로 저장되는 경우)."""
    return (s or '').replace('&amp;', '&').replace('&#39;', "'") \
        .replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>')


def app_name_of(body):
    """본문 첫 이미지 alt 텍스트 = 스토어 이름 그대로."""
    return _unesc(SL.clean_name(SL.alt_text(body)).strip())


def strip_body_labels(body, app_name):
    """본문에서 앱 이름에 붙은 스토어 라벨을 제거한다. 반환: (본문, 제거 건수)"""
    if not body or not app_name:
        return body, 0
    nm = app_name.lower()
    removed = 0

    # ── 유형 1: ru 식 — 라벨이 앞에 붙고 앱 이름이 뒤에 오는 형태
    #    "Приложения в Google Play – Cat Ark — это ..." → "Cat Ark — это ..."
    for lb in SL.LABEL_PHRASES:
        pat = re.compile(re.escape(lb) + r'\s*[\-–—:|｜：]\s*' + re.escape(app_name), re.I)
        body, k = pat.subn(app_name, body)
        removed += k

    # ── 유형 2: 앱 이름 직후 라벨 — "AppName - 라벨"
    while True:
        target = None
        for m in _RX_LABEL_ALL.finditer(body):
            head = body[:m.start()]
            if not head.strip():
                continue
            dm = _RX_DELIM.search(head)
            if not dm:
                continue
            tok = head[:dm.start()].rstrip()
            if not tok.lower().endswith(nm):
                continue
            start = dm.start() + 1
            while start < len(body) and body[start] in ' \t':
                start += 1
            target = (start, m.end())
            break
        if not target:
            break
        s, e = target
        tail = body[e:].lstrip(' ')
        body = body[:s] + ' ' + tail
        removed += 1

    # ── 유형 3: 앞쪽 라벨 단독 (문장 시작) — "Приложения в Google Play – AppName"
    for lb in SL.LABEL_PHRASES:
        pat = re.compile(r'(?:(?<=^)|(?<=\n)|(?<=[.!?]\s))' + re.escape(lb)
                         + r'\s*[\-–—:|｜：]\s*' + re.escape(app_name), re.I)
        body, k = pat.subn(app_name, body)
        removed += k

    return body, removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--lang', default='')
    args = ap.parse_args()

    dirs = sorted(glob.glob(os.path.join(CONTENT, 'game', '*')))
    if args.lang:
        dirs = [d for d in dirs if os.path.basename(d) == args.lang]

    n_file = 0
    n_hit = 0
    for d in dirs:
        lang = os.path.basename(d)
        for p in sorted(glob.glob(os.path.join(d, '*.md'))):
            fm, body = parse_md(p)
            if not fm:
                continue
            if (fm.get('sourceName') or '').strip() not in ('Google Play', 'App Store'):
                continue
            nm = app_name_of(body)
            if not nm:
                continue
            nb, n = strip_body_labels(body, nm)
            if n <= 0:
                continue
            n_file += 1
            n_hit += n
            print('[%s] %s — %d건' % (lang, os.path.basename(p), n))
            if args.apply:
                write_md(p, fm, nb)

    print('\n파일 %d건 / 라벨 %d개 제거 %s'
          % (n_file, n_hit, '(apply)' if args.apply else '(dry)'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
