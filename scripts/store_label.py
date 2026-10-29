# -*- coding: utf-8 -*-
"""스토어 라벨(Google Play / App Store 통용 접미사) 분리 유틸.

문제: Google Play 스토어 페이지의 <meta property="og:title"> 은
      앱 이름 뒤에 **언어별 스토어 라벨**이 붙은 형태로 나온다.
        ko "몬도쿠 - Google Play의 앱"
        es "몬도쿠 - Aplicaciones en Google Play"
        de "CookieRun: Kingdom – Apps bei Google Play"
        fr "Bubble Clue Sort – Applications sur Google Play"
        pt "Azur Lane – Apps no Google Play"
        ru "Приложения в Google Play – 1001Jewel Nights"  ← **앞쪽**에 붙음
        id "Clash of Clans - Aplikasi di Google Play"
        ar "CookieRun Classic - التطبيقات على Google Play"
        hi "Animal Forest : Fuzzy Seasons - Google Play पर ऐप्लिकेशन"
        bn "Block Puzzle Wood Blast - Google Play তে অ্যাপ"
        ja "桃源暗鬼 Crimson Inferno - Google Play のアプリ"
        zh "GrandChase - Google Play 上的应用"

      → 앱 이름에 스토어 라벨이 섞여 저장되고, 제목·설명·slug까지 오염된다.

제거 원칙 (안전 우선):
- 라벨 **앞에 구분자가 붙어 있는 경우에만** 제거한다. → 앱 이름에 붙은 라벨만 대상.
- "gratis di App Store" 처럼 라벨이 본문 서술로 쓰인 경우(앞에 구분자 없음)는 건드리지 않는다.
- 뒤쪽 구분자는 **남긴다** → "Name - LABEL — tail" → "Name — tail" / "Name: tail"
- 앱 이름 내부의 구분자(콜론 등)는 절대 건드리지 않는다.
"""
import re

# 언어별 스토어 라벨 문자열
LABEL_PHRASES = [
    # ko / ja / zh
    'Google Play의 앱', 'Google Play アプリ', 'Google Play 應用程式',
    'Google Play의 애플리케이션', 'Google Play 응용 프로그램',
    'Google Play アプリ', 'Google Play のアプリ', 'Google Play 上的应用',
    'Google Play 应用', 'Google Play 应用程式', 'Google Play 应用程式',
    # es / de / fr / pt / id / ru / ar / hi / bn
    'Aplicaciones en Google Play', 'Apps bei Google Play',
    'Applications sur Google Play', 'Apps no Google Play',
    'Aplikasi di Google Play', 'Приложения в Google Play',
    'Приложения Google Play', 'التطبيقات على Google Play',
    'تطبيقات على Google Play', 'Google Play पर ऐप्लिकेशन',
    'Google Play তে অ্যাপ', 'Google Play-তে অ্যাপ',
    # en
    'Apps on Google Play', 'Google Play Store', 'App Store',
]
# 긴 것부터 매칭
LABEL_PHRASES.sort(key=len, reverse=True)

_LABELS = '|'.join(re.escape(p) for p in LABEL_PHRASES)
_RX_LABEL = re.compile(_LABELS)

# 라벨 바로 앞 구분자 (하이픈류·세로선·콜론). 공백만으로는 안 된다 —
# "gratis di App Store" 는 라벨이 본문 서술이라 건드리면 안 된다.
_DELIM_BEFORE = re.compile(r'[\-–—:|｜：][\s]*$')


def _strip_once(s):
    """한 번의 라벨 제거 시도. (문자열, 제거여부)"""
    for m in _RX_LABEL.finditer(s):
        start, end = m.start(), m.end()
        head = s[:start]
        # 1) 문자열 시작 → ru 유형(앞쪽 라벨): 라벨 + 뒤 구분자 제거
        if not head.strip():
            tail = s[end:]
            mt = re.match(r'[\s\-–—:|｜：]+', tail)
            if mt:
                tail = tail[mt.end():]
            return head + tail, True
        # 2) 라벨 앞이 구분자 → 앱 이름에 붙은 라벨
        if _DELIM_BEFORE.search(head):
            # 앞 구분자까지 제거, 뒤 구분자는 남긴다
            m2 = _DELIM_BEFORE.search(head)
            return head[:m2.start()] + s[end:], True
        # 3) 라벨 앞이 일반 단어 → 본문 서술이므로 건드리지 않음
        return s, False
    return s, False


def strip_store_label(text):
    """제목/설명에서 앱 이름에 붙은 스토어 라벨을 제거한다.

    반환값: (정리된 문자열, 라벨 제거 여부)
    """
    if not text:
        return text, False
    s = text.strip()
    changed = False
    # 라벨 없이 순수 "Google Play" 만 뒤에 붙은 형태
    mb = _RX_BARE_GP.search(s)
    if mb and not _RX_LABEL.search(s):
        s = s[:mb.start()].rstrip(' -–—:|')
        changed = True
    for _ in range(4):
        s2, ch = _strip_once(s)
        if not ch:
            break
        s = s2.strip()
        changed = True
    if not changed:
        return text, False
    # ⚠️ 이름 내부의 구두점 앞 공백을 지우면 앱 이름이 손상된다
    #    ("Genshin Impact · Cloud" → "Genshin Impact· Cloud")
    #    → 공백 축약만 한다.
    s = re.sub(r'[\s]{2,}', ' ', s).strip()
    s = re.sub(r'^[\s\-–—:|｜：,、·]+', '', s)
    s = re.sub(r'[\s\-–—:|｜：,、·]+$', '', s)
    return s, True


def clean_name(name):
    """앱/게임 이름에서 스토어 라벨만 떼어 낸다 (그 외는 그대로)."""
    out, _ = strip_store_label(name)
    return out


# alt 텍스트에 대괄호가 들어간 이름("[Premium] RPG Isekai Rondo")도 잡으려면
# 닫는 대괄호를 뒤에서 찾아야 한다. `!\[([^\]]*)\]` 는 여기서 "Premium" 만 잡는다.
_RX_ALT_FULL = re.compile(r'!\[(.*?)\]\(')
_RX_ALT = re.compile(r'!\[([^\]]*)\]')


def alt_text(body):
    """본문 첫 이미지 alt 텍스트 (대괄호 포함 이름을 온전히)."""
    if not body:
        return ''
    m = _RX_ALT_FULL.search(body)
    if m:
        return m.group(1).strip()
    m = _RX_ALT.search(body)
    return m.group(1).strip() if m else ''


# 본문은 줄바꿈·문장 구조가 살아야 하므로 마크다운 이미지 alt 텍스트만 손본다.
_RX_ALT = re.compile(r'!\[([^\]]*)\]')


def strip_body_captions(body):
    """본문에서 마크다운 이미지 alt 텍스트의 스토어 라벨만 제거한다.

    ⚠️ 본문 **서술**("disponible en Google Play")은 지우면 정보가 사라진다 →
       건드리지 않는다. alt 텍스트("![앱명 - 라벨](url)")만 손본다.
    """
    if not body:
        return body, False
    changed = False

    def _repl(m):
        nonlocal changed
        txt = m.group(1)
        new, ch = strip_store_label(txt)
        if ch:
            changed = True
            return '![' + new + ']'
        return m.group(0)

    out = _RX_ALT.sub(_repl, body)
    return out, changed


def has_store_label(text):
    """스토어 라벨이 **앱 이름에 붙어** 있는지 판별 (본문 서술은 제외)."""
    if not text:
        return False
    for m in _RX_LABEL.finditer(text):
        head = text[:m.start()]
        if not head.strip():
            return True
        if _DELIM_BEFORE.search(head):
            return True
    # 라벨 없이 **순수 "Google Play"** 만 붙은 형태 ("… - Disney Speedstorm - Google Play")
    if _RX_BARE_GP.search(text):
        return True
    return False


# 끝에 붙은 순수 "Google Play" (통상 스토어 방문 문구)
_RX_BARE_GP = re.compile(r'(?:^|[\-–—:|｜：])[\s]*Google Play[\s]*$')
