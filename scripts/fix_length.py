#!/usr/bin/env python3
"""
글자수 보정 패스.
content/translations/{lang}/*.json 중 1500-2300자 범위 밖인 본문만
확장/축약 전용 프롬프트로 재처리해 범위 안으로 보정한다.

사용법: python scripts/fix_length.py [--lang zh,fr]
"""
import json, os, re, sys, time, glob, urllib.request, urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
# 제공자 자동 선택: config.json 의 provider 값을 따른다 (없으면 키가 있는 쪽을 우선).
_PROVIDER = (CONFIG.get('provider') or '').strip().lower()
if not _PROVIDER:
    _PROVIDER = 'glm' if (CONFIG.get('glm') or {}).get('api_key') else 'deepseek'
PROV = CONFIG.get(_PROVIDER) or {}
if not (PROV.get('api_key') or '').strip():
    for _alt in ('glm', 'deepseek'):
        if (CONFIG.get(_alt) or {}).get('api_key'):
            _PROVIDER, PROV = _alt, CONFIG[_alt]
            break
API_KEY = PROV.get('api_key', '')
BASE_URL = PROV.get('base_url', '').rstrip('/')
MODEL = PROV.get('model', '')
IS_GLM = (_PROVIDER == 'glm')
LANGS = CONFIG['languages']
CHAR_MIN = CONFIG['char_min']
CHAR_MAX = CONFIG['char_max']
TRANS_DIR = os.path.join(BASE, 'content', 'translations')

MAX_RETRY = 4

def call_deepseek(messages):
    body = {
        'model': MODEL,
        'messages': messages,
        'max_tokens': 8192,
        'temperature': 0.5,
        'response_format': {'type': 'json_object'},
    }
    # thinking 파라미터는 DeepSeek 전용 — GLM 은 받지 않으므로 넣지 않는다.
    if not IS_GLM:
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
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            print(f'  네트워크 오류({attempt+1}/3), 재시도...')
            time.sleep(3)
    raise RuntimeError('network fail')


def fix_length(lang, lang_name, body, n):
    mode = 'expand' if n < CHAR_MIN else 'condense'
    if mode == 'expand':
        goal = f'MUST reach at least {CHAR_MIN} characters. Add background context, explain the data and its implications in depth, and add concrete examples.'
    else:
        goal = f'MUST stay under {CHAR_MAX} characters. Trim redundancy while keeping every key fact and figure.'

    system = (
        'You are a professional editor for a macro-economics blog. '
        'You return valid JSON only, with a single "body" field.'
    )
    base_user = f"""The following {lang_name} article body is currently {n} characters long.

Rewrite ONLY the body so that it is between {CHAR_MIN} and {CHAR_MAX} characters (counting spaces). {goal}

Keep the Markdown structure ("##" headings and paragraphs). Keep the same meaning and all key facts and figures. Do NOT change the title or add any commentary.

Return JSON: {{"body": "..."}}

ARTICLE BODY:
{body}"""

    user = base_user
    last_body = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            content = call_deepseek(
                [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
            )
        except Exception as e:
            print(f'    [{attempt}] 호출 실패: {type(e).__name__}')
            continue
        try:
            obj = json.loads(content)
        except json.JSONDecodeError:
            # 모델이 코드블록이나 설명을 섞어 보내는 경우에 대비한 보정 파싱
            m = re.search(r'\{.*"body"\s*:\s*"(?:[^"\\]|\\.)*"\s*\}', content, re.S)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except json.JSONDecodeError:
                    obj = None
            else:
                obj = None
            if obj is None:
                print(f'    [{attempt}] JSON 파싱 실패 (응답 {len(content or "")}자)')
                continue
        new_body = obj.get('body', '') or ''
        if not new_body.strip():
            print(f'    [{attempt}] 빈 응답')
            continue
        last_body = new_body
        nn = len(new_body)
        if CHAR_MIN <= nn <= CHAR_MAX:
            print(f'    {lang}: {n}자 -> {nn}자 [OK]')
            return new_body
        print(f'    [{attempt}] {lang}: {n}자 -> {nn}자 (여전히 범위 밖)')
        user = base_user + f'\n\nYour rewrite was {nn} characters. Try again: {goal}'
    # 보정 실패 시 원본 유지 (파이프라인을 죽이지 않는다)
    if last_body:
        print(f'    {lang}: 보정 재시도 소진, 마지막 결과 사용 ({len(last_body)}자)')
        return last_body
    print(f'    {lang}: 보정 실패 — 원본 유지 ({n}자)')
    return body


def main():
    args = sys.argv[1:]
    only_langs = None
    if '--lang' in args:
        only_langs = set(args[args.index('--lang') + 1].split(','))

    total = 0
    for lang, lang_name in LANGS.items():
        if only_langs and lang not in only_langs:
            continue
        for path in sorted(glob.glob(os.path.join(TRANS_DIR, lang, '*.json'))):
            d = json.load(open(path, encoding='utf-8'))
            n = len(d['body'])
            if CHAR_MIN <= n <= CHAR_MAX:
                continue
            total += 1
            slug = d['slug']
            print(f'[{slug} / {lang}] {n}자 보정')
            try:
                new_body = fix_length(lang, lang_name, d['body'], n)
            except Exception as e:
                print(f'  !! {slug}/{lang} 보정 중단: {type(e).__name__}: {e}')
                continue
            d['body'] = new_body
            json.dump(d, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
            time.sleep(0.3)
    print(f'보정 완료: {total}개 파일 처리')


if __name__ == '__main__':
    main()
