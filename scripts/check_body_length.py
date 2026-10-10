#!/usr/bin/env python3
"""
포스트 본문 길이 실측기 — 색인 제외 판단용.

왜 필요한가 (2026-10-10 실측 함정)
  처음에 본문 길이를 이렇게 쟀다:
      re.search(r'<div class="prose">(.*?)</div>', html, re.S)
  `</div>` 가 non-greedy 라서 **본문 안의 첫 중첩 div 에서 끊긴다.**
  포스트 본문에는 상품 카드·광고 등 div 가 들어 있어서, 실제 3,951자짜리
  본문이 1,657자로 측정됐다. 그 잘못된 숫자로 "게임 글이 짧아서 색인에서
  제외됐다"고 오진했다. 길이는 원인이 아니었다.

  → 본문 전체는 `<div class="prose">` 부터 `</main>` 까지로 재야 한다.
  → 그리고 본문에 삽입된 카드·광고 텍스트는 빼야 글 길이만 남는다.

사용법
  python scripts/check_body_length.py              # 전체 분포 요약
  python scripts/check_body_length.py --thin 2000  # 기준 미만 목록
  python scripts/check_body_length.py --url ko/post/xxx.html
"""
import os, re, sys, glob, collections, statistics

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 본문에 삽입되는 부가 블록 — 글 길이에서 제외한다.
NOISE_PATTERNS = [
    re.compile(r'<aside\b.*?</aside>', re.S),
    re.compile(r'<script\b.*?</script>', re.S),
    re.compile(r'<style\b.*?</style>', re.S),
    re.compile(r'data-affiliate-slot.*?</section>', re.S),
    re.compile(r'<figure\b.*?</figure>', re.S),
    re.compile(r'<div[^>]*class="[^"]*adsbygoogle[^"]*"[^>]*>.*?</div>', re.S),
]

SKIP_DIRS = ('_shared', '_shots', '.cache', 'node_modules', 'content', '.git', 'assets')


def article_text(html):
    """본문 prose~main 구간에서 태그·부가블록을 제거한 실제 텍스트."""
    i = html.find('<div class="prose">')
    if i < 0:
        return ''
    j = html.find('</main>', i)
    seg = html[i:j] if j > i else html[i:]
    for pat in NOISE_PATTERNS:
        seg = pat.sub(' ', seg)
    seg = re.sub(r'<[^>]+>', ' ', seg)
    seg = re.sub(r'&[a-z]+;|&#\d+;', ' ', seg)
    return re.sub(r'\s+', ' ', seg).strip()


def iter_pages():
    for f in glob.glob(os.path.join(BASE, '**', '*.html'), recursive=True):
        rel = os.path.relpath(f, BASE).replace('\\', '/')
        if any(rel.startswith(d + '/') or ('/' + d + '/') in rel for d in SKIP_DIRS):
            continue
        parts = rel.split('/')
        if parts[-1] != 'index.html' and (
                parts[0] in ('post', 'game') or (len(parts) > 1 and parts[1] in ('post', 'game'))):
            yield rel, f


def main():
    args = sys.argv[1:]
    one = args[args.index('--url') + 1] if '--url' in args else None
    thin = int(args[args.index('--thin') + 1]) if '--thin' in args else 0

    if one:
        h = open(os.path.join(BASE, one), encoding='utf-8').read()
        print(f'{one}: {len(article_text(h))}자')
        return

    rows = []
    for rel, path in iter_pages():
        try:
            html = open(path, encoding='utf-8').read()
        except OSError:
            continue
        n = len(article_text(html))
        if n:
            rows.append((rel, n))

    if not rows:
        print('측정된 페이지가 없다 — build.py 를 먼저 실행하세요')
        return

    lens = sorted(n for _, n in rows)
    q = statistics.quantiles(lens, n=10) if len(lens) >= 10 else []
    print(f'측정 페이지 {len(rows)}개')
    print(f'  최소 {lens[0]} · 10% {q[0]:.0f} · 중앙 {statistics.median(lens):.0f} '
          f'· 90% {q[-1]:.0f} · 최대 {lens[-1]}')

    if thin:
        under = sorted((r for r in rows if r[1] < thin), key=lambda t: t[1])
        print(f'\n{thin}자 미만: {len(under)}개')
        for rel, n in under[:40]:
            print(f'  {n:5d}  {rel}')
        if len(under) > 40:
            print(f'  ... 외 {len(under) - 40}개')


if __name__ == '__main__':
    main()
