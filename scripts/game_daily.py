#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""게임 데일리 레이더 — 중국 게임미디어 기사를 재각색해 /game/ 섹션에 발행.

소스 우선순위 (로이 지시 2026-09-30):
  1) 위챗 公众号 「游创工坊」 당일 게시글 — Sogou 검색 → mp.weixin.qq.com 기사
  2) 위챗 公众号 「游戏日报」 당일 게시글 — 공식 사이트 news.yxrb.net 직접 크롤링
     (공중파 신문이 아니라 위챗 채널의 미러 사이트. 서버 렌더링이라 크롤링이 안정적)

동작:
  - 원본 1개를 뽑아 13개 언어로 각각 네이티브 재창작 (번역 아님)
  - 게임명은 반드시 해당 언어의 공식 현지화 명칭 (임의 번역 금지 — 로이 최우선 규칙)
  - 하루 1편 원칙: content/game_daily_seen.json 의 당일 기록으로 중복 발행 방지
  - 출력: content/game/{lang}/{slug}.md (kind=game)

사용법:
  python scripts/game_daily.py            # 당일 기사 13개 언어 발행
  python scripts/game_daily.py --dry      # 수집+프롬프트만 확인 (LLM 미호출)
  python scripts/game_daily.py --force    # 당일 발행 여부 무시하고 강제 실행
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

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME_DIR = os.path.join(BASE, 'content', 'game')
ASSETS_IMG_DIR = os.path.join(BASE, 'assets', 'img', 'game')
SEEN_FILE = os.path.join(BASE, 'content', 'game_daily_seen.json')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

GONGFANG = '游创工坊'   # --url 수동 링크 전용 — Sogou 미색인으로 자동 발견 불가 (로이 2026-09-30)
GAMELOOK_NAME = 'GameLook'  # 1순위 자동 소스 (공식 사이트 gamelook.com.cn, 로이 지정)
GAMELOOK_HOME = 'http://www.gamelook.com.cn/'
YXRB_NAME = '游戏日报'   # 2순위 위챗 채널 (공식 사이트 미러)
YXRB_HOME = 'http://news.yxrb.net/'

# app_radar 와 규격을 맞춘다 (제목/설명/언어검증/JSON 파싱 재사용).
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import app_radar as ar  # noqa: E402

LOCALES = ar.LOCALES
CJK = ar.CJK
# 언어별 본문 하한 — 상한(3000자)은 전 언어 공통, 하한만 문자 밀도로 환산(2026-10-02 실측)
# 실측 분포: 라틴 2400~2700 / ko 2000 / ja 1300 / zh 900~1100
CJK_LO = {'zh': 1200, 'ja': 1600, 'ko': 1800}
TITLE_MAX = ar.TITLE_MAX
TITLE_DEFAULT = ar.TITLE_DEFAULT
SEO_DESC_MIN = ar.SEO_DESC_MIN
SEO_DESC_MAX = ar.SEO_DESC_MAX

# 소스 본문 길이 상한 — 너무 긴 원문은 잘라 LLM 토큰을 아낀다.
SRC_CAP = 12000


# ---------------------------------------------------------------- http utils
def _get_once(url, timeout):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': '*',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def get(url, timeout=30, retries=3):
    """yxrb 등 중국 사이트는 간헐적으로 타임아웃 → 재시도 + http/https 교대."""
    last = None
    for i in range(retries + 1):
        try:
            u = url
            if i % 2 == 1:
                u = ('https' + url[4:]) if url.startswith('http:') else ('http' + url[5:])
            return _get_once(u, timeout)
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(4)
    raise last


def get_bytes(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def strip_tags(fragment):
    txt = re.sub(r'<script.*?</script>|<style.*?</style>', '', fragment, flags=re.S)
    txt = re.sub(r'<br\s*/?>', '\n', txt)
    txt = re.sub(r'</p>', '\n\n', txt)
    txt = re.sub(r'<[^>]+>', '', txt)
    return htmlmod.unescape(txt)


# ---------------------------------------------------------------- 소스 1) GameLook (공식 사이트)
def fetch_gamelook(today_cn):
    """gamelook.com.cn 에서 당일(중국 시간) 최신 기사 1개. 없으면 48시간 이내."""
    try:
        home = get(GAMELOOK_HOME)
    except Exception as e:
        print('   gamelook 홈 조회 실패: %s' % e)
        return None
    links = re.findall(r'href="(https?://www\.gamelook\.com\.cn/\d{4}/\d{2}/\d+/)"', home)
    uniq = []
    for l in links:
        l = l.replace('https:', 'http:')
        if l not in uniq:
            uniq.append(l)
    uniq.sort(reverse=True)   # URL 날짜 내림차순 = 최신 우선
    for url in uniq[:6]:
        art = fetch_gamelook_article(url, today_cn)
        if art:
            return art
    print('   gamelook에서 가용 기사 없음')
    return None


def fetch_gamelook_article(url, today_cn):
    try:
        html = get(url)
    except Exception:
        return None
    tm = re.search(r'<title>(.*?)</title>', html, re.S)
    dm = re.search(r'(\d{4}-\d{2}-\d{2})', html)
    if not (tm and dm):
        return None
    date = dm.group(1)
    if not same_day_window(date, today_cn, 48):
        return None
    title = htmlmod.unescape(tm.group(1)).strip()
    title = re.sub(r'\s*\|\s*游戏大观\s*\|.*$', '', title).strip()
    cm = (re.search(r'<div class="entry-content[^"]*">(.*?)</div>\s*<(?:div|footer|section)',
                    html, re.S)
          or re.search(r'<div class="entry-content[^"]*">(.*)', html, re.S))
    text = strip_tags(cm.group(1)).strip() if cm else ''
    # 「GameLook专稿，禁止转载！」 표기는 원문 고지일 뿐 — 재각색 자료에서는 제거
    text = re.sub(r'【[^】]*专稿[^】]*】', '', text).strip()
    if len(text) < 500:
        return None
    # 본문 첫 이미지 → 없으면 og:image
    # ⚠️ GameLook 은 지연로딩(lazy) 사이트: src 는 항상 lazy.png 플레이스홀더이고
    #    실제 이미지는 data-original 에 있다(2026-09-30 실측). 우선순위:
    #    data-original > data-src > src(placeholder 제외) > og:image
    img = ''
    body_html = cm.group(1) if cm else html
    for attr in ('data-original', 'data-src', 'src'):
        im = re.search(r'<img[^>]+%s="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"' % attr,
                       body_html, re.I)
        if im and 'lazy' not in im.group(1).lower():
            img = im.group(1)
            break
    if not img:
        im = re.search(r'og:image" content="([^"]+)"', html)
        if im:
            img = im.group(1)
    return {
        'title': title,
        'author': GAMELOOK_NAME,
        'date': date,
        'url': url,
        'text': text[:SRC_CAP],
        'image': img,
        'account': GAMELOOK_NAME,
    }


# -------------------------------------------------- 소스 보류) 游创工坊 (Sogou — 자동 발견 불가)
# 2026-09-30 실측: 미인증 계정이라 Sogou 색인에 없음. --url 수동 링크 경로로만 사용.
def fetch_gongfang(today_cn):
    """Sogou 위챗 기사 검색에서 游创工坊 계정의 최근 기사 1개를 가져온다.

    실측(2026-09-30): 해당 이름의 인증 계정이 Sogou 색인에 없다 → 대부분 None 반환.
    로이가 위챗에서 채널을 확인해주면 이 경로가 다시 살아난다 (코드는 유지).
    """
    try:
        html = get('https://weixin.sogou.com/weixin?type=2&tsn=2&query='
                   + urllib.request.quote(GONGFANG))
    except Exception as e:
        print('   Sogou 조회 실패: %s' % e)
        return None
    if 'antispider' in html or '验证码' in html:
        print('   Sogou 차단(캡차) → 패스')
        return None
    # 결과 블록: 계정명 + /link?url=... 기사 링크 쌍으로 스캔
    blocks = re.findall(r'<li[^>]*id="sogou_vr_11026301[^"]*"[^>]*>(.*?)</li>', html, re.S)
    for b in blocks[:10]:
        acc = re.search(r'account_name_\d+\"[^>]*>(.*?)</a>', b)
        acc_name = strip_tags(acc.group(1)).strip() if acc else ''
        if GONGFANG not in acc_name:
            continue
        link = re.search(r'href="(/link\?url=[^"]+)"', b)
        if not link:
            continue
        art = sogou_link_article('https://weixin.sogou.com' + link.group(1))
        if art and same_day_window(art.get('date', ''), today_cn, 48):
            return art
    print('   Sogou에서 %s 계정의 당일 기사 없음' % GONGFANG)
    return None


def sogou_link_article(link_url):
    """Sogou /link 리다이렉트 페이지에서 실제 mp.weixin.qq.com 주소를 뽑아 기사를 가져온다."""
    try:
        page = get(link_url)
    except Exception:
        return None
    m = re.search(r'(https?://mp\.weixin\.qq\.com/s[^"\'\s<]+)', page)
    if not m:
        return None
    return fetch_wechat_article(m.group(1))


def fetch_wechat_article(url):
    """mp.weixin.qq.com 기사 페이지 파싱 (공개 페이지, 로그인 불필요)."""
    try:
        html = get(url)
    except Exception as e:
        print('   위챗 기사 조회 실패: %s' % e)
        return None
    t = re.search(r'<h1[^>]*id="activity-name"[^>]*>(.*?)</h1>', html, re.S)
    c = re.search(r'<div[^>]*id="js_content"[^>]*>(.*)', html, re.S)
    a = re.search(r'<a[^>]*id="js_name"[^>]*>(.*?)</a>', html, re.S)
    d = re.search(r"var createTime = '([^']+)'", html)
    if not (t and c):
        return None
    text = strip_tags(c.group(1)).strip()
    if len(text) < 500:
        return None
    return {
        'title': strip_tags(t.group(1)).strip(),
        'author': strip_tags(a.group(1)).strip() if a else GONGFANG,
        'date': (d.group(1)[:10] if d else ''),
        'url': url,
        'text': text[:SRC_CAP],
        'image': '',
        'account': GONGFANG,
    }


# ---------------------------------------------------------------- 소스 2) 游戏日报 (공식 사이트)
def fetch_yxrb(today_cn):
    """news.yxrb.net 에서 당일(중국 시간) 최신 기사 1개. 없으면 48시간 이내 최신."""
    try:
        home = get(YXRB_HOME)
    except Exception as e:
        print('   yxrb 홈 조회 실패: %s' % e)
        return None
    links = re.findall(r'href="(/\d{4}/\d{4}/\d+\.html)"', home)
    seen, uniq = set(), []
    for l in links:
        if l not in seen:
            seen.add(l)
            uniq.append(l)
    # 최신 순 정렬(날짜 내림차순) 후 첫 성공 건
    uniq.sort(reverse=True)
    for rel in uniq[:8]:
        art = fetch_yxrb_article(YXRB_HOME.rstrip('/') + rel, today_cn)
        if art:
            return art
    print('   yxrb에서 가용 기사 없음')
    return None


def fetch_yxrb_article(url, today_cn):
    try:
        html = get(url)
    except Exception:
        return None
    tm = re.search(r'<title>(.*?)</title>', html, re.S)
    dm = re.search(r'(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}', html)
    if not (tm and dm):
        return None
    date = dm.group(1)
    if not same_day_window(date, today_cn, 48):
        return None
    am = re.search(r'作者\s*[:：]\s*([^<\s]{1,20})', html)
    # 본문 컨테이너: <article class="article-style"> … </article>
    # (주의: 'textwidget' 은 푸터 위젯이라 처음 등장하는 것은 기사가 아니다)
    cm = re.search(r'<article class="article-style"[^>]*>(.*?)</article>', html, re.S)
    if not cm:
        cm = re.search(r'<article[^>]*>(.*?)</article>', html, re.S)
    text = strip_tags(cm.group(1)).strip() if cm else ''
    if len(text) < 500:
        return None
    # 본문 앞에 사이트 문구가 붙는다 → 첫 제목 반복 이후부터가 기사 본문
    title = htmlmod.unescape(tm.group(1)).strip()
    # "<기사 제목> - <섹션> - <사이트명>" → 기사 제목만 남긴다
    parts = [p.strip() for p in title.split(' - ')]
    while len(parts) > 1 and (('游戏日报' in parts[-1])
                             or parts[-1] in ('资讯', '访谈', '游理游据', '服务')):
        parts = parts[:-1]
    title = ' - '.join(parts).strip() or title
    if title and title in text:
        text = text[text.index(title) + len(title):].strip() or text
    img = ''
    im = re.search(r'property="og:image" content="([^"]+)"', html)
    if im:
        img = im.group(1)
    return {
        'title': title,
        'author': (am.group(1).strip() if am else YXRB_NAME),
        'date': date,
        'url': url,
        'text': text[:SRC_CAP],
        'image': img,
        'account': YXRB_NAME,
    }


def same_day_window(date_str, today_cn, hours):
    """기사 날짜가 오늘 또는 최근 N시간 이내인지."""
    try:
        d = datetime.date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return False
    delta = (today_cn - d).days
    return 0 <= delta <= max(1, hours // 24)


def cn_today():
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=8)).date()


# ---------------------------------------------------------------- 이미지
def save_image(url, slug):
    """원문 대표 이미지를 내려받아 assets/img/game/ 에 저장. 실패 시 ''."""
    if not url:
        return ''
    try:
        # img.gamelook.com.cn 은 Referer 없으면 403 (핫링크 차단, 2026-10-01 실측)
        req = urllib.request.Request(url, headers={
            'User-Agent': UA,
            'Referer': GAMELOOK_HOME,
            'Accept': 'image/avif,image/webp,image/*,*/*;q=0.8',
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if len(data) < 2000:
            return ''
        ext = '.jpg'
        m = re.search(r'\.(jpe?g|png|webp)(?:[?#]|$)', url, re.I)
        if m:
            ext = '.' + m.group(1).lower().replace('jpeg', 'jpg')
        rel = os.path.join('assets', 'img', 'game', slug + ext)
        os.makedirs(os.path.join(BASE, 'assets', 'img', 'game'), exist_ok=True)
        open(os.path.join(BASE, rel), 'wb').write(data)
        return '/' + rel.replace('\\', '/')
    except Exception as e:
        print('   이미지 저장 실패(%s): %s' % (url[:60], e))
        return ''


# ---------------------------------------------------------------- seen
def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            data = json.load(open(SEEN_FILE, encoding='utf-8'))
        except Exception as e:
            # 손상돼도 파이프라인은 계속 — 아래 is_url_published()의 파일시스템 방어선이 지킨다
            print('   ⚠ seen 파일 손상(무시): %s' % e)
            data = None
        if isinstance(data, dict):
            data.setdefault('dates', {})
            data.setdefault('urls', {})
            return data
        if data is not None:
            print('   ⚠ seen 파일 형식 이상(무시): %s' % type(data).__name__)
    return {'dates': {}, 'urls': {}}


def save_seen(seen):
    """원자적 쓰기 + 실패 로그. 예외를 삼키지 않는다(조용한 실패가 중복 발행의温床이 됨)."""
    try:
        os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
        tmp = SEEN_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(seen, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SEEN_FILE)   # Windows에서 rename 실패를 피하려고 replace 사용
        return True
    except Exception as e:
        print('   ⚠ seen 저장 실패 — 다음 실행에서 중복 발행 가능: %s' % e)
        return False


# ------------------------------------------------- 중복 판정 (순수 함수 — 단위 테스트 가능)
def url_in_seen(seen, url):
    """seen 상태에 이미 발행된 URL 인지. seen 为 None/损坏时安全返回 False."""
    if not url or not isinstance(seen, dict):
        return False
    return url in (seen.get('urls') or {})


def _fm_field(head, key):
    """frontmatter 텍스트에서 key: 값을 大小문자 무시하고 추출(따옴표/공백 허용)."""
    m = re.search(r'^%s:[ \t]*(.+?)[ \t]*$' % re.escape(key), head, re.M | re.I)
    if not m:
        return ''
    return m.group(1).strip().strip('"').strip("'").strip()


def scan_published():
    """content/game/**/*.md frontmatter 를 훑어 (sourceUrl 집합, {lang: title 집합}) 반환.

    2026-10-02 재발事故의 진짜 근원: seen.json 이 git merge 로 유실되어
    '이미 발행함' 상태가 사라졌기 때문에 파일시스템이 진짜 유일한 진실이다.
    measured: 183 files / 1.2MB → 약 0.2초 (ネットワーク不要).
    """
    urls, titles = set(), {}
    if not os.path.isdir(GAME_DIR):
        return urls, titles
    for lang in sorted(os.listdir(GAME_DIR)):
        d = os.path.join(GAME_DIR, lang)
        if not os.path.isdir(d):
            continue
        tset = set()
        for f in os.listdir(d):
            if not f.endswith('.md'):
                continue
            try:
                # frontmatter 는 파일 머리 20줄 안에만 있다 — 본문 전체를 읽지 않는다
                with open(os.path.join(d, f), encoding='utf-8') as fh:
                    head = ''.join([next(fh, '') for _ in range(20)])
            except (OSError, UnicodeDecodeError):
                continue
            su = _fm_field(head, 'sourceUrl')
            if su:
                urls.add(su)
            ti = _fm_field(head, 'title')
            if ti:
                tset.add(norm_title(ti))
        if tset:
            titles[lang] = tset
    return urls, titles


def norm_title(t):
    """제목 비교용 정규화 — 공백/대소문자/구두점 제거."""
    return re.sub(r'[\s　]+', '', (t or '')).strip().lower()


def find_published_url(url, fs_urls=None):
    """파일시스템에 같은 sourceUrl 원고가 이미 있는가.

    fs_urls 를 넘기면 디스크 스캔을 건너뛴다(단위 테스트용).
    """
    if not url:
        return False
    if fs_urls is None:
        fs_urls = scan_published()[0]
    return url in fs_urls


def find_published_title(title, lang, fs_titles=None):
    """같은 언어 디렉터리에 동일 제목(H1)이 이미 있는가 — 다언어 중복은 정상이라 lang scopes."""
    if not title or not lang:
        return False
    if fs_titles is None:
        fs_titles = scan_published()[1]
    return norm_title(title) in (fs_titles.get(lang) or set())


# ---------------------------------------------------------------- LLM
SYSTEM_GAME = """You are a native-level game-industry journalist for the games section of an international website.
You take a Chinese gaming-industry article and write an ORIGINAL local article in the target language
about the same story. It must read like local coverage, never like a translation.

GAME NAME DISCIPLINE (HIGHEST PRIORITY — the article is rejected if violated):
1. Every game title in the source must appear under its OFFICIAL localized name for the target
   language, exactly as the publisher uses it in that market.
   Example: 崩坏：星穹铁道 is officially "Honkai: Star Rail" (en), "붕괴: 스타레일" (ko),
   "崩壊：スターレイル" (ja), "Honkai: Star Rail" (es/de/fr/pt/id), "Honkai: Star Rail" (ru),
   "होंकाई: स्टार रेल" is NOT official — if unsure for hi/ar/bn, keep the English official name.
2. On the FIRST mention write: Official Localized Name（original Chinese name in full-width
   parentheses）. On every later mention, the localized name alone.
3. If — and only if — the game has NO official localized name in that language (for example a
   small Chinese indie title), translate the name carefully and literally, and ALWAYS keep the
   original Chinese name in parentheses on first mention.
4. NEVER freely translate or transliterate a game title that has an official localized name.
   A machine-translation style title is the single worst failure this site can publish.
5. Company and studio names use their official localized form: miHoYo / HoYoverse, Tencent,
   NetEase, Kuro Games, Papergames, and so on. Never transliterate company names that have an
   established Latin form.

FACT DISCIPLINE: every fact (dates, versions, numbers, features, quotes, revenue figures) must
come only from the source material. Do not add outside knowledge, do not speculate beyond what
the source states. If the source does not answer an obvious reader question, say so honestly in
one short sentence.

ATTRIBUTION: you are reporting on industry news, not reviewing a game. Never claim to have
played anything.

OUTPUT DISCIPLINE: you reply with one single raw JSON object and nothing else. No markdown code
fence, no commentary, no trailing comma. Inside string values, write every line break as \\n and
escape every double quote, so the JSON can always be parsed by a machine."""


def user_prompt(lang, lang_name, art, lo, hi, slug_hint='', dmin_prompt=None):
    tmax = TITLE_MAX.get(lang, TITLE_DEFAULT)
    if dmin_prompt is None:
        dmin_prompt = SEO_DESC_MIN
    slug_part = ('"slug": 5-8 lowercase English words joined by hyphens that describe the story.\n'
                 if not slug_hint else
                 '"slug": EXACTLY "%s" (reuse the given slug, do not change it).\n' % slug_hint)
    return (
        "Write one news analysis article in " + lang_name + ".\n\n"
        "THE ANGLE\n"
        "The reader follows the game industry but has not read this Chinese coverage. Give them "
        "the story, why it matters, and what to watch next. Neutral, concrete, no hype.\n\n"
        "GAME NAME RULE (repeat): every game title must use its official localized name in "
        + lang_name + ", with the original Chinese name in parentheses on first mention. "
        "Only use a literal translation when no official localized name exists.\n\n"
        "STRUCTURE (## headings, in this order):\n"
        "section 1 = what happened\n"
        "section 2 = why it matters\n"
        "section 3 = what to watch next\n"
        "- No FAQ section. Keep it tight — just the news, why it matters, what comes next.\n\n"
        "HEADING LANGUAGE RULE (mandatory, most-violated rule):\n"
        "- EVERY \"##\" and \"###\" heading MUST be written in " + lang_name + ".\n"
        "- The English labels above describe the sections only. NEVER keep them as headings.\n"
        "  Writing \"## What Happened\" is a hard failure — the article is discarded.\n"
        "- Write headings the way a local " + lang_name + " publication would, not word-for-word.\n"
        "- Only proper nouns (game/company names) may stay in their original script.\n\n"
        "LENGTH DISCIPLINE — this is the rule most often failed\n"
        "- The whole body MUST fit in " + str(hi) + " characters. Budget it before you write:\n"
        "  opening ~120 words, 'What Happened' ~150, 'Why It Matters' ~140, "
        "'What To Watch Next' ~120.\n"
        "- Use SHORT paragraphs (2-3 sentences). No bullet lists of more than 4 items.\n"
        "- Cut background and restatement. Do not repeat the headline in section 1.\n"
        "- If you are running long, compress 'Why It Matters' first — never exceed "
        + str(hi) + " characters.\n\n"
        "CONSTRAINTS\n"
        "- body length: " + str(lo) + " to " + str(hi) + " characters (count the final text).\n"
        "- description (SEO meta): " + str(dmin_prompt) + " to " + str(SEO_DESC_MAX)
        + " characters, plain sentences, no clickbait.\n"
        "- title: at most " + str(tmax) + " characters, states the concrete story.\n"
        "- markdown only (##, ###, lists). No HTML, no images.\n\n"
        "OUTPUT JSON keys:\n"
        "\"title\", \"description\", \"body\",\n" + slug_part + "\n"
        "SOURCE MATERIAL (Chinese gaming media — " + art['account'] + ", by "
        + (art.get('author') or 'staff') + ", " + (art.get('date') or '') + "):\n"
        "----------------\n"
        "HEADLINE: " + art['title'] + "\n\n"
        + art['text'] + "\n"
        "----------------\n")


def gen_lang(lang, lang_name, art, lo, hi, slug_hint=''):
    """1개 언어 생성. 성공 시 dict, 실패 시 None."""
    import llm
    # 설명 하한 — 로이 SEO 규칙(라틴 300자)을 문자 체계별로 환산.
    # 한국어·일본어·중국어는 한 글자의 정보량이 라틴의 ~2배라 300자를 요구하면
    # LLM 이 자연스러운 설명보다 2배 길게 써야 해서 구조적으로 실패한다(2026-10-01 실측:
    # CJK 7개 언어 전부 설명 180~280자로 3회 재시도 후 탈락). 60% 기준 180자로 환산한다.
    desc_min = 180 if lang in CJK else SEO_DESC_MIN
    last_why = ''
    for attempt in range(1, 4):
        msgs = [{'role': 'system', 'content': SYSTEM_GAME},
                {'role': 'user', 'content': user_prompt(lang, lang_name, art, lo, hi, slug_hint,
                                                        dmin_prompt=desc_min)}]
        if attempt >= 2:
            # 동적 피드백 — 실제 부족분을 숫자로 알려준다(2026-10-01 ko 3연속 짧은 출력 대응)
            hint = ('Your previous answer was rejected by the validator. '
                    'The rejection reason was: ' + (last_why or 'length/JSON/name rules') + '. '
                    'Fix THAT specific problem. Also re-check: JSON-only reply, '
                    'official localized game names, everything in ' + lang_name + '. '
                    'LENGTH IS COUNTED IN CHARACTERS OF THE FINAL TEXT — count carefully and '
                    'err on the LONG side: description at least ' + str(desc_min + 40)
                    + ' characters, body at least ' + str(lo) + ' characters.')
            msgs.append({'role': 'user', 'content': hint})
        got, provider = llm.chat(msgs, purpose='write')
        obj = ar.extract_json(ar.strip_code_fences(got))
        body = (obj.get('body') or '').strip()
        title = (obj.get('title') or '').strip()
        desc = (obj.get('description') or '').strip()
        if not (body and title and desc):
            last_why = 'missing title/description/body fields'
            print('   [%d] 항목 누락' % attempt)
            continue
        n = len(body)
        if n < lo - 400:
            last_why = ('body was %d characters, minimum is %d — expand every section '
                        'with concrete facts' % (n, lo))
            print('   [%d] 본문 %d자 < 하한 %d' % (attempt, n, lo))
            continue
        # 상한은 10% 여유만 허용 — 3000자 이내 원칙을 지킨다(로이 2026-10-02)
        if n > int(hi * 1.1):
            last_why = 'body was %d characters, maximum is %d — cut the weakest sections' % (n, hi)
            print('   [%d] 본문 %d자 > 상한 %d' % (attempt, n, hi))
            continue
        if len(desc) < desc_min - 40:
            last_why = ('description was only %d characters, minimum is %d — write %d+ characters, '
                        '5-6 full sentences' % (len(desc), desc_min, desc_min + 40))
            print('   [%d] 설명 %d자 < %d (SEO 규칙, 문자체계 환산)' % (attempt, len(desc), desc_min))
            continue
        if not ar.script_ok(lang, title + ' ' + body[:800]):
            print('   [%d] 언어 불일치' % attempt)
            continue
        t = ar.trim_title(title, TITLE_MAX.get(lang, TITLE_DEFAULT)) or title
        d = desc[:SEO_DESC_MAX]
        obj = {'title': t, 'description': d, 'body': body, 'slug': (obj.get('slug') or '')}
        print('   [%d] %s 본문 %d자 / 제목 %d자 / 설명 %d자 (%s)'
              % (attempt, lang, n, len(t), len(d), provider))
        return obj
    return None


def slugify(s):
    s = (s or '').strip().lower()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s-]+', '-', s).strip('-')
    return s[:60]


def unique_slug(slug, seen_slugs):
    base = slug or ('daily-' + datetime.date.today().strftime('%Y%m%d'))
    out, i = base, 2
    while out in seen_slugs:
        out = '%s-%d' % (base, i)
        i += 1
    seen_slugs.add(out)
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='수집만 확인(생성 없음)')
    ap.add_argument('--force', action='store_true', help='당일 발행 기록 무시')
    ap.add_argument('--url', help='직접 지정한 위챗(mp.weixin.qq.com) 기사 URL — 游创工坊 등')
    args = ap.parse_args()

    today_cn = cn_today()
    today_str = today_cn.isoformat()
    seen = load_seen()

    # 로이가 직접 준 링크 (1순위) — 날짜 무관 강제 대상.
    art = None
    if args.url:
        print('[0] 지정된 위챗 링크 시도…')
        art = fetch_wechat_article(args.url)
        if not art:
            print('    지정 링크 파싱 실패')
            return 1
        print('    선정: %s / %s / %s' % (art['title'][:40], art.get('author'), art.get('date')))
        if art['url'] in seen.get('urls', {}):
            print('    이미 처리한 기사 → 종료')
            return 0
        try:
            d = datetime.date.fromisoformat(art.get('date', '') or today_str)
            age = (today_cn - d).days
            if age > 2:
                print('    ⚠ %d일 전 기사입니다 (구형 데이터 주의)' % age)
        except ValueError:
            pass
    elif not args.force and seen['dates'].get(today_str):
        print('[%s] 오늘 이미 발행 완료 (%s) → 종료' % (today_str, seen['dates'][today_str]))
        return 0

    if not art:
        # 1순위 GameLook → 2순위 游戏日报 (로이 2026-09-30 지정)
        print('[1] GameLook (공식 사이트) 시도…')
        art = fetch_gamelook(today_cn)
        if art:
            print('    선정: %s / %s' % (art['title'][:40], art['date']))
        else:
            print('[2] %s (공식 사이트) 시도…' % YXRB_NAME)
            art = fetch_yxrb(today_cn)
            if not art:
                print('    가용 기사 없음 → 종료 (재시도 필요)')
                return 1
            print('    선정: %s / %s / %s' % (art['title'][:40], art.get('author'), art['date']))

    # 원문 1개 확정 후 — seen 상태 + 실제 디스크 양쪽으로 중복 검사.
    # (seen.json 은 git merge 로 유실될 수 있어 파일시스템을 진짜 방어선으로 삼는다)
    # --force 는 seen 기록만 무시한다. 디스크에 이미 있는 원문은 --force 로도 덮어쓰지 않는다
    # (같은 원문을 다른 날짜로 재발행하면 H1 중복 SEO 페이지가 생긴다 — 2026-10-02 사고).
    if not args.force and url_in_seen(seen, art['url']):
        print('    이미 처리한 기사(seen, %s) → 종료' % seen['urls'][art['url']])
        return 0
    if find_published_url(art['url']):
        print('    이미 발행된 원문(파일시스템 중복) → 종료: %s' % art['url'])
        return 0

    print('    원문 %d자 / 출처 %s' % (len(art['text']), art['url']))
    if args.dry:
        print('    본문 미리보기: %s' % art['text'][:300])
        return 0

    # 기존 게시물 slug 충돌 방지
    seen_slugs = set()
    for lang, _cc, _hl, _gl, _n in LOCALES:
        d = os.path.join(GAME_DIR, lang)
        if os.path.isdir(d):
            seen_slugs.update(os.path.splitext(f)[0] for f in os.listdir(d)
                              if f.endswith('.md'))

    # en 먼저 → slug 확보 → 나머지 12개 언어
    order = [('en', 'English')] + [(r[0], r[4]) for r in LOCALES if r[0] != 'en']
    results = {}
    slug = ''
    for lang, lang_name in order:
        # 로이 지시(2026-10-02): 게임 뉴스 글은 모든 언어 3000자 이내로.
        # (이전 CJK 3200~5000 / 라틴 3000~5000 → 실제 5800~6900자까지 나와 너무 길었다)
        # 하한은 언어별 문자 밀도 환산 (실측: 라틴 2400~2700 / ko 2000 / ja 1300 / zh 900~1100)
        lo = CJK_LO.get(lang) if lang in CJK else 2000
        hi = 3000
        print('[%s] 생성 중…' % lang)
        obj = gen_lang(lang, lang_name, art, lo, hi, slug_hint=slug)
        if not obj:
            print('[%s] 생성 실패 → 건너뜀' % lang)
            continue
        if lang == 'en' and not slug:
            slug = unique_slug(slugify(obj.get('slug')), seen_slugs)
            print('    slug = %s' % slug)
        results[lang] = obj
    if not results:
        print('모든 언어 생성 실패')
        return 1
    if not slug:  # en 실패 시 다른 언어 slug 로 대체
        slug = unique_slug(slugify(next(iter(results.values())).get('slug')), seen_slugs)

    # 발행 직전 최종 방어선 ① — 같은 언어 디렉터리에 동일 H1 이 이미 있으면 아예 발행하지 않는다.
    # 다른 각도로 다시 쓰게 하는 대신 종료한다(기존 원고 보존이 SEO 에도 안전).
    # 같은 언어 안에서만 비교한다 — 전 언어 비교는 정상적인 다언어 중복을 잡기 때문이다.
    dup_titles = []
    for lang, obj in results.items():
        if find_published_title(obj.get('title'), lang):
            dup_titles.append('%s: %s' % (lang, obj.get('title')))
    if dup_titles:
        print('    ⚠ 동일 제목(H1)이 이미 존재 — 발행 취소: %s' % '; '.join(dup_titles))
        return 0

    # 발행 직전 최종 방어선 ② — 저장 경로가 이미 있으면 덮어쓰지 않는다.
    # 위 title 검사 이후지만 slug 충돌 변형까지 이最後の 순간에 한 번 더 확인한다.
    for lang in results:
        out = os.path.join(GAME_DIR, lang, slug + '.md')
        if os.path.exists(out):
            print('    ⚠ 동일 slug 파일이 이미 존재 — 발행 취소: content/game/%s/%s.md'
                  % (lang, slug))
            return 0

    # 이미지 저장 (en 메타 og:image)
    img_rel = save_image(art.get('image', ''), slug)

    # 파일 기록
    for lang, obj in results.items():
        outdir = os.path.join(GAME_DIR, lang)
        os.makedirs(outdir, exist_ok=True)
        fm = ('---\n'
              'slug: %s\n'
              'title: "%s"\n'
              'description: "%s"\n'
              'category: "Apps & Games"\n'
              'date: "%s"\n'
              'sourceName: "%s"\n'
              'sourceUrl: "%s"\n'
              'lang: "%s"\n'
              'status: ""\n'
              'image: "%s"\n'
              'releaseDate: ""\n'
              'price: ""\n'
              'developer: "%s"\n'
              'genre: "Game Industry News"\n'
              'kind: "game"\n'
              'news: "true"\n'
              'upcoming: "false"\n'
              '---\n\n') % (slug,
                             obj['title'].replace('"', "'"),
                             obj['description'].replace('"', "'"),
                             today_str,
                             art['account'].replace('"', "'"),
                             art['url'],
                             lang,
                             img_rel,
                             (art.get('author') or art['account']).replace('"', "'").strip())
        open(os.path.join(outdir, slug + '.md'), 'w', encoding='utf-8').write(fm + obj['body'] + '\n')
        print('[%s] 저장: content/game/%s/%s.md (%d자)' % (lang, lang, slug, len(obj['body'])))

    seen.setdefault('dates', {})[today_str] = art['url']
    seen.setdefault('urls', {})[art['url']] = today_str
    if not save_seen(seen):
        # 저장 실패해도 원고는 이미 발행됨. 다음 실행이 중복을 막을 수 있도록 즉시 로그로 남긴다.
        print('   ⚠ seen 기록이 저장되지 않았습니다 — 다음 실행은 파일시스템 방어선으로만 '
              '중복을 막습니다 (%s)' % SEEN_FILE)
    print('완료: %d개 언어 / %s / 원문: %s' % (len(results), slug, art['url']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
