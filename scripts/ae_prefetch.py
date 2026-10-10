"""🔴 AliExpress 상품 풀 전량 수확 (2026-10-10, 로이 지시).

왜 이 파일이 있는가
------------------
기존 `ae_autoslot.py` 는 **매 빌드마다 API를 실시간으로 때렸다.**
그 결과:

  1. API가 타임아웃/서명오류 → `_FAIL_STREAK` 가 3에 닿은 뒤
     **이후 모든 포스트가 조용히 카드를 못 넣는다** (실측 3개 누락).
  2. 빌드耗时이 API 응답에 좌우 → 10분 넘게 걸릴 때 있다.
  3. "모든 글에 확정적으로 들어간다" 는 로이의 요구를 만족 못 한다.

→|商品을 **한 번 전량 수확해서 로컬 JSON에 고정**한다.
   빌드는 이 파일만 읽는다. 네트워크 0. 100% 확정.

수확 대상
--------
- 모든 `TOPIC_QUERY` / `TOPIC_QUERY_I18N` / `ECON_MAP` / `CATEGORY_MAP` 검색어
- × 9개 언어 (`target_language` 로 상품명이 번역된다)
- × 2개 배송지 (KR·US)

예상 규모: 61 검색어 × 9 언어 × 2 배송지 = 1,098 건.
1 건당 25초 타임아웃 worst case 면 7시간이지만, 실제 평균 0.4초다.
중복 제거 후 페이지당 20개씩 저장.

사용법
------
    python scripts/ae_prefetch.py            # 전체 수확 (한 번)
    python scripts/ae_prefetch.py --check    # 현재 풀 통계만
    python scripts/ae_prefetch.py --refresh "phone stand"   # 특정어만 갱신

출력: `content/affiliate_pool.json` (git 커밋 대상)
      → 이 파일이 사라지면 build 는 자동으로 STATIC_POOL 로 폴백한다.
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import aliexpress                      # noqa: E402
import ae_autoslot as ae               # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(BASE, "content", "affiliate_pool.json")

# 🔑 빌드가 실제로 사용하는 언어만. (zh/bn/hi 는 상품명 번역을 지원하지
#    않으므로 영어 결과를 그대로 쓴다 → 별도 수확 불필요)
LANGS = ["en", "ko", "ja", "de", "es", "fr", "pt", "ru", "id"]
SHIP = ["US", "KR"]
PAGE_SIZE = 20
SLEEP = 0.0           # 스레드 병렬이라 요청 간 지연은 불필요
WORKERS = 6           # 동시 요청 수. 6 이 실측 안정값(그 이상은 타임아웃 증가)


def all_terms():
    """수확해야 할 검색어 전체집 (순서 유지)."""
    terms = []
    seen = set()

    # dict_values / list / tuple / str 을 모두 안전하게 순회한다.
    # (dict_values 에서 직접 순회하면 ValueError 가 난다 — 실측 함정)
    def add(value):
        if isinstance(value, str):
            value = [value]
        elif not isinstance(value, (list, tuple, set)):
            value = list(value)          # dict_values → list
        for t in value:
            t = (t or "").strip().lower()
            if t and t not in seen:
                seen.add(t)
                terms.append(t)

    add(ae.TOPIC_QUERY.values())
    add(ae.TOPIC_QUERY_I18N.values())
    for conf in ae.CATEGORY_MAP.values():
        add(conf["keywords"])
    for keywords in ae.ECON_MAP.values():
        add(keywords)
    return terms


def pool_key(keyword, lang, ship):
    """안정적인 캐시 키. 파일을 열어도 같은 값이어야 한다."""
    safe = re.sub(r"[^a-z0-9]+", "_", keyword.lower())[:60]
    return "%s|%s|%s" % (safe, lang, ship)


def load_pool():
    if not os.path.exists(POOL):
        return {}
    try:
        with open(POOL, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("items") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_pool(items, meta):
    os.makedirs(os.path.dirname(POOL), exist_ok=True)
    payload = dict(meta)
    payload["items"] = items
    tmp = POOL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, POOL)          # 원자적 교체 — 빌드 중 읽혀도 안전


def fetch(keyword, lang, ship):
    """검색 1건 → 정규화된 상품 리스트. 실패 시 빈 리스트(전체는 절대 멈추지 않음).

    ⚠️ 이 함수는 스레드에서 호출된다. 예외를 밖으로 던지면 안 된다.
    """
    for attempt in range(3):           # 타임아웃 재시도 3회
        try:
            resp = aliexpress.search_products(
                keyword, page_size=PAGE_SIZE, currency="USD",
                country=ship, language=lang,
            )
        except Exception as exc:
            if attempt == 2:
                return [], "%s: %s" % (type(exc).__name__, exc)
            time.sleep(1.5 * (attempt + 1))      # 백오프 후 재시도
            continue

        _total, products = aliexpress.extract_products(resp)
        out = []
        for item in products:
            norm = ae._normalize(item, ship)
            if not norm:
                continue
            if ae._JUNK_PAT.search(norm.get("title") or ""):
                continue
            if ae._is_junk_price(norm.get("price")):
                continue
            out.append(norm)
        return out, None
    return [], "retry exhausted"


def run(refresh=None, force=False):
    meta = {
        "_note": "ae_prefetch.py 가 생성. 빌드는 이 파일만 읽는다(네트워크 0).",
        "_schema": 1,
        "langs": LANGS,
        "ship": SHIP,
        "page_size": PAGE_SIZE,
        "_generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    items = load_pool()

    terms = [refresh.lower()] if refresh else all_terms()
    todo = [(t, l, s) for t in terms for l in LANGS for s in SHIP]

    # 캐시 히트면 건너뛴다 (--force 면 전부 다시)
    if not force and not refresh:
        todo = [x for x in todo if pool_key(*x) not in items]

    print("검색어 %d개 × 언어 %d × 배송지 %d = %d건%s"
          % (len(terms), len(LANGS), len(SHIP), len(todo),
             " (캐시 유지)" if len(todo) < len(terms) * len(LANGS) * len(SHIP)
             else ""))
    if not todo:
        print("전부 캐시됨. 작업 종료.")
        return 0

    ok = err = empty = 0
    errors = {}
    t0 = time.time()
    done = 0
    # 🔑 60건마다 중간 저장 — 프로세스가 죽어도 지금까지 분은 살린다.
    #   (순차版은 끝나야 저장돼서 47분 작업이 전부 날아갈 뻔했다)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool_exec:
        futures = {
            pool_exec.submit(fetch, t, l, s): (t, l, s)
            for (t, l, s) in todo
        }
        for fut in as_completed(futures):
            term, lang, ship = futures[fut]
            done += 1
            try:
                got, err_msg = fut.result()
            except Exception as exc:        # 스레드에서 예외가 새는 경우
                got, err_msg = [], "worker: %s" % type(exc).__name__

            if err_msg:
                err += 1
                kind = err_msg.split(":")[0]
                errors[kind] = errors.get(kind, 0) + 1
            elif not got:
                empty += 1
            else:
                items[pool_key(term, lang, ship)] = got
                ok += 1

            if done % 60 == 0 or done == len(todo):
                print("  [%4d/%4d] 성공 %d · 빈결과 %d · 실패 %d  (%.0fs)"
                      % (done, len(todo), ok, empty, err, time.time() - t0))
                sys.stdout.flush()
                save_pool(items, meta)          # 중간 저장
            time.sleep(SLEEP)

    save_pool(items, meta)

    total_products = sum(len(v) for v in items.values())
    print("\n=== 완료 ===")
    print("저장: %s" % POOL)
    print("키 %d개 · 상품 %d개 · %.1f KB"
          % (len(items), total_products, os.path.getsize(POOL) / 1024))
    print("이번 실행: 성공 %d / 빈결과 %d / 실패 %d" % (ok, empty, err))
    if errors:
        print("에러 유형:", json.dumps(errors, ensure_ascii=False))

    # 언어별 커버리지
    cov = {}
    for key in items:
        parts = key.split("|")
        if len(parts) == 3:
            cov[parts[1]] = cov.get(parts[1], 0) + len(items[key])
    print("언어별 상품수:", json.dumps(cov, ensure_ascii=False))
    return 0 if ok else 1


def check():
    items = load_pool()
    if not items:
        print("풀 비어 있다. `python scripts/ae_prefetch.py` 를 먼저 돌려라.")
        return 1
    print("키 %d개 · 상품 %d개" % (len(items), sum(len(v) for v in items.values())))
    by_lang = {}
    for key, prods in items.items():
        parts = key.split("|")
        if len(parts) == 3:
            by_lang[parts[1]] = by_lang.get(parts[1], 0) + len(prods)
    print("언어별:", json.dumps(by_lang, ensure_ascii=False))
    # 상위 상품명 5개 (실물 확인용)
    sample = []
    for prods in items.values():
        sample.extend(prods)
    uniq = {p["id"]: p for p in sample if p.get("id")}
    top = sorted(uniq.values(), key=lambda p: -len(p.get("title") or ""))[:5]
    for p in top:
        print("  -", (p.get("title") or "")[:60],
              "|", p.get("price"), p.get("currency"),
              "| 커미션", p.get("commission"))
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--check" in argv:
        sys.exit(check())
    refresh = None
    if "--refresh" in argv:
        idx = argv.index("--refresh")
        refresh = argv[idx + 1] if len(argv) > idx + 1 else None
    sys.exit(run(refresh=refresh, force="--force" in argv))