#!/usr/bin/env python3
"""
DeepSeek 재가공기 — 크롤링된 원문(content/raw/*.json)을 저작권 안전하게 재창작.
원문 복사가 아닌 "요약 + 독립 분석" 형태로 1500-2300자 영문 글을 생성해
content/posts/{slug}.md 로 저장한다.

사용법: python scripts/rewrite.py
"""
import json, os, re, sys, time, datetime, urllib.request, urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
SRC = json.load(open(os.path.join(BASE, 'config_sources.json'), encoding='utf-8'))
API_KEY = CONFIG['deepseek']['api_key']
BASE_URL = CONFIG['deepseek']['base_url'].rstrip('/')
MODEL = CONFIG['deepseek']['model']
CHAR_MIN = SRC.get('min_chars', 1500)
CHAR_MAX = SRC.get('max_chars', 2300)

RAW_DIR = os.path.join(BASE, 'content', 'raw')
POSTS_DIR = os.path.join(BASE, 'content', 'posts')
os.makedirs(POSTS_DIR, exist_ok=True)

MAX_RETRY = 4

def call_deepseek(messages):
    body = {
        'model': MODEL,
        'messages': messages,
        'thinking': {'type': 'disabled'},
        'max_tokens': 8192,
        'temperature': 0.6,
        'response_format': {'type': 'json_object'},
    }
    req = urllib.request.Request(
        BASE_URL + '/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API_KEY},
    )
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, timeout=180)
            data = json.loads(resp.read().decode('utf-8'))
            return data['choices'][0]['message']['content']
        except urllib.error.HTTPError as e:
            print(f'  HTTP {e.code}: {e.read().decode("utf-8")[:200]}')
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            print(f'  네트워크 오류({attempt+1}/3), 재시도...')
            time.sleep(3)
    raise RuntimeError('network fail')

def iso_to_date(s):
    if not s:
        return datetime.date.today().isoformat()
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    return datetime.date.today().isoformat()

def rewrite(raw):
    system = (
        'You are an economic analyst and editor for a macro-economics blog. '
        'You return valid JSON only. You rewrite source material into original, '
        'copyright-safe analysis — never copying sentences from the source.'
    )
    base_user = f"""Rewrite the following news item into an original macro-economics article.

STRICT rules:
1. Do NOT copy any sentence from the source. Summarize the key facts in your own words.
2. Add your own independent analysis: what it means for markets, investors, or policy.
3. Structure with Markdown: use a short intro, then 2-3 "##" sections (e.g. "## Key Facts", "## Analysis", "## Implications").
4. The body MUST be between {CHAR_MIN} and {CHAR_MAX} characters (English, counting spaces). Expand with background and analysis if too short.
5. The title must be SEO-friendly and specific. The description is one sentence.
6. Never invent false numbers — keep the source's facts, reinterpret them in your own words.

Return JSON with these fields:
- "title": string
- "description": string (one sentence)
- "category": string (one of: Monetary Policy, Inflation, China, Bonds, Labor Markets, Global Macro, US Economy, Central Banking, Economic Research)
- "body": string (Markdown)

SOURCE: {raw.get('source_name')}
TITLE: {raw.get('title')}
PUBLISHED: {raw.get('pubDate')}
CONTENT:
{raw.get('description')}"""

    user = base_user
    for attempt in range(1, MAX_RETRY + 1):
        content = call_deepseek([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ])
        try:
            obj = json.loads(content)
        except json.JSONDecodeError:
            print(f'  [{attempt}] JSON 파싱 실패')
            continue
        body = obj.get('body', '')
        n = len(body)
        if CHAR_MIN <= n <= CHAR_MAX:
            print(f'  {raw["slug"]}: 제목 {len(obj.get("title",""))}자 / 본문 {n}자 [OK]')
            return obj
        print(f'  [{attempt}] {raw["slug"]} 본문 {n}자 (범위 밖), 재시도...')
        hint = f'Your body was {n} characters. Rewrite it to be between {CHAR_MIN} and {CHAR_MAX} characters.' + (' Expand with more analysis and background.' if n < CHAR_MIN else ' Trim redundancy while keeping key facts.')
        user = base_user + '\n\n' + hint
    print(f'  {raw["slug"]}: 재시도 소진, 마지막 결과 사용')
    return obj

def main():
    raws = sorted([f for f in os.listdir(RAW_DIR) if f.endswith('.json')])
    # 이미 posts로 변환된 slug 제외
    existing = set(os.path.splitext(f)[0] for f in os.listdir(POSTS_DIR) if f.endswith('.md'))
    done = 0
    for fn in raws:
        raw = json.load(open(os.path.join(RAW_DIR, fn), encoding='utf-8'))
        slug = raw['slug']
        if slug in existing:
            continue
        print(f'[{slug}] 재가공')
        obj = rewrite(raw)
        date = iso_to_date(raw.get('pubDate'))
        fm = f"""---
slug: {slug}
title: "{obj.get('title', raw['title']).replace(chr(34), chr(39))}"
description: "{obj.get('description', '').replace(chr(34), chr(39))}"
category: "{obj.get('category', raw['category'])}"
date: "{date}"
sourceName: "{raw.get('source_name', '')}"
sourceUrl: "{raw.get('source_url', '')}"
---

"""
        body = obj.get('body', '')
        out_path = os.path.join(POSTS_DIR, slug + '.md')
        open(out_path, 'w', encoding='utf-8').write(fm + body.strip() + '\n')
        done += 1
        time.sleep(0.3)
    print(f'재가공 완료: {done}개')

if __name__ == '__main__':
    main()
