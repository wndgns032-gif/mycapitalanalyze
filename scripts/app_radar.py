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

지원 언어 검증 (로이 지시 2026-10-02):
  한국어를 지원하지 않는 게임을 한국어로 발행할 필요는 없다.
  → 우선순위대로 늘어놓은 뒤 앞에서부터 훑어, **그 언어를 실제로 지원하는** 첫 후보를 고른다.
    Apple : lookup 의 languageCodesISO2A (스토어가 공식 표기하는 지원 언어 목록)
    Play  : hl={lang} 리스팅 설명이 영문 리스팅과 그대로 같으면 미현지화로 판정
            (비라틴 언어는 해당 문자군 존재 여부를 먼저 확인)
  검증을 통과하는 후보가 없으면 그 언어로는 발행하지 않는다(억지로 쓰지 않음).
  비상 시 config.public.json 의 apps_require_lang_support 를 false 로, 또는 --no-lang-gate.

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
import glob
import hashlib
import html as htmlmod
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse

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
# Apple 피드를 긁을 스토어프론트 — 전 언어 공용 후보 풀을 만들 때 사용한다.
APPLE_CC_POOL = [c[1] for c in LOCALES]
# 비라틴 표기 언어 — 같은 정보량이 더 적은 글자로 표현된다 (글자수 기준 완화 대상)
NON_LATIN = {'ko', 'ja', 'zh', 'ar', 'hi', 'bn', 'ru'}
CJK = {'ko', 'ja', 'zh'}

# ---------------------------------------------------------------------------
# 지원 언어 검증 (로이 지시 2026-10-02)
#   "한국어를 지원하지 않는 게임을 한국어로 발행할 필요가 없다"
#   → 발행 전에 스토어 리스팅이 해당 언어를 실제로 지원하는지 확인한다.
#     Apple : lookup 응답의 languageCodesISO2A (스토어가 공식 표기하는 지원 언어)
#     Play  : hl={lang} 로 받은 설명이 영문 리스팅과 동일하면 미현지화로 판정
#             (비라틴 언어는 해당 문자군 존재 여부로 먼저 확인)
# ---------------------------------------------------------------------------
APPLE_LANG_CODES = {
    'en': {'EN'},
    'ko': {'KO'},
    'ja': {'JA'},
    'zh': {'ZH', 'CN'},
    'es': {'ES'},
    'de': {'DE'},
    'fr': {'FR'},
    'pt': {'PT'},
    'id': {'ID'},
    'ru': {'RU'},
    'hi': {'HI'},
    'ar': {'AR'},
    'bn': {'BN'},
}

SCRIPT_HINT = {
    'ko': re.compile(r'[\uac00-\ud7af]'),
    'ja': re.compile(r'[\u3040-\u30ff\uff66-\uff9f]'),
    'zh': re.compile(r'[\u4e00-\u9fff]'),
    'ar': re.compile(r'[\u0600-\u06ff]'),
    'hi': re.compile(r'[\u0900-\u097f]'),
    'bn': re.compile(r'[\u0980-\u09ff]'),
    'ru': re.compile(r'[\u0400-\u04ff]'),
}

# 언어 검증 때문에 후보를 최대 몇 개까지 뒤져볼지 (HTTP 호출 폭주 방지)
MAX_TRY = 40

# 출시예정 앱을 받을 수 있는 언어 수 (로이 지시 2026-10-04).
#   "언어별 대표 나라의 출시예정 게임을 찾으라" → 각 언어가 자기 스토어프론트 기준으로
#   뽑되, 출시예정 후보는 희소(실측 12개)하므로 상한을 둔다.
#   12개로 두면 언어당 1개(=13개 언어 거의 전부)까지 허용, 후보 소진 시 자연히 일반작으로 내려간다.
#   ⚠️ 전 언어가 같은 앱이 되는 것을 막는 것은 이 값이 아니라 '언어별 후보 풀이 다르다'는 사실이다
#      (Apple 피드가 스토어프론트별로 다르고, Play 관련앱 확장도 hl/gl 별로 다르다).
UPCOMING_SHARE_LANGS = 13   # 언어 수와 동일 — 모든 언어가 출시예정을 받을 수 있게

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


def today_count(d, today_str):
    """해당 언어 디렉터리에 '오늘' 발행된 글 수 (프론트매터 date 기준)."""
    n = 0
    if not os.path.isdir(d):
        return 0
    for fn in os.listdir(d):
        if not fn.endswith('.md'):
            continue
        try:
            head = open(os.path.join(d, fn), encoding='utf-8').read(2500)
        except Exception:
            continue
        m = re.search(r'^date:\s*"?([0-9]{4}-[0-9]{2}-[0-9]{2})"?', head, re.M)
        if m and m.group(1) == today_str:
            n += 1
    return n

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

# Apple RSS 피드의 카테고리 라벨은 현지어로 온다(게임/ゲーム/游戏/ Games ...).
# genre=6014 파라미터는 레거시 RSS에서 무시된다(실측: 게임 피드와 일반 피드 결과 동일).
# → 게임 판정은 1차로 이 현지어 라벨, 2차로 lookup 의 영문 primaryGenreName 으로 한다.
GAME_WORDS = ('game', 'games', '게임', 'ゲーム', '游戏', '遊戲', 'juegos', 'jeux',
              'jogos', 'spiele', 'spiel', 'giochi', 'игры', 'игра', 'permainan',
              'trò chơi', 'गेम', 'গেম', 'ألعاب')
GAME_WORD_RE = re.compile('|'.join(re.escape(w) for w in GAME_WORDS), re.I)

# 앱/게임 글 분량 (로이 방침 2026-10-03: "글이 꼭 안 길어도 돼, 적당히 SEO에 걸릴만큼만")
# 예전엔 하한 3000~3200 / 상한 5000+ 이라 영어가 9000자까지 나왔다.
# SEO 에 걸릴 최소선만 남기고 상한을 확 낮춘다. CI 는 config.json 을 워크플로에서
# 하드코딩 생성하므로, 이 기본값이 클라우드 실행에서도 적용된다.
APP_CHAR_MIN, APP_CHAR_MAX = 1500, 3200          # 라틴 계열 등 기본
APP_CJK_MIN, APP_CJK_MAX = 1600, 3000            # CJK 는 글자당 정보량이 많아 더 짧아도 충분


def apple_feeds(cc):
    return [
        'https://itunes.apple.com/%s/rss/newapplications/limit=100/json' % cc,
        'https://itunes.apple.com/%s/rss/newfreeapplications/limit=100/json' % cc,
    ]


# Apple RSS 를 아예 안 주는 스토어프론트 — 실측(2026-10-04): bd 는 643바이트 빈 피드(entry 0개).
# 그대로 두면 해당 언어(bn)는 Play 고정목록에만 의존해 후보가 빈약해진다.
# 같은 언어권에서 실제로 피드를 주는 이웃 스토어로 대체한다.
APPLE_CC_FALLBACK = {
    'bd': 'in',   # 방글라데시 → 인도 (벵골어권 인접, RSS 정상)
}

# Play 컬렉션도 스토어프론트가 아예 안 주는 지역이 있다(실측: gl=bd 는 사전등록 목록 0개).
PLAY_GL_FALLBACK = {
    'bd': 'in',
}


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


_CACHE = {}


def apple_lookup(app_id, cc):
    ck = 'apple:%s:%s' % (app_id, cc)
    if ck in _CACHE:
        return _CACHE[ck]
    try:
        d = get_json('https://itunes.apple.com/lookup?id=%s&country=%s' % (app_id, cc))
        res = (d or {}).get('results') or []
        out = res[0] if res else {}
    except Exception as ex:
        print('   lookup 실패(%s): %s' % (app_id, str(ex)[:80]))
        out = {}
    _CACHE[ck] = out
    return out


def apple_lookup_many(app_ids, cc):
    """Apple lookup 은 id 파라미터를 여러 개 받는다 → 후보 수십 개를 한 번에 조회.
    지원 언어 검증을 후보 여러 개에 한꺼번에 적용하려고 미리 캐시를 채워둔다."""
    # 주의: id 파라미터를 반복(id=1&id=2)하면 마지막 것 하나만 돌아온다 → 쉼표로 묶어야 한다.
    ids = [str(i) for i in app_ids if str(i).isdigit()]
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        url = ('https://itunes.apple.com/lookup?id=%s&country=%s'
               % (','.join(chunk), cc))
        try:
            d = get_json(url)
        except Exception as ex:
            print('   batch lookup 실패: %s' % str(ex)[:60])
            continue
        for r in (d or {}).get('results') or []:
            tid = str(r.get('trackId') or '')
            if tid:
                _CACHE['apple:%s:%s' % (tid, cc)] = r


def apple_detail_cached(app_id, cc):
    """캐시에서 상세를 찾는다. 해당 스토어프론트 조회가 없으면 전 스토어 공용 'us' 캐시로 대체.

    후보 풀이 전 스토어프론트 공용이 되면서(2026-10-04) 언어별 cc 조회를 전부 미리
    채우면 HTTP 호출이 13배로 늘어난다. 지원 언어·장르·출시일은 스토어프론트와
    무관한 값이므로 us 조회 1번으로 충분하다(현지어 이름이 필요할 때만 개별 조회).

    ⚠️ 2026-10-04 실측: cn 스토어프론트는 일부 앱(ANANTA id 6776533126)을 아예
      안 준다 → releaseDate 를 못 얻어 '출시예정' 딱지가 빠지는 사고가 났다.
      us 캐시에도 없으면 마지막으로 kr 로 직접 조회해 출시일을 확보한다.
    """
    got = (_CACHE.get('apple:%s:%s' % (app_id, cc))
           or _CACHE.get('apple:%s:us' % app_id))
    if got:
        return got
    if cc != 'kr':
        try:
            d = apple_lookup(app_id, 'kr')
            if d and d.get('releaseDate'):
                return d
        except Exception:
            pass
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

# Play 컬렉션 슬러그 — 어느 컬렉션에 실렸는지가 곧 '출시예정' 판정의 근거다.
#   2026-10-04 실측: 예전에 쓰던 topselling_new_free(_game) 은 봇에게 앱 상세 링크를
#   **0개** 준다(빈 껍데기). 거기서 뽑히던 22개는 본문이 아니라 window.WIZ_global_data
#   전역 상수("fXOvac" 18개)여서 hl/gl 과 무관하게 전부 동일했다 → 후보가 매일 똑같았다.
#   → 실제로 상세 링크를 주는 아래 두 컬렉션으로 교체한다(로이 제안: 소스를 먼저 넓힌다).
PLAY_COLLECTIONS = [
    # 사전등록 전용 — 이 목록에 실려 있다는 것 자체가 '아직 미출시'의 출처 기반 증거
    ('promotion_3000000d51_pre_registration_games', 'Game', 'upcoming'),
    # 얼리 액세스 — 정식 출시 전이지만 설치는 가능. 별도 취급(출시예정으로 보지 않음)
    ('promotion_30029b2_earlyaccessgames', 'Game', None),
]


def play_collections(hl, gl):
    """Play 후보 소스 목록 → (url, category, upcoming_hint)"""
    return [
        ('https://play.google.com/store/apps/collection/%s?hl=%s&gl=%s'
         % (slug, hl, gl), cat, hint)
        for slug, cat, hint in PLAY_COLLECTIONS
    ]


# 상세 페이지 링크에서 패키지를 뽑는 정규식 — 컬렉션 페이지 본문에 실제로 존재하는 유일한 신호
PLAY_DETAIL_RE = re.compile(r'/store/apps/details\?id=([A-Za-z][A-Za-z0-9_.]+)')


def play_pkg_ids(html_text, limit=24):
    """컬렉션 페이지에서 패키지 ID 목록을 뽑는다 — 본문 상세 링크만 사용.

    ⚠️ 2026-10-05 실측: 예전처럼 JSON 블록을 폴백으로 돌리면 window.WIZ_global_data
      전역 상수("fXOvac" 18개)까지 긁어온다. 실측 사고 — 상세링크 20개를 찾은 뒤에도
      limit 24를 채우려 폴백이 돌아 Clash Royale·Clash of Clans 같은 출시 완료작이
      섞여 '출시예정'으로 복구되는 일이 있었다.
      → 폴백 완전 제거. 상세 링크가 0개면 그 컬렉션은 빈 것으로 본다.
    """
    out, seen = [], set()
    for tok in PLAY_DETAIL_RE.findall(html_text or ''):
        if tok in seen or tok.count('.') < 1:
            continue
        if len(tok) > 64:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            return out
    return out


def play_details(pkg, hl, gl):
    """상세 페이지 파싱: 이름·개발자·설명·평점·가격·이미지·사전등록 여부."""
    ck = 'play:%s:%s:%s' % (pkg, hl, gl)
    if ck in _CACHE:
        return _CACHE[ck]
    out = _play_details_uncached(pkg, hl, gl)
    _CACHE[ck] = out
    return out


def _play_details_uncached(pkg, hl, gl):
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
    # 오탐 차단: 이미 출시된 앱도 이벤트/업데이트 예고에 'Coming soon' 같은 문구를 쓴다.
    # 진짜 사전등록(출시 전) 앱은 리뷰가 없다 → 리뷰가 있으면 출시된 앱으로 본다.
    # (실측: Blood Strike 2.5주년 페이지가 마커에 걸려 출시예정으로 오탐된 적 있다)
    if upcoming and 'PreOrder' not in avail and (rating_cnt or 0) > 0:
        upcoming = False
    # ⚠️ 2026-10-04 로이 지적으로 **삭제한 로직** (되살리지 말 것):
    #   "Install 버튼이 없으면 출시예정" 이라는 판정을 여기서 했었다. 오탐의 원인.
    #   실측으로 틀렸음이 증명됐다 —
    #     · 출시된 게임(Kingshot)도 ko/kr·ja/jp 페이지에선 Install 버튼이 안 나온다
    #     · 출시된 레이싱 마스터는 전 스토어에서 Install 버튼 0개 → 출시예정으로 오탐
    #     · 반대로 진짜 사전예약 ANANTA 페이지에도 사전등록 마커가 아예 없다
    #   즉 Play 웹페이지(봇 렌더)로는 사전등록 여부를 판별할 수 없다.
    #   → 출시예정 판정은 **Apple 의 미래 releaseDate** 만 신뢰한다(is_upcoming_cand).
    #     불확실하면 출시예정으로 표시하지 않는다(오탐이 로이 지적의 핵심이었다).

    updated = ''
    m = re.search(r'(\d{4})[.\-년]\s*(\d{1,2})[.\-월]\s*(\d{1,2})', page)
    if m:
        updated = '%s-%02d-%02d' % (m.group(1), int(m.group(2)), int(m.group(3)))

    # 관련/추천 앱 — Play 목록 페이지가 봇에겐 고정이라 후보가 매일 똑같다.
    # 상세에 노출된 다른 패키지로 후보 풀을 넓혀야 사전등록 앱을 만날 수 있다.
    rel, rseen = [], set()
    for m in re.finditer(r'"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+){2,})"', page):
        p = m.group(1)
        if p == pkg or p in rseen or len(p) > 64:
            continue
        if any(b in p.lower() for b in BAD_SEGMENTS):
            continue
        rseen.add(p)
        rel.append(p)
        # 앞쪽은 시드와 같은 고정 추천 블록이라 40개까지 받아야 새 앱이 섞인다.
        if len(rel) >= 40:
            break

    return {
        'store': 'play', 'pkg': pkg, 'name': name or pkg, 'developer': dev,
        'description': long_desc or short, 'category': cat or 'App',
        'rating': rating, 'rating_count': rating_cnt, 'price': price,
        'free': price in ('0', '0.0', '0.00', ''), 'upcoming': upcoming,
        'image': img, 'updated': updated, 'related': rel,
        'url': 'https://play.google.com/store/apps/details?id=%s&hl=%s&gl=%s' % (pkg, hl, gl),
    }


# ---------------------------------------------------------------- 후보 수집

def collect_apple_all():
    """**모든 스토어프론트**의 Apple 신작 피드를 한 번에 모은다 (로이 지시 2026-10-04).

    이전엔 언어별로 자기 스토어프론트(us/kr/jp...)만 훑었다. 그러면 한국어 글은
    한국 스토어에 뜬 출시예정 앱만 볼 수 있었고, 그날 그 스토어에 출시예정이 없으면
    그냥 일반 신작을 발행했다 — "출시예정 우선순위"가 지켜지지 않은 원인.
    전 스토어프론트를 합치면 하루에 찾을 수 있는 출시예정 후보가 늘고,
    어느 스토어에서 발견됐든 **그 앱이 지원하는 모든 언어로** 발행할 수 있다.
    """
    pool, seen = [], set()
    for cc in APPLE_CC_POOL:
        got = 0
        for url in apple_feeds(cc):
            try:
                items = parse_apple_feed(get_json(url), cc)
            except Exception as ex:
                print('   [apple %s] 피드 실패: %s' % (cc, str(ex)[:50]))
                continue
            got += len(items)
            for it in items:
                if it['key'] in seen:
                    continue
                seen.add(it['key'])
                pool.append(it)
        # 빈 피드 스토어프론트(bd 등)는 이웃 스토어로 대체한다.
        if got == 0 and cc in APPLE_CC_FALLBACK:
            alt = APPLE_CC_FALLBACK[cc]
            alt_n = 0
            for url in apple_feeds(alt):
                try:
                    items = parse_apple_feed(get_json(url), alt)
                except Exception as ex:
                    print('   [apple %s→%s] 대체 피드 실패: %s' % (cc, alt, str(ex)[:40]))
                    continue
                alt_n += len(items)
                for it in items:
                    if it['key'] in seen:
                        continue
                    seen.add(it['key'])
                    pool.append(it)
            print('   [apple %s] 피드 0개 → %s 로 대체 (%d개)' % (cc, alt, alt_n))
    print('   Apple 전 스토어프론트 %d곳 → 후보 %d개' % (len(APPLE_CC_POOL), len(pool)))
    return pool


def collect(cc, hl, gl, use_play=True, apple_pool=None):
    """언어권 1개의 후보 풀. apple_pool 이 오면 이미 모아둔 전 스토어 Apple 후보를 쓴다."""
    pool = []
    if apple_pool is None:
        got = 0
        for url in apple_feeds(cc):
            try:
                items = parse_apple_feed(get_json(url), cc)
                got += len(items)
                pool.extend(items)
            except Exception as ex:
                print('   [apple %s] 피드 실패: %s' % (cc, str(ex)[:50]))
        if got == 0 and cc in APPLE_CC_FALLBACK:
            alt = APPLE_CC_FALLBACK[cc]
            for url in apple_feeds(alt):
                try:
                    pool.extend(parse_apple_feed(get_json(url), alt))
                except Exception as ex:
                    print('   [apple %s→%s] 대체 피드 실패: %s' % (cc, alt, str(ex)[:40]))
    else:
        pool.extend(apple_pool)
    if use_play:
        got_any = False
        for cand in ([gl] + ([PLAY_GL_FALLBACK[gl]] if gl in PLAY_GL_FALLBACK else [])):
            if got_any:
                break
            for url, kind, hint in play_collections(hl, cand):
                try:
                    ids = play_pkg_ids(get_text(url))
                except Exception as ex:
                    print('   [play %s] 수집 실패: %s' % (cand, str(ex)[:50]))
                    continue
                if not ids:
                    continue
                got_any = True
                for pkg in ids:
                    # hint == 'upcoming' → 사전등록 컬렉션에서 왔다는 것 자체가 출처 기반 증거.
                    # 상세 페이지를 뒤져 추측하지 않는다(그 방식은 오탐으로 폐기).
                    item = {
                        'store': 'play', 'key': 'play:%s' % pkg, 'ident': pkg,
                        'name': pkg, 'category': kind, 'released': None,
                        'url': ('https://play.google.com/store/apps/details?id=%s&hl=%s&gl=%s'
                                % (pkg, hl, cand)),
                    }
                    if hint:
                        item['upcoming'] = True
                        item['_src'] = hint
                    pool.append(item)
                time.sleep(0.4)   # 연속 요청 시 429 가 났다(실측 2026-10-04)
    uniq, seen = [], set()
    for it in pool:
        if it['key'] in seen:
            continue
        seen.add(it['key'])
        uniq.append(it)
    return uniq


# Play 사전등록 판별 캐시 — 사전등록 여부는 언어와 무관하므로 pkg 기준으로 한 번만 조회한다.
_PLAY_UP_CACHE = {}


def play_upcoming(pkg):
    """Play 앱의 사전등록(출시 예정) 여부 — 언어 무관 전역 캐시."""
    if pkg in _PLAY_UP_CACHE:
        return _PLAY_UP_CACHE[pkg]
    try:
        v = bool(play_details(pkg, 'en', 'us').get('upcoming'))
    except Exception:
        v = False
    _PLAY_UP_CACHE[pkg] = v
    return v


def expand_play(cands, hl, gl, seed_limit=6, rel_limit=28):
    """Play 후보 풀을 넓힌다.

    Play 목록 페이지는 봇에겐 컬렉션·국가·검색어와 무관하게 **같은 고정 목록**을 준다
    (2026-10-03 실측: 7개 국가·4종 URL 전부 동일 22개).
    그래서 매일 같은 유명 게임만 후보가 되고 사전등록 앱은 영원히 안 잡힌다.
    상세 페이지에 노출된 관련 앱으로 시드를 넓혀 다양한 후보를 확보한다.

    ⚠️ 2026-10-04 실측: 이 함수가 hl/gl 을 'en'/'us' 로 하드코딩해 호출해서
       13개 언어가 완전히 같은 관련앱 체인을 따라갔다 → 후보 풀이 동일(md5 일치)
       → 13개 언어가 전부 같은 사전등록 앱(ANANTA)을 뽑는 사고로 이어졌다.
       반드시 그 언어의 hl/gl 로 조회해 언어별 후보가 갈라지게 한다.
    """
    seeds = [c for c in cands if c.get('store') == 'play'][:seed_limit]
    if not seeds:
        return cands
    have = {c['ident'] for c in cands if c.get('store') == 'play'}
    added = []
    for s in seeds:
        # 언어별로 다른 리스팅을 받아야 관련앱도 달라진다 → hl/gl 은 인자값 그대로.
        d = play_details(s['ident'], hl, gl) or {}
        for p in (d.get('related') or [])[:rel_limit]:
            if p in have:
                continue
            have.add(p)
            added.append({'store': 'play', 'key': 'play:%s' % p, 'ident': p, 'name': p,
                          'category': 'App', 'released': None,
                          'url': 'https://play.google.com/store/apps/details?id=%s&hl=%s&gl=%s'
                                 % (p, hl, gl)})
    if added:
        print('   Play 관련 앱으로 후보 +%d개 (총 %d)' % (len(added), len(cands) + len(added)))
    return cands + added


def mark_upcoming(cands):
    """Play 후보의 사전등록 여부를 확정한다.

    collect() 는 Play 후보를 상세 조회 없이 넣기 때문에 released 가 None 이다.
    그대로 두면 rank 의 is_upcoming 이 Play 후보를 영원히 False 로 처리해서
    '출시 예정' 큐가 비고 신작만 발행된다 (로이 지적 2026-10-03).

    판정 우선순위 (2026-10-04 개편 — 로이 제안 "소스에서 먼저 뽑는다"):
      1) 출처 기반: 사전등록 전용 컬렉션에서 수집된 후보('_src' 플래그)는 확정 True.
         상세 페이지를 뒤져 추측하지 않는다 — 그 방식은 오탐 때문에 폐기했다.
      2) 그 외에는 상세의 명시적 마커(play_upcoming)만 참고한다.
    """
    hit = 0
    for c in cands:
        if c.get('store') != 'play':
            continue
        if c.get('upcoming') and c.get('_src'):
            hit += 1
            continue          # 출처가 이미 증명 → 덮어쓰지 않는다
        c['upcoming'] = play_upcoming(c['ident'])
        hit += 1 if c['upcoming'] else 0
    print('   Play 사전등록 확인 %d개 중 %d개 출시예정' % (
        sum(1 for c in cands if c.get('store') == 'play'), hit))
    return cands


def is_upcoming_cand(c, today):
    """출시예정 판정 — '미래 출시일이 확인된 경우에만' True.

    날짜가 없거나 오늘/과거면 절대 출시예정이 아니다. (2026-10-04 로이 지적:
    출시일이 지났는데도 '출시 예정' 딱지가 붙어 있는 글이 발견됨)
    """
    if c.get('store') == 'play':
        return bool(c.get('upcoming'))
    rel = c.get('released')
    return bool(rel and rel.replace(tzinfo=None) > today)


def load_upcoming_backlog(path, today_str):
    """저장해 둔 출시예정 후보를 읽는다. 출시일이 지나간 것은 버린다.

    왜 필요한가: Apple 신작 피드는 롤링 윈도라 **출시예정 앱이 몇 시간만 지나면
    피드에서 사라진다**(2026-10-04 실측: 08시에 있던 후보가 같은 날 다시 조회하니 없음).
    발견한 순간 저장해 두면 그 뒤 며칠간 계속 '출시예정 1순위'로 쓸 수 있다.
    """
    obj = load_json(path, {})
    items = obj.get('items') or []
    out = []
    for it in items:
        rd = (it.get('releaseDate') or '')[:10]
        if not rd or rd <= today_str:
            continue      # 이미 출시됐거나 날짜 불명 → 폐기
        out.append(it)
    return out


def save_upcoming_backlog(path, items):
    save_json(path, {'updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                     'items': items})


def backlog_to_cands(items):
    """백로그 항목 → 후보 dict (순위 계산에 바로 넣을 수 있는 형태)."""
    out = []
    for it in items:
        out.append({
            'store': it.get('store', 'apple'), 'key': it['key'], 'ident': it['ident'],
            'name': it.get('name') or it['ident'],
            'category': it.get('category') or '',
            'released': parse_iso(it.get('releaseDate')),
            'url': it.get('url', ''), '_backlog': True,
        })
    return out


def candidates_from_seeds(seeds_path, today):
    """시드 파일에 적어둔 출시예정 게임·앱을 실제 후보로 만든다.

    왜 필요한가: Apple 신작 RSS 피드엔 **출시 예정(사전예약) 앱이 안 들어있다** —
    'new applications'는 말 그대로 '막 출시된' 앱만 나열한다(로이 지적 2026-10-04:
    무한대/아난타, 바람의흔적이 피드에 없어 못 찾았다). 그래서 로이가 팁을 주거나
    우리가 검색으로 찾은 출시예정 게임을 시드로 두고, 매 실행마다 상세에서
    '아직 출시 전인지' 확인해 후보로 넣는다. 출시일이 지나면 자동으로 후보에서 빠진다.
    """
    cfg = load_json(seeds_path, {})
    games = cfg.get('games') or []
    out = []
    for g in games:
        aid = str(g.get('apple_id') or '').strip()
        acc = (g.get('apple_cc') or 'us').strip()
        # 같은 게임의 Apple 출시일이 미래인지 = 신뢰 가능한 유일한 출시예정 신호.
        # (Play 웹페이지로는 사전등록 판별 불가 — 2026-10-04 실측)
        apple_up = False
        rel = None
        if aid:
            d = apple_lookup(aid, acc)
            rel = parse_iso(d.get('releaseDate')) if d else None
            apple_up = bool(rel and rel.replace(tzinfo=None) > today)
            if apple_up:
                # 다른 언어 cc 로 조회할 때 매번 HTTP 안 가도록 캐시를 복사해 둔다
                for cc in APPLE_CC_POOL:
                    _CACHE.setdefault('apple:%s:%s' % (aid, cc), d)
                out.append({
                    'store': 'apple', 'key': 'apple:%s' % aid, 'ident': aid,
                    'name': d.get('trackName') or g.get('name') or aid,
                    'category': d.get('primaryGenreName') or '',
                    'released': rel, 'game': ('yes' if d.get('primaryGenreName') == 'Games' else 'no'),
                    'url': 'https://apps.apple.com/%s/app/id%s' % (acc, aid),
                    '_seed': True,
                })
        pkg = str(g.get('play_pkg') or '').strip()
        if pkg and pkg.count('.') >= 2:
            # 같은 게임이므로 Apple 이 미래 출시면 Play 판도 출시예정이다.
            # (Play 자체 판정은 못 믿지만, Apple 로 확인된 게임은 확실하다)
            if apple_up or (not aid and (play_details(pkg, 'en', 'us') or {}).get('upcoming')):
                pd = play_details(pkg, 'en', 'us') or {}
                out.append({
                    'store': 'play', 'key': 'play:%s' % pkg, 'ident': pkg,
                    'name': pd.get('name') or g.get('name') or pkg,
                    'category': pd.get('category') or '',
                    'released': rel, 'upcoming': True,
                    'game': ('yes' if (pd.get('category') or '').upper().startswith('GAME') else 'no'),
                    'url': 'https://play.google.com/store/apps/details?id=%s' % pkg,
                    '_seed': True,
                })
    return out


def apple_search_upcoming(terms, today):
    """Apple 검색 API 로 출시예정 앱을 자동 발굴한다.

    시드의 search_terms(게임명·'사전예약' 등)를 각 스토어프론트에서 검색 →
    releaseDate 가 미래인 결과만 모은다. Apple 검색은 사전예약 앱도 반환한다
    (실측 2026-10-04: 'ananta' 검색으로 무한대 2027-01-15 가 잡힌다).
    """
    out, seen = [], set()
    for cc in APPLE_CC_POOL:
        for term in terms:
            try:
                u = ('https://itunes.apple.com/search?term=%s&country=%s&entity=software&limit=25'
                     % (urllib.parse.quote(term), cc))
                r = get_json(u)
            except Exception:
                continue
            for x in (r.get('results') or []):
                tid = str(x.get('trackId') or '')
                if not tid or tid in seen:
                    continue
                rel = parse_iso(x.get('releaseDate'))
                if not (rel and rel.replace(tzinfo=None) > today):
                    continue
                seen.add(tid)
                for c in APPLE_CC_POOL:
                    _CACHE.setdefault('apple:%s:%s' % (tid, c), x)
                out.append({
                    'store': 'apple', 'key': 'apple:%s' % tid, 'ident': tid,
                    'name': x.get('trackName') or tid,
                    'category': x.get('primaryGenreName') or '',
                    'released': rel, 'url': x.get('trackViewUrl') or '',
                    'game': ('yes' if x.get('primaryGenreName') == 'Games' else 'no'),
                    '_search': True,
                })
    return out


def merge_upcoming_backlog(path, cands, today, today_str):
    """이번 실행에서 발견한 출시예정 후보를 백로그에 합쳐 저장한다."""
    backlog = load_upcoming_backlog(path, today_str)
    by_key = {b['key']: b for b in backlog}
    for c in cands:
        if not is_upcoming_cand(c, today):
            continue
        rel = c.get('released')
        by_key[c['key']] = {
            'key': c['key'], 'store': c['store'], 'ident': c['ident'],
            'name': c.get('name') or c['ident'],
            'category': c.get('category') or '',
            'releaseDate': rel.strftime('%Y-%m-%d') if rel else today_str,
            'url': c.get('url', ''),
        }
    items = sorted(by_key.values(), key=lambda x: x['releaseDate'])
    save_upcoming_backlog(path, items)
    return items


def refresh_upcoming_flags(base, today_str):
    """발행된 글의 '출시 예정' 딱지를 매일 다시 검사한다.

    발행 시점엔 미래였던 출시일이 시간이 지나면 지나간 날짜가 된다.
    그대로 두면 이미 출시된 앱에 '출시 예정' 이 붙어 떠다닌다(로이 지적 2026-10-04).
    → 출시일이 오늘보다 같거나 앞서면 upcoming 을 false 로 되돌린다.

    ⚠️ 2026-10-05 실측: Play 글의 releaseDate 는 출시일이 아니라 **최종 업데이트
      날짜**가 박힌다(상세의 LAST UPDATE). 그래서 사전등록 컬렉션 출신 글
      (예: 어바타)이 과거 날짜 때문에 배지를 잃었다.
      → **Apple 글만** 날짜로 재판정한다. Play 글은 사전등록 컬렉션에서 수집한
        출처(_src) 기반이므로 날짜로 해제하지 않는다 — 컬렉션에서 내려간 것이
        진짜 출시 신호다(발행 시점에 컬렉션에 있었는지는 frontmatter 로 알 수 없어
        보수적으로 유지한다. 잘못 유지되는 비용 < 잘못 해제되는 비용).
    """
    game_dir = os.path.join(base, 'content', 'game')
    fixed, kept, nodate, skipped = [], 0, 0, 0
    for path in sorted(glob.glob(os.path.join(game_dir, '*', '*.md'))):
        try:
            raw = open(path, encoding='utf-8').read()
        except Exception:
            continue
        m = re.match(r'^---\s*\n(.*?)\n---', raw, re.S)
        if not m:
            continue
        head = m.group(1)
        fmv = {}
        for line in head.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                fmv[k.strip()] = v.strip().strip('"')
        if str(fmv.get('upcoming', '')).lower() not in ('1', 'true', 'yes', 'y'):
            continue
        src = (fmv.get('sourceUrl') or '')
        if 'play.google.com' in src:
            skipped += 1    # Play 글은 출시예정이 출처 기반이므로 날짜로 해제하지 않는다
            continue
        rel = (fmv.get('releaseDate') or '').strip()[:10]
        if not rel:
            nodate += 1
            continue
        if rel <= today_str:
            new_head, n = re.subn(r'(?m)^upcoming:\s*.*$', 'upcoming: false', head)
            if n:
                open(path, 'w', encoding='utf-8').write(
                    raw[:m.start(1)] + new_head + raw[m.end(1):])
                fixed.append((os.path.basename(path), rel))
        else:
            kept += 1
    print('[refresh] 출시예정 재검사: 유지 %d건 / 출시일 경과로 해제 %d건 / '
          '날짜없음 %d건 / Play(출처기반) 유지 %d건'
          % (kept, len(fixed), nodate, skipped))
    for fn, rel in fixed:
        print('   → 해제: %s (출시일 %s ≤ 오늘 %s)' % (fn, rel, today_str))
    return len(fixed)


def mark_games(cands, cc):
    """게임 여부를 후보마다 확정한다.
    Apple 은 lookup 의 영문 primaryGenreName(스토어프론트와 무관하게 영어로 온다)으로,
    조회 실패 시에만 현지어 카테고리 라벨로 판정한다."""
    for c in cands:
        if c.get('store') == 'apple':
            d = apple_detail_cached(c['ident'], cc)
            if d.get('primaryGenreName'):
                c['game'] = 'yes' if d['primaryGenreName'] == 'Games' else 'no'
                continue
        if c.get('store') == 'play':
            # Play 컬렉션 라벨은 믿을 수 없다 — 게임 컬렉션에도 같은 고정 페이지가 와서
            # 'App' 으로 들어온다. 상세의 applicationCategory 로 판정한다.
            d = play_details(c['ident'], 'en', 'us') or {}
            cat = d.get('category') or ''
            if cat:
                c['game'] = 'yes' if (cat.upper().startswith('GAME')
                                      or GAME_WORD_RE.search(cat)) else 'no'
                continue
        c['game'] = 'yes' if GAME_WORD_RE.search(c.get('category') or '') else 'no'
    return cands


def rank_candidates(cands, seen, pub_urls, pub_ids, today, rotate_idx=0):
    """로이 확정 우선순위 (2026-10-03 개정) — 고정 순서, 회전 없음:
       1 출시예정 게임(Play) → 2 출시예정 앱(Play) → 3 출시예정 게임(Apple)
       → 4 신작 게임(Play) → 5 신작 게임(Apple) → 6 신작 앱(Play) → 7 신작 앱(Apple)
       → (최후 비상용) 출시예정 앱(Apple)

    로이 지시 2026-10-03: "앱스토어 출시예정 말고 플레이스토어 출시 예정 게임/앱을 제일 최우선".
    Play 앱은 사전등록(PreOrder) 상태를 상세에서 확인한 플래그로, Apple 앱은
    출시일이 미래인지로 판정한다.

    우선순위 전체를 '순서대로 늘어놓은 리스트'로 돌려준다. 호출부가 앞에서부터
    하나씩 지원 언어를 검증해, 그 언어를 실제로 지원하는 첫 후보를 발행한다.

    rotate_idx (2026-10-04 추가): 각 우선순위 티어 안에서 시작 위치를 밀어주는 값.
      없으면 모든 언어가 같은 티어에서 **똑같은 1위 후보**(예: 유일한 출시예정 Play
      게임 ANANTA)를 집어 대서 13개 언어가 전부 같은 글을 발행했다(실측).
      언어별로 i 번째부터 훑게 해서 서로 다른 출시예정 앱을 나눠 갖게 한다.
    """
    fresh = [c for c in cands
             if c['key'] not in seen and c['key'] not in pub_ids and c['url'] not in pub_urls]
    if not fresh:
        return []

    def is_game(c):
        return c.get('game') == 'yes'

    def is_play(c):
        return c.get('store') == 'play'

    def is_upcoming(c):
        # Play = 상세에서 확인한 사전등록 플래그 / Apple = 출시일이 미래인지
        return is_upcoming_cand(c, today)

    play_game_up = [c for c in fresh if is_play(c) and is_game(c) and is_upcoming(c)]
    play_app_up = [c for c in fresh if is_play(c) and not is_game(c) and is_upcoming(c)]
    apple_game_up = [c for c in fresh if not is_play(c) and is_game(c) and is_upcoming(c)]
    play_game_new = [c for c in fresh if is_play(c) and is_game(c) and not is_upcoming(c)]
    apple_game_new = [c for c in fresh if not is_play(c) and is_game(c) and not is_upcoming(c)]
    play_app_new = [c for c in fresh if is_play(c) and not is_game(c) and not is_upcoming(c)]
    apple_app_new = [c for c in fresh if not is_play(c) and not is_game(c)
                     and not is_upcoming(c)]
    apple_app_up = [c for c in fresh if not is_play(c) and not is_game(c) and is_upcoming(c)]

    def rel_key(c):
        return c.get('released') or datetime.datetime.min

    def rotate(lst, k):
        """같은 티어 안에서 시작점을 k 만큼 민다 → 언어마다 다른 후보를 먼저 만난다."""
        if not lst:
            return lst
        k %= len(lst)
        return lst[k:] + lst[:k]

    out = []
    for q, rev, tier in ((play_game_up, False, '출시예정게임(Play)'),
                         (play_app_up, False, '출시예정앱(Play)'),
                         (apple_game_up, False, '출시예정게임(Apple)'),
                         (play_game_new, True, '신작게임(Play)'),
                         (apple_game_new, True, '신작게임(Apple)'),
                         (play_app_new, True, '신작앱(Play)'),
                         (apple_app_new, True, '신작앱(Apple)'),
                         (apple_app_up, False, '출시예정앱(비상)')):
        # 출시예정은 임박순(오름차순), 나머지는 최신순(내림차순)
        # 정렬 후에 회전해야 언어별로 "그 티어의 i번째"가 일관되게 달라진다.
        for c in rotate(sorted(q, key=rel_key, reverse=rev), rotate_idx):
            c['_tier'] = tier
            out.append(c)
    return out


def pick_one(cands, seen, pub_urls, pub_ids, today, rotate_idx=None):
    """하위 호환용 — 우선순위 1위 후보만 돌려준다."""
    r = rank_candidates(cands, seen, pub_urls, pub_ids, today)
    return r[0] if r else None


# ---------------------------------------------------------------- 지원 언어 검증

def apple_lang_ok(detail, lang):
    """Apple 스토어가 표기한 지원 언어(languageCodesISO2A)에 lang 이 있는지.
    반환 (True/False/None, 이유) — None 은 '스토어가 언어 정보를 주지 않아 판정 보류'."""
    codes = detail.get('languageCodesISO2A') or []
    if not codes:
        return None, '언어 정보 미공개(보류)'
    want = APPLE_LANG_CODES.get(lang, {lang.upper()})
    ups = []
    for c in codes:
        u = str(c).upper().replace('_', '-')
        if u not in ups:
            ups.append(u)
    for u in ups:
        for w in want:
            if u == w or u.startswith(w + '-'):
                return True, '/'.join(ups[:14])
    return False, '지원언어=' + '/'.join(sorted(set(ups))[:14])


def play_lang_ok(desc_local, pkg, hl, gl):
    """구글플레이: hl 로 받은 리스팅이 그 언어로 실제 현지화돼 있는지."""
    base = (hl or 'en').split('-')[0].lower()
    if base == 'en':
        return None, '영어는 기본 리스팅(판정 불가)'
    if not desc_local:
        return False, '설명 없음'
    hint = SCRIPT_HINT.get(base)
    if hint and hint.search(desc_local):
        return True, '현지 문자 확인'
    eng = play_details(pkg, 'en', 'us')
    ed = re.sub(r'\s+', '', (eng or {}).get('description') or '')
    if not ed:
        return None, '영문 리스팅 조회 실패(보류)'
    if re.sub(r'\s+', '', desc_local)[:1000] == ed[:1000]:
        return False, '영문 리스팅과 동일(미현지화)'
    return True, '영문 리스팅과 상이'


def lang_gate(item, lang, cc, hl, gl):
    """후보가 이번에 발행하려는 언어를 실제로 지원하는지.
    (ok, 이유) — ok 가 False 면 그 언어로는 발행하지 않는다."""
    if item['store'] == 'apple':
        detail = apple_lookup(item['ident'], cc)
        if not detail:
            return False, '상세 조회 실패'
        return apple_lang_ok(detail, lang)
    d = play_details(item['ident'], hl, gl)
    if not d or not d.get('description'):
        return False, '상세 조회 실패'
    return play_lang_ok(d.get('description'), item['ident'], hl, gl)


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
            'SUPPORTED LANGUAGES: %s' % (', '.join(detail.get('languageCodesISO2A') or [])
                                         or 'not disclosed'),
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
    # ⚠️ 2026-10-04 실측: 여기서 play_details(hl, gl) 의 upcoming 값을 쓰면
    #   순위 계산(mark_upcoming → 언어무관 'en/us' 기준)과 결과가 어긋난다.
    #   실측 사례: Racing Master 가 '출시예정게임(Play)' 티어로 뽑혔는데 글은 released 로 발행.
    #   원인 = Install 버튼 유무가 hl/gl 마다 달라서 en/us 는 사전예약, 현지 스토어는 출시로 나옴.
    #   → 판정은 **정본 하나**로 통일하고, 현지 리스팅(hl/gl)은 본문 텍스트용으로만 쓴다.
    #
    # ⚠️ 정본이 2026-10-05 다시 바뀌었다(로이 제안 채택): 상세 페이지 추측은 아예 신뢰하지 않고
    #   **사전등록 전용 컬렉션에서 수집됐다는 출처(_src)** 를 최우선 근거로 쓴다.
    #   (실측: 컬렉션에서 왔는데도 play_upcoming() 이 False 라 released 로 발행되는 사고 발생)
    if item.get('upcoming') and item.get('_src'):
        upcoming = True      # 출처가 증명 — 상세 페이지를 다시 물어보지 않는다
    else:
        upcoming = bool(play_upcoming(item['ident']))
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


SYSTEM_APP = """You are a native-level writer for the apps and games section of an international website.
You write in the target language the way a local writer for that market would, never like a translation.
Your tone is plain, concrete and free of hype.

FACT DISCIPLINE (highest priority): every price, date, feature, platform, device, region, rating, download count and sales figure must come only
from the source material you are given. If the source material does not contain a fact, say so honestly in one short sentence instead of filling the gap.
You never claim to have played, tested, reviewed or measured the product.

NAME DISCIPLINE (SEO, very high priority): the product name is the single most important search term.
Use the exact official name given in the material — verbatim, in its original script — in the title, the description, the opening section,
the verdict block, and naturally at least 5 more times across the body sections (aim for 8+ mentions total in a normal article).
For games, never re-translate, abbreviate or rename the game title; if the store listing gives a localized name, use exactly that name every time.
Never replace the name with pronouns or generic words like "this app" when the name would fit naturally.

OUTPUT DISCIPLINE (second highest priority): you reply with one single raw JSON object and nothing else.
No markdown code fence, no commentary before or after, no trailing comma.
Inside string values, write every line break as \\n and escape every double quote, so the JSON can always be parsed by a machine."""


# 제목 글자 수 상한 — 로이 확정. 라틴/키릴 70자, 한국어·일본어·힌디어 55자, 중국어 42자.
TITLE_MAX = {'en': 70, 'ko': 55, 'ja': 55, 'zh': 42, 'hi': 55,
             'es': 70, 'de': 70, 'fr': 70, 'pt': 70, 'ru': 70, 'id': 70, 'ar': 70}
# 출시 완료작은 후기 의도어를 붙이므로 조금 더 허용
RELEASED_MAX = {'en': 85, 'ko': 60, 'ja': 60, 'zh': 45, 'hi': 65}
TITLE_DEFAULT = 70

# 제목 초과 시 쓰는 안전 템플릿 — {app} 만 앱 이름으로 치환한다.
# 앱 이름이 30자 안팎이어도 언어별 상한을 넘지 않도록 접미사를 짧게 유지한다.
TITLE_LOCALIZED = {
    'en': '{app} — Price, Features and Who Should Skip',
    'ko': '{app} — 살 만한가요? 가격·기능·건너뛸 사람',
    'ja': '{app} — 買う価値はある？価格・機能・向かない人',
    'zh': '{app} — 值得买吗？价格、功能与不适合的人',
    'es': '{app} — precio, funciones y a quién no',
    'de': '{app} — Preis, Funktionen, für wen nicht',
    'fr': '{app} — prix, fonctions et qui doit éviter',
    'pt': '{app} — preço, funções e quem deve evitar',
    'ru': '{app} — цена, функции и кому не стоит',
    'id': '{app} — harga, fitur dan siapa yang tidak',
    'ar': '{app} — السعر، الميزات، ومن يتجنبها',
    'bn': '{app} — দাম, ফিচার এবং কারা না',
    'hi': '{app} — कीमत, फीचर्स और किन्हें नहीं',
}

# 언어 불일치 감지용 문자 체계 — 모델이 소스(스토어 설명) 언어를 따라가
# 엉뚱한 언어로 글을 쓰는 것을 막는다. (예: pt 섹션에 스페인어 글)
SCRIPT_RANGES = {
    'ko': [(0xAC00, 0xD7A3), (0x1100, 0x11FF)],
    'ja': [(0x3040, 0x30FF)],
    'zh': [(0x4E00, 0x9FFF)],
    'ru': [(0x0400, 0x04FF)],
    'ar': [(0x0600, 0x06FF)],
    'hi': [(0x0900, 0x097F)],
    'bn': [(0x0980, 0x09FF)],
}


# 라틴 문자 언어끼리는 문자 체계로 구분이 안 된다 → 기능어(stopword) 점수로 판정한다.
LATIN_MARKERS = {
    'en': ['the', 'and', 'with', 'this', 'for', 'that', 'you', 'are', 'price', 'features'],
    'es': ['el', 'la', 'los', 'las', 'que', 'para', 'con', 'precio', 'una', 'pero', 'más'],
    'pt': ['para', 'com', 'não', 'você', 'uma', 'preço', 'isso', 'está', 'mais', 'como'],
    'id': ['yang', 'tidak', 'dengan', 'untuk', 'ini', 'aplikasi', 'harga', 'ada', 'bisa'],
    'de': ['und', 'der', 'die', 'das', 'mit', 'für', 'nicht', 'ist', 'ein', 'sind', 'wird'],
    'fr': ['le', 'la', 'les', 'que', 'pour', 'avec', 'est', 'un', 'vous', 'dans', 'sont'],
    'it': ['il', 'la', 'che', 'per', 'con', 'non', 'una', 'sono', 'anche'],
}


# 제목 단축용 구분자 — 제목이 길 때 뒤쪽 절부터 떼어 정보량을 최대한 남긴다.
TITLE_TRIM_SEPS = [' — ', '—', ' · ', '、', '，', ', ', ',', ' | ']


def clip_body(body, limit):
    """상한을 넘는 본문을 문단 단위로 자른다 (로이 방침: 길게 늘이지 않는다).

    모델이 분량 지시를 무시하고 5000~9000자로 내보내는 일이 반복돼서 넣은 마지막 안전장치.
    문단 경계에서 자르므로 문장 중간이 잘리지는 않는다.
    """
    if len(body) <= limit:
        return body
    out, n = [], 0
    for para in body.split('\n\n'):
        if n + len(para) > limit:
            break
        out.append(para)
        n += len(para) + 2
    return '\n\n'.join(out).rstrip()


def trim_title(title, lim):
    """상한을 넘는 제목을 구분자 단위로 잘라 되돌린다. 실패하면 ''."""
    if len(title) <= lim:
        return title
    best = ''
    for sep in TITLE_TRIM_SEPS:
        parts = title.split(sep)
        while len(parts) > 1:
            parts = parts[:-1]
            cand = sep.join(parts).rstrip(' ,、，:：-—·')
            if len(cand) <= lim and len(cand) > len(best):
                best = cand
    if not best and ' ' in title:
        parts = title.split(' ')
        while len(parts) > 1:
            parts = parts[:-1]
            cand = ' '.join(parts).rstrip(' ,-—·')
            if len(cand) <= lim and len(cand) >= lim * 0.6:
                best = cand
                break
    return best


def latin_lang_score(text):
    low = (text or '').lower()
    return {k: sum(len(re.findall(r'\b%s\b' % re.escape(w), low)) for w in ws)
            for k, ws in LATIN_MARKERS.items()}


def script_ok(lang, text):
    """생성된 글이 대상 언어로 쓰였는지 확인한다.

    비라틴(한/일/중/러/아/힌/벵)은 문자 체계로, 라틴 계열은 기능어 점수로 판정한다.
    모델이 스토어 설명의 언어를 그대로 따라가 옆 나라 언어로 써버리는 것을 막는다.
    """
    letters = [c for c in (text or '') if c.isalpha()]
    if len(letters) < 100:
        return True
    hit = lambda rng: sum(1 for c in letters
                          if any(lo <= ord(c) <= hi for lo, hi in rng))
    own = SCRIPT_RANGES.get(lang)
    if own:
        if hit(own) / len(letters) < 0.15:
            return False
        # 중국어 글에 일본어 가나가 섞이면 일본어다
        if lang == 'zh' and hit([(0x3040, 0x30FF)]) / len(letters) > 0.02:
            return False
        return True
    # 라틴 계열: 다른 문자 체계가 섞이면 실패
    foreign = sum(hit(r) for r in SCRIPT_RANGES.values())
    if foreign / len(letters) >= 0.10:
        return False
    score = latin_lang_score(text)
    best = max(score, key=score.get)
    # 판정 언어가 1위가 아니거나, 기능어가 거의 안 보이면 의심
    return best == lang and score.get(lang, 0) >= 5

# FAQ 3문항 — 반드시 이 언어 표기를 그대로 쓴다 (로이 확정).
# 출시 전: 언제 출시 / 무료 여부 / 어떻게 예약
# 출시 후: 어떤 앱 / 무료 여부 / 어떻게 예약
FAQ_LOCALIZED = {
    'en': {'pre': ['When does it come out?', 'Is it free?', 'How do I pre-order it?'],
           'post': ['What kind of app is it?', 'Is it free?', 'How do I pre-order it?']},
    'ko': {'pre': ['언제 출시되나요?', '무료인가요?', '어떻게 예약하나요?'],
           'post': ['어떤 앱인가요?', '무료인가요?', '어떻게 예약하나요?']},
    'ja': {'pre': ['いつ配信されますか？', '無料ですか？', 'どうやって予約しますか？'],
           'post': ['どんなアプリですか？', '無料ですか？', 'どうやって予約しますか？']},
    'zh': {'pre': ['什么时候上线？', '免费吗？', '怎么预约？'],
           'post': ['这是一款什么应用？', '免费吗？', '怎么预约？']},
    'hi': {'pre': ['यह कब लॉन्च होगा?', 'क्या यह फ्री है?', 'इसे कैसे बुक करें?'],
           'post': ['यह कैसा ऐप है?', 'क्या यह फ्री है?', 'इसे कैसे बुक करें?']},
    'es': {'pre': ['¿Cuándo se lanza?', '¿Es gratis?', '¿Cómo se reserva?'],
           'post': ['¿Qué tipo de app es?', '¿Es gratis?', '¿Cómo se reserva?']},
    'de': {'pre': ['Wann erscheint es?', 'Ist es kostenlos?', 'Wie kann ich es vorbestellen?'],
           'post': ['Was für eine App ist das?', 'Ist es kostenlos?', 'Wie kann ich es vorbestellen?']},
    'fr': {'pre': ['Quand sort-il ?', 'Est-ce gratuit ?', 'Comment le précommander ?'],
           'post': ['Quel type d’application est-ce ?', 'Est-ce gratuit ?', 'Comment le précommander ?']},
    'pt': {'pre': ['Quando será lançado?', 'É gratuito?', 'Como reservar?'],
           'post': ['Que tipo de app é?', 'É gratuito?', 'Como reservar?']},
    'ru': {'pre': ['Когда выйдет?', 'Это бесплатно?', 'Как оформить предзаказ?'],
           'post': ['Что это за приложение?', 'Это бесплатно?', 'Как оформить предзаказ?']},
    'id': {'pre': ['Kapan rilisnya?', 'Apakah gratis?', 'Bagaimana cara memesannya?'],
           'post': ['Aplikasi apa ini?', 'Apakah gratis?', 'Bagaimana cara memesannya?']},
    'ar': {'pre': ['متى يصدر؟', 'هل هو مجاني؟', 'كيف أحجزه مسبقًا؟'],
           'post': ['ما نوع هذا التطبيق؟', 'هل هو مجاني؟', 'كيف أحجزه مسبقًا؟']},
    'bn': {'pre': ['এটি কখন প্রকাশিত হবে?', 'এটি কি ফ্রি?', 'কীভাবে প্রি-অর্ডার করব?'],
           'post': ['এটি কী ধরনের অ্যাপ?', 'এটি কি ফ্রি?', 'কীভাবে প্রি-অর্ডার করব?']},
}
FAQ_DEFAULT = FAQ_LOCALIZED['en']

LANG_NATIVE = {'en': 'English', 'ko': 'Korean', 'ja': 'Japanese', 'zh': 'Chinese',
               'hi': 'Hindi', 'es': 'Spanish', 'de': 'German', 'fr': 'French',
               'pt': 'Portuguese', 'ru': 'Russian', 'id': 'Indonesian', 'ar': 'Arabic'}

SEO_DESC_MIN = 300   # 로이 지시: SEO 설명은 300자 이상
SEO_DESC_MAX = 480   # 상한(한/일/중 320자 내외, 영문 320자 권장)

# 재시도 시 붙이는 보강 지시 — 분량 미달을 "군더더기"로 채우지 않게 막는다.
RETRY_HINT = (
    "Your previous draft was rejected. Fix it without inventing anything: "
    "(a) if it was too short, add one concrete sentence drawn from the SOURCE MATERIAL to each "
    "H2 section instead of padding with generalities; "
    "(b) if it was too long, delete the least specific sentence in each section; "
    "(c) if the title was over the limit, shorten the last phrase only and keep the app name; "
    "(d) never use the words review, hands-on, tested or played."
)


def title_max(lang, released=False):
    """제목 글자 수 상한 — 초과 글이 버려지므로 생성 단계에서 다시 시도시킨다."""
    base = TITLE_MAX.get(lang, TITLE_DEFAULT)
    if released:
        base = max(base, RELEASED_MAX.get(lang, base))
    return base


# 앱 vs 게임 분류 — 스토어 장르 문자열이 여러 언어로 오므로 다 잡는다.
# Apple: "Games, Adventure" / "Spiele, Rollenspiel" / "游戏, 模拟" ...
# Play : applicationCategory "GAME_STRATEGY" 같은 GAME_ 접두.
GAME_KIND_PAT = re.compile(
    r'(^|\s|/|，|、)(games?|spiele|juegos|jeux|jogos|игры)'
    r'|(^|\s)(游戏|ゲーム|게임)'
    r'|GAME_[A-Z]+', re.I)


def kind_of(genre, category=''):
    """"game" or "app" — 앱/게임 분류. 근거는 스토어가 준 장르 문자열뿐."""
    s = '%s %s' % (genre or '', category or '')
    return 'game' if GAME_KIND_PAT.search(s) else 'app'


def strip_code_fences(text):
    """모델이 코드펜스(```json ... ```)로 감싸 보내도 JSON 만 남긴다."""
    t = (text or '').strip()
    if t.startswith('```'):
        t = re.sub(r'^```[a-zA-Z]*\s*', '', t)
        t = re.sub(r'```\s*$', '', t).strip()
    return t


def released_facts(item, cc, hl, gl):
    """출시 완료작 전용 자료 — 후기·평점·설치수까지 포함한 실제 리뷰형 리포트용."""
    if item['store'] == 'apple':
        detail = apple_lookup(item['ident'], cc)
        if not detail:
            return None
        revs = apple_reviews(item['ident'], cc)
        rel = item['released'] or parse_iso(detail.get('releaseDate'))
        lines = [
            'APP NAME: %s' % (detail.get('trackName') or item['name']),
            'DEVELOPER: %s' % (detail.get('artistName') or item.get('artist', '')),
            'STORE CATEGORY: %s' % (detail.get('primaryGenreName') or item.get('category', '')),
            'GENRES: %s' % ', '.join(detail.get('genres') or []),
            'PRICE: %s' % (detail.get('formattedPrice') or 'not disclosed'),
            'IN-APP PURCHASES: %s' % ('yes' if detail.get('description')
                                      and 'In-App Purchase' in detail['description'] else 'not disclosed'),
            'VERSION: %s' % (detail.get('version') or 'not disclosed'),
            'RATING: %.1f out of 5 from %s ratings' % (float(detail.get('averageUserRating') or 0),
                                                       detail.get('userRatingCount') or 0),
            'CONTENT RATING: %s' % (detail.get('contentAdvisoryRating') or 'not disclosed'),
            'LAST UPDATED: %s' % (parse_iso(detail.get('currentVersionReleaseDate')).strftime('%Y-%m-%d')
                                  if parse_iso(detail.get('currentVersionReleaseDate')) else 'not disclosed'),
            'SUPPORTED DEVICES: %s' % ', '.join(detail.get('supportedDevices') or []) or 'not disclosed',
            'LANGUAGES: %s' % (detail.get('languageCodesISO2A') or 'not disclosed'),
            'STATUS: RELEASED (available now, release date %s)' % (
                rel.strftime('%Y-%m-%d') if rel else 'not disclosed'),
            'LISTING URL: %s' % (detail.get('trackViewUrl') or item['url']),
        ]
        if detail.get('releaseNotes'):
            lines += ['', "WHAT'S NEW: %s" % clip(detail['releaseNotes'], 400)]
        lines += ['', 'OFFICIAL DESCRIPTION:', clip(detail.get('description') or '', 2600)]
        if revs:
            lines += ['', 'USER REVIEWS FROM THE STORE LISTING '
                          '(opinions of real users, not our own testing):']
            for r in revs[:6]:
                lines.append('- %d/5 "%s": %s' % (int(float(r.get('rating') or 0)),
                                                  clip(r['title'], 90), clip(r['body'], 200)))
        return '\n'.join(lines)

    d = play_details(item['ident'], hl, gl)
    if not d or not d.get('description'):
        return None
    lines = [
        'APP NAME: %s' % d.get('name'),
        'DEVELOPER: %s' % (d.get('developer') or 'not disclosed'),
        'STORE CATEGORY: %s' % d.get('category'),
        'PRICE: %s' % ('Free' if d.get('free') else ('%s (paid)' % (d.get('price') or 'paid'))),
        'IN-APP PURCHASES: not disclosed',
        'RATING: %.1f out of 5 from %s ratings' % (float(d.get('rating') or 0),
                                                   d.get('rating_count') or 0),
        'INSTALL COUNT: %s' % (d.get('installs') or 'not disclosed'),
        'LAST UPDATED: %s' % (d.get('updated') or 'not disclosed'),
        'STATUS: RELEASED (available now)',
        'LISTING URL: %s' % d.get('url'),
    ]
    lines += ['', 'OFFICIAL DESCRIPTION:', clip(d.get('description') or '', 2600)]
    return '\n'.join(lines)


def user_prompt(lang, mat, app_name, lang_name, released, cmin, cmax, desc_min, desc_max,
                store_url):
    """출시예정(사전예약 가이드) / 출시완료(실제 후기형 리포트) 공통 프롬프트.

    공통 금지 규칙: 리뷰·테스트·체험 표현 금지, 허위 사실 금지, SEO 300자 이상 description 금지.
    """
    tmax = RELEASED_MAX.get(lang, TITLE_MAX.get(lang, TITLE_DEFAULT)) if released \
        else TITLE_MAX.get(lang, TITLE_DEFAULT)
    title_struct = (TITLE_LOCALIZED.get(lang, TITLE_LOCALIZED['en'])
                    .replace('{app}', app_name).replace('<APP NAME>', app_name))
    status = ('It is NOT available yet.' if not released
              else 'It is ALREADY released and downloadable.')
    report = ('BUY-OR-SKIP GUIDE for a title that is not released yet'
              if not released else
              'BUY-OR-SKIP REPORT for a title that is already released')
    # 섹션 라벨은 "무엇을 다루는지"만 전달한다 — 헤딩 문구 자체는 아래 HEADING LANGUAGE RULE 이
    # 대상 언어로 쓰게 한다. (영어 라벨을 그대로 노출하면 ko/zh/hi 글에 영어 헤딩이 박힌다)
    # 로이 지시 2026-10-03: FAQ(자주 묻는 질문) 섹션은 쓰지 않는다.
    # 앱 소개만 간결하게 — 길게 늘이지 말고 SEO에 걸릴 정도만.
    structure = ('"## what <APP NAME> is", "## release date and pre-order", "## key features", '
                 '"## who it is for and who should skip", "## is it worth pre-ordering", '
                 '"## pricing and what is still unclear"' if not released else
                 '"## what <APP NAME> is", "## key features", '
                 '"## who it is for and who should skip", "## pricing and availability", '
                 '"## what to expect"')
    angle = ("The title is NOT released yet. Frame it as an upcoming launch and say clearly that "
             "details may change before release. Be explicit that pre-ordering is free and "
             "reversible only if the store listing actually says so." if not released else
             "The title is already released and downloadable. You may discuss the experience, but "
             "ONLY by attributing it to the official store listing or to store reviewers, and "
             "never by claiming you used it.")
    extra = ("UPCOMING-SPECIFIC RULES\n"
             "- Never promise a release date the store has not confirmed, never promise pre-order "
             "rewards, and never write a review.\n\n" if not released else
             "RELEASED-SPECIFIC RULES\n"
             "- Use the confirmed facts in the material whenever they help the reader decide: "
             "version, rating and rating count, install count, content rating, last updated date, "
             "and what's new.\n"
             "- Treat store reviews as user opinions: attribute them with the natural wording of "
             + lang_name + " and never average anything yourself.\n"
             "- [what to expect] describes what the official listing suggests about the "
             "experience, always framed as expectation, never as something you tried or "
             "measured.\n\n")
    # FAQ 는 쓰지 않는다 (로이 지시 2026-10-03) — faq 관련 변수는 의도적으로 만들지 않는다.

    return (
        "You are writing one " + report + ".\n\n"
        "THE ANGLE YOU ARE WRITING\n"
        "The reader has just discovered this app or game and wants one thing: a practical "
        "buy-or-skip answer they can act on now. They are not looking for news, and they are not "
        "looking for a full review, because nobody has played or tested this yet. Your job is to "
        "gather only what the official store listing actually confirms, lay it out clearly, and "
        "tell them whether it is worth reserving, downloading or skipping, while being honest "
        "about what is still unknown.\n\n"
        "THE READER'S QUESTIONS (answer all of these across the article)\n"
        "1. What is this?\n"
        "2. What does it cost?\n"
        "3. When can I get it?\n"
        "4. Is it worth pre-ordering (or downloading) now?\n"
        "5. What will I actually get for my money?\n"
        "6. Who should skip this?\n"
        "7. What is the single most important flaw or limitation?\n"
        "8. Is there a better alternative?\n"
        "9. What is the catch?\n"
        "10. What do I do next?\n\n"
        "LANGUAGE: write natively in " + lang_name + ". You MUST use the exact words from the "
        "localizations in this prompt. They are the words real users of " + lang_name + " type "
        "into a search box. Do not translate them, do not paraphrase them, and do not invent your "
        "own variant. If a heading word appears in the localization table, use it exactly as "
        "written.\n\n"
        "HEADING LANGUAGE RULE (mandatory, most-violated rule):\n"
        "- EVERY \"##\" section heading MUST be written in "
        + lang_name + ". NEVER leave an English heading in the article.\n"
        "- The section list below uses plain English labels only to tell you what each section "
        "covers. Keep the meaning, translate the wording into natural " + lang_name + " the way a "
        "local publication in that market would headline it.\n"
        "- Example: the label \"## key features\" must become \"## 주요 기능\" in Korean, "
        "\"## 主な機能\" in Japanese, \"## 主要功能\" in Chinese, \"## Características "
        "principales\" in Spanish — never \"## Key Features\".\n"
        "- Do NOT add a FAQ / 자주 묻는 질문 / よくある質問 section. It is filler and is rejected.\n"
        "- The only thing that may stay in its original script is the app/game name itself.\n\n"
        "STATUS: " + status + "\n"
        "APP NAME: " + app_name + "\n"
        "OFFICIAL STORE URL: " + store_url + "\n\n"
        "NAME FREQUENCY RULE (SEO-critical): use the exact app/game name \"" + app_name + "\" "
        "verbatim throughout the article — title, description, opening, verdict block, and "
        "naturally in every major body section. Total mentions "
        "across the article: at least 8. Write \"" + app_name + "\" exactly as given, never "
        "abbreviated, never re-translated, never swapped for \"this app\" or similar.\n\n"
        "TITLE FORMULAS — choose ONE structure and fill it in. Do NOT invent your own structure.\n"
        "1. <APP NAME> — is it worth buying? Price, features and who should skip\n"
        "2. <APP NAME> — what you get for the price: features, limits and who should skip\n"
        "3. <APP NAME> — is it worth pre-ordering? Price, features and who should skip\n"
        "4. <APP NAME> — what you get for free: features, upgrade cost and who should skip\n"
        "5. <APP NAME> — is it worth switching? Price, features and who should skip\n"
        "6. <APP NAME> — what you get on day one: price, features and who should skip\n"
        "7. <APP NAME> — our honest verdict: is it worth buying? Price, features and who should skip\n"
        "8. <APP NAME> — is it worth downloading? Price, features and who should skip\n"
        "9. <APP NAME> — the honest catch: what you get, what it costs and who should skip\n\n"
        "LOCALIZED TITLE RULES\n"
        "Use the localized title structure provided in this prompt (or, if none is provided, "
        "translate formula 1 above by hand into " + lang_name + "). Keep the exact app name and "
        "the exact localized intent words — those words are the search terms real users of "
        + lang_name + " type, so they must appear verbatim. If the localized structure contains a "
        "question word, keep the question form. Do NOT insert any words that are not in the "
        "provided localization. Do NOT translate the app name. Do NOT add any other words. If the "
        "localized structure uses a bracketed app-name slot, put the app name there and nothing "
        "else.\n"
        "HARD LIMIT: the finished title MUST NOT exceed " + str(tmax) + " characters. Titles longer "
        "than this are rejected by the site builder and the article is thrown away. Shorten it "
        "yourself: drop the weakest of the three trailing phrases or shorten the ending. Keep the "
        "app name and the strongest intent word and the who-should-skip phrase whenever "
        "possible.\n\n"
        "LOCALIZED TITLE STRUCTURE FOR " + lang_name + ": " + title_struct + "\n\n"
        "DESCRIPTION\n"
        "One or two sentences, at least " + str(desc_min) + " characters and no more than "
        + str(desc_max) + " characters, that a searcher would read as a direct answer: name the "
        "app, say what it is, state the price model, and state who should skip.\n\n"
        "STRUCTURE (write them in this order, and write them in " + lang_name + "):\n"
        + structure + "\n\n"
        "WRITING ORDER (write them in this order, do not reorder, do not merge, do not split)\n"
        "1. Opening section (no h1 heading, no h2 heading): 2 to 5 sentences. State what the app or "
        "game is, name the price model, name who it is for, and give a direct answer to \"is it "
        "worth it?\". No preamble, no greeting, no rhetorical question, no heading of any kind "
        "above it.\n"
        "2. Quick verdict block (no heading): a dash list of 3 to 5 items, each 1 to 2 sentences, "
        "each beginning with a dash. Order: what it is, what it costs, what is confirmed, what is "
        "unclear, who should skip.\n"
        "3. The body sections above, in the order given.\n"
        "- Keep it tight: an app introduction, not an encyclopedia entry. No FAQ section, no "
        "checklist, no concluding summary heading.\n\n"
        "SECTION RULES (the English in brackets only tells you which section is meant — the heading "
        "you actually print must follow the HEADING LANGUAGE RULE above)\n"
        "- [pricing / availability] (or [pricing and what is still unclear]): always "
        "state the price model, and always state whether there are in-app purchases, a "
        "subscription, or a trial. If the store page says nothing about the price, say so in one "
        "honest sentence instead of guessing.\n"
        "- [who it is for and who should skip]: name at least one concrete type of user who "
        "should skip it, based only on the features the listing actually shows.\n"
        "- [key features]: turn the store text into 4 to 6 short items, each starting with a "
        "concrete noun or verb.\n"
        "- [cons and limitations] (or [what to expect]): name at least one honest "
        "limitation or uncertainty.\n\n"
        + extra +
        "CONTENT LENGTH\n"
        "- The H2 body must be between " + str(cmin) + " and " + str(cmax) + " characters of plain "
        "text. The H2 body means all H2 sections and all sentences under them, "
        "but NOT the opening section and NOT the quick verdict block. Counting is done after "
        "Markdown is removed.\n"
        "- Do not count the opening section or the quick verdict list toward this limit; they are "
        "extra and short.\n"
        "- Keep it TIGHT: aim near the LOW end of the range, not the top. This is an app "
        "introduction, not a full review or a buying encyclopedia.\n"
        "- Do not pad with filler sentences. Every added sentence must carry a fact or a "
        "judgment.\n\n"
        "CURRENCY AND SPECIFICITY RULES\n"
        "- Use the exact price shown in the store listing. Never round it, never convert it, and "
        "never invent a price.\n"
        "- Use the exact version, rating, rating count, install count, content rating and update "
        "date when the source material provides them.\n"
        "- Do not invent a release date, a discount, a launch window, a feature, a platform, a "
        "region, or a supported-device list. If the source does not say it, say it does not say.\n"
        "- Never promise a future update, a future price, or a future platform.\n\n"
        "HARD RULES — BREAKING ANY OF THESE IS AN AUTOMATIC REJECT\n"
        "1. Never use \"review\" or \"hands-on\" or \"tested\" or \"played\" or \"our experience\" "
        "anywhere in the H1, the title, or any H2 heading. You have not played it.\n"
        "2. Never use the phrase \"is it worth it\" or \"worth buying\" or \"worth downloading\" or "
        "\"verdict\" anywhere except the title. The body must answer the "
        "question without repeating those words.\n"
        "3. Never write \"In this article\" or \"In this guide\" or \"Let's dive in\" or any "
        "meta-introduction.\n"
        "4. Never invent a rating, a score, a percentage, or a star value.\n"
        "5. Never invent a comparison price, a discount, or a competitor's price.\n"
        "6. Never use a colon in the title. Never use a question mark in the title unless the "
        "localized structure contains one.\n"
        "7. Never exceed " + str(tmax) + " characters in the title.\n"
        "8. Never promise a release date, a pre-order reward, or a future update.\n"
        "9. Never open with a question or a greeting.\n"
        "10. Never mention the source name or the phrase \"store listing\" more than once.\n\n"
        "SOURCE MATERIAL\n"
        "----------------\n" + mat + "\n----------------\n\n"
        "OUTPUT\n"
        "Return raw JSON and nothing else. No markdown fences, no commentary, no keys other than "
        "these four:\n"
        "title, description, category, body\n"
        "body holds the opening section, the quick verdict block, and the H2 sections.\n"
        "Use \"\\n\" for newlines inside strings.\n"
        "category must always be exactly \"Apps & Games\".\n"
    )


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
    ap.add_argument('--per-lang', type=int, default=0,
                    help='언어당 하루 발행 수 (기본 1)')
    ap.add_argument('--limit', type=int, default=0,
                    help='이번 실행 전체 상한 (0=제한 없음)')
    ap.add_argument('--locale', default='', help='특정 언어만 (예: ko)')
    ap.add_argument('--force', action='store_true',
                    help='오늘 이미 발행한 언어도 무시하고 추가 생성')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--no-play', action='store_true', help='구글플레이 소스 제외')
    ap.add_argument('--no-lang-gate', action='store_true',
                    help='지원 언어 검증 끄기(비상용: 후보가 없을 때만 사용)')
    ap.add_argument('--refresh-upcoming', action='store_true',
                    help='발행된 글의 "출시 예정" 딱지만 재검사하고 종료')
    ap.add_argument('--base', default=BASE)
    args = ap.parse_args()

    base = os.path.abspath(args.base)

    if args.refresh_upcoming:
        # 본 실행(main)이 UTC 기준 오늘을 쓰므로 단독 실행도 같은 기준으로 맞춘다.
        refresh_upcoming_flags(
            base, datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'))
        return 0
    game_dir = os.path.join(base, 'content', 'game')
    assets_dir = os.path.join(base, 'assets', 'img', 'apps')
    seen_file = os.path.join(base, 'content', 'app_radar_seen.json')

    cfg = load_json(os.path.join(base, 'config.json'), {})
    # 로이 지시: 모든 언어에 하루 1개씩.
    per_lang = args.per_lang or int(cfg.get('apps_per_lang_per_day', 1))
    # 로이 방침(2026-10-03): 앱/게임 글은 "적당히 SEO에 걸릴 만큼만" — 길게 늘이지 않는다.
    # CI 는 config.json 을 워크플로 본문에서 하드코딩 생성하므로, config 값이 없을 때의
    # 기본값을 여기에 박아 둬야 클라우드에서도 같은 분량으로 나온다.
    cmin = int(cfg.get('app_char_min', APP_CHAR_MIN))
    cmax = int(cfg.get('app_char_max', APP_CHAR_MAX))
    # 로이 지시(2026-10-02): 그 언어를 지원하지 않는 앱/게임은 그 언어로 발행하지 않는다.
    pubcfg = load_json(os.path.join(base, 'config.public.json'), {})
    strict_lang = not args.no_lang_gate and bool(
        pubcfg.get('apps_require_lang_support', cfg.get('apps_require_lang_support', True)))

    sys.path.insert(0, os.path.join(base, 'scripts'))
    try:
        import llm  # noqa: E402
    except Exception:
        print('scripts/llm.py 를 불러올 수 없습니다.')
        return 1

    today = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    today_str = today.strftime('%Y-%m-%d')

    if args.locale:
        if args.locale not in LOCALE_BY_CODE:
            print('알 수 없는 locale: %s' % args.locale)
            return 1
        wanted = [LOCALE_BY_CODE[args.locale]]
    else:
        wanted = list(LOCALES)

    # 오늘 이미 발행한 언어는 건수만큼 차감 → 하루 1개 원칙이 깨지지 않는다.
    order = []
    quota = {}
    for row in wanted:
        lang = row[0]
        have = 0 if args.force else today_count(os.path.join(game_dir, lang), today_str)
        need = max(0, per_lang - have)
        quota[lang] = need
        order.extend([row] * need)
        if have and need == 0:
            print('[%s] 오늘 %d건 이미 발행 → 건너뜀' % (lang, have))
    if args.limit and len(order) > args.limit:
        order = order[:args.limit]
    target = len(order)

    seen = load_seen(seen_file)
    pub_urls, taken, pub_ids = published_meta(
        [game_dir, os.path.join(base, 'content', 'posts')])

    print('App Radar v3 — 오늘 %s / 총 %d건 (%s)'
          % (today_str, target,
             ', '.join('%s×%d' % (r[0], order.count(r)) for r in wanted) if order else '0건'))

    # 출시일이 지나 '출시 예정'이 아닌데 딱지가 남아 있는 글을 먼저 정리한다.
    refresh_upcoming_flags(base, today_str)

    # Apple 후보는 전 스토어프론트에서 한 번만 모아 전 언어가 공유한다 →
    # 어느 나라 스토어에 떴든 출시예정 앱을 모든 언어가 볼 수 있다.
    apple_pool = collect_apple_all()

    # 출시예정 시드(로이 팁·사전 조사한 게임) + Apple 검색 자동 발굴.
    # — Apple 신작 RSS 에는 사전예약 앱이 안 들어있어 시드/검색으로 따로 긁어온다.
    seeds_path = os.path.join(base, 'content', 'app_upcoming_seeds.json')
    seed_cands = candidates_from_seeds(seeds_path, today)
    search_terms = (load_json(seeds_path, {}).get('search_terms') or [])
    search_cands = apple_search_upcoming(search_terms, today) if search_terms else []
    extra = seed_cands + search_cands
    if extra:
        print('   시드·검색 출시예정 후보 %d개 (시드 %d / 검색 %d): %s'
              % (len(extra), len(seed_cands), len(search_cands),
                 ', '.join((c.get('name') or c['ident'])[:18] for c in extra)))
    apple_pool = apple_pool + extra

    # 출시예정 백로그(어제·지난 실행에서 발견해 둔 것)를 오늘 후보에 되살린다.
    backlog_path = os.path.join(base, 'content', 'app_upcoming.json')
    backlog = load_upcoming_backlog(backlog_path, today_str)
    have = {c['key'] for c in apple_pool}
    revived = [b for b in backlog if b['key'] not in have]
    if revived:
        print('   출시예정 백로그 %d개 복원: %s'
              % (len(revived), ', '.join(b.get('name', '')[:20] for b in revived)))
        apple_pool.extend(backlog_to_cands(revived))
    apple_lookup_many([c['ident'] for c in apple_pool], 'us')
    n_up = sum(1 for c in apple_pool if is_upcoming_cand(c, today))
    print('   그중 출시예정(미래 출시일) %d개' % n_up)

    done = 0
    tried = 0
    reserved = set()   # 이번 실행에서 이미 뽑은 앱 (다른 언어가 같은 앱을 또 쓰지 않도록)
    # 출시예정 앱을 쓴 언어 수. 로이 지시(2026-10-04): "언어별 대표 나라의 출시예정 게임을 찾으라"
    # → 후보가 1개뿐일 때 13개 언어가 전부 같은 앱을 쓰면 안 된다(전 언어 동일 글이 됨).
    #   출시예정은 '먼저 뽑힌 언어'에게만 주고, 그 뒤 언어는 일반 신작으로 내려보낸다.
    upcoming_quota = int(cfg.get('upcoming_share_langs', UPCOMING_SHARE_LANGS))
    upcoming_used = 0
    for lang, cc, hl, gl, lang_name in order:
        if done >= target:
            break
        tried += 1
        outdir = os.path.join(game_dir, lang)
        os.makedirs(outdir, exist_ok=True)
        print('\n[%s] apple=%s / play=%s-%s' % (lang, cc, gl, hl))
        # 언어마다 자신의 Apple 스토어프론트 피드를 따로 긁는다(us/kr/jp/cn...).
        # 피드 내용이 실제로 다르기 때문에(실측 105~124개, 서로 다른 앱) 여기서 갈라진다.
        cands = collect(cc, hl, gl, use_play=not args.no_play, apple_pool=apple_pool)
        print('   후보 %d개' % len(cands))

        # (반드시 순위 계산보다 먼저: 게임 여부가 우선순위를 결정한다)
        cands = expand_play(cands, hl, gl)   # Play 고정 목록 우회 → 관련 앱으로 풀 확장
        mark_games(cands, cc)
        mark_upcoming(cands)   # Play 사전등록 판별 → 반드시 순위 계산보다 먼저
        # 발견한 출시예정은 즉시 백로그에 저장 — 피드에서 사라져도 며칠간 계속 쓴다.
        if lang == order[0][0]:
            backlog = merge_upcoming_backlog(backlog_path, cands, today, today_str)

        # 13개 언어가 같은 티어의 같은 1위 후보를 집지 않도록, 언어 인덱스로 회전시킨다.
        rank = rank_candidates(cands, seen | reserved, pub_urls, pub_ids, today,
                               rotate_idx=tried - 1)
        if not rank:
            print('   신규 후보 없음 → 다음 언어')
            continue

        # 우선순위 앞에서부터 훑어 "이 언어를 실제로 지원하는" 첫 후보를 고른다.
        # 이 언어가 출시예정을 쓸 수 있는지 먼저 판단한다(전 언어 동일글 방지).
        allow_upcoming = upcoming_used < upcoming_quota
        item = None
        mat = upcoming = img_url = disp_name = None
        for cand in rank[:MAX_TRY]:
            cname = cand.get('name') or cand.get('ident')
            if is_upcoming_cand(cand, today) and not allow_upcoming:
                print('   건너뜀(출시예정 할당 소진): %s' % cname)
                continue
            if strict_lang:
                ok, why = lang_gate(cand, lang, cc, hl, gl)
                if ok is False:
                    print('   제외(미지원 언어): %s [%s] — %s'
                          % (cname, cand.get('_tier', ''), why))
                    continue
            m, up, iu, dn = build_material(cand, cc, hl, gl, today)
            if not m:
                # 출시예정 후보는 seen 에 넣지 않는다. 한 번 조회가 실패했다고
                # 영원히 제외하면 출시예정 큐가 텅 빈다(실측: Tideward 가 이렇게 소실됨).
                if is_upcoming_cand(cand, today):
                    print('   제외(자료 없음·출시예정은 보류): %s' % cname)
                else:
                    seen.add(cand['key'])
                    save_seen(seen_file, seen)
                    print('   제외(자료 없음): %s' % cname)
                continue
            item, mat, upcoming, img_url, disp_name = cand, m, up, iu, dn
            break

        if not item:
            print('   %s 언어를 지원하는 신규 후보 없음 → 다음 언어' % lang)
            continue
        # 출시예정 앱은 여기서 예약(reserved)하지 않는다 — 후보 자체가 희소해서
        # 예약해 버리면 뒤 언어는 아예 못 쓴다. 대신 upcoming_quota 로 언어 수를 제한한다.
        if upcoming:
            upcoming_used += 1
            print('   선정: %s (%s / %s) ← 출시예정 [%d/%d]'
                  % (item.get('name'), item['store'], item.get('_tier', ''),
                     upcoming_used, upcoming_quota))
        else:
            reserved.add(item['key'])
            print('   선정: %s (%s / %s)' % (item.get('name'), item['store'],
                                              item.get('_tier', '')))
        if args.dry:
            print('   출시예정=%s / 자료 %d자 / 표시명=%s' % (upcoming, len(mat), disp_name))
            print('\n'.join('   ' + l for l in mat.split('\n')[:10]))
            continue

        img_md = ''
        rel = save_image(img_url, assets_dir, item['key'])
        if rel:
            img_md = '![%s](%s)\n\n' % ((disp_name or 'app').replace('[', ''), rel)

        released = not upcoming
        # 로이 방침(2026-10-03): 짧게. 예전엔 CJK 하한 3200 / 상한 5000+ 라
        # 영어가 9093자까지 나왔다. 이제 전 언어가 1500~3200 선에서 나온다.
        # 출시 완료작이라고 분량을 더 늘리지 않는다(정보가 많아도 길게 쓸 필요 없다).
        if lang in CJK:
            lo, hi = APP_CJK_MIN, APP_CJK_MAX
        else:
            lo, hi = cmin, cmax
        accept_lo = lo - 300   # 경계에서 살짝 짧게 나온 것까지는 허용
        desc_min, desc_max = SEO_DESC_MIN, SEO_DESC_MAX

        # 출시 완료작은 후기·평점·설치수가 들어간 확장 자료를 쓴다(실제 후기형 리포트).
        material = mat
        if released:
            material = released_facts(item, cc, hl, gl) or mat

        tmax_eff = title_max(lang, released)
        obj, body = {}, ''
        for attempt in range(1, 4):
            msgs = [{'role': 'system', 'content': SYSTEM_APP},
                    {'role': 'user', 'content': user_prompt(
                        lang, material, disp_name or 'app', lang_name, released,
                        lo, hi, desc_min, desc_max, item['url'])}]
            if attempt >= 2:
                msgs.append({'role': 'user', 'content': RETRY_HINT})
            got, provider = llm.chat(msgs, purpose='write')
            obj = extract_json(strip_code_fences(got))
            body = (obj.get('body') or '').strip()
            n = len(body)
            tlen = len(obj.get('title') or '')
            dlen = len(obj.get('description') or '')
            print('   [%d] %s 본문 %d자 / 제목 %d자 / 설명 %d자'
                  % (attempt, provider, n, tlen, dlen))
            ok = (accept_lo <= n <= hi and obj.get('title')
                  and tlen <= tmax_eff and 280 <= dlen <= desc_max
                  and script_ok(lang, body))
            if ok:
                break
            if not script_ok(lang, body):
                why = '언어 불일치(%s 아님)' % lang
            elif n < accept_lo:
                why = '본문 너무 짧음'
            elif n > hi:
                why = '본문 너무 김'
            elif tlen > tmax_eff:
                why = '제목 초과(%d>%d)' % (tlen, tmax_eff)
            elif dlen > desc_max:
                why = '설명 초과(%d>%d)' % (dlen, desc_max)
            else:
                why = '설명 부족(%d<300)' % dlen
            print('      재시도: %s' % why)
            time.sleep(0.4)
        # 재시도로도 상한을 못 지키면 기계적으로 자른다.
        # (모델이 "짧게" 지시를 무시하고 5000~9000자를 내보내는 경우가 계속됐다)
        if n > hi:
            cut = clip_body(body, hi)
            print('      본문 초과 → %d자에서 %d자로 절단(상한 %d)' % (n, len(cut), hi))
            body, n = cut, len(cut)
        if not obj.get('title') or not body:
            print('   생성 실패 → 다음 실행에서 재시도')
            continue
        # 잘못된 언어로 쓰인 글은 그 언어 섹션에 실릴 수 없다 → 버리고 다음 실행에 새로 뽑는다.
        if not script_ok(lang, body):
            print('   언어 불일치(%s) → 폐기, 다음 실행에서 다시 시도' % lang)
            continue
        # 하한도 못 채운 글은 발행하지 않는다. (실측 2026-10-05: zh 가 234자 한 문단으로
        # 발행됨 — 재시도 3회를 다 썼다는 이유로 길이 검사를 그냥 통과했었다.
        # 짧은 글은 검색에 걸리지도 않고 품질만 떨어뜨린다 → 다음 실행에 다시 뽑게 한다)
        if n < accept_lo:
            print('   본문 하한 미달(%d<%d) → 폐기, 다음 실행에서 다시 시도' % (n, accept_lo))
            continue

        title = re.sub(r'"', "'", obj['title']).strip()
        # 제목이 언어별 상한을 넘으면 검색 결과에서 잘린다 →
        # 1) 원래 제목에서 뒤쪽 절을 떼어 살리고, 2) 안 되면 안전 템플릿으로 교체.
        if len(title) > tmax_eff:
            trimmed = trim_title(title, tmax_eff)
            if trimmed:
                title = trimmed
            else:
                tpl = TITLE_LOCALIZED.get(lang, TITLE_LOCALIZED['en'])
                cand = tpl.replace('{app}', (disp_name or 'app').strip())
                if len(cand) <= tmax_eff:
                    title = cand
                else:
                    app_nm = (disp_name or 'app').strip()
                    title = (app_nm if len(app_nm) <= tmax_eff
                             else app_nm[:tmax_eff - 1].rstrip() + '…')
            print('   제목 초과 → %d자로 교체: %s' % (len(title), title))
        # SEO 설명: 300자 이상(로이 지시) ~ desc_max 이하. 넘으면 잘라내지 않고 재시도 대상으로 본다.
        desc = re.sub(r'"', "'", (obj.get('description') or '').strip())
        slug = slugify(title, item['key'], taken)
        taken.add(slug)
        src_name = 'Google Play' if item['store'] == 'play' else 'App Store'
        # 팩트박스(출시일·가격·개발사)용 — 전부 스토어 상세에서 그대로 뽑은 값만 쓴다.
        m_price = re.search(r'^PRICE:\s*(.+)$', material, re.M)
        m_dev = re.search(r'^DEVELOPER:\s*(.+)$', material, re.M)
        m_rel = re.search(r'(20\d\d-\d\d-\d\d)', material)
        # ⚠️ 2026-10-04 실측: 위 정규식은 본문 첫 'YYYY-MM-DD' 를 물어오기 때문에
        #   스토어 설명에 적힌 이벤트 날짜를 출시일로 박아버린다
        #   (WellnessFit: 실제 2026-09-01 인데 frontmatter 에는 2027-02-11 → D-day 오류).
        #   Apple 글은 lookup 이 준 releaseDate 를 정본으로 쓴다.
        apple_rel = ''
        if item['store'] == 'apple':
            _d = apple_detail_cached(item['ident'], cc)
            _rd = parse_iso((_d or {}).get('releaseDate'))
            if _rd:
                apple_rel = _rd.strftime('%Y-%m-%d')
        m_gen = (re.search(r'^GENRES?:\s*(.+)$', material, re.M | re.I)
                 or re.search(r'^STORE CATEGORY:\s*(.+)$', material, re.M))
        price = (m_price.group(1).strip() if m_price else '')
        if price.lower() in ('not disclosed', 'n/a', ''):
            price = ''
        fm = ('---\n'
              'slug: %s\n'
              'title: "%s"\n'
              'description: "%s"\n'
              'category: "Apps & Games"\n'
              'date: "%s"\n'
              'sourceName: "%s"\n'
              'sourceUrl: "%s"\n'
              'lang: "%s"\n'
              'status: "%s"\n'
              'image: "%s"\n'
              'releaseDate: "%s"\n'
              'price: "%s"\n'
              'developer: "%s"\n'
              'genre: "%s"\n'
              'kind: "%s"\n'
              'sourceCollection: "%s"\n'
              'upcoming: "%s"\n'
              '---\n\n') % (slug, title, desc,
                            datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'),
                            src_name, item['url'], lang,
                            'released' if released else 'upcoming',
                            rel or '',
                            (apple_rel or (m_rel.group(1) if m_rel else '')),
                            price.replace('"', "'"),
                            (m_dev.group(1).strip().replace('"', "'") if m_dev else ''),
                            (m_gen.group(1).strip().replace('"', "'") if m_gen else ''),
                            kind_of(m_gen.group(1) if m_gen else '', item.get('category') or ''),
                            # 출처를 영구 기록 — 사전등록 컬렉션은 롤링이라 "지금 목록에
                            # 있나"로 과거 글을 재판정하면 방금 발행한 글까지 틀어진다.
                            (item.get('_src') or ''),
                            'false' if released else 'true')
        open(os.path.join(outdir, slug + '.md'), 'w', encoding='utf-8').write(
            fm + img_md + body + '\n')
        seen.add(item['key'])
        save_seen(seen_file, seen)
        print('   발행[%s]: %s (%d자)' % (lang, slug, len(body)))
        done += 1
        time.sleep(0.3)

    print('\nApp Radar 완료: %d건 (오늘 %s)' % (done, today_str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
