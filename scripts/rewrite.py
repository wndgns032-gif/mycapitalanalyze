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
PROVIDER = CONFIG.get('provider', 'deepseek')
PROV = CONFIG[PROVIDER]
API_KEY = PROV['api_key']
BASE_URL = PROV['base_url'].rstrip('/')
MODEL = PROV['model']
CHAR_MIN = CONFIG.get('char_min', SRC.get('min_chars', 3000))
CHAR_MAX = CONFIG.get('char_max', SRC.get('max_chars', 5000))

RAW_DIR = os.path.join(BASE, 'content', 'raw')
POSTS_DIR = os.path.join(BASE, 'content', 'posts')
os.makedirs(POSTS_DIR, exist_ok=True)

MAX_RETRY = 4

def extract_json(text):
    text = (text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
        text = re.sub(r'```\s*$', '', text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def call_llm(messages):
    body = {
        'model': MODEL,
        'messages': messages,
        'max_tokens': 16384,
        'temperature': 0.6,
        'response_format': {'type': 'json_object'},
    }
    if PROVIDER == 'deepseek':
        body['thinking'] = {'type': 'disabled'}
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
3. Structure with Markdown: use a short intro, then "## Key Facts", "## Analysis" / "## Implications", and finally a "## FAQ" section with exactly 3 short question/answer pairs (use "###" for each question). No "#" H1 heading — the page template already prints the title as H1.
4. The body MUST be between {CHAR_MIN} and {CHAR_MAX} characters (English, counting spaces). Expand with background and analysis if too short.
5. The title must be SEO-friendly and specific. The description is one sentence.
6. Never invent false numbers — keep the source's facts, reinterpret them in your own words.

Return JSON with these fields:
- "slug": string — a URL slug of 4-8 English words, lowercase, hyphen-separated, containing the main keyword (e.g. "ny-fed-subprime-credit-risk-methodology"). No dates, no stop-word stuffing, no numbers-only.
- "title": string
- "description": string (one sentence)
- "category": string (one of: Monetary Policy, Inflation, China, Bonds, Labor Markets, Global Macro, US Economy, Central Banking, Economic Research)
- "body": string (Markdown)

SOURCE: {raw.get('source_name')}
TITLE: {raw.get('title')}
PUBLISHED: {raw.get('pubDate')}
CONTENT:
{raw.get('content') or raw.get('description')}"""

    user = base_user
    obj = {'title': '', 'description': '', 'category': '', 'body': ''}
    for attempt in range(1, MAX_RETRY + 1):
        content = call_llm([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ])
        parsed = extract_json(content)
        if not parsed or not isinstance(parsed, dict):
            print(f'  [{attempt}] JSON 파싱 실패')
            continue
        obj = parsed
        body = str(obj.get('body', '') or '')
        n = len(body)
        if CHAR_MIN <= n <= CHAR_MAX:
            print(f'  {raw["slug"]}: 제목 {len(obj.get("title",""))}자 / 본문 {n}자 [OK]')
            return obj
        print(f'  [{attempt}] {raw["slug"]} 본문 {n}자 (범위 밖), 재시도...')
        hint = f'Your body was {n} characters. Rewrite it to be between {CHAR_MIN} and {CHAR_MAX} characters.' + (' Expand with more analysis and background.' if n < CHAR_MIN else ' Trim redundancy while keeping key facts.')
        user = base_user + '\n\n' + hint
    print(f'  {raw["slug"]}: 재시도 소진, 마지막 결과 사용')
    return obj

def make_slug(raw_slug, title, taken):
    """모델이 만든 키워드 슬러그를 정제한다. 실패하면 해시 슬러그로 폴백."""
    cand = re.sub(r'[^a-z0-9\-]', '', str(title or '').lower().replace(' ', '-'))
    cand = re.sub(r'-{2,}', '-', cand).strip('-')
    words = [w for w in cand.split('-') if w][:8]
    if len(words) < 3:
        return None
    slug = '-'.join(words)
    if slug in taken:
        slug = slug + '-' + str(raw_slug)[-6:]
    return slug


def clean_body(s):
    """GLM이 본문 앞에 붙이는 '# 제목' H1 제거 — 페이지 템플릿의 H1과 중복된다."""
    s = (s or '').strip()
    lines = s.split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].lstrip().startswith('# '):
        lines.pop(0)
    return '\n'.join(lines).strip()


def main():
    raws = sorted([f for f in os.listdir(RAW_DIR) if f.endswith('.json')])
    # 이미 posts로 변환된 slug 제외
    existing = set(os.path.splitext(f)[0] for f in os.listdir(POSTS_DIR) if f.endswith('.md'))
    # 하루 발행량 상한 — 대량 자동 생성은 구글 '스케일드 콘텐츠 어뷰즈' 리스크
    cap = int(CONFIG.get('max_new_posts_per_run', 3))
    done = 0
    for fn in raws:
        if done >= cap:
            print(f'  일일 발행 상한({cap}개) 도달 — 나머지는 다음 실행으로 이월')
            break
        raw = json.load(open(os.path.join(RAW_DIR, fn), encoding='utf-8'))
        slug = raw['slug']
        if slug in existing:
            continue
        print(f'[{slug}] 재가공')
        obj = rewrite(raw)
        body = clean_body(obj.get('body', ''))
        # 품질 게이트: 너무 짧은 글은 발행하지 않고 다음 실행에 다시 시도한다
        if len(body) < CHAR_MIN * 0.6:
            print(f'  [{slug}] 본문 {len(body)}자 — 기준 미달, 발행 보류')
            continue
        date = iso_to_date(raw.get('pubDate'))
        # SEO 친화적 키워드 슬러그 (모델 생성 → 실패 시 원본 해시 슬러그 유지)
        taken = set(os.path.splitext(f)[0] for f in os.listdir(POSTS_DIR) if f.endswith('.md'))
        nice = make_slug(slug, obj.get('title', ''), taken)
        if nice:
            print(f'  slug: {slug} -> {nice}')
            slug = nice
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
        out_path = os.path.join(POSTS_DIR, slug + '.md')
        open(out_path, 'w', encoding='utf-8').write(fm + body.strip() + '\n')
        done += 1
        time.sleep(0.3)
    print(f'재가공 완료: {done}개')

if __name__ == '__main__':
    main()
