#!/usr/bin/env python3
"""
글자수 보정 패스.
content/translations/{lang}/*.json 중 1500-2300자 범위 밖인 본문만
확장/축약 전용 프롬프트로 재처리해 범위 안으로 보정한다.

사용법: python scripts/fix_length.py [--lang zh,fr] [--limit 12] [--deadline 780]

- --limit    : 1회 실행에서 보정할 파일 최대 개수 (기본 12). 매일 전부 돌리면
               비용/시간이 폭발하므로 예산을 걸어 둔다. 남은 건 다음 실행에서 처리.
- --deadline : 전체 실행 제한 초 (기본 780). 넘으면 남은 파일은 다음 실행으로 미룬다.
- 처리 순서는 최신 글 우선(mtime 내림차순) — 오래된 글보다 새 글이 먼저 정상화된다.
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
# 실제 호출은 scripts/llm.py 에 위임 (Flash 전용 + 제공자 자동 폴백)
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import llm  # noqa: E402
API_KEY = PROV.get('api_key', '')
BASE_URL = PROV.get('base_url', '').rstrip('/')
MODEL = PROV.get('model', '')
LANGS = CONFIG['languages']
CHAR_MIN = CONFIG['char_min']
CHAR_MAX = CONFIG['char_max']
TRANS_DIR = os.path.join(BASE, 'content', 'translations')

MAX_RETRY = 3  # 한 파일당 최대 재시도. 실패해도 마지막 결과를 저장해 다음 실행에서 이어간다.

def call_deepseek(messages):
    """Flash 모델로 호출 (함수명은 호환을 위해 유지)."""
    content, _provider = llm.chat(
        messages,
        max_tokens=8192,
        temperature=0.5,
        response_format={'type': 'json_object'},
        timeout=180,
    )
    return content


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

    def _opt(name, default):
        if name in args:
            try:
                return int(args[args.index(name) + 1])
            except (ValueError, IndexError):
                return default
        return default

    limit = _opt('--limit', int(os.environ.get('FIX_LENGTH_LIMIT', '12')))
    deadline = _opt('--deadline', int(os.environ.get('FIX_LENGTH_DEADLINE', '780')))
    t_start = time.time()
    pending = 0

    # 보정 대상 목록을 먼저 모은다 (최신 글 우선: mtime 내림차순)
    targets = []
    for lang, lang_name in LANGS.items():
        if only_langs and lang not in only_langs:
            continue
        for path in glob.glob(os.path.join(TRANS_DIR, lang, '*.json')):
            try:
                d = json.load(open(path, encoding='utf-8'))
                n = len(d['body'])
            except Exception:
                continue
            if CHAR_MIN <= n <= CHAR_MAX:
                continue
            try:
                mt = os.path.getmtime(path)
            except OSError:
                mt = 0
            targets.append((mt, path, lang, lang_name, n))
    targets.sort(key=lambda x: -x[0])
    pending = len(targets)
    if pending > limit:
        print(f'보정 대상 {pending}개 → 이번 실행에서는 최신 {limit}개만 처리 (예산)')
    else:
        print(f'보정 대상 {pending}개')

    total = 0
    for mt, path, lang, lang_name, n in targets:
        if total >= limit:
            print(f'  예산 {limit}개 도달 — 나머지 {pending - total}개는 다음 실행으로 미룸')
            break
        if time.time() - t_start > deadline:
            print(f'  데드라인 {deadline}s 도달 — 나머지 {pending - total}개는 다음 실행으로 미룸')
            break
        d = json.load(open(path, encoding='utf-8'))
        slug = d['slug']
        print(f'[{slug} / {lang}] {n}자 보정')
        try:
            new_body = fix_length(lang, lang_name, d['body'], n)
        except Exception as e:
            print(f'  !! {slug}/{lang} 보정 중단: {type(e).__name__}: {e}')
            continue
        d['body'] = new_body
        json.dump(d, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        total += 1
        time.sleep(0.3)
    print(f'보정 완료: {total}개 파일 처리 (대상 {pending}개)')


if __name__ == '__main__':
    main()
