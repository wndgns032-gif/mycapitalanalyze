#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
App Radar — 신규/출시예정 앱·게임 자동 발행기 (mycapitalanalyze.com)

Apple App Store 공개 피드에서 신규 앱·게임과 사전예약(출시 예정) 앱을 수집해
영문 포스트를 content/posts/ 에 직접 생성한다. 이후 기존 파이프라인이 그대로 처리한다.

    python scripts/crawler.py 를 쓰지 않는 이유:
    App Radar 는 원문 기사 재가공이 아니라 "앱 소개"라 프롬프트·구조가 다르다.
    rewrite.py 를 거치지 않고 여기서 완성본 md 를 만들면 rewrite.py 수정이 불필요하다.

데이터 소스 (전부 무료 · 키 불필요 · 실측 200 OK):
    https://itunes.apple.com/{cc}/rss/newapplications/limit=100/json          신규 앱 전체
    https://itunes.apple.com/{cc}/rss/newapplications/limit=100/genre=6014/json   신규 게임
    https://itunes.apple.com/lookup?id={id}&country={cc}                      상세 설명·평점·출시일
    https://itunes.apple.com/{cc}/rss/customerreviews/page=1/id={id}/json     리뷰 발췌 (best effort)

주의: 구글플레이(play.google.com)는 중국 IP에서 차단된다. `--play` 를 주면 시도만 하고
실패하면 조용히 건너뛴다. GitHub Actions(해외 네트워크)에서 돌리면 플레이스토어도 수집된다.

LLM 호출은 scripts/llm.py 에 위임 (Flash 전용 + 제공자 자동 폴백). 본 스크립트에 키 없음.

사용법:
    python scripts/app_radar.py                    # 기본 3개 수집·발행
    python scripts/app_radar.py --limit 1          # 1개만
    python scripts/app_radar.py --dry              # 후보/자료 확인만 (LLM 호출·파일 쓰기 없음)
    python scripts/app_radar.py --posts-dir DIR --assets-dir DIR   # 출력 경로 지정 (테스트용)
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(_HERE)

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/122.0 Safari/537.36')

STOREFRONT = os.environ.get('APPSTORE_CC', 'us')

FEEDS = [
    'https://itunes.apple.com/%s/rss/newapplications/limit=100/json' % STOREFRONT,
    'https://itunes.apple.com/%s/rss/newapplications/limit=100/genre=6014/json' % STOREFRONT,
    'https://itunes.apple.com/%s/rss/newfreeapplications/limit=100/json' % STOREFRONT,
]


# ---------------------------------------------------------------- http utils

def get_json(url, timeout=25):
    req = urllib.request.Request(
        url, headers={'User-Agent': UA, 'Accept': 'application/json, text/plain, */*'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def get_bytes(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- 디스크 상태

def load_seen(path):
    try:
        return set(json.load(open(path, encoding='utf-8')))
    except Exception:
        return set()


def save_seen(path, ids):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(sorted(ids)[-2000:], open(path, 'w', encoding='utf-8'))
    except Exception:
        pass


def published_urls(posts_dir):
    """이미 발행된 포스트의 sourceUrl 목록 (중복 방지 1차 가드)."""
    out = set()
    if not os.path.isdir(posts_dir):
        return out
    for fn in os.listdir(posts_dir):
        if not fn.endswith('.md'):
            continue
        try:
            head = open(os.path.join(posts_dir, fn), encoding='utf-8').read(4000)
        except Exception:
            continue
        m = re.search(r'^sourceUrl:\s*"?([^"\n]+)"?\s*$', head, re.M)
        if m:
            out.add(m.group(1).strip())
    return out


def existing_slugs(posts_dir):
    out = set()
    if not os.path.isdir(posts_dir):
        return out
    for fn in os.listdir(posts_dir):
        if fn.endswith('.md'):
            out.add(fn[:-3])
    return out


# ---------------------------------------------------------------- 피드 파싱

def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:
        return None


def parse_feed(data):
    """RSS JSON -> [{app_id, name, artist, category, released, url, icon}]"""
    out = []
    entries = (data or {}).get('feed', {}).get('entry') or []
    if isinstance(entries, dict):
        entries = [entries]
    for e in entries:
        try:
            app_id = e['id']['attributes']['im:id']
            name = e['im:name']['label']
        except Exception:
            continue
        imgs = e.get('im:image') or []
        icon = imgs[-1]['label'] if imgs else ''
        out.append({
            'app_id': str(app_id),
            'name': name,
            'artist': (e.get('im:artist') or {}).get('label', ''),
            'category': ((e.get('category') or {}).get('attributes') or {}).get('label', ''),
            'released': parse_iso((e.get('im:releaseDate') or {}).get('label')),
            'url': ((e.get('link') or {}).get('attributes') or {}).get('href', '')
                   or 'https://apps.apple.com/us/app/id%s' % app_id,
            'icon': icon,
        })
    return out


def lookup(app_id):
    """상세 정보 (설명·평점·출시일·가격). 실패 시 빈 dict."""
    try:
        d = get_json('https://itunes.apple.com/lookup?id=%s&country=%s' % (app_id, STOREFRONT))
        res = (d or {}).get('results') or []
        return res[0] if res else {}
    except Exception as ex:
        print('   lookup 실패(%s): %s' % (app_id, str(ex)[:80]))
        return {}


def reviews(app_id, limit=3):
    """고객 리뷰 발췌 (best effort — 없으면 빈 리스트)."""
    try:
        d = get_json('https://itunes.apple.com/%s/rss/customerreviews/page=1/id=%s/'
                     'sortby=mosthelpful/json' % (STOREFRONT, app_id))
        entries = (d or {}).get('feed', {}).get('entry') or []
        if isinstance(entries, dict):
            entries = [entries]
        out = []
        for e in entries[:limit + 1]:
            try:
                title = e['title']['label']
                body = e['content']['label']
                rating = ((e.get('im:rating') or {}).get('label'))
            except Exception:
                continue
            # 첫 entry는 앱 자체 정보인 경우가 있어 제목/본문이 같으면 제외
            if title and body and title != body:
                out.append({'title': title, 'body': body[:400], 'rating': rating})
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def fetch_play_candidates(limit=20):
    """구글플레이 신규/사전예약 후보. 중국 IP에서는 실패 → 빈 리스트."""
    try:
        html = get_bytes('https://play.google.com/store/apps?hl=en&gl=US').decode('utf-8', 'replace')
        ids = re.findall(r'/store/apps/details\?id=([A-Za-z0-9_.-]+)', html)
        seen, out = set(), []
        for pkg in ids:
            if pkg in seen:
                continue
            seen.add(pkg)
            out.append({'play_id': pkg})
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


# ---------------------------------------------------------------- 후보 선정

def collect_candidates(use_play=False):
    pool = {}
    for url in FEEDS:
        try:
            for app in parse_feed(get_json(url)):
                pool.setdefault(app['app_id'], app)
        except Exception as ex:
            print('  피드 실패: %s (%s)' % (url.split('/')[-1], str(ex)[:60]))
    if use_play:
        got = fetch_play_candidates()
        if got:
            print('  구글플레이 후보 %d개 (상세 조회는 플레이 정책상 생략)' % len(got))
    return list(pool.values())


def pick(cands, seen_ids, pub_urls, target, today):
    """하루치 선정: 출시예정 우선 1건 + 신규 앱/게임 골고루."""
    fresh = [c for c in cands
             if c['app_id'] not in seen_ids
             and c['url'] not in pub_urls
             and not any(c['url'].split('?')[0].rstrip('/').split('/')[-1].lstrip('id')
                         and c['url'] in u for u in pub_urls)]
    # URL 중복은 apps.apple.com/us/app/xxx/idNNN 형태라 pub_urls 비교만으로 충분하지만
    # id 기반 추가 검사로 안전성을 높인다.
    fresh = [c for c in fresh
             if ('/id%s' % c['app_id']) not in ' '.join(pub_urls)
             and c['app_id'] not in seen_ids]

    upcoming = [c for c in fresh if c['released'] and c['released'].replace(tzinfo=None) > today]
    released = [c for c in fresh if c not in upcoming]
    games = [c for c in released if (c['category'] or '').lower().startswith('game')]
    apps = [c for c in released if c not in games]

    # 최신순 정렬
    for lst in (upcoming, games, apps):
        lst.sort(key=lambda c: c['released'] or datetime.datetime.min, reverse=True)

    picked, queues = [], [upcoming, games, apps, released]
    idx = 0
    while len(picked) < target and idx < 40:
        q = queues[idx % 3]
        if q:
            item = q.pop(0)
            if item not in picked:
                picked.append(item)
        idx += 1
    if len(picked) < target:
        for c in released:
            if c not in picked:
                picked.append(c)
            if len(picked) >= target:
                break
    return picked[:target]


# ---------------------------------------------------------------- 원문 자료

def clip(s, n):
    s = re.sub(r'\s+', ' ', (s or '')).strip()
    return s if len(s) <= n else s[:n] + '...'


def material(app, detail, revs, today):
    rel = app['released']
    if rel:
        upcoming = rel.replace(tzinfo=None) > today
        rel_txt = rel.strftime('%Y-%m-%d')
    else:
        upcoming, rel_txt = False, 'unknown'

    lines = [
        'APP NAME: %s' % detail.get('trackName') or app['name'],
        'DEVELOPER: %s' % (detail.get('artistName') or app['artist']),
        'STORE CATEGORY: %s' % (detail.get('primaryGenreName') or app['category']),
        'GENRES: %s' % ', '.join(detail.get('genres') or []),
        'PRICE: %s' % (detail.get('formattedPrice') or 'not disclosed'),
        'VERSION: %s' % (detail.get('version') or 'n/a'),
        'STATUS: %s' % ('UPCOMING / PRE-ORDER (release date: %s)' % rel_txt if upcoming
                        else 'RELEASED (%s)' % rel_txt),
        'LISTING URL: %s' % (detail.get('trackViewUrl') or app['url']),
    ]
    rating = detail.get('averageUserRating')
    cnt = detail.get('userRatingCount')
    if rating and cnt:
        lines.append('RATING: %.1f (%s ratings)' % (float(rating), cnt))
    lines.append('')
    lines.append('OFFICIAL DESCRIPTION:')
    lines.append(clip(detail.get('description') or '', 2500) or '(none provided)')
    notes = detail.get('releaseNotes')
    if notes:
        lines.append('')
        lines.append("WHAT'S NEW: %s" % clip(notes, 500))
    if revs:
        lines.append('')
        lines.append('EARLY USER FEEDBACK (may be sparse; quotes from the store listing):')
        for r in revs:
            lines.append('- "%s" (%s/5): %s' % (clip(r['title'], 90), r.get('rating') or '?',
                                                clip(r['body'], 220)))
    return '\n'.join(lines), upcoming


# ---------------------------------------------------------------- LLM 프롬프트

SYSTEM_APP = (
    "You write practical, search-friendly app and game introductions for an international "
    "tech audience. Your English is plain, concrete and free of hype. You never invent "
    "features, prices or dates: everything you state must come from the source material."
)


def user_prompt(app, mat, upcoming, cmin, cmax):
    if upcoming:
        structure = (
            '"## What We Know So Far", "## Expected Release Date", "## Key Features", '
            '"## Who It Is For", "## Pricing", "## FAQ"'
        )
        angle = ("The app is NOT released yet. Frame it as an upcoming launch and be explicit "
                 "that details may change before release.")
    else:
        structure = (
            '"## What {APP} Is", "## Key Features", "## Who It Is For", '
            '"## Pricing and Availability", "## Early Impressions", "## FAQ"'
        ).replace('{APP}', app['name'])
        angle = ("Introduce it as a freshly released title. If there is little user feedback yet, "
                 "say so plainly instead of pretending there is consensus.")
    structure = structure.replace('{APP}', app['name'])
    return (
        "Write one English article about the app below.\n\n"
        "SOURCE MATERIAL (facts come only from here):\n"
        "----------------\n%s\n----------------\n\n"
        "RULES\n"
        "1. Output ONLY JSON: {\"title\":..., \"description\":..., \"category\":..., \"body\":...}\n"
        "2. title: include the exact app name; add intent words (Release Date / Features / Price / "
        "Review) only if they fit naturally; max 70 characters.\n"
        "3. description: one sentence, 140-160 characters, useful in a search result.\n"
        "4. category: always \"Apps & Games\".\n"
        "5. body: Markdown, %d-%d characters. Start with 1-2 short paragraphs (no heading above them).\n"
        "6. Use exactly these \"##\" sections, in this order: %s.\n"
        "7. The FAQ must hold exactly 3 questions as \"###\" headings, each answered in 1-3 sentences.\n"
        "8. Never use \"#\" H1 and never insert images or markdown image syntax.\n"
        "9. Put the app name in the first sentence. Include the official store listing URL once as a "
        "markdown link labelled \"App Store\".\n"
        "10. Do NOT claim you tested or played it. Do not invent phone specs, download counts or\n"
        "    sales figures. If the material lacks something, write one honest sentence about the gap.\n"
        "11. %s\n"
        "12. No em-dash lists, no bullet-point walls, no marketing superlatives like \"revolutionary\".\n"
    ) % (mat, cmin, cmax, structure, angle)


def extract_json(text):
    t = (text or '').strip()
    if t.startswith('```'):
        t = re.sub(r'^```[a-zA-Z]*\s*', '', t)
        t = re.sub(r'```\s*$', '', t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find('{')
    depth = 0
    for i in range(start, len(t)):
        if t[i] == '{':
            depth += 1
        elif t[i] == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    break
    return {}


def slugify(title, taken, fallback_hash):
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", '-', s).strip('-')
    words = [w for w in s.split('-') if w][:8]
    slug = '-'.join(words) or 'app'
    if len(slug) < 6:
        slug = 'app-' + slug
    if slug not in taken:
        return slug
    return slug + '-' + fallback_hash


# ---------------------------------------------------------------- 메인

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='생성할 글 수 (기본 config 또는 3)')
    ap.add_argument('--dry', action='store_true', help='LLM 호출·파일 쓰기 없이 후보와 자료만 확인')
    ap.add_argument('--play', action='store_true', help='구글플레이 소스도 시도(해외 네트워크 필요)')
    ap.add_argument('--posts-dir', default=None)
    ap.add_argument('--assets-dir', default=None)
    ap.add_argument('--seen-file', default=None)
    ap.add_argument('--base', default=BASE)
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    posts_dir = args.posts_dir or os.path.join(base, 'content', 'posts')
    assets_dir = args.assets_dir or os.path.join(base, 'assets', 'img', 'apps')
    seen_file = args.seen_file or os.path.join(base, 'content', 'app_radar_seen.json')

    cfg = {}
    try:
        cfg = json.load(open(os.path.join(base, 'config.json'), encoding='utf-8'))
    except Exception:
        pass
    target = args.limit or int(cfg.get('apps_per_day', 3))
    # 길이 기준: config.json 의 app_char_min/max 가 우선, 없으면 사이트 표준(char_min/max)을 따른다.
    cmin = int(cfg.get('app_char_min', cfg.get('char_min', 3000)))
    cmax = int(cfg.get('app_char_max', cfg.get('char_max', 5000)))

    sys.path.insert(0, os.path.join(base, 'scripts'))
    try:
        import llm  # noqa: E402
    except Exception:
        print('scripts/llm.py 를 불러올 수 없습니다. (python scripts/app_radar.py 로 실행하세요)')
        return 1

    today = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    seen = load_seen(seen_file)
    pub_urls = published_urls(posts_dir)
    taken = existing_slugs(posts_dir)

    print('App Radar — 대상 %d건 / 스토어=%s' % (target, STOREFRONT))
    cands = collect_candidates(use_play=args.play)
    print('후보 풀: %d개 (신규+사전예약)' % len(cands))
    picked = pick(cands, seen, pub_urls, target, today)
    print('선정: %d개\n' % len(picked))

    os.makedirs(posts_dir, exist_ok=True)
    done = 0
    for app in picked:
        print('[%s] %s — %s' % (app['app_id'], app['name'], app['category'] or 'App'))
        detail = lookup(app['app_id'])
        if not detail:
            print('   상세 조회 실패 → 건너뜀')
            continue
        revs = reviews(app['app_id'])
        mat, upcoming = material(app, detail, revs, today)
        if args.dry:
            print('   출시예정=%s / 리뷰 %d건 / 자료 %d자' % (upcoming, len(revs), len(mat)))
            print('   URL: %s' % (detail.get('trackViewUrl') or app['url']))
            print('   --- 자료 미리보기 ---')
            print('\n'.join('   ' + l for l in mat.split('\n')[:8]))
            continue

        art = detail.get('artworkUrl512') or app['icon']
        img_md = ''
        if art:
            slug_hint = hashlib.sha1(app['app_id'].encode()).hexdigest()[:8]
            fn = 'app-%s.jpg' % slug_hint
            dest = os.path.join(assets_dir, fn)
            try:
                if not os.path.exists(dest):
                    os.makedirs(assets_dir, exist_ok=True)
                    data = get_bytes(art)
                    open(dest, 'wb').write(data)
                img_md = '![%s](/assets/img/apps/%s)\n\n' % (app['name'].replace('[', ''), fn)
            except Exception as ex:
                print('   이미지 저장 실패: %s' % str(ex)[:60])

        body_txt = None
        obj = {}
        for attempt in range(1, 4):
            got, provider = llm.chat(
                [{'role': 'system', 'content': SYSTEM_APP},
                 {'role': 'user', 'content': user_prompt(app, mat, upcoming, cmin, cmax)}],
                purpose='write')
            obj = extract_json(got)
            body_txt = (obj.get('body') or '').strip()
            n = len(body_txt)
            print('   [%d] %s 응답 %d자' % (attempt, provider, n))
            if cmin <= n <= cmax and obj.get('title'):
                break
            hint = ('Too short (%d). Expand with more concrete detail from the material.' % n
                    if n < cmin else 'Too long (%d). Trim redundant sentences.' % n)
            print('      재시도: %s' % hint)
            time.sleep(0.4)
        if not obj.get('title') or not body_txt:
            print('   생성 실패 → 다음 실행에서 다시 시도됩니다')
            continue

        title = re.sub(r'"', "'", obj.get('title') or app['name'])
        desc = re.sub(r'"', "'", clip(obj.get('description') or '', 200))
        slug = slugify(title, taken, hashlib.sha1(app['app_id'].encode()).hexdigest()[:6])
        taken.add(slug)
        url = detail.get('trackViewUrl') or app['url']
        fm = ('---\n'
              'slug: %s\n'
              'title: "%s"\n'
              'description: "%s"\n'
              'category: "Apps & Games"\n'
              'date: "%s"\n'
              'sourceName: "App Store"\n'
              'sourceUrl: "%s"\n'
              '---\n\n') % (slug, title, desc,
                            datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'), url)
        open(os.path.join(posts_dir, slug + '.md'), 'w', encoding='utf-8').write(
            fm + img_md + body_txt + '\n')
        seen.add(app['app_id'])
        save_seen(seen_file, seen)
        print('   발행: %s (%d자)' % (slug, len(body_txt)))
        done += 1
        time.sleep(0.3)

    print('\nApp Radar 완료: %d건' % done)
    return 0


if __name__ == '__main__':
    sys.exit(main())
