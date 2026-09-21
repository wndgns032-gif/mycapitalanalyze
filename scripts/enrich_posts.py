#!/usr/bin/env python3
"""본문 보강(enrich) 패스 — StoryScope 편집 체크리스트 적용.

근거 자료: https://lazyowen.com/guides/storyscope-ai-fiction
(COLM 2026 논문 「StoryScope: Investigating idiosyncrasies in AI fiction」 한국어 정리)

핵심: AI 글은 '문체'가 아니라 '이야기를 짜는 방식'에서 걸린다.
      → 문장을 다듬는 대신 구조 습관(빼기 4 / 넣기 4)을 고친다.
      대상은 소설이므로 수치가 아니라 '원리'만 금융 블로그에 맞게 가져온다.

빼기: ①화자가 주제·교훈을 직접 말함 ②감각·분위기 수사 과다 ③인과가 한 줄 ④결말이 너무 말끔
넣기: ⑤실명·구체 지표 ⑥독자에게 직접 말 걸기 ⑦시간 순서 흔들기 ⑧판단을 한쪽으로 정리하지 않기

보강된 글은 프론트매터에 `enriched: <날짜>` 가 붙고, 해당 slug의 기존 번역 파일이 삭제된다
→ 다음 translate.py 실행에서 4개 언어가 새 본문으로 다시 번역된다.

사용법: python scripts/enrich_posts.py [--limit 3] [--deadline 600] [--slug <slug>]
"""
import json
import os
import re
import sys
import time
import glob
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import llm  # noqa: E402

POSTS_DIR = os.path.join(BASE, 'content', 'posts')
TRANS_DIR = os.path.join(BASE, 'content', 'translations')
CHAR_MIN = CONFIG['char_min']
CHAR_MAX = CONFIG['char_max']
LANGS = CONFIG.get('translate_languages') or CONFIG['languages']
MAX_RETRY = 2

SYSTEM = (
    'You are a senior editor at a macro-finance news blog. '
    'You rewrite articles so they read like they were written by an experienced human analyst. '
    'You return valid JSON only.'
)

TEMPLATE = """Rewrite the English article body below using the StoryScope editing checklist.

Background: the COLM 2026 paper "StoryScope: Investigating idiosyncrasies in AI fiction" found that
AI text is detected by HOW THE PIECE IS STRUCTURED, not by wording. The paper studied fiction, so
apply the PRINCIPLES below (not fiction devices, not the paper's numbers).

REMOVE (AI habits):
1. Any sentence where the narrator states the theme or lesson directly ("The takeaway is...",
   "This is a useful signal that...", "Investors should...", "Overall, this shows...").
   Replace it with a concrete fact, date, figure, or what an actual named body said or did.
2. Clusters of atmospheric flourish or grand metaphors. Keep at most one; prefer plain prose.
3. A single straight line of cause-and-effect. Add ONE side branch that the article already implies:
   a second data point, a dissenting view, or a historical parallel.
4. An ending that closes too neatly. End with one open question or unresolved tension instead.

ADD (human habits):
5. Name real institutions, people, reports, dates and field names explicitly instead of vague
   references ("the regulator" -> the specific body named in the article).
6. Address the reader directly once (e.g. "If you report under UK EMIR, the practical question is...").
7. Break strict chronological order: move ONE important fact later instead of front-loading every
   key point in the opening paragraphs.
8. Leave ONE genuinely ambiguous judgment unresolved instead of settling everything on one side.

HARD RULES:
- Do NOT invent facts, numbers, dates, names, or quotes. Every proper noun and figure must already
  be in the source article. If a specific name is not there, keep the generic term the article uses.
- Keep every existing fact, figure, date and proper noun.
- Keep the Markdown structure: the "## Key Facts", "## Analysis" and "## FAQ" sections with their
  "###" questions. Do not add or remove FAQ questions.
- Length MUST be between {cmin} and {cmax} characters including spaces (currently {n}).
- Professional financial news prose. No emoji, no marketing voice, no bullet-point lists inside prose.

Return JSON: {{"body": "<full rewritten markdown>", "changes": ["<short note>", "<short note>", "..."]}}
"changes" = 3 to 6 short notes describing what you removed and added.

ARTICLE BODY:
{body}"""


def parse_md(path):
    raw = open(path, encoding='utf-8').read()
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', raw, re.S)
    if not m:
        raise ValueError('프론트매터 없음: %s' % path)
    return m.group(1), m.group(2).strip(), raw


def call_llm(body, n):
    user = TEMPLATE.format(cmin=CHAR_MIN, cmax=CHAR_MAX, n=n, body=body)
    for attempt in range(1, MAX_RETRY + 1):
        try:
            content, _prov = llm.chat(
                [{'role': 'system', 'content': SYSTEM},
                 {'role': 'user', 'content': user}],
                max_tokens=8192, temperature=0.6,
                response_format={'type': 'json_object'}, timeout=240,
                purpose='write')
        except Exception as e:
            print('    [%d] 호출 실패: %s' % (attempt, type(e).__name__))
            continue
        obj = None
        try:
            obj = json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r'\{.*"body"\s*:\s*"(?:[^"\\]|\\.)*"\s*[\},]', content, re.S)
            if m:
                try:
                    obj = json.loads(m.group(0).rstrip(',') + '}')
                except json.JSONDecodeError:
                    obj = None
        if not obj or not (obj.get('body') or '').strip():
            print('    [%d] 응답 파싱 실패 (%d자)' % (attempt, len(content or '')))
            continue
        new_body = obj['body'].strip()
        nn = len(new_body)
        if CHAR_MIN <= nn <= CHAR_MAX:
            return new_body, obj.get('changes') or []
        print('    [%d] 길이 %d자 (범위 밖)' % (attempt, nn))
        user = user + ('\n\nYour rewrite was %d characters. It must be between %d and %d.'
                       % (nn, CHAR_MIN, CHAR_MAX))
    return None, []


def drop_translations(slug):
    """보강된 글의 기존 번역을 삭제 → 다음 translate.py 실행에서 새 본문으로 재번역."""
    removed = []
    for lang in LANGS:
        p = os.path.join(TRANS_DIR, lang, slug + '.json')
        if os.path.exists(p):
            os.remove(p)
            removed.append(lang)
    return removed


def main():
    args = sys.argv[1:]
    only_slug = args[args.index('--slug') + 1] if '--slug' in args else None

    def _opt(name, default):
        if name in args:
            try:
                return int(args[args.index(name) + 1])
            except (ValueError, IndexError):
                return default
        return default

    limit = _opt('--limit', int(os.environ.get('ENRICH_LIMIT', '3')))
    deadline = _opt('--deadline', int(os.environ.get('ENRICH_DEADLINE', '600')))
    t0 = time.time()
    today = datetime.now().strftime('%Y-%m-%d')

    targets = []
    for path in glob.glob(os.path.join(POSTS_DIR, '*.md')):
        slug = os.path.splitext(os.path.basename(path))[0]
        if only_slug and slug != only_slug:
            continue
        fm_text, body, raw = parse_md(path)
        if re.search(r'^enriched:', fm_text, re.M):
            continue  # 이미 보강됨
        targets.append((os.path.getmtime(path), path, slug, body, raw, fm_text))
    targets.sort(key=lambda x: -x[0])  # 최신 글 우선
    print('보강 대상 %d건 (이번 실행 최대 %d건)' % (len(targets), limit))

    done = 0
    for mt, path, slug, body, raw, fm_text in targets:
        if done >= limit:
            print('  예산 도달 — 나머지 %d건은 다음 실행으로' % (len(targets) - done))
            break
        if time.time() - t0 > deadline:
            print('  데드라인 도달 — 나머지 %d건은 다음 실행으로' % (len(targets) - done))
            break
        print('[%s] %d자 보강 시작' % (slug, len(body)))
        new_body, changes = call_llm(body, len(body))
        if not new_body:
            print('  !! %s 보강 실패 — 원본 유지' % slug)
            continue
        fm_new = fm_text.rstrip() + ('\nenriched: "%s"' % today)
        out = '---\n%s\n---\n\n%s\n' % (fm_new, new_body)
        tmp = path + '.tmp'
        open(tmp, 'w', encoding='utf-8').write(out)
        os.replace(tmp, path)
        removed = drop_translations(slug)
        done += 1
        print('  OK %d자 (기존 %d자) | 번역 재생성 예정: %s' % (len(new_body), len(body), ','.join(removed) or '-'))
        for c in changes[:6]:
            print('     - %s' % str(c)[:120])

    print('보강 완료: %d건 (대상 %d건)' % (done, len(targets)))


if __name__ == '__main__':
    main()
