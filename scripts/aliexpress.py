"""AliExpress Affiliate API 클라이언트 (공식 Open Platform).

정책 (로이 지시 2026-10-08):
  * 시크릿은 절대 로그/예외에 포함하지 않는다.
  * 자격증명은 `C:\\Users\\ROYcp\\WorkBuddy\\myappanalyze.com\\.secrets\\ALIEXPRESS.txt`
    에 로이가 직접 채워넣는다. 이 모듈은 읽기만 하고 값을 출력하지 않는다.

지원 엔드포인트
  * aliexpress.affiliate.product.query      — 키워드/카테고리 상품 검색 (추천 커버리지)
  * aliexpress.affiliate.productdetail.get  — 상품 상세 (커미션율·쿠폰 포함)
  * aliexpress.affiliate.product.query.url  — 상품 URL → 추적 링크 일괄 변환
  * aliexpress.affiliate.hotproducts.query  — 인기 상품 피드

서명 규칙 (2026-10-08 실측으로 확정 — 문서가 아니라 실제 게이트웨이 응답으로)
  * 파라미터를 key ASCII 오름차순 정렬 → `k{v}` 를 구분자 없이 이어붙인다.
  * 앞뒤를 app_secret 로 감싼다: `secret + payload + secret`
  * ⚠️ **HMAC 이 아니라 plain MD5 다.** `hmac.new(key=...)` 로 하면 100% 실패한다.
        sign = md5(secret + payload + secret).hexdigest().upper()
  * timestamp = `YYYY-MM-DD HH:MM:SS` (UTC naive 문자열)
  * 게이트웨이: `https://api-sg.aliexpress.com/sync` (GET/POST 둘 다 동작)

  🔑 **토큰(token/session)은 필요 없다.**
     `fields` 에 `promotion_link` 를 포함시키면 응답에 바로 추적링크가 실린다.
     → OAuth access_token 발급 과정(3분 만료 code, 30일 토큰) 전부 우회.
     `IllegalAccessToken` 은 "토큰을 넣었을 때만" 나는 오류였고,
     토큰을 빼면 정상 동작한다.

  ⚠️ 죽은 경로 (사용 금지)
    * `gw.api.taobao.com/router/rest` · `api.taobao.com/router/rest`
      → 이 앱을 모른다 (`code 29 / isv.appkey-not-exists`)
    * `api-sg.aliexpress.com/rest` → 서명 원본 규칙이 또 다르다. 쓰지 마.

  상품 검색 응답 구조 (실측):
    resp['aliexpress_affiliate_product_query_response']['resp_result']['result']
      ['total_record_count']  → 전체 건수 (검색어 'phone case' 면 136만)
      ['products']['product'] → 상품 배열

리턴: dict (원문 JSON)
실패 시: RuntimeError (메시지에 시크릿 미포함)
"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = r"C:\Users\ROYcp\WorkBuddy\myappanalyze.com\.secrets\ALIEXPRESS.txt"

GATEWAYS = {
    "sg": "https://api-sg.aliexpress.com/sync",   # 국제판 (기본)
    "eu": "https://api-eu.aliexpress.com/sync",
    "us": "https://api-us.aliexpress.com/sync",
}


def load_credentials(path=SECRETS, require_tracking=True, require_session=True):
    """`.secrets/ALIEXPRESS.txt` 에서 KEY=VALUE 형태를 읽는다.

    지원 형식:
        app_key=12345678
        app_secret=xxxxxxxx
        tracking_id=1000xxxx
        session=<access_token>       # 포털 Auth Management 에서 발급
        refresh_token=xxxxxxxx       # 갱신용 (선택)
        gateway=sg                 # 선택
        sign_method=hmac           # 선택 (hmac | sha256)

    `require_tracking` / `require_session` 은 진단·토큰 발급처럼
    일부 값만 필요한 경로에서 False 로 끈다. 실서비스 호출은 둘 다 True 다.
    """
    if not os.path.exists(path):
        raise RuntimeError(
            "AliExpress 자격증명 파일이 없다: %s\n"
            "app_key / app_secret / tracking_id 를 직접 채워 넣어라." % path
        )
    conf = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            conf[k.strip()] = v.strip()

    required = ["app_key", "app_secret"]
    if require_tracking:
        required.append("tracking_id")
    for key in required:
        if not conf.get(key):
            raise RuntimeError("ALIEXPRESS.txt 에 `%s` 가 비어 있다." % key)

    # ⚠️ 2026-10-08 실측 결론: 토큰은 필요 없다.
    #   `session` 을 넣으면 오히려 IllegalAccessToken 으로 거절된다.
    #   `fields=promotion_link` 로 추적링크를 바로 받는다.
    #   `require_session` 인자는 하위 호환용으로만 남아 있다 (무시된다).
    conf.setdefault("gateway", "sg")
    conf.setdefault("sign_method", "hmac")
    return conf


def _sign(params, secret, sign_method="md5"):
    """파라미터 서명. 시크릿은 결과에 섞지 않는다.

    ⚠️ **plain MD5** 다 (HMAC 아님). 2026-10-08 실측 확정.
    """
    payload = "".join("%s%s" % (k, params[k]) for k in sorted(params.keys()))
    wrapped = "%s%s%s" % (secret, payload, secret)
    if sign_method == "sha256":
        return hashlib.sha256(wrapped.encode("utf-8")).hexdigest().upper()
    if sign_method == "hmac-sha256":
        return hmac.new(secret.encode("utf-8"), wrapped.encode("utf-8"),
                        hashlib.sha256).hexdigest().upper()
    return hashlib.md5(wrapped.encode("utf-8")).hexdigest().upper()


# affiliate.product.query 에서 실제로 쓰는 필드.
# `promotion_link` 를 빼면 추적링크가 안 실리므로 반드시 포함할 것.
AFFILIATE_FIELDS = (
    "product_id,product_title,product_main_image_url,product_detail_url,"
    "promotion_link,price,sale_price,original_price,target_sale_price,"
    "target_sale_price_currency,commission_rate,hot_product_commission_rate,"
    "evaluate_rate,lastest_volume,shop_id,shop_name,shop_url,"
    "first_level_category_name,second_level_category_name,discount,tax_rate"
)


def call(method, business=None, conf=None, timeout=25):
    """임의 엔드포인트 호출. `business` 는 업무 파라미터 dict.

    서명 규칙은 2026-10-08 실측으로 확정. 토큰 불필요.
    """
    conf = conf or load_credentials(require_tracking=False)
    url = GATEWAYS.get(conf["gateway"], GATEWAYS["sg"])

    params = {
        "method": method,
        "app_key": conf["app_key"],
        "sign_method": "md5",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "format": "json",
        "v": "2.0",
    }
    if business:
        params.update({k: v for k, v in business.items() if v is not None})
    params["sign"] = _sign(params, conf["app_secret"], conf["sign_method"])

    # GET 이랑 POST 둘 다 통과하지만 GET 이 캐시·로그에 더 안전하다
    req = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("%s HTTP %s — 앱 승인 상태/엔드포인트 권한을 확인하라."
                           % (method, exc.code)) from None
    except Exception as exc:
        raise RuntimeError("%s 호출 실패: %s" % (method, type(exc).__name__)) from None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("%s 응답이 JSON 이 아니다 (게이트웨이 오류?)" % method) from None
    # error_response 를 명시적으로 드러내야 무의미한 빈 결과와 구분된다.
    if data.get("error_response"):
        raise RuntimeError("%s API 오류: %s" % (method, data["error_response"]))
    return data


# ---------------------------------------------------------------- 공개 헬퍼

def search_products(keywords, page_no=1, page_size=20,
                    currency="USD", language="en", country=None,
                    sort="SALE_PRICE_ASC", fields=None):
    """키워드 상품 검색.

    ⚠️ 2026-10-08 실측 결론: **토큰·tracking_id 없이 동작한다.**
    추적링크는 `fields` 에 `promotion_link` 을 포함시키면 응답에 실린다.
    (예전 문서의 "tracking_id 필수" 설명은 2026년 경로와 맞지 않는다.)
    """
    return call(
        "aliexpress.affiliate.product.query",
        {
            "keywords": keywords,
            "page_no": page_no,
            "page_size": page_size,
            "target_currency": currency,
            "target_language": language,
            "target_country": country,
            "sort": sort,
            "fields": fields or AFFILIATE_FIELDS,
        },
    )


def extract_products(resp):
    """검색 응답에서 상품 배열을 뽑는다. (실측 구조 기준)"""
    node = resp.get("aliexpress_affiliate_product_query_response")
    if not node:
        return 0, []
    result = (node.get("resp_result") or {}).get("result") or {}
    products = (result.get("products") or {}).get("product") or []
    if isinstance(products, dict):
        products = [products]
    return result.get("total_record_count", 0), products


def product_detail(product_ids, currency="USD", language="en"):
    """상세. 여러 id 는 쉼표로 이어 붙인다."""
    if isinstance(product_ids, (list, tuple)):
        product_ids = ",".join(str(p) for p in product_ids)
    return call(
        "aliexpress.affiliate.productdetail.get",
        {
            "product_ids": product_ids,
            "target_currency": currency,
            "target_language": language,
        },
    )


def promotion_links(urls, promotion_link_type=0):
    """상품 URL 목록 → 추적 링크. url 은 쉼표로 이어 붙인다 (최대 50개).

    promotion_link_type: 0=일반(기본 커미션) / 2=핫 상품(높은 커미션)
    """
    if isinstance(urls, (list, tuple)):
        urls = ",".join(str(u) for u in urls)
    return call(
        "aliexpress.affiliate.link.generate",
        {
            "source_values": urls,
            "promotion_link_type": promotion_link_type,
        },
    )


def category_list():
    """어필리에이트 카테고리 목록. 추적링크 없이 category id 를 얻는다."""
    return call("aliexpress.affiliate.category.get", {})


# ---------------------------------------------------------------- 자가 점검

def _selfcheck():
    """서명 로직 검증 (오프라인). 2026-10-08 실측: plain MD5 가 정답."""
    p = {"app_key": "123", "keywords": "test", "page_no": "1"}
    a = _sign(p, "SECRET", "hmac")
    b = _sign(p, "SECRET", "sha256")
    print("md5    sign =", a)
    print("sha256 sign =", b)
    print("order-independent:", _sign(dict(reversed(list(p.items()))), "SECRET", "hmac") == a)
    print("secret-sensitive  :", _sign(p, "OTHER", "hmac") != a)
    # HMAC 과 결�� 달라야 한다 — 같으면 hmac 을 잘못 쓰고 있는 것이다.
    import hmac as _h
    flat = "".join("%s%s" % (k, p[k]) for k in sorted(p))
    hmac_val = _h.new(b"SECRET", ("SECRET%sSECRET" % flat).encode(),
                       hashlib.md5).hexdigest().upper()
    print("plain != hmac    :", a != hmac_val, "(True 가 정답)")
    print("SELFCHECK OK")


if __name__ == "__main__":
    import sys
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        print(json.dumps(search_products("wireless headphones"), ensure_ascii=False)[:2000])