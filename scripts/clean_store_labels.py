# -*- coding: utf-8 -*-
"""기존 발행 글의 **스토어 라벨 오염** 일괄 제거 (2026-10-29).

문제: Play 스토어 og:title 에 붙는 언어별 라벨(" - Aplicaciones en Google Play",
      " – Apps bei Google Play", " - Google Play のアプリ" 등) 이
      앱 이름에 섞인 채 frontmatter title/description 에 저장되어 있다.
      → 제목이 "아니라 이름 뒤에 스토어 설명이 붙은" 형태가 되고,
        검색 결과에서 앱 이름이 뭉개진다.

처리:
- title / description / 본문 이미지 캡션에서 스토어 라벨만 제거한다.
- **slug 는 건드리지 않는다** (이미 색인된 URL 이라 변경하면 404 발생).
- description 이 300자(로이 기준) 미달이 되면 표시만 하고 건드리지 않는다
  (내용을 지우는 것보다 그대로 두는 게 낫다).
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

DESC_MIN = 300


def parse_md(path):
    raw = io.open(path, encoding='utf-8').read()
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', raw, re.S)
    if not m:
        return None, None, ''
    fm = {}
    for line in m.group(1).splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm, m.group(1), m.group(2).strip()


def write_md(path, fm, body):
    out = ['---']
    for k, v in fm.items():
        out.append('%s: "%s"' % (k, v.replace('"', '\\"')))
    out.append('---')
    out.append('')
    out.append(body)
    out.append('')
    io.open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='실제 저장 (없으면 미리보기)')
    ap.add_argument('--lang', default='')
    args = ap.parse_args()

    dirs = sorted(glob.glob(os.path.join(CONTENT, 'game', '*')))
    if args.lang:
        dirs = [d for d in dirs if os.path.basename(d) == args.lang]

    n_file = 0
    n_title = 0
    n_desc = 0
    n_body = 0
    short_desc = []
    for d in dirs:
        lang = os.path.basename(d)
        for p in sorted(glob.glob(os.path.join(d, '*.md'))):
            fm, fmb, body = parse_md(p)
            if not fm:
                continue
            t = fm.get('title') or ''
            ds = fm.get('description') or ''
            if not (SL.has_store_label(t) or SL.has_store_label(ds)
                    or SL.has_store_label(body)):
                continue
            if SL.has_store_label(body):
                # 본문은 이미지 alt 텍스트만 대상
                _nb, _ = SL.strip_body_captions(body)
                if _nb == body:
                    body_has = False
                else:
                    body_has = True
            else:
                body_has = False
            if not (SL.has_store_label(t) or SL.has_store_label(ds) or body_has):
                continue
            n_file += 1
            nt, ch1 = SL.strip_store_label(t)
            nd, ch2 = SL.strip_store_label(ds)
            nb, ch3 = SL.strip_body_captions(body)
            if ch1:
                n_title += 1
            if ch2:
                n_desc += 1
                if len(nd) < DESC_MIN:
                    short_desc.append((lang, os.path.basename(p), len(nd)))
            if ch3:
                n_body += 1
            print('[%s] %s' % (lang, os.path.basename(p)))
            if ch1:
                print('   제목: %s' % t)
                print('    →  %s' % nt)
            if ch2:
                print('   설명: %s' % ds[:110])
                print('    →  %s' % nd[:110])
            if ch3 and not (ch1 or ch2):
                print('   본문 캡션 라벨 제거')
            if args.apply:
                if ch1:
                    fm['title'] = nt
                if ch2:
                    fm['description'] = nd
                write_md(p, fm, nb if ch3 else body)

    print('\n파일 %d건 / 제목 %d건 / 설명 %d건 / 본문 %d건 %s'
          % (n_file, n_title, n_desc, n_body, '(apply)' if args.apply else '(dry)'))
    if short_desc:
        print('⚠️ 라벨 제거 후 설명이 %d자 미만 — %d건' % (DESC_MIN, len(short_desc)))
        for lang, f, n in short_desc[:20]:
            print('   %s/%s → %d자' % (lang, f, n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
