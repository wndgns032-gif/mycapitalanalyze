#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
App Radar v2 — 언어권별 스토어에서 신작/출시예정 앱·게임 자동 발행

로이 지시(2026-09-30) 반영:
  * **각 언어권 스토어에서 직접 수집한다.** 영어권 글을 번역해 다른 언어로 올리지 않는다.
    → 한국 스토어 신작은 한국어로, 일본 스토어 신작은 일본어로 쓴다. (번역 단계 없음)
  * Apple App Store(스토어프론트 cc) + 구글플레이(hl/gl) 를 언어권별로 함께 훑는다.
  * 결과는 content/game/{lang}/ 에 저장 → build.py 가 /game/, /{lang}/game/ 로 빌드한다.

데이터 소스 (전부 무료 · 키 불필요):
  Apple  https://itunes.apple.com/{cc}/rss/newapplications/limit=100/json
         https://itunes.apple.com/{cc}/rss/newapplications/limit=100/genre=6014/json   (게임)
         https://itunes.apple.com/{cc}/rss/newfreeapplications/limit=100/json
         https://itunes.apple.com/lookup?id={id}&country={cc}              상세·설명·평점
         https://itunes.apple.com/{cc}/rss/customerreviews/page=1/id={id}/json
  Play   https://play.google.com/store/apps/collection/topselling_new_free?hl={hl}&gl={gl}
         https://play.google.com/store/apps/collection/topselling_new_free_game?hl=..&gl=..
         https://play.google.com/store/apps/details?id={pkg}&hl={hl}&gl={gl}  상세·설명·평점

선정 규칙: 출시예정(사전예약/사전등록) 우선 → 게임 → 앱. 하루 3건(기본)을
서로 다른 언어권에서 1건씩 뽑아 모든 언어가 고르게 채워지게 한다(로테이션 커서).

LLM 호출은 scripts/llm.py 에 위임 (Flash 전용 + 제공자 자동 폴백). 본 스크립트에 키 없음.

사용법:
    python scripts/app_radar.py                     # 기본 3건 (서로 다른 언어 3개)
    python scripts/app_radar.py --limit 6           # 6건
    python scripts/app_radar.py --locale ko         # 한국어권만
    python scripts/app_radar.py --dry               # 후보/자료만 확인 (LLM 호출·쓰기 없음)
    python scripts/app_radar.py --base DIR          # 출력 기준 디렉터리 (테스트용)
"""
import argparse
import datetime
import hashlib
import html as htmlmod
import json
import os
import re
import sys
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(_HERE)

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/122.0 Safari/537.36')

# (언어코드, Apple 스토어프론트, Play hl, Play gl, 프롬프트용 언어명)
LOCALES = [
    ('en', 'us', 'en',    'us', 'English'),
    ('ko', 'kr', 'ko',    'kr', 'Korean'),
    ('ja', 'jp', 'ja',    'jp', 'Japanese'),
    ('zh', 'cn', 'zh-CN', 'tw', 'Simplified Chinese'),
    ('es', 'es', 'es',    'es', 'Spanish'),
    ('de', 'de', 'de',    'de', 'German'),
    ('fr', 'fr', 'fr',    'fr', 'French'),
    ('pt', 'br', 'pt-BR', 'br', 'Portuguese (Brazil)'),
    ('id', 'id', 'id',    'id', 'Indonesian'),
    ('ru', 'ru', 'ru',    'ru', 'Russian'),
    ('hi', 'in', 'hi',    'in', 'Hindi'),
    ('ar', 'sa', 'ar',    'sa', 'Arabic'),
    ('bn', 'bd', 'bn',    'bd', 'Bengali'),
]
LOCALE_BY_CODE = {c[0]: c for c in LOCALES}
# 비라틴 표기 언어 — 같은 정보량이 더 적은 글자로 표현된다 (글자수 기준 완화 대상)
NON_LATIN = {'ko', 'ja', 'zh', 'ar', 'hi', 'bn', 'ru'}

# 구글플레이 사전등록(출시예정) 표기 — 언어권별 버튼/배지 문구
PREORDER_MARKERS = [
    '사전 등록', '사전등록', '출시 예정', '출시예정',
    '事前登録', '配信予定', '予約注文', 'リリース予定',
    'Pre-register', 'Pre-registration', 'Coming soon', 'Early access',
    '預先註冊', '預註冊', '即將推出', '即将推出', '即将上市',
    'Vorregistrierung', 'pré-inscription', 'Pré-inscription',
    'предрегистрация', 'Предзаказ', 'Pra-pendaftaran', 'Segera hadir',
]

PKG_BLOCK_RE = re.compile(r'\[((?:"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+"\s*,?\s*){2,})\]')
PKG_TOKEN_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$')
BAD_SEGMENTS = ('google', 'gstatic', 'googleusercontent', 'android', 'w3', 'schema',
                'youtube', 'gvt1', 'ggpht', 'blogspot', 'github', 'example')


# ---------------------------------------------------------------- http utils

def get_json(url, timeout=25):
    req = urllib.request.Request(
        url, headers={'User-Agent': UA, 'Accept': 'application/json, text/plain, */*'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def get_text(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': '*'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def get_bytes(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def unescape(s):
    try:
        return htmlmod.unescape(s or '')
    except Exception:
        return s or ''


# ---------------------------------------------------------------- 상태 파일

def load_json(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def save_json(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(obj, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception:
        pass


def load_seen(path):
    v = load_json(path, [])
    return set(v) if isinstance(v, list) else set()


def save_seen(path, ids):
    save_json(path, sorted(ids)[-3000:])


def published_meta(dirs):
    """이미 발행된 글의 (sourceUrl 집합, slug 집합, 스토어 식별자 집합).

    식별자(Apple app id / Play 패키지명)까지 뽑는 이유:
    같은 앱이 스토어프론트만 다르게(us/kr/jp…) 다시 수집되는 것을 막기 위함.
    """
    urls, slugs, ids = set(), set(), set()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if not fn.endswith('.md'):
                    continue
                slugs.add(fn[:-3])
                try:
                    head = open(os.path.join(root, fn), encoding='utf-8').read(4000)
                except Exception:
                    continue
                m = re.search(r'^sourceUrl:\s*"?([^"\n]+)"?\s*$', head, re.M)
                if not m:
                    continue
                u = m.group(1).strip()
                urls.add(u)
                a = re.search(r'/id(\d+)', u)
                if a:
                    ids.add('apple:%s' % a.group(1))
                p = re.search(r'[?&]id=([A-Za-z0-9_.\-]+)', u)
                if p:
                    ids.add('play:%s' % p.group(1))
    return urls, slugs, ids


# ---------------------------------------------------------------- Apple

def apple_feeds(cc):
    return [
        'https://itunes.apple.com/%s/rss/newapplications/limit=100/json' % cc,
        'https://itunes.apple.com/%s/rss/newapplications/limit=100/genre=6014/json' % cc,
        'https://itunes.apple.com/%s/rss/newfreeapplications/limit=100/json' % cc,
    ]


def parse_iso(s):
    """ISO 문자열 → tz 없는(naive) UTC datetime. 비교 시 offset 충돌을 없애기 위함."""
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def parse_apple_feed(data, cc):
    out = []
    entries = (data or {}).get('feed', {}).get('entry') or []
    if isinstance(entries, dict):
        entries = [entries]
    for e in entries:
        try:
            app_id = str(e['id']['attributes']['im:id'])
            name = e['im:name']['label']
        except Exception:
            continue
        imgs = e.get('im:image') or []
        out.append({
            'store': 'apple',
            # key 에 스토어프론트를 넣지 않는다: 같은 앱은 언어권이 달라도 1번만 발행한다.
            # (한국 스토어에 뜬 앱을 영어로 한 번 썼으면 한국어로 또 쓰지 않는다)
            'key': 'apple:%s' % app_id,
            'ident': app_id,
            'name': name,
            'artist': (e.get('im:artist') or {}).get('label', ''),
            'category': ((e.get('category') or {}).get('attributes') or {}).get('label', ''),
            'released': parse_iso((e.get('im:releaseDate') or {}).get('label')),
            'url': ((e.get('link') or {}).get('attributes') or {}).get('href', '')
                   or 'https://apps.apple.com/%s/app/id%s' % (cc, app_id),
            'icon': imgs[-1]['label'] if imgs else '',
        })
    return out


def apple_lookup(app_id, cc):
    try:
        d = get_json('https://itunes.apple.com/lookup?id=%s&country=%s' % (app_id, cc))
        res = (d or {}).get('results') or []
        return res[0] if res else {}
    except Exception as ex:
        print('   lookup 실패(%s): %s' % (app_id, str(ex)[:80]))
        return {}


def apple_reviews(app_id, cc, limit=3):
    try:
        d = get_json('https://itunes.apple.com/%s/rss/customerreviews/page=1/id=%s/'
                     'sortby=mosthelpful/json' % (cc, app_id))
        entries = (d or {}).get('feed', {}).get('entry') or []
        if isinstance(entries, dict):
            entries = [entries]
        out = []
        for e in entries[:limit + 1]:
            try:
                title, body = e['title']['label'], e['content']['label']
            except Exception:
                continue
            if title and body and title != body:
                out.append({'title': title, 'body': body[:400],
                            'rating': (e.get('im:rating') or {}).get('label')})
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


# ---------------------------------------------------------------- 구글플레이

def play_collections(hl, gl):
    return [
        ('https://play.google.com/store/apps/collection/topselling_new_free?hl=%s&gl=%s'
         % (hl, gl), 'App'),
        ('https://play.google.com/store/apps/collection/topselling_new_free_game?hl=%s&gl=%s'
         % (hl, gl), 'Game'),
    ]


def play_pkg_ids(html_text, limit=24):
    """클러스터 페이지에서 패키지 ID 목록을 뽑는다 (JS 렌더 전 JSON 블록)."""
    out, seen = [], set()
    for block in PKG_BLOCK_RE.findall(html_text or ''):
        for tok in re.findall(r'"([^"]+)"', block):
            tok = tok.strip()
            if not PKG_TOKEN_RE.match(tok) or tok.count('.') < 2:
                continue
            if len(tok) > 64 or len(tok) < 6:
                continue
            low = tok.lower()
            if any(b in low for b in BAD_SEGMENTS):
                continue
            if low.endswith(('.png', '.jpg', '.jpeg', '.js', '.css', '.svg', '.html',
                             '.json', '.webp', '.gif')):
                continue
            if tok in seen:
                continue
            seen.add(tok)
            out.append(tok)
            if len(out) >= limit:
                return out
    return out


def play_details(pkg, hl, gl):
    """상세 페이지 파싱: 이름·개발자·설명·평점·가격·이미지·사전등록 여부."""
    url = 'https://play.google.com/store/apps/details?id=%s&hl=%s&gl=%s' % (pkg, hl, gl)
    try:
        page = get_text(url)
    except Exception as ex:
        print('   play 상세 실패(%s): %s' % (pkg, str(ex)[:60]))
        return {}
    if pkg not in page:
        return {}

    def meta(prop):
        m = re.search(r'property="og:%s"\s+content="([^"]*)"' % prop, page)
        return (m.group(1) if m else '').strip()

    name = meta('title') or ''
    name = re.sub(r'\s*-\s*(Apps on Google Play|Google Play\s*(앱|アプリ|应用|응용 프로그램)).*$',
                  '', name).strip()

    img = meta('image') or ''
    long_desc = ''
    i = page.find('itemprop="description"')
    if i > 0:
        gt = page.find('>', i)
        end = page.find('</div>', gt)
        seg = page[gt + 1:end] if end > gt else page[gt + 1:gt + 8000]
        long_desc = re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', seg))).strip()

    short = ''
    m = re.search(r'"description":"((?:[^"\\]|\\.)*)"', page)
    if m:
        short = unescape(m.group(1))

    dev = ''
    m = re.search(r'"author":\{"@type":"[^"]+","name":"((?:[^"\\]|\\.)*)"', page)
    if m:
        dev = unescape(m.group(1))

    cat = ''
    m = re.search(r'"applicationCategory":"([^"]+)"', page)
    if m:
        cat = m.group(1)

    rating = rating_cnt = None
    m = re.search(r'"ratingValue":"?([\d.]+)"?', page)
    if m:
        rating = float(m.group(1))
    m = re.search(r'"ratingCount":"?(\d+)"?', page)
    if m:
        rating_cnt = int(m.group(1))

    price = ''
    m = re.search(r'"offers":\[\{"@type":"Offer","price":"([^"]*)"', page)
    if m:
        price = m.group(1)

    avail = ''
    m = re.search(r'"availability":"([^"]+)"', page)
    if m:
        avail = m.group(1)
    upcoming = ('PreOrder' in avail) or any(k in page for k in PREORDER_MARKERS)

    updated = ''
    m = re.search(r'(\d{4})[.\-년]\s*(\d{1,2})[.\-월]\s*(\d{1,2})', page)
    if m:
        updated = '%s-%02d-%02d' % (m.group(1), int(m.group(2)), int(m.group(3)))

    return {
        'store': 'play', 'pkg': pkg, 'name': name or pkg, 'developer': dev,
        'description': long_desc or short, 'category': cat or 'App',
        'rating': rating, 'rating_count': rating_cnt, 'price': price,
        'free': price in ('0', '0.0', '0.00', ''), 'upcoming': upcoming,
        'image': img, 'updated': updated,
        'url': 'https://play.google.com/store/apps/details?id=%s&hl=%s&gl=%s' % (pkg, hl, gl),
    }


# ---------------------------------------------------------------- 후보 수집

def collect(cc, hl, gl, use_play=True):
    """언어권 1개의 후보 풀(Apple + Play)."""
    pool = []
    for url in apple_feeds(cc):
        try:
            pool.extend(parse_apple_feed(get_json(url), cc))
        except Exception as ex:
            print('   [apple %s] 피드 실패: %s' % (cc, str(ex)[:50]))
    if use_play:
        for url, kind in play_collections(hl, gl):
            try:
                ids = play_pkg_ids(get_text(url))
            except Exception as ex:
                print('   [play %s] 수집 실패: %s' % (gl, str(ex)[:50]))
                continue
            for pkg in ids:
                pool.append({'store': 'play', 'key': 'play:%s' % pkg, 'ident': pkg,
                             'name': pkg, 'category': kind, 'released': None,
                             'url': 'https://play.google.com/store/apps/details?id=%s&hl=%s&gl=%s'
                                    % (pkg, hl, gl)})
    uniq, seen = [], set()
    for it in pool:
        if it['key'] in seen:
            continue
        seen.add(it['key'])
        uniq.append(it)
    return uniq


def pick_one(cands, seen, pub_urls, pub_ids, today, rotate_idx):
    """출시예정 → 게임 → 앱 순으로 1건. rotate_idx로 소스 순서를 섞는다."""
    fresh = [c for c in cands
             if c['key'] not in seen and c['key'] not in pub_ids and c['url'] not in pub_urls]
    if not fresh:
        return None

    def is_game(c):
        return 'game' in (c.get('category') or '').lower()

    games = [c for c in fresh if is_game(c)]
    apps = [c for c in fresh if not is_game(c)]
    upcoming = [c for c in fresh
                if c.get('released') and c['released'].replace(tzinfo=None) > today]

    queues = [upcoming, games, apps]
    for k in range(3):
        q = queues[(rotate_idx + k) % 3]
        if q:
            q.sort(key=lambda c: c.get('released') or datetime.datetime.min, reverse=True)
            return q[0]
    return fresh[0]


# ---------------------------------------------------------------- 자료/프롬프트

def clip(s, n):
    s = re.sub(r'\s+', ' ', (s or '')).strip()
    return s if len(s) <= n else s[:n] + '...'


def build_material(item, cc, hl, gl, today):
    """Apple/Play 상세를 LLM용 원문 자료로 변환 → (자료, upcoming, 이미지URL, 표시명)"""
    if item['store'] == 'apple':
        detail = apple_lookup(item['ident'], cc)
        if not detail:
            return None, False, '', ''
        revs = apple_reviews(item['ident'], cc)
        rel = item['released'] or parse_iso(detail.get('releaseDate'))
        upcoming = bool(rel and rel.replace(tzinfo=None) > today)
        lines = [
            'APP NAME: %s' % (detail.get('trackName') or item['name']),
            'DEVELOPER: %s' % (detail.get('artistName') or item.get('artist', '')),
            'STORE CATEGORY: %s' % (detail.get('primaryGenreName') or item.get('category', '')),
            'GENRES: %s' % ', '.join(detail.get('genres') or []),
            'PRICE: %s' % (detail.get('formattedPrice') or 'not disclosed'),
            'VERSION: %s' % (detail.get('version') or 'n/a'),
            'STATUS: %s' % (('UPCOMING / PRE-ORDER (release date: %s)' % rel.strftime('%Y-%m-%d'))
                            if upcoming else
                            ('RELEASED (%s)' % rel.strftime('%Y-%m-%d') if rel else 'RELEASED')),
            'LISTING URL: %s' % (detail.get('trackViewUrl') or item['url']),
        ]
        if detail.get('averageUserRating') and detail.get('userRatingCount'):
            lines.append('RATING: %.1f (%s ratings)' % (float(detail['averageUserRating']),
                                                        detail['userRatingCount']))
        lines += ['', 'OFFICIAL DESCRIPTION:',
                  clip(detail.get('description') or '', 2600) or '(none provided)']
        if detail.get('releaseNotes'):
            lines += ['', "WHAT'S NEW: %s" % clip(detail['releaseNotes'], 400)]
        if revs:
            lines += ['', 'EARLY USER FEEDBACK (quotes from the store listing):']
            for r in revs:
                lines.append('- "%s" (%s/5): %s' % (clip(r['title'], 90), r.get('rating') or '?',
                                                    clip(r['body'], 200)))
        img = detail.get('artworkUrl512') or detail.get('artworkUrl100') or item.get('icon', '')
        return '\n'.join(lines), upcoming, img, (detail.get('trackName') or item['name'])

    d = play_details(item['ident'], hl, gl)
    if not d or not d.get('description'):
        return None, False, '', ''
    upcoming = bool(d.get('upcoming'))
    price_txt = 'Free' if d.get('free') else ('%s (paid)' % (d.get('price') or 'paid'))
    lines = [
        'APP NAME: %s' % d.get('name'),
        'DEVELOPER: %s' % (d.get('developer') or 'not disclosed'),
        'STORE CATEGORY: %s' % d.get('category'),
        'PRICE: %s' % price_txt,
        'LAST UPDATE: %s' % (d.get('updated') or 'n/a'),
        'STATUS: %s' % ('UPCOMING / PRE-REGISTRATION (not released yet)' if upcoming
                        else 'RELEASED (available now)'),
        'LISTING URL: %s' % d.get('url'),
    ]
    if d.get('rating') and d.get('rating_count'):
        lines.append('RATING: %.1f (%s ratings)' % (d['rating'], d['rating_count']))
    lines += ['', 'OFFICIAL DESCRIPTION:', clip(d.get('description') or '', 2600)]
    return '\n'.join(lines), upcoming, d.get('image', ''), d.get('name')


SYSTEM_APP = (
    "You write practical, search-friendly app and game introductions for an international "
    "audience. Your writing is plain, concrete and free of hype. You never invent features, "
    "prices or dates: every fact must come from the source material."
)


def user_prompt(lang_name, mat, upcoming, app_name, cmin, cmax):
    if upcoming:
        structure = ('"## What We Know So Far", "## Expected Release Date", "## Key Features", '
                     '"## Who It Is For", "## Pricing", "## FAQ"')
        angle = ("The title is NOT released yet. Frame it as an upcoming launch and say clearly "
                 "that details may change before release.")
    else:
        structure = ('"## What {APP} Is", "## Key Features", "## Who It Is For", '
                     '"## Pricing and Availability", "## Early Impressions", "## FAQ"')
        angle = ("Introduce it as a freshly released title. If there is little user feedback yet, "
                 "say so plainly instead of pretending there is consensus.")
    structure = structure.replace('{APP}', app_name)
    return (
        "Write ONE article in {LANG} about the app/game below.\n"
        "The source material is already in {LANG} (it comes straight from that storefront).\n"
        "Do NOT translate anything and do NOT switch language: title, description and body must "
        "all be natural {LANG}.\n\n"
        "SOURCE MATERIAL (facts come only from here):\n"
        "----------------\n{material}\n----------------\n\n"
        "RULES\n"
        "1. Output ONLY JSON: {{\"title\":..., \"description\":..., \"category\":..., \"body\":...}}\n"
        "2. title: keep the exact app name; add intent words (release date / features / price / "
        "review) only if they fit naturally in {LANG}; max 70 characters.\n"
        "3. description: one sentence, 140-160 characters.\n"
        "4. category: always \"Apps & Games\".\n"
        "5. body: Markdown, {cmin}-{cmax} characters. Start with 1-2 short paragraphs (no heading "
        "above them).\n"
        "6. Use exactly these \"##\" sections, in this order — but write the heading text in "
        "{LANG} (translate the heading wording, keep the order and the number of sections): "
        "{structure}.\n"
        "7. The FAQ must hold exactly 3 questions as \"###\" headings, each answered in 1-3 "
        "sentences.\n"
        "8. Never use \"#\" H1 and never insert images or markdown image syntax.\n"
        "9. Put the app name in the first sentence. Link the official store listing once.\n"
        "10. Do NOT claim you tested or played it. Do not invent download counts or sales "
        "figures. If the material lacks something, write one honest sentence about the gap.\n"
        "11. {angle}\n"
        "12. No marketing superlatives like \"revolutionary\". No bullet-point walls.\n"
    ).format(LANG=lang_name, material=mat, cmin=cmin, cmax=cmax,
             structure=structure, angle=angle)


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


def slugify(title, ident, taken):
    """라틴 문자가 있으면 그대로, 전부 비라틴(한/중/일/아랍 등)이면 식별자 기반 slug."""
    s = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    words = [w for w in s.split('-') if len(w) > 1][:8]
    if len(words) >= 2:
        slug = '-'.join(words)
    else:
        slug = re.sub(r'[^a-z0-9]+', '-', ident.lower()).strip('-') or 'app'
    if len(slug) < 6:
        slug = 'app-' + slug
    if slug not in taken:
        return slug
    return slug + '-' + hashlib.sha1(ident.encode('utf-8')).hexdigest()[:6]


def save_image(url, assets_dir, ident):
    if not url:
        return ''
    ext = '.jpg'
    m = re.search(r'\.(png|jpe?g|webp)(?:\?|$)', url.lower())
    if m:
        g = m.group(1)
        ext = '.' + ('jpg' if g.startswith('jp') else g)
    fn = 'app-%s%s' % (hashlib.sha1(ident.encode('utf-8')).hexdigest()[:10], ext)
    dest = os.path.join(assets_dir, fn)
    try:
        if not os.path.exists(dest):
            os.makedirs(assets_dir, exist_ok=True)
            open(dest, 'wb').write(get_bytes(url))
        return '/assets/img/apps/%s' % fn
    except Exception as ex:
        print('   이미지 저장 실패: %s' % str(ex)[:60])
        return ''


# ---------------------------------------------------------------- 메인

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='생성할 글 수 (기본 3)')
    ap.add_argument('--locale', default='', help='특정 언어만 (예: ko)')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--no-play', action='store_true', help='구글플레이 소스 제외')
    ap.add_argument('--base', default=BASE)
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    game_dir = os.path.join(base, 'content', 'game')
    assets_dir = os.path.join(base, 'assets', 'img', 'apps')
    seen_file = os.path.join(base, 'content', 'app_radar_seen.json')
    cursor_file = os.path.join(base, 'content', 'app_radar_rotate.json')

    cfg = load_json(os.path.join(base, 'config.json'), {})
    target = args.limit or int(cfg.get('apps_per_day', 3))
    cmin = int(cfg.get('app_char_min', cfg.get('char_min', 3000)))
    cmax = int(cfg.get('app_char_max', cfg.get('char_max', 5000)))

    sys.path.insert(0, os.path.join(base, 'scripts'))
    try:
        import llm  # noqa: E402
    except Exception:
        print('scripts/llm.py 를 불러올 수 없습니다.')
        return 1

    today = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

    if args.locale:
        if args.locale not in LOCALE_BY_CODE:
            print('알 수 없는 locale: %s' % args.locale)
            return 1
        order = [LOCALE_BY_CODE[args.locale]]
    else:
        cursor = int(load_json(cursor_file, {'cursor': 0}).get('cursor', 0) or 0)
        order = [LOCALES[(cursor + i) % len(LOCALES)] for i in range(len(LOCALES))]

    seen = load_seen(seen_file)
    pub_urls, taken, pub_ids = published_meta(
        [game_dir, os.path.join(base, 'content', 'posts')])

    print('App Radar v2 — 목표 %d건 / 언어 순서: %s'
          % (target, ' > '.join(c[0] for c in order[:max(target, 1)])))

    done = 0
    tried = 0
    reserved = set()   # 이번 실행에서 이미 뽑은 앱 (다른 언어가 같은 앱을 또 쓰지 않도록)
    for lang, cc, hl, gl, lang_name in order:
        if done >= target:
            break
        tried += 1
        outdir = os.path.join(game_dir, lang)
        os.makedirs(outdir, exist_ok=True)
        print('\n[%s] apple=%s / play=%s-%s' % (lang, cc, gl, hl))
        cands = collect(cc, hl, gl, use_play=not args.no_play)
        print('   후보 %d개' % len(cands))
        item = pick_one(cands, seen | reserved, pub_urls, pub_ids, today, tried)
        if not item:
            print('   신규 후보 없음 → 다음 언어')
            continue
        reserved.add(item['key'])
        print('   선정: %s (%s)' % (item.get('name'), item['store']))

        mat, upcoming, img_url, disp_name = build_material(item, cc, hl, gl, today)
        if not mat:
            print('   상세 조회 실패 → 건너뜀')
            seen.add(item['key'])
            save_seen(seen_file, seen)
            continue
        if args.dry:
            print('   출시예정=%s / 자료 %d자 / 표시명=%s' % (upcoming, len(mat), disp_name))
            print('\n'.join('   ' + l for l in mat.split('\n')[:10]))
            continue

        img_md = ''
        rel = save_image(img_url, assets_dir, item['key'])
        if rel:
            img_md = '![%s](%s)\n\n' % ((disp_name or 'app').replace('[', ''), rel)

        # 비라틴 언어(한/중/일/아랍/힌디/벵골/러시아)는 같은 분량이 더 적은 글자로 나온다.
        # 영어 기준 글자수를 그대로 강요하면 불필요한 재시도만 늘어난다 → 60% 기준을 쓴다.
        if lang in NON_LATIN:
            lo, hi = max(1200, int(cmin * 0.6)), int(cmax * 0.6)
        else:
            lo, hi = cmin, cmax

        obj, body = {}, ''
        for attempt in range(1, 4):
            got, provider = llm.chat(
                [{'role': 'system', 'content': SYSTEM_APP},
                 {'role': 'user', 'content': user_prompt(
                     lang_name, mat, upcoming, disp_name or 'app', cmin, cmax)}],
                purpose='write')
            obj = extract_json(got)
            body = (obj.get('body') or '').strip()
            n = len(body)
            print('   [%d] %s 응답 %d자' % (attempt, provider, n))
            if lo <= n <= hi and obj.get('title'):
                break
            print('      재시도: %s' % ('너무 짧음' if n < lo else '너무 김'))
            time.sleep(0.4)
        if not obj.get('title') or not body:
            print('   생성 실패 → 다음 실행에서 재시도')
            continue

        title = re.sub(r'"', "'", obj['title']).strip()
        desc = re.sub(r'"', "'", clip(obj.get('description') or '', 200))
        slug = slugify(title, item['key'], taken)
        taken.add(slug)
        src_name = 'Google Play' if item['store'] == 'play' else 'App Store'
        fm = ('---\n'
              'slug: %s\n'
              'title: "%s"\n'
              'description: "%s"\n'
              'category: "Apps & Games"\n'
              'date: "%s"\n'
              'sourceName: "%s"\n'
              'sourceUrl: "%s"\n'
              'lang: "%s"\n'
              '---\n\n') % (slug, title, desc,
                            datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'),
                            src_name, item['url'], lang)
        open(os.path.join(outdir, slug + '.md'), 'w', encoding='utf-8').write(
            fm + img_md + body + '\n')
        seen.add(item['key'])
        save_seen(seen_file, seen)
        print('   발행[%s]: %s (%d자)' % (lang, slug, len(body)))
        done += 1
        time.sleep(0.3)

    if not args.locale and not args.dry:
        cur = int(load_json(cursor_file, {'cursor': 0}).get('cursor', 0) or 0)
        save_json(cursor_file, {'cursor': (cur + max(tried, 1)) % len(LOCALES)})

    print('\nApp Radar 완료: %d건' % done)
    return 0


if __name__ == '__main__':
    sys.exit(main())
