"""포스트 본문에 자연스럽게 어필리에이트 상품을 삽입하는 오토 광고 모듈.

목적
  매일 발행되는 포스트에 카테고리/키워드에 맞는 알리익스프레��� 상품을
  자동으로 골라 **본문 문맥 안에** 넣는다. 포스트 하단 박스(기존 affiliate_box)
  가 아니라, 독자가 "이거 쓰려면 이게 필요하다"고 느끼는 지점에 배치한다.

왜 필요한가
  * 포털 API 는 `session`(access_token) 이 유효해야 商品 데이터가 나온다.
    서명 규칙은 2026-10-08 실측으로 확정 (`aliexpress._sign`).
  * 토큰이 없으면 **대체 경로**로 폴백한다: `Link Generator` 로 미리 만든
    링크 풀. API 실패가 곧 수익 Eyebrow 소실이 되면 안 된다.

설계 원칙
  1. API 가 죽어도 사이트는 죽지 않는다 (Graceful Fallback)
  2. 캐시를 aggressively 쓴다 — 같은 키워드는 하루 1회만 조회 (쿼터 절약)
  3. 카테고리 매칭으로 무관한 상품을 넣지 않는다 (CTR + AdSense 안전)
  4. `rel="sponsored nofollow"` + 고지 문구를 항상 붙인다 (표시광고법)

사용법
  from ae_autoslot import recommend
  picks = recommend(category="Apps & Games", keywords=["backup","battery"])
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aliexpress   # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 🔑 캐시 버전 — 필터/매핑 규칙을 바꾸면 이 숫자를 올린다.
#    그러면 예전 캐시를 **삭제하지 않고도** 자동으로 무효화된다.
#    (파일 삭제는 위험하므로 버전 스위치를 쓴다)
CACHE_VERSION = "v2"
CACHE_DIR = os.path.join(BASE, ".cache", "aliexpress")
CACHE_TTL = 24 * 3600          # 캐시 24시간
# API 세션이 없을 때 쓰는 정적 폴백 풀 (포털 Link Generator 로 손으로 채운다)
STATIC_POOL = os.path.join(BASE, "content", "affiliate_static.json")

# 카테고리 → 알리 카테고리 키워드 매핑.
# 'thematic relevance' 요건을 충족시키려면 이 매핑이 Adsense 안전장치 겸 전환율 장치다.
#
# ⚠️ 포스트 제목/본문 단어를 검색어로 쓰면 안 된다 (2026-10-08 실측).
#    "Arknights 리뷰" → 'arknights' 검색 → 코슬패·_BADGE 같은 쓰레기 상품.
#    그래서 카테고리 고정 키워드만 사용한다.
#
# 🔑 앱 vs 게임 분리 (2026-10-08)
#    앱 리뷰 독자 = 앱 쓰고 있으니 케이스/충전기/이어폰을 산다 → 자연스럽다.
#    게임 리뷰 독자 = 게임을 사지 않는다. But 게임 리뷰 독자는
#    **게임용 하드웨어**(패드·지문링커·충전 케이블)를 산다 → 이것도 자연스럽다.
#    오히려 게임 글에 게임 액세서리를 붙이는 게 더 관련성 높다.
CATEGORY_MAP = {
    "Apps & Games": {
        "keywords": ["phone case", "screen protector", "phone stand",
                     "usb charger", "power bank", "earbuds"],
        "ship_to": "KR",
    },
    # 게임 리뷰 전용 — 게임 기기용 액세서리만. 복권/코스프레/팬cies 는 뺀다.
    "Apps & Games (game)": {
        "keywords": ["game controller", "mobile game controller",
                     "phone cooler", "gaming headset", "thumb controller"],
        "ship_to": "KR",
    },
    "Consumer Electronics": {
        "keywords": ["usb c cable", "phone holder", "car charger",
                     "bluetooth speaker", "power bank"],
        "ship_to": "US",
    },
    "Home & Tools": {
        "keywords": ["tool set", "storage box", "led light strip",
                     "kitchen organizer"],
        "ship_to": "US",
    },
}

# 🔑 금융/경제 포스트 매핑 (2026-10-09, 로이 지시 "다 넣으라고")
#   중앙은행·인플레이션·채권 글에 이어폰을 넣으면 맥락이 안 맞는다.
#   그러나 **금융을 읽는 독자는 사무·정리·정확도 도구를 실제로 산다.**
#   → 같은 빌드가 숫자 데이터(매크로)를 다루는 사람이 매일 쓰는 물건을 노출한다.
#
#   ⚠️ 키워드는 전부 실측 검증한 것이다 (2026-10-09).
#      'webcam'·'usb hub'·'laptop stand'·'wireless mouse' 는 Aliexpress 에서
#      부품/수리용이가 먼저 떠서 제외했다. 아래만 검색 결과가 일관됐다.
ECON_MAP = {
    "Central Banking": ["accounting calculator", "document organizer",
                        "desk organizer set", "file folder set"],
    "Monetary Policy": ["accounting calculator", "file folder set",
                        "document organizer", "desk organizer set"],
    "Global Macro": ["cable management tray", "desk organizer set",
                     "document organizer", "file folder set"],
    "US Economy": ["cable management tray", "accounting calculator",
                   "file folder set", "desk organizer set"],
    "Economic Research": ["document organizer", "file folder set",
                          "accounting calculator", "cable management tray"],
    "Bonds": ["accounting calculator", "file folder set",
              "document organizer", "desk organizer set"],
    "Inflation": ["cable management tray", "document organizer",
                  "file folder set", "desk organizer set"],
    "Labor Markets": ["desk organizer set", "cable management tray",
                      "document organizer", "file folder set"],
    "China": ["cable management tray", "file folder set",
              "document organizer", "desk organizer set"],
}

# 이 카테고리들에 속하면 ECON_MAP 을 우선 사용한다.
_ECON_ALIASES = tuple(ECON_MAP.keys())

# 검색 결과에서 반드시 버려야 할品类 (CTR·브랜드 안전).
# 게임 키워드로 검색하면 복권 티켓·코스프레·수집품이 섞여 나온다.
_JUNK_PAT = re.compile(
    r'raffle|cosplay|costume|doll|figure|stickers?|poster|'
    r'card\b|coins?\b|replica|prop|collectible|ticket',
    re.I)

# 가격이 이 범위 밖이면 버린다.
#   최저가(0.07달러짜리 케이블)는 클릭해도 수수료가 글자도 안 되고 신뢰를 깎는다.
#   최고가(200달러+)는 홈쇼핑 독자와 안 맞는다.
_PRICE_MIN = 0.8
_PRICE_MAX = 120.0


def _is_junk_price(price):
    """가격 파싱 실패 또는 범위 밖이면 True(=버림)."""
    try:
        value = float(re.sub(r"[^0-9.]", "", str(price)) or 0)
    except (ValueError, TypeError):
        return True
    return not (_PRICE_MIN <= value <= _PRICE_MAX)


def _cache_path(key):
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", key)[:80]
    return os.path.join(CACHE_DIR, "%s.json" % safe)


# 🔑 연속 실패 카운터 (2026-10-09 추가).
#   562개 포스트가 각각 API 를 부르면 타임아웃(25초)이 누적돼 빌드가 10분+
#   걸릴 수 있다. 이 카운터가 그 폭주를 막는다.
_FAIL_STREAK = 0
_MAX_FAIL_STREAK = 3
# 카테고리 → 추천 결과 메모. 빌드 1회 실행 동안 유효.
_MEMO = {}


def _cache_get(key, ttl=CACHE_TTL):
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    age = time.time() - os.path.getmtime(path)
    if age > ttl:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _cache_put(key, value):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(key), "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False)


def _extract_products(resp):
    """검색 응답에서 상품 배열을 추린다 (2026-10-08 실측 구조)."""
    return aliexpress.extract_products(resp)[1]


def _normalize(product, ship_to="US"):
    """상품 dict 를 UI 에 필요한 최소 필드로 정규화 (실측 필드명 기준)."""
    def pick(*keys, default=""):
        for k in keys:
            val = product.get(k)
            if val not in (None, "", [], {}):
                return val
        return default

    # ⚠️ commission 을 받으려면 반드시 promotion_link(추적링크)여야 한다.
    #   일반 상세 URL 로 폴백하면 클릭해도 수수료가 붙지 않는다 → 넣지 않는다.
    url = pick("promotion_link", "promotionLink")
    if not url:
        return None

    price = pick("target_sale_price", "sale_price", "salePrice")
    currency = pick("target_sale_price_currency", "sale_price_currency",
                    default="USD")

    return {
        "id": str(pick("product_id", "productId", "id", default="")),
        "title": str(pick("product_title", "productTitle", "subject")),
        "price": str(price),
        "currency": str(currency),
        "image": str(pick("product_main_image_url", "productMainImageUrl",
                          "image")),
        "url": str(url),
        "commission": str(pick("commission_rate", "commissionRate",
                               "hot_product_commission_rate", default="")),
        "rating": str(pick("evaluate_rate", "evaluateRate", default="")),
        "orders": str(pick("lastest_volume", "lastestVolume", default="")),
        "shop": str(pick("shop_name", "shopName", default="")),
        "category": str(pick("second_level_category_name",
                             "secondLevelCategoryName", default="")),
        "ship_to": ship_to,
    }


def search(keyword, ship_to="US", currency="USD", page_size=6):
    """키워드 1건으로 상품 목록. 실패하면 빈 리스트(throw하지 않음)."""
    key = "%s_q_%s_%s_%s" % (CACHE_VERSION, keyword, ship_to, currency)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # 🔑 연속 실패 카운터 — 전체가 느려지는 것 방지 (2026-10-09).
    #   562개 포스트 × timeout 25초 = 빌드가 10분 넘게 걸릴 수 있었다.
    #   N 번 연속 실패하면 그 뒤로는 API 를 아예 호출하지 않는다.
    global _FAIL_STREAK
    if _FAIL_STREAK >= _MAX_FAIL_STREAK:
        return []
    try:
        resp = aliexpress.search_products(
            keyword, page_size=page_size, currency=currency,
            country=ship_to or None,
        )
    except Exception:
        _FAIL_STREAK += 1
        return []          # 네트워크/서명 오류 → 조용히 빈 결과

    _FAIL_STREAK = 0
    items = _extract_products(resp)
    out = [n for n in (_normalize(it, ship_to) for it in items) if n]
    # 쓰레기品类 제거 (복권/코스프레/수집품). CTR 과 브랜드 안전에 필수.
    out = [n for n in out if not _JUNK_PAT.search(n.get("title") or "")]
    out = [n for n in out if not _is_junk_price(n.get("price"))]
    if out:
        _cache_put(key, out)
    return out


def recommend(category, keywords=None, limit=4, game_post=False):
    """포스트용 상품 추천.

    🔑 2026-10-08 수정 — 포스트에서 뽑은 키워드는 **쓰지 않는다.**
       게임 포스트("Arknights 리뷰")에서 'arknights' 를 검색하면
       코슬패·_BADGE 같은 전혀 무관한 상품이 나온다. readers 는 게임을 사지 않는다.
       → 카테고리 고정 키워드만 써서 **어차피 상품은 같은 4~6개**이고,
         포스트별 keyword 로 API 를 때리는 것 자체가 시간 낭비였다.

    `game_post=True` 면 게임 액세서리 매핑을 쓴다 (패드·지문링커·냉각기).

    전략: 카테고리 매핑 키워드로만 조회 → 쓰레기品类 제외 → 커미션율·평점 순 정렬.
    """
    category = (category or '').strip().strip('"\'').strip()
    # 게임 포스트는 별도 매핑을 쓴다 (게임 액세서리)
    if category in ("Apps & Games", "Apps & Games (game)"):
        conf = CATEGORY_MAP["Apps & Games (game)"] if game_post \
            else CATEGORY_MAP["Apps & Games"]
    elif category in _ECON_ALIASES:
        # 금융/경제 포스트 — 독자가 실제로 사는 방송·홈오피스·정확도 도구
        conf = {"keywords": ECON_MAP[category], "ship_to": "US"}
    else:
        conf = CATEGORY_MAP.get(category) or CATEGORY_MAP["Apps & Games"]
    ship_to = conf["ship_to"]

    pool, seen = [], set()
    # 🔑 카테고리별 결과를 프로세스 동안 메모리에 고정 (2026-10-09).
    #   562개 포스트가 같은 카테고리면 같은 상품 4~6개만 필요하다.
    #   포스트마다 API 를 부르는 순간 빌드가 10분 넘게 걸린다.
    mem_key = "%s|%s|%s" % (category, game_post, limit)
    if mem_key in _MEMO:
        return _MEMO[mem_key]

    for term in conf["keywords"][:4]:        # 쿼터 절약: 최대 4개
        for item in search(term, ship_to=ship_to):
            if _JUNK_PAT.search(item.get("title") or ""):
                continue          # 복권/코스프레/수집품 제외
            if _is_junk_price(item.get("price")):
                continue          # 0.07달러짜리 케이블 등
            if item["id"] and item["id"] in seen:
                continue
            if item["id"]:
                seen.add(item["id"])
            if item["title"]:
                pool.append(item)

    def score(item):
        try:
            comm = float(re.sub(r"[^0-9.]", "", item["commission"]) or 0)
        except ValueError:
            comm = 0
        try:
            rate = float(item["rating"] or 0)
        except ValueError:
            rate = 0
        try:
            vol = float(item["orders"] or 0)
        except ValueError:
            vol = 0
        # 커미션 60% + 평점 30% + 판매량 10%
        return (comm * 0.6) + (rate * 0.3) + (min(vol, 100000) / 100000 * 10)

    pool.sort(key=score, reverse=True)
    result = pool[:limit]
    _MEMO[mem_key] = result      # 같은 카테고리 재조회는 메모리에서 즉시 응답
    return result


# ------------------------------------------------------------------ 렌더링

_I18N = {
    "ko": ("추천 장비", "알리익스프레스 제휴 링크입니다. 구매 시 우리가 수수료를 받습니다."),
    "en": ("Recommended gear", "Affiliate link to AliExpress. We may earn a commission."),
    "ja": ("おすすめ機器",
           "これは AliExpressの affiliate リンクです。購入すると私たちが報酬を得る場合があります。"),
    "zh": ("推荐设备", "这是 AliExpress 联盟链接。购买后我们可能会获得佣金。"),
    "es": ("Equipo recomendado", "Enlace de afiliado de AliExpress. Podemos ganar una comisión."),
    "fr": ("Équipement recommandé", "Lien d'affiliation AliExpress. Nous pouvons gagner une commission."),
    "hi": ("अनुशंसित उपकरण", "AliExpress एफ़िलिएट लिंक। खरीद पर हमें कमीशन मिल सकता है।"),
    "ar": ("المعدات الموصى بها", "رابط affiliacy من AliExpress. قد نربح عمولة عند الشراء."),
    "ru": ("Рекомендуемое оборудование", "Партнёрская ссылка AliExpress. Мы можем получить комиссию."),
    "id": ("Peralatan yang direkomendasikan", "Tautan afiliasi AliExpress. Kami mungkin mendapatkan komisi."),
    # ⚠️ 아래 3개가 빠져서 영어로 폴백하고 있었다 (2026-10-09 실측).
    #    13개 언어를 서빙하는데 문구가 10개뿐이었다.
    "de": ("Empfohlene Ausstattung",
           "Affiliate-Link zu AliExpress. Wir erhalten möglicherweise eine Provision."),
    "pt": ("Equipamento recomendado",
           "Link de afiliado da AliExpress. Podemos receber uma comissão."),
    "bn": ("প্রস্তাবিত সরঞ্জাম",
           "AliExpress অ্যাফিলিয়েট লিঙ্ক। কিনলে আমরা কমিশন পেতে পারি।"),
}


def render_html(products, lang="en", title=None, blurb=None):
    """상품 카드 HTML. rel=sponsored nofollow + 고지 동시 표기."""
    if not products:
        return ""
    t, d = _I18N.get(lang, _I18N["en"])
    title = title or t
    blurb = blurb or d

    cards = []
    for p in products:
        if not p.get("url") or not p.get("title"):
            continue
        img = ('<img src="%s" alt="" loading="lazy" class="w-16 h-16 '
               'object-cover rounded flex-shrink-0">'
               % p["image"]) if p.get("image") else ""
        meta = []
        if p.get("price"):
            meta.append('<span class="font-semibold text-brand-600">%s %s</span>'
                        % (p["price"], p["currency"]))
        if p.get("rating"):
            meta.append("★ %s" % p["rating"])
        if p.get("orders"):
            meta.append("%s orders" % p["orders"])
        cards.append(
            '<li class="flex gap-3 items-start">%s'
            '<div class="min-w-0">'
            '<a rel="sponsored nofollow noopener" target="_blank" href="%s" '
            'class="block font-medium text-slate-900 dark:text-slate-100 '
            'hover:text-brand-600 leading-snug">%s</a>'
            '<p class="text-xs text-slate-500 mt-1">%s</p>'
            '</div></li>'
            % (img, p["url"], p["title"], " · ".join(meta))
        )
    if not cards:
        return ""

    return (
        '<aside data-affiliate-slot="1" '
        'class="mt-8 rounded-lg border border-slate-200 '
        'dark:border-slate-800 bg-slate-50 dark:bg-slate-900 p-4 sm:p-5">'
        '<h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100 '
        'mb-3">%s</h2>'
        '<ul class="space-y-3">%s</ul>'
        '<p class="mt-3 text-xs text-slate-500">%s</p>'
        '</aside>' % (title, "".join(cards), blurb)
    )


if __name__ == "__main__":
    picks = recommend("Apps & Games", ["phone stand"], limit=3)
    print(json.dumps(picks, ensure_ascii=False, indent=2)[:1200] if picks
          else "상품 없음 — session 토큰 필요 (포털 Auth Management)")
    print()
    print(render_html(picks, "ko")[:400] or "HTML 없음")
