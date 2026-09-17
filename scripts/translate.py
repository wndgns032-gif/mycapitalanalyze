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

PROVIDER = CONFIG.get('provider', 'deepseek')
PROV = CONFIG[PROVIDER]
API_KEY = PROV['api_key']
BASE_URL = PROV['base_url'].rstrip('/')
MODEL = PROV['model']
LANGS = CONFIG.get('translate_languages') or CONFIG['languages']
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


# 한국어 번역 품질 게이트 — 중국어 잔재(번역 안 된 한자) 감지
CJK_RE = re.compile(r'[\u4e00-\u9fff]')

def quality_gate(lang, title, body):
    """(ok, hint) 반환. 통과하면 (True, None)."""
    if lang == 'ko':
        n = len(CJK_RE.findall(body))
        if n > 8:
            return False, (
                f'Your {lang} body still contains {n} untranslated Chinese Han characters '
                f'(e.g. 公积金, 市值, 中际旭创). Modern Korean does NOT use Hanja in normal prose. '
                f'You MUST convert every Chinese term into proper Korean: '
                f'translate the meaning or transliterate the official Korean name '
                f'(e.g. 宁德时代 -> CATL, 公积金 -> 주택공적금). Output Korean ONLY, no Hanja.'
            )
    return True, None

# ---------- LLM 호출 (provider 공통) ----------
def extract_json(text):
    """모델이 코드펜스나 설명을 붙여도 JSON만 뽑아낸다."""
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
        text = re.sub(r'```\s*$', '', text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def call_llm(messages, json_mode=False, net_retries=3):
    body = {
        'model': MODEL,
        'messages': messages,
        'max_tokens': 8192,
        'temperature': 0.5,
    }
    if PROVIDER == 'deepseek':
        body['thinking'] = {'type': 'disabled'}
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
    # 언어별 현실적인 길이 목표 (영문 원본 대비 문자 밀도 반영)
    if density == 'dense':
        lo, hi = int(CHAR_MIN * 0.60), int(CHAR_MAX * 0.72)
    elif density == 'verbose':
        lo, hi = int(CHAR_MIN * 1.10), int(CHAR_MAX * 1.10)
    else:
        lo, hi = int(CHAR_MIN * 0.85), int(CHAR_MAX * 0.92)
    if density == 'dense':
        density_hint = (
            f'IMPORTANT: {lang_name} uses roughly 40-50% FEWER characters than English, '
            f'so a literal translation will be far too short. You MUST substantially EXPAND '
            f'the content to reach at least {lo} characters: add background context, '
            f'explain the economic data and its implications in depth, and add concrete examples.'
        )
    elif density == 'verbose':
        density_hint = (
            f'IMPORTANT: {lang_name} runs roughly 15-25% LONGER than English, so a literal '
            f'translation will likely exceed the limit. You MUST CONDENSE the text to stay '
            f'under {hi} characters while keeping all key facts and figures.'
        )
    else:
        density_hint = (
            f'Keep the body between {lo} and {hi} characters (counting spaces).'
        )

    user = f"""Translate the following English article into {lang_name}.

Return a JSON object with exactly these three string fields:
- "title": the translated article title
- "description": the translated meta description (one sentence)
- "body": the translated article body in Markdown (keep "##" headings and paragraph breaks)

STRICT length rule for "body":
The body text MUST be between {lo} and {hi} characters (counting spaces) in {lang_name}. This is a hard requirement.

{density_hint}

Keep all key facts and figures. Never invent false data. The goal is a natural, well-developed article of the required length.

Original English:
TITLE: {title}
DESCRIPTION: {desc}

BODY:
{body}"""

    last = {'title': '', 'description': '', 'body': ''}
    for attempt in range(1, MAX_RETRY + 1):
        content = call_llm(
            [{'role': 'system', 'content': system},
             {'role': 'user', 'content': user}],
            json_mode=True,
        )
        obj = extract_json(content or '')
        if not obj or not isinstance(obj, dict):
            print(f'    [{attempt}] JSON 파싱 실패, 재시도...')
            continue
        t = str(obj.get('title', '') or '')
        d = str(obj.get('description', '') or '')
        b = str(obj.get('body', '') or '')
        last = {'slug': slug, 'lang': lang, 'title': t, 'description': d, 'body': b}
        n = char_count(b)
        if not (lo <= n <= hi):
            print(f'    [{attempt}] {lang} body {n}자 (범위 밖), 재시도...')
            if n < CHAR_MIN:
                hint = f'Your previous body was {n} characters, which is TOO SHORT. Expand it substantially: add background context, explain the economic data and its implications in more depth, and give concrete examples. It must reach at least {lo} characters.'
            else:
                hint = f'Your previous body was {n} characters, which is TOO LONG. Condense it to at most {hi} characters while keeping all key points.'
            user += '\n\n' + hint
            continue
        ok, hint = quality_gate(lang, t, b)
        if not ok:
            print(f'    [{attempt}] {lang} 품질 게이트 실패, 재시도...')
            user += '\n\n' + hint
            continue
        print(f'    {lang}: 제목 {len(t)}자 / 본문 {n}자 [OK]')
        return {'slug': slug, 'lang': lang, 'title': t, 'description': d, 'body': b}
    # 재시도 소진 — 그래도 결과 있으면 마지막 것 반환
    print(f'    {lang}: 재시도 소진, 마지막 결과 사용 ({char_count(last["body"])}자)')
    return last

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
