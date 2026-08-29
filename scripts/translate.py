#!/usr/bin/env python3
"""
DeepSeek V4 Flash 번역 파이프라인.
content/posts/*.md (영어 원본) → 12개 언어 번역 → content/translations/{lang}/{slug}.json

사용법:
  python scripts/translate.py                 # 전체 번역
  python scripts/translate.py fed-rate-outlook-2026   # 특정 slug만
  python scripts/translate.py --lang ko       # 특정 언어만
"""
import json, os, re, sys, time, urllib.request, urllib.error, glob

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))

API_KEY = CONFIG['deepseek']['api_key']
BASE_URL = CONFIG['deepseek']['base_url'].rstrip('/')
MODEL = CONFIG['deepseek']['model']
LANGS = CONFIG['languages']
CHAR_MIN = CONFIG['char_min']
CHAR_MAX = CONFIG['char_max']

POSTS_DIR = os.path.join(BASE, 'content', 'posts')
TRANS_DIR = os.path.join(BASE, 'content', 'translations')

MAX_RETRY = 5

# 언어별 문자 밀도 특성 (번역 시 길이 보정 전략)
# dense: 영어보다 문자 수가 훨씬 적음 → 확장 필수
# verbose: 영어보다 길어짐 → 축약 필수
# normal: 대체로 비슷
LANG_DENSITY = {
    'zh': 'dense', 'ja': 'dense', 'ko': 'dense',
    'es': 'verbose', 'fr': 'verbose', 'de': 'verbose',
    'pt': 'verbose', 'ru': 'verbose', 'id': 'verbose',
    'hi': 'normal', 'ar': 'normal', 'bn': 'normal',
}

# ---------- 프론트매터 파싱 ----------
def parse_md(path):
    raw = open(path, encoding='utf-8').read()
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', raw, re.S)
    if not m:
        raise ValueError(f'프론트매터 없음: {path}')
    fm = {}
    for line in m.group(1).splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm, m.group(2).strip()

def char_count(s):
    """공백 포함 문자 수"""
    return len(s)

# ---------- DeepSeek 호출 ----------
def call_deepseek(messages, json_mode=False, net_retries=3):
    body = {
        'model': MODEL,
        'messages': messages,
        'thinking': {'type': 'disabled'},   # 비사고 모드 (번역엔 사고 불필요)
        'max_tokens': 8192,
        'temperature': 0.5,
    }
    if json_mode:
        body['response_format'] = {'type': 'json_object'}
    req = urllib.request.Request(
        BASE_URL + '/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + API_KEY,
        },
    )
    last_err = None
    for attempt in range(net_retries):
        try:
            resp = urllib.request.urlopen(req, timeout=180)
            data = json.loads(resp.read().decode('utf-8'))
            return data['choices'][0]['message']['content']
        except urllib.error.HTTPError as e:
            print(f'  HTTP {e.code}: {e.read().decode("utf-8")[:300]}')
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            print(f'  네트워크 오류({attempt+1}/{net_retries}), 재시도...')
            time.sleep(3)
    raise last_err

# ---------- 번역 ----------
def translate_post(slug, lang, lang_name, title, desc, body):
    system = (
        'You are a professional translator AND editor for a macro-economics blog. '
        'You return valid JSON only, with no extra commentary. '
        'You strictly obey the character-count requirement for the body.'
    )
    density = LANG_DENSITY.get(lang, 'normal')
    if density == 'dense':
        density_hint = (
            f'IMPORTANT: {lang_name} uses roughly 40-50% FEWER characters than English, '
            f'so a literal translation will be far too short. You MUST substantially EXPAND '
            f'the content to reach at least {CHAR_MIN} characters: add background context, '
            f'explain the economic data and its implications in depth, and add concrete examples.'
        )
    elif density == 'verbose':
        density_hint = (
            f'IMPORTANT: {lang_name} runs roughly 15-25% LONGER than English, so a literal '
            f'translation will likely exceed the limit. You MUST CONDENSE the text to stay '
            f'under {CHAR_MAX} characters while keeping all key facts and figures.'
        )
    else:
        density_hint = (
            f'Keep the body between {CHAR_MIN} and {CHAR_MAX} characters (counting spaces).'
        )

    user = f"""Translate the following English article into {lang_name}.

Return a JSON object with exactly these three string fields:
- "title": the translated article title
- "description": the translated meta description (one sentence)
- "body": the translated article body in Markdown (keep "##" headings and paragraph breaks)

STRICT length rule for "body":
The body text MUST be between {CHAR_MIN} and {CHAR_MAX} characters (counting spaces) in {lang_name}. This is a hard requirement.

{density_hint}

Keep all key facts and figures. Never invent false data. The goal is a natural, well-developed article of the required length.

Original English:
TITLE: {title}
DESCRIPTION: {desc}

BODY:
{body}"""

    for attempt in range(1, MAX_RETRY + 1):
        content = call_deepseek(
            [{'role': 'system', 'content': system},
             {'role': 'user', 'content': user}],
            json_mode=True,
        )
        try:
            obj = json.loads(content)
        except json.JSONDecodeError:
            print(f'    [{attempt}] JSON 파싱 실패, 재시도...')
            continue
        t = obj.get('title', '')
        d = obj.get('description', '')
        b = obj.get('body', '')
        n = char_count(b)
        if not (CHAR_MIN <= n <= CHAR_MAX):
            print(f'    [{attempt}] {lang} body {n}자 (범위 밖), 재시도...')
            if n < CHAR_MIN:
                hint = f'Your previous body was {n} characters, which is TOO SHORT. Expand it substantially: add background context, explain the economic data and its implications in more depth, and give concrete examples. It must reach at least {CHAR_MIN} characters.'
            else:
                hint = f'Your previous body was {n} characters, which is TOO LONG. Condense it to at most {CHAR_MAX} characters while keeping all key points.'
            user += '\n\n' + hint
            continue
        print(f'    {lang}: 제목 {len(t)}자 / 본문 {n}자 [OK]')
        return {'slug': slug, 'lang': lang, 'title': t, 'description': d, 'body': b}
    # 재시도 소진 — 그래도 결과 있으면 마지막 것 반환
    print(f'    {lang}: 재시도 소진, 마지막 결과 사용 ({char_count(b)}자)')
    return {'slug': slug, 'lang': lang, 'title': t, 'description': d, 'body': b}

# ---------- 메인 ----------
def main():
    args = sys.argv[1:]
    only_slug = None
    only_lang = None
    i = 0
    while i < len(args):
        if args[i] == '--lang':
            only_lang = args[i + 1]; i += 2
        else:
            only_slug = args[i]; i += 1

    posts = sorted(glob.glob(os.path.join(POSTS_DIR, '*.md')))
    for path in posts:
        slug = os.path.splitext(os.path.basename(path))[0]
        if only_slug and slug != only_slug:
            continue
        fm, body = parse_md(path)
        title, desc = fm['title'], fm['description']
        print(f'[{slug}]')
        for lang, lang_name in LANGS.items():
            if only_lang and lang != only_lang:
                continue
            out_dir = os.path.join(TRANS_DIR, lang)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, slug + '.json')
            if os.path.exists(out_path):
                print(f'    {lang}: 이미 존재, 스킵')
                continue
            result = translate_post(slug, lang, lang_name, title, desc, body)
            json.dump(result, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
            time.sleep(0.3)  # rate limit 여유

if __name__ == '__main__':
    main()
