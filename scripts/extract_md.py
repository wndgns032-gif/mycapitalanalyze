#!/usr/bin/env python3
"""
기존 post/*.html 에서 영어 원본 본문을 추출해 content/posts/*.md 로 변환.
일회성 마이그레이션 스크립트.
"""
import re, os, json, glob

POST_DIR = 'post'
OUT_DIR = 'content/posts'
os.makedirs(OUT_DIR, exist_ok=True)

# HTML 엔티티 디코드
def unescape(s):
    return (s.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
             .replace('&quot;', '"').replace('&#x27;', "'").replace('&#39;', "'"))

# 간단 HTML -> Markdown (h2/h3/p/ul/ol/li/strong/em/blockquote)
def html_to_md(fragment):
    fragment = re.sub(r'<h2[^>]*>(.*?)</h2>', lambda m: '\n## ' + strip_tags(m.group(1)) + '\n', fragment, flags=re.S)
    fragment = re.sub(r'<h3[^>]*>(.*?)</h3>', lambda m: '\n### ' + strip_tags(m.group(1)) + '\n', fragment, flags=re.S)
    fragment = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', lambda m: '\n> ' + strip_tags(m.group(1)).replace('\n', '\n> ') + '\n', fragment, flags=re.S)
    fragment = re.sub(r'<ul[^>]*>(.*?)</ul>', lambda m: '\n' + ul_to_md(m.group(1)) + '\n', fragment, flags=re.S)
    fragment = re.sub(r'<ol[^>]*>(.*?)</ol>', lambda m: '\n' + ol_to_md(m.group(1)) + '\n', fragment, flags=re.S)
    fragment = re.sub(r'<li[^>]*>(.*?)</li>', lambda m: strip_tags(m.group(1)), fragment, flags=re.S)
    fragment = re.sub(r'<p[^>]*>(.*?)</p>', lambda m: '\n' + strip_tags(m.group(1)) + '\n', fragment, flags=re.S)
    fragment = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', fragment, flags=re.S)
    fragment = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', fragment, flags=re.S)
    fragment = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', fragment, flags=re.S)
    fragment = re.sub(r'<br\s*/?>', '\n', fragment)
    fragment = re.sub(r'<[^>]+>', '', fragment)
    fragment = unescape(fragment)
    # 연속 빈 줄 정리
    fragment = re.sub(r'[ \t]+', ' ', fragment)
    fragment = re.sub(r'\n\s*\n+', '\n\n', fragment)
    return fragment.strip()

def strip_tags(s):
    s = re.sub(r'<[^>]+>', '', s)
    return unescape(s).strip()

def ul_to_md(inner):
    items = re.findall(r'<li[^>]*>(.*?)</li>', inner, flags=re.S)
    return '\n'.join('- ' + strip_tags(i) for i in items)

def ol_to_md(inner):
    items = re.findall(r'<li[^>]*>(.*?)</li>', inner, flags=re.S)
    return '\n'.join(f'{n}. ' + strip_tags(i) for n, i in enumerate(items, 1))

for path in sorted(glob.glob(os.path.join(POST_DIR, '*.html'))):
    slug = os.path.splitext(os.path.basename(path))[0]
    raw = open(path, encoding='utf-8').read()

    # title (h1)
    title = ''
    m = re.search(r'<h1[^>]*>(.*?)</h1>', raw, re.S)
    if m:
        title = strip_tags(m.group(1))

    # description (article header 내 <p>)
    desc = ''
    m = re.search(r'<header[^>]*class="[^"]*mb-8[^"]*".*?<p[^>]*>(.*?)</p>', raw, re.S)
    if m:
        desc = strip_tags(m.group(1))

    # category
    cat = ''
    m = re.search(r'<span class="[^"]*bg-brand-50[^"]*"[^>]*>(.*?)</span>', raw, re.S)
    if m:
        cat = strip_tags(m.group(1))

    # date
    date = ''
    m = re.search(r'<time datetime="([^"]+)"', raw)
    if m:
        date = m.group(1)

    # 본문: 모든 prose div 합치기
    prose_parts = re.findall(r'<div class="prose">(.*?)</div>', raw, re.S)
    body = '\n\n'.join(html_to_md(p) for p in prose_parts)

    fm = f"""---
slug: {slug}
title: "{title}"
description: "{desc}"
category: "{cat}"
date: "{date}"
---

"""
    out = os.path.join(OUT_DIR, slug + '.md')
    open(out, 'w', encoding='utf-8').write(fm + body + '\n')
    print(f'{slug}: title={title!r} cat={cat!r} date={date!r} body_len={len(body)}')
