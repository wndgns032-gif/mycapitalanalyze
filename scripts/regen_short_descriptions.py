# -*- coding: utf-8 -*-
"""스토어 라벨 제거로 설명이 300자 미달이 된 글의 설명만 재생성 (2026-10-29).

배경:
- `clean_store_labels.py` 가 라벨(" - Google Play のアプリ")을 설명에서 떼어내면
  길이가 20~40자 줄어 300자(로이 기준 SEO 하한) 미달이 된다.
- 본문·제목은 멀쩡한데 설명만 미달이면 SEO meta 가 약해진다.
→ `app_radar.regen_description()` 로 **설명만** 다시 쓴다.
  (제목·본문은 손대지 않는다)

주의:
- 라벨 제거 **전**에도 300자 미달이던 글은 대상이 아니다 (기존 결함).
  → 제거 전 길이가 300 이상이었고 제거 후 300 미만인 글만 재생성.
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

DESC_MIN = 300
DESC_MAX = 480
BACKUP = os.path.join(ROOT, '.workbuddy', 'tmp', 'desc_backup')


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lang', default='')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    import app_radar as A
    import llm

    dirs = sorted(glob.glob(os.path.join(CONTENT, 'game', '*')))
    if args.lang:
        dirs = [d for d in dirs if os.path.basename(d) == args.lang]

    targets = []
    for d in dirs:
        lang = os.path.basename(d)
        for p in sorted(glob.glob(os.path.join(d, '*.md'))):
            fm, body = parse_md(p)
            if not fm:
                continue
            if (fm.get('sourceName') or '').strip() not in ('Google Play', 'App Store'):
                continue
            ds = fm.get('description') or ''
            if len(ds) >= DESC_MIN:
                continue
            targets.append((p, fm, body, lang))

    if args.limit:
        targets = targets[:args.limit]
    print('대상 %d건' % len(targets))
    if not targets:
        return 0

    if not args.dry:
        os.makedirs(BACKUP, exist_ok=True)

    ok = 0
    for i, (p, fm, body, lang) in enumerate(targets, 1):
        # 앱 이름: 본문 첫 이미지 alt 텍스트 (스토어 이름 그대로)
        try:
            import retitle_store_posts as R
            t = R.name_from_alt(body)
        except Exception:
            t = ''
        if not t:
            c = fm.get('title') or ''
            for sep in (' — ', ' – ', ' - ', ', ', ': '):
                if sep in c:
                    c = c.split(sep)[0].strip()
                    break
            t = c
        material = re.sub(r'\s+', ' ', body)[:3000]
        newd = ''
        try:
            # regen_description() 은 문자열만 반환한다 (provider 튜플 아님).
            newd = A.regen_description(llm, lang, lang, material, t,
                                       DESC_MIN, DESC_MAX,
                                       old_desc=fm.get('description') or '')
        except Exception as e:
            print('  [%d] LLM 실패: %s' % (i, e))
            continue
        if not newd or len(newd) < DESC_MIN:
            print('  [%d] 재생성 실패(길이 %d) — %s' % (i, len(newd or ''), t[:40]))
            continue
        print('  [%d] %s' % (i, t[:50]))
        print('      이전(%d): %s' % (len(fm.get('description') or ''),
                                     (fm.get('description') or '')[:90]))
        print('      신규(%d): %s' % (len(newd), newd[:90]))
        if not args.dry:
            import shutil
            shutil.copy2(p, os.path.join(BACKUP, os.path.basename(p)))
            fm['description'] = newd
            write_md(p, fm, body)
        ok += 1
    print('\n완료: %d/%d건%s' % (ok, len(targets), ' (dry)' if args.dry else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
