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
    img = ''
    im = re.search(r'<img[^>]+src="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"',
                   cm.group(1) if cm else html, re.I)
    if im:
        img = im.group(1)
    else:
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
        data = get_bytes(url)
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
            return json.load(open(SEEN_FILE, encoding='utf-8'))
        except Exception:
            pass
    return {'dates': {}, 'urls': {}}


def save_seen(seen):
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    json.dump(seen, open(SEEN_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


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


def user_prompt(lang, lang_name, art, lo, hi, slug_hint=''):
    tmax = TITLE_MAX.get(lang, TITLE_DEFAULT)
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
        "STRUCTURE (## headings, in this order, written natively in " + lang_name + "):\n"
        "## What Happened\n"
        "## Why It Matters\n"
        "## The Bigger Picture\n"
        "## What To Watch Next\n"
        "## FAQ  (3-4 questions as ### subheadings, each answered in 1-2 sentences)\n\n"
        "CONSTRAINTS\n"
        "- body length: " + str(lo) + " to " + str(hi) + " characters (count the final text).\n"
        "- description (SEO meta): " + str(SEO_DESC_MIN) + " to " + str(SEO_DESC_MAX)
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
    for attempt in range(1, 4):
        msgs = [{'role': 'system', 'content': SYSTEM_GAME},
                {'role': 'user', 'content': user_prompt(lang, lang_name, art, lo, hi, slug_hint)}]
        if attempt >= 2:
            msgs.append({'role': 'user',
                         'content': 'Your previous answer was rejected by the validator. Re-check: '
                                    'JSON-only reply, body/description length limits, title limit, '
                                    'official localized game names, language must be '
                                    + lang_name + '.'})
        got, provider = llm.chat(msgs, purpose='write')
        obj = ar.extract_json(ar.strip_code_fences(got))
        body = (obj.get('body') or '').strip()
        title = (obj.get('title') or '').strip()
        desc = (obj.get('description') or '').strip()
        if not (body and title and desc):
            print('   [%d] 항목 누락' % attempt)
            continue
        n = len(body)
        if n < lo - 400:
            print('   [%d] 본문 %d자 < 하한 %d' % (attempt, n, lo))
            continue
        if n > int(hi * 1.4):
            print('   [%d] 본문 %d자 > 상한 %d' % (attempt, n, hi))
            continue
        if len(desc) < SEO_DESC_MIN - 20:
            print('   [%d] 설명 %d자 < %d (로이 SEO 규칙: 300자 이상)' % (attempt, len(desc), SEO_DESC_MIN))
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

    if art['url'] in seen.get('urls', {}):
        print('    이미 처리한 기사 (%s) → 종료' % seen['urls'][art['url']])
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
        lo, hi = (3200, 5000) if lang in CJK else (3000, 5000)
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
    save_seen(seen)
    print('완료: %d개 언어 / %s / 원문: %s' % (len(results), slug, art['url']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
