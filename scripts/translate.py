#!/usr/bin/env python3
"""
DeepSeek V4 Flash 번역 파이프라인.
content/posts/*.md (영어 원본) → 12개 언어 번역 → content/translations/{lang}/{slug}.json

사용법:
  python scripts/translate.py                 # 전체 번역
  python scripts/translate.py fed-rate-outlook-2026   # 특정 slug만
  python scripts/translate.py --lang ko       # 특정 언어만
"""
import json, os, re, sys, time, urllib.request, urllib.error, glob, builtins, threading
from concurrent.futures import ThreadPoolExecutor

# 워커 스레드가 동시에 출력하면 로그가 뒤섞이므로 모듈 전역 print를 잠금 버전으로 대체한다.
# (이 모듈 안의 모든 print()가 자동으로 이 함수를 쓴다)
# RLock이어야 한다: 누군가 이미 락을 쥔 상태에서 print()를 호출해도 스스로 교착되지 않는다.
# (Lock으로 쓰면 첫 작업이 끝나는 순간 모든 워커가 영구히 멈춘다 — 실제로 한 번 겪은 사고다)
_LOG_LOCK = threading.RLock()


def print(*a, **k):  # noqa: A001
    with _LOG_LOCK:
        builtins.print(*a, **k)


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))

# LLM 호출은 scripts/llm.py 에 위임한다 (Flash 전용 + 제공자 자동 폴백)
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import llm  # noqa: E402
LANGS = CONFIG.get('translate_languages') or CONFIG['languages']
CHAR_MIN = CONFIG['char_min']
CHAR_MAX = CONFIG['char_max']

POSTS_DIR = os.path.join(BASE, 'content', 'posts')
TRANS_DIR = os.path.join(BASE, 'content', 'translations')

MAX_RETRY = 5

# 이 접두사로 시작하는 글은 번역하지 않는다 (예: 저작권 리스크가 있는 외부 칼럼 재가공본)
EXCLUDE_PREFIXES = tuple(CONFIG.get('translate_exclude_prefixes') or [])
# 한 번의 실행에서 처리할 최대 번역 수 (0 = 무제한). CI 폭주·비용 폭발 방지.
MAX_PER_RUN = int(CONFIG.get('max_translations_per_run') or 0)
# 동시에 돌릴 워커 수
WORKERS = max(1, int(CONFIG.get('translate_workers') or 1))

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

# 번역하지 말고 그대로 둬도 되는 로마자 토큰 (기관 약칭/지표명)
LATIN_ALLOW = {'fed', 'fred', 'cboe', 'cpi', 'ppi', 'gdp', 'm1', 'm2', 'kc', 'prs', 'irs',
               'imf', 'oecd', 'ecb', 'boj', 'bis', 'bls', 'bea', 'ai', 'etf', 'us', 'uk', 'eu'}

LATIN_WORD_RE = re.compile(r'\b[A-Za-z]{3,}\b')


def quality_gate(lang, title, body):
    """(ok, hint) 반환. 통과하면 (True, None)."""
    # 1) 한국어에 중국어 한자 잔재가 남으면 실패
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
    # 2) CJK 언어에 영어 단어가 그대로 남으면 실패 (기관명 오역의 주원인)
    if lang in ('ko', 'zh', 'ja'):
        leftovers = [w for w in LATIN_WORD_RE.findall(body) if w.lower() not in LATIN_ALLOW]
        if len(leftovers) > 6:
            sample = ', '.join(sorted(set(leftovers))[:12])
            return False, (
                f'Your {lang} text still contains {len(leftovers)} untranslated English words '
                f'(e.g. {sample}). Translate EVERY English term into natural {lang} '
                f'financial terminology — only established acronyms (Fed, FRED, CPI, GDP, Cboe) '
                f'may stay in Latin script. Also check proper nouns: "Kansas City Fed" is '
                f'캔자스시티 연준 / 堪萨斯城联储, NOT a Korean bank.'
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
    """Flash 모델로 호출. (content, provider) 중 content만 반환."""
    content, _provider = llm.chat(
        messages,
        max_tokens=16384,
        temperature=0.5,
        response_format={'type': 'json_object'} if json_mode else None,
        timeout=180,
        retries=max(1, net_retries - 1),
    )
    return content

# ---------- 번역 ----------
def translate_post(slug, lang, lang_name, title, desc, body):
    system = (
        'You are a professional financial translator AND editor for a macro-economics blog. '
        'You write in the register of a national financial newspaper '
        '(e.g. Hankyoreh/Chosun Biz for Korean, Nikkei for Japanese, FT/Le Monde style for European). '
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

TERMINOLOGY RULES (critical):
- Translate EVERY term into natural {lang_name} financial terminology. Do not leave English words in the text.
- Only these may stay in Latin script: Fed, FRED, CPI, GDP, Cboe, ETF, IMF, ECB, BIS, BLS, OECD.
- Institution names must be translated by MEANING, never by sound:
  "Kansas City Fed" = 캔자스시티 연준 (ko) / 堪萨斯城联储 (zh) / カンザスシティ連邦準備銀行 (ja) — it is NOT a Korean "Korea City Bank".
  "Federal Reserve" = 연방준비제도(Fed) / 美联储 / FRB.
  "Treasury" (securities) = 미 국채 / 美国国债 / 米国債 — not "재무부" when it means bonds.
  "financial market" = 금융시장 (ko) — never "재무 시장".
  "monetary policy" = 통화정책, "inflation" = 인플레이션(물가 상승), "yield curve" = 수익률 곡선,
  "policy rate" = 정책금리, "skew" = 스큐(비대칭도), "subprime" = 서브프라임(저신용).
- Keep every number, date and unit exactly as in the source. Never invent data.

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
            print(f'    [{attempt}] JSON 파싱 실패, 재시도... (응답 {len(content or "")}자)')
            print('    응답 앞부분:', repr((content or '')[:160]))
            print('    응답 뒷부분:', repr((content or '')[-160:]))
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
    # 재시도 소진 — 본문이 사실상 비어 있으면 실패로 간주해 파일을 쓰지 않는다
    if char_count(last.get('body', '')) < 500:
        print(f'    {lang}: 번역 실패 (본문 {char_count(last.get("body", ""))}자) — 다음 실행에 재시도')
        return None
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

    tasks = []
    for path in sorted(glob.glob(os.path.join(POSTS_DIR, '*.md'))):
        slug = os.path.splitext(os.path.basename(path))[0]
        if only_slug and slug != only_slug:
            continue
        if any(slug.startswith(p) for p in EXCLUDE_PREFIXES):
            print(f'[{slug}] 제외 규칙 해당 — 번역 건너뜀')
            continue
        fm, body = parse_md(path)
        for lang, lang_name in LANGS.items():
            if only_lang and lang != only_lang:
                continue
            out_path = os.path.join(TRANS_DIR, lang, slug + '.json')
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            if os.path.exists(out_path):
                continue
            tasks.append((slug, lang, lang_name, fm['title'], fm['description'], body, out_path))

    if not tasks:
        print('번역할 대상 없음 (모두 최신)')
        return

    if MAX_PER_RUN and len(tasks) > MAX_PER_RUN:
        print(f'대상 {len(tasks)}건 — 이번 실행은 최대 {MAX_PER_RUN}건만 처리한다')
        tasks = tasks[:MAX_PER_RUN]

    total = len(tasks)
    print(f'번역 대상 {total}건 / 동시 워커 {WORKERS}개')
    counter = {'done': 0}

    def work(task):
        slug, lang, lang_name, title, desc, body, out_path = task
        result = translate_post(slug, lang, lang_name, title, desc, body)
        if result:
            tmp = out_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            os.replace(tmp, out_path)   # 원자적 교체 — 중간에 죽어도 반쪽 파일이 남지 않는다
        counter['done'] += 1
        print(f'  [{counter["done"]}/{total}] {lang} {slug} '
              f'{"OK" if result else "실패"}')
        return result

    fails = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for task, result in zip(tasks, ex.map(work, tasks)):
            if not result:
                fails.append(f'{task[1]}/{task[0]}')

    print(f'\n번역 완료: {total - len(fails)}/{total} 성공')
    if fails:
        print('실패(다음 실행에서 재시도): ' + ', '.join(fails[:30]))


if __name__ == '__main__':
    main()
