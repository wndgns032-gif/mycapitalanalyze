# -*- coding: utf-8 -*-
"""기존 스토어 글 제목 일괄 재생성 (2026-10-29).

로이: "앱/게임명이 꼭 정확히 들어가는 대신에 제목은 유동적으로 글 내용에 맞게 바꿔줘,
      지금은 완전 고정이라서 별로인거 같아"

→ 이미 발행된 스토어 글의 제목이 "앱명 — 살 만한가요? 가격·기능·건너ᛸ 사람" 처럼
  전부 같은 꼬리말이라 검색 결과에서 구분되지 않는다.
→ 각 글의 **본문 내용**을 보고 제목을 다시 쓴다. 앱/게임명은 정확히 유지.

원칙:
- 이미 발행된 frontmatter 의 title 만 교체한다 (본문·설명·slug 는 손대지 않는다).
- 앱/게임 이름은 그대로, 뒤 꼬리말만 내용 기반으로.
- 새 제목이 기존 제목과 같으면 건너뛴다.
- 같은 언어 안에서 제목이 겹치면 다른 걸로 다시 시도.
"""
import argparse
import glob
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT = r'D:\workbuddy\blog\mycapitalanalyze.com'
CONTENT = os.path.join(ROOT, 'content')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

# 앱/게임 스토어 글만 대상. 산업 뉴스 글(game_daily)은 이미 자유 제목을 쓴다.
STORE_DIRS = [os.path.join(CONTENT, 'game')]
LEGACY = os.path.join(CONTENT, 'posts')

# "앱명 — 살 만한가요? …" 같은 **구형 고정 꼬리말** 패턴 (재생성 대상 판별)
# ⚠️ 언어별로 구형 문구가 다르다. 일본어/중문어/스페인어 등을漏치면
#    제목 재생성이 아예 동작하지 않는다(대상 0건).
OLD_TAILS = {
    'ko': ['살 만한가요', '무엇을 얻는가', '어떤 사람에게 맞는가', '무료로 쓸 수 있는 범위',
           '어떤 사람에게 어울리는 게임인가', '무료로 할 수 있는 범위',
           '가격·기능·건너ᛸ 사람', '가격·게임성·건너ᛸ 사람'],
    'en': ['is it worth buying', 'is it worth downloading', 'is it worth playing',
           'worth switching', 'what you get on day one', 'our honest verdict',
           'the honest catch', 'is it worth pre-ordering'],
    'ja': ['買う価値はある', '何が得られるか', 'どんな人に合うか', '無料の範囲',
           'どんな人に合うゲームか', 'プレイする価値はある'],
    'zh': ['值得买吗', '花钱买到什么', '适合什么类型的人', '免费能用什么',
           '值得玩吗', '适合什么类型的玩家', '免费能玩什么'],
    'es': ['precio, funciones y a quién no', 'género, jugabilidad y precio',
           'para quién es este juego', 'qué es gratis y qué cuesta dinero',
           '¿vale la pena jugarlo', 'a quién no'],
    'fr': ['prix, fonctions et qui doit éviter', 'genre, gameplay et prix',
           'à qui s’adresse ce jeu', 'ce qui est gratuit et ce qui coûte',
           'vaut-il le coup de jouer'],
    'de': ['Preis, Funktionen, für wen nicht', 'Genre, Gameplay und Kosten',
           'Für wen das Spiel passt', 'Was gratis ist und was Geld kostet',
           'lohnt sich das Spielen'],
    'pt': ['preço, funções e quem deve evitar', 'gênero, jogabilidade e preço',
           'para quem é este jogo', 'o que é grátis e o que custa',
           'vale a pena jogar'],
    'ru': ['цена, функции и кому не стоит', 'жанр, геймплей и цена',
           'кому подойдет эта игра', 'что бесплатно, а за что платить',
           'стоит ли играть'],
    'id': ['harga, fitur dan siapa yang tidak', 'genre, gameplay dan harga',
           'untuk siapa game ini', 'yang gratis dan yang berbayar',
           'layak dimainkan'],
    'ar': ['السعر، الميزات، ومن يتجنبها', 'النوع، طريقة اللعب والسعر',
           'لمن هذه اللعبة', 'ما هو مجاني وما يدفع مقابله', 'هل يستحق اللعب'],
    'bn': ['দাম, ফিচার এবং কারা না', 'ধরন, গেমপ্লে ও দাম',
           'এই গেম কার জন্য', 'কোনটা বিনা খরচে, কোনটা টাকায়', 'খেলার যোগ্য'],
    'hi': ['कीमत, फीचर्स और किन्हें नहीं', 'शैली, गेमप्ले और कीमत',
           'यह गेम किसके लिए है', 'क्या मुफ़्त है और किसे पैसे देने हैं', 'खेलने लायक'],
}

# 라벨 제거 후 남은 **단일 단어 꼬리말** ("앱이름 — 가격").
# ⚠️ 부분 문자열로 검사하면 정상 제목("무료 9×9 블록 퍼즐")도 걸린다
#    → 반드시 **제목 끝**에 붙어 있을 때만 매칭한다.
GENERIC_TAILS = {
    'ko': ['가격', '무료'],
    'en': ['price', 'free'],
    'ja': ['価格', '無料'],
    'zh': ['价格', '免费'],
    'es': ['precio', 'gratis'],
    'fr': ['prix', 'gratuit'],
    'de': ['preis', 'gratis'],
    'pt': ['preço', 'grátis'],
    'ru': ['цена', 'бесплатно'],
    'id': ['harga', 'gratis'],
    'ar': ['السعر', 'مجاني'],
    'bn': ['দাম', 'ফ্রি'],
    'hi': ['कीमत', 'मुफ़्त'],
}
# 언어 판별 불가 fallback (영문/숫자만 있거나 표에 없는 경우)
OLD_TAILS_FALLBACK = ['is it worth', 'worth buying', 'worth playing', 'worth downloading',
                      'who should skip', 'what is free', 'what costs money']

LANG_NAME = {
    'ko': 'Korean', 'en': 'English', 'ja': 'Japanese', 'zh': 'Chinese',
    'es': 'Spanish', 'de': 'German', 'fr': 'French', 'pt': 'Portuguese',
    'ru': 'Russian', 'id': 'Indonesian', 'ar': 'Arabic', 'bn': 'Bengali',
    'hi': 'Hindi',
}


def parse_md(path):
    raw = io.open(path, encoding='utf-8').read()
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', raw, re.S)
    if not m:
        return None, ''
    fm = {}
    for line in m.group(1).splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm, m.group(2).strip()


def write_md(path, fm, body):
    out = ['---']
    for k, v in fm.items():
        out.append('%s: "%s"' % (k, v.replace('"', '\\"')))
    out.append('---')
    out.append('')
    out.append(body)
    out.append('')
    io.open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(out))


def is_store_post(fm, body):
    """스토어에서 발췌한 앱/게임 글인지 판별.

    ⚠️ 산업 뉴스 글(GameLook·游戏日报 등)도 같은 content/game 에 있고
       category="Apps & Games" + sourceUrl 을 갖는다 → **제외**해야 한다.
       (뉴스 글 제목은 이미 사람이 쓴 자유 제목이라 건드리면 안 된다)
    """
    if fm.get('category') != 'Apps & Games':
        return False
    if (fm.get('sourceName') or '').strip() not in ('Google Play', 'App Store'):
        return False
    return bool(fm.get('sourceUrl'))


def has_old_tail(title, lang='ko', app_name=''):
    """구형 고정 꼬리말로 "이름만 + 꼬리말" 형태인 제목인지 판별 (언어별).

    판별 기준:
      - 제목 = "앱이름 + 고정꼬리말" 일 때만 True.
      - app_name 을 넘겨주면 **머리부분이 앱 이름과 실제로 일치하는지**까지 본다.
        → "앱이름：내용，值得玩吗" 처럼 앞에 내용이 있는 정상 제목은 걸리지 않는다.
      - 부분 문자열 매칭은 "무료 9x9 블록 퍼즐" 정상 제목까지 잡는다.
    """
    t = (title or '').strip()
    if not t:
        return False
    tails = OLD_TAILS.get(lang, OLD_TAILS_FALLBACK)
    if lang not in OLD_TAILS:
        tails = list(tails) + list(OLD_TAILS_FALLBACK)
    nm = (app_name or '').strip()
    low = t.lower()
    STRIP = ' \u2014\u2013-:\u00b7|,\u060c\u3001?\uff1f!\uff01'
    for x in list(tails) + list(GENERIC_TAILS.get(lang, [])):
        xl = x.lower()
        if not low.endswith(xl):
            continue
        head = t[:len(t) - len(x)].strip(STRIP)
        if not head:
            continue
        if nm:
            # 머리부분에서 앱 이름을 빼고 남은 게 있으면(=내용이 붙었다) 정상 제목.
            if nm not in head:
                continue
            residue = head.replace(nm, '', 1).strip(STRIP)
            if len(residue) >= 4:
                continue
            return True
        else:
            if len(head) <= 60:
                return True
    return False


# 대괄호가 이스케이프된 alt("![\[Premium\] RPG ...]")도 잡아야 한다.
#   `\\.` 로 이스케이프된 문자를 먼저 소비해 `\]` 가 닫는 괄호로 오인되는 것을 막는다.
_RX_ALT = re.compile(r'^!\[((?:\\.|[^\]\\])*)\]', re.M)


def name_from_alt(body):
    """본문 첫 이미지 alt 텍스트 = 앱/게임 이름 (가장 신뢰할 수 있는 소스).

    ⚠️ 제목에서 이름을 뽑으면 "이름 : 꼬리말 – 더" 같은 형태에서
       이름과 설명이 섞여 구분하기 어렵다.
       → alt 텍스트는 수집 시 스토어 이름 그대로 들어가므로 정확하다.
       (384/384 스토어 글에서 alt 가 이름과 일치 확인)
    """
    m = _RX_ALT.search(body or '')
    if not m:
        return ''
    t = m.group(1).strip()
    # 마크다운 alt 에서 대괄호가 이스케이프된 경우 풀어 준다
    # (앱 이름에 대괄호가 있으면 "[Premium] RPG Isekai Rondo" → \[Premium\] RPG Isekai Rondo)
    t = t.replace('\\[', '[').replace('\\]', ']')
    # HTML 엔티티 복원 (&#39; → ', &amp; → &)
    # → title_name_intact() 가 엔티티 형태/실제 형태 **둘 다** 허용하므로 안전.
    t = t.replace('&amp;', '&').replace('&quot;', '"').replace('&#39;', "'")
    try:
        import store_label as SL
        t = SL.clean_name(t) or t
    except Exception:
        pass
    return t.strip()


def app_name_of(fm, body):
    """앱/게임 이름을 얻는다.

    우선순위:
    1) 본문 첫 이미지 alt 텍스트 (스토어 이름 그대로 → 가장 정확)
    2) 제목에서 추출 (fallback)
    """
    nm = name_from_alt(body)
    if nm and len(nm) <= 90:
        return nm
    return app_name_of_title(fm)


def app_name_of_title(fm):
    """frontmatter 의 제목에서 앱/게임 이름만 뽑아낸다 (fallback).

    제목은 "이름 — 꼬리말" 형태라 구분자로 끊으면 이름이 나온다.
    ⚠️ 앱 이름 자체에 하이픈이 들어있는 경우가 많다
       ("Chromarow - Color Routes", "Deckbane - 포커 & 덱빌딩").
       → **마지막 구분자**를 기준으로 끊어야 이름이 온전히 남는다.
    ⚠️ 스토어 라벨(" - Aplicaciones en Google Play")이 붙어 있을 수 있다
       → 라벨을 먼저 떼어 낸 뒤 이름을 뽑는다.
    """
    t = (fm.get('title') or '').strip()
    try:
        import store_label as SL
        t = SL.clean_name(t) or t
    except Exception:
        pass
    for sep in (' — ', ' – '):
        if sep in t:
            return t.split(sep)[0].strip()
    # " - " 는 앱 이름에 자주 들어가므로 **뒤에서부터** 마지막 것을 쓴다.
    if ' - ' in t:
        parts = t.rsplit(' - ', 1)
        if len(parts[0]) <= 60:
            return parts[0].strip()
    return t


def title_prompt(app_nm, body, lang, tmax, is_game):
    """본문 내용을 근거로 제목만 다시 쓰게 한다."""
    import app_radar as A
    pool = A.title_intent_pool(lang, is_game=is_game)
    snippet = re.sub(r'\s+', ' ', body)[:2500]
    sysmsg = (
        'You write SEO headlines for an international app and games site. '
        'You reply with one single raw JSON object and nothing else: {"title": "..."}. '
        'No markdown fence, no commentary.'
    )
    user = (
        'APP/GAME NAME: ' + app_nm + '\n'
        'LANGUAGE: write the title entirely in ' + LANG_NAME.get(lang, lang) + '.\n\n'
        'ARTICLE BODY (the title must describe THIS article):\n' + snippet + '\n\n'
        'TASK: write a headline for this article.\n'
        '- The title MUST begin with the exact name below, copied character by character, '
        'including any hyphen, colon, ampersand or punctuation inside it:\n'
        '    NAME = "' + app_nm + '"\n'
        '  Do not translate, transliterate, shorten, reorder, or replace any punctuation inside '
        'the name. If the name contains " - ", keep that " - " exactly. If it contains a colon, '
        'keep the colon.\n'
        '- After the name, write what this article is actually about: its genre, its price '
        'model, its core system, its session length, its device support, or the reader it is '
        'for. Use the facts from the body above.\n'
        '- Make the tail specific to this app. A tail that could be pasted onto any app name is '
        'rejected — it must carry information.\n'
        '- You may use a question form if the article answers a question a reader would ask, but '
        'the name comes first either way.\n'
        '- Vary the shape: not every title needs the same separator or the same length. Do not '
        'use the same separator in every title. Use a question form in some titles and a '
        'description in others.\n'
        '- Write the price the way a local reader would: use the currency word or symbol '
        'naturally ("$4.99", "무료", "￦7,700", "4,99 €"). Never write a bare converted number '
        'with no currency meaning ("7,700"), and never write the currency twice.\n'
        '- Maximum ' + str(tmax) + ' characters. No exclamation mark, no hype adjective, no '
        'number the body does not state.\n'
        '- SEARCH-TERM POOL (optional, use freely): ' + ' / '.join(pool) + '\n\n'
        'JSON FORMAT: {"title": "..."}\n'
    )
    return sysmsg, user


def has_tail_content(title, app_name):
    """제목에 이름 외에 내용 서술이 있는지 판별.

    라벨 제거로 "앱이름" 만 남은 경우("CookieRun Classic", "Bubble Clue Sort — prix")
    → 이름 외 부분이 4자 미만이면 내용 서술이 없는 것으로 본다.
    """
    t = (title or '').strip()
    nm = (app_name or '').strip()
    if not nm:
        return True
    rest = t.replace(nm, '', 1).strip(' —–-:·|,，、?？!！')
    return len(rest) >= 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lang', default='ko')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='구형 꼬리말이 아니어도 다시 씀')
    ap.add_argument('--fix-name-only', action='store_true',
                    help='이름만 남은 제목(스토어 라벨 제거 후)도 재생성')
    args = ap.parse_args()

    import app_radar as A
    import llm

    paths = []
    for d in STORE_DIRS:
        paths += glob.glob(os.path.join(d, args.lang, '*.md'))
    paths.sort()

    targets = []
    for p in paths:
        fm, body = parse_md(p)
        if not fm:
            continue
        if not is_store_post(fm, body):
            continue
        title = fm.get('title') or ''
        if not args.force and not has_old_tail(title, args.lang, app_name_of(fm, body)):
            # 구형 꼬리말이 아니어도 **이름만 남은 제목**은 재생성 대상
            # (스토어 라벨 제거 후 "앱이름 — 가격" / "앱이름" 형태로 남은 경우)
            if not (args.fix_name_only and not has_tail_content(title, app_name_of(fm, body))):
                continue
        targets.append((p, fm, body))
    if args.limit:
        targets = targets[:args.limit]

    print('대상 %d건 (언어=%s)' % (len(targets), args.lang))
    if not targets:
        return 0

    used = set()
    shapes = set()
    shape_count = {}
    sep_count = {}
    ok = 0
    for i, (p, fm, body) in enumerate(targets, 1):
        nm = app_name_of(fm, body)
        if not nm:
            continue
        tmax = A.title_max(args.lang, fm.get('status') == 'released')
        is_game = 'game' in p.replace('\\', '/')
        sysmsg, user = title_prompt(nm, body, args.lang, tmax, is_game)
        got = ''
        try:
            got, prov = llm.chat([{'role': 'system', 'content': sysmsg},
                                  {'role': 'user', 'content': user}], purpose='write')
        except Exception as e:
            print('  [%d] LLM 실패: %s' % (i, e))
            continue
        o = A.extract_json(A.strip_code_fences(got))
        newt = (o.get('title') or '').strip()
        if not newt:
            print('  [%d] 제목 파싱 실패 — %s' % (i, nm))
            continue
        newt = re.sub(r'"', "'", newt)
        # 🔴 제목 초과 → 한 번 더 짧게 요청 (LLM 은 길이를 자꾸 넘긴다)
        tok, why = A.check_title(newt, nm, args.lang, tmax, is_game=is_game)
        if not tok and why.startswith('제목 초과'):
            shorter = (user + '\n- IMPORTANT: the previous attempt was %d characters, over the '
                       'limit of %d. Write a MUCH shorter title — cut every optional word, '
                       'keep only the app name and the single most useful fact.\n'
                       % (len(newt), tmax))
            try:
                got2, _ = llm.chat([{'role': 'system', 'content': sysmsg},
                                    {'role': 'user', 'content': shorter}], purpose='write')
                o2 = A.extract_json(A.strip_code_fences(got2))
                t2 = re.sub(r'"', "'", (o2.get('title') or '').strip())
                if t2:
                    tok2, why2 = A.check_title(t2, nm, args.lang, tmax, is_game=is_game)
                    if tok2:
                        newt = t2
                        tok, why = True, ''
                    else:
                        print('  [%d] 짧게 재시도도 실패(%s)' % (i, why2))
            except Exception as e:
                print('  [%d] 재시도 실패: %s' % (i, e))
        if not tok and why.startswith('제목 초과'):
            # 그래도 초과 → 구분자 단위로 잘라 되돌린다 (앱/게임명은 절대 자르지 않음).
            trimmed = A.trim_title(newt, tmax)
            if trimmed and nm and nm in trimmed:
                t2, w2 = A.check_title(trimmed, nm, args.lang, tmax, is_game=is_game)
                if t2:
                    print('  [%d] 제목 초과 → 절단(%d자)' % (i, len(trimmed)))
                    newt, tok, why = trimmed, True, ''
        if not tok:
            print('  [%d] 검증 실패(%s) — %s' % (i, why, newt[:60]))
            continue
        # ⚠️ 앱/게임명은 원본 그대로여야 한다 (구분자까지).
        #    모델이 "Chromarow - Color Routes" 를 "Chromarow, Color Routes" 로
        #    바꾸는 사고를 여기서 막는다.
        okname, nwhy = A.title_name_intact(newt, nm)
        if not okname:
            print('  [%d] 이름 손상(%s) — %s' % (i, nwhy, newt[:60]))
            continue
        if newt == fm.get('title'):
            print('  [%d] 기존과 동일 — %s' % (i, nm))
            continue
        key = re.sub(r'\W+', '', newt.lower())
        if key in used:
            print('  [%d] 이번 실행 내 중복 — %s' % (i, newt[:50]))
            continue
        # 🔴 형태 중복 방지: 제목 전체가 달라도 "이름 + 쉼표 + 꼬리말" 처럼
        #   구조가 같으면 검색 결과에서 여전히 똑같아 보인다.
        #   → **구분자 패턴**과 **꼬리말 첫 단어**로 구조 서명을 만든다.
        nm_part = nm.split(' — ')[0].split(' - ')[0].strip()
        tail_txt = newt
        if nm_part and tail_txt.startswith(nm_part):
            tail_txt = tail_txt[len(nm_part):]
        # 앞쪽 구분자 (— , - : · 등)
        m_sep = re.match(r'^([\s—–\-:·,،、]+)', tail_txt)
        sep_sig = 'SEP' if (m_sep and m_sep.group(1).strip()) else 'NOSEP'
        # 꼬리말 첫 단어 (문자 4개)
        tw = re.sub(r'^[^\w가-힣ぁ-ん一-鿿]+', '', tail_txt).strip()
        first_w = re.sub(r'[^\w가-힣ぁ-ん一-鿿]', '', tw.split()[0])[:4] if tw.split() else ''
        sig = (sep_sig, first_w)
        # 🔴 형태 중복 방지: **같은 구분자**가 과도하게 반복되면 검색 결과에서
        #   "이름, 꼬리말" 모양이 전부 같아 보인다.
        #   → 버리지 않고 **구분자만 바꿔서** 살린다 (제목 내용을 버리면 손해).
        #   ⚠️ 앱/게임 이름 안에 들어있는 쉼표·하이픈은 건드리면 안 된다.
        #     (예: "Chromarow, Color Routes" → 이름이 깨진다)
        #     → 구분자는 **이름 바로 뒤에서 시작하는 것만** 본다.
        tail_start = 0
        if nm_part and newt.startswith(nm_part):
            tail_start = len(nm_part)
        sep_txt = newt[tail_start:]
        m_sep2 = re.match(r'^([\s—–\-:·,،、]+)', sep_txt)
        sep_real = (m_sep2.group(1).strip() if m_sep2 else '')
        sep_key = sep_real[0] if sep_real else 'NOSEP'
        sep_count[sep_key] = sep_count.get(sep_key, 0) + 1
        if sep_count[sep_key] >= 4:
            # 이 구분자는 이미 과다 → 다른 구분자로 바꿔본다 (순서대로 시도).
            rest = sep_txt.lstrip(' —–-:·,،、｜：')
            # ⚠️ 언어별 적절한 구분자가 다르다.
            #   ko/ja/zh 는 "，" "：" "｜" 가 자연스럽고, " — " 는 어색하다.
            #   라틴계는 " — " " – " ": " 가 자연스럽다.
            if args.lang in ('ja', 'zh', 'ko'):
                alts = ['，', '：', '｜', '、']
            else:
                alts = [', ', ' — ', ': ', ' – ']
            if sep_key == ',':
                alts = [a for a in alts if a.strip() != ',']
            done = False
            for alt in alts:
                cand2 = newt[:tail_start] + alt + rest
                if len(cand2) > tmax:
                    continue
                # ⚠️ 앱/게임명 안의 구분자는 건드리지 않는다.
                #   (예: "Chromarow - Color Routes" → "Chromarow, Color Routes" 로 깨지면 안 됨)
                okname, nwhy = A.title_name_intact(cand2, nm)
                if not okname:
                    continue
                print('  [%d] 구분자 %s 과다 → %s 로 변경' % (i, sep_key, alt.strip()))
                newt = cand2
                sep_key = alt.strip()[0]
                sep_count[sep_key] = sep_count.get(sep_key, 0) + 1
                done = True
                break
            if not done:
                print('  [%d] 구분자 %s 과다 — 대체 구분자 없음, 그대로 유지' % (i, sep_key))
        shape_count[sig] = shape_count.get(sig, 0) + 1
        shapes.add(sig)
        used.add(key)
        print('  [%d] %s' % (i, nm))
        print('      이전: %s' % fm.get('title'))
        print('      변경: %s' % newt)
        if not args.dry:
            fm['title'] = newt
            write_md(p, fm, body)
        ok += 1
    print('\n완료: %d/%d건 변경%s' % (ok, len(targets), ' (dry)' if args.dry else ''))
    print(llm.stats_report())
    return 0


if __name__ == '__main__':
    sys.exit(main())
