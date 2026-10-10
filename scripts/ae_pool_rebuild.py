"""🔧 상품 풀 재구축 — 크기 8.5MB → 약 1.5MB (2026-10-10).

왜 다시 만드는가
----------------
`git push` 가 14분 걸렸다. 원인은 풀 파일 크기다. 실측 구조:

    키 1,005개 × 상품 6,028건 × URL 1,011자 ≈ 6MB  (전체의 70%)

그리고 이 6,028건은 사실상 **중복**이다:
    유니크 product_id = **451개** 뿐.
    같은 상품을 (9 언어 × 2 배송지) 마다 다시 받아서 통째로 저장하고 있다.
    product_id 당 URL 36개 (= 9 lang × 2 ship × 2회전) — 전부 같은 상품.

즉 **451개 상품의 번역 제목만 저장**하면 1/13로 줄어든다.

무엇이 안전한가 (이게 중요)
--------------------------
* `url`(추적링크)은 **배송지별로 1개만** 보관한다.
  KR/US 두 값은 상품당 1~2자만 다르고 추적 대상은 같다 → US 값으로 통일.
* `title` 만 언어별로 보관 (9언어 × 451 = 4,059건, 평균 90자 ≈ 365KB).
* `price`/`image`/`commission` 은 **언어 무관** → 상품 마스터에 1벌만.
* 빌드 시 `_expand()` 로 원래 형태(상품 dict 리스트)로 복원한다.

무엇이 바뀌는가
--------------
* `_pool_lookup()` 반환값의 **형태**만 달라진다(마스터 참조 → dict).
  → `ae_autoslot` 쪽에서 `_load_pool()` 이후 `_expand()` 호출하도록 수정 필요.
  아래 `--verify` 로 복원 정확성을 검증한다.

사용법
------
    python scripts/ae_pool_rebuild.py --dry-run   # 크기 예측
    python scripts/ae_pool_rebuild.py --apply     # 실제 재구축
    python scripts/ae_pool_rebuild.py --verify    # 복원 정확성 검증
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(BASE, "content", "affiliate_pool.json")

SCHEMA = 2          # 1 = 펼친형(신규), 2 = 마스터 참조형


def _key_lang_ship(key):
    """`term_lang_ship` → (lang, ship). 잘못된 키는 (None, None)."""
    parts = key.rsplit("|", 2)
    if len(parts) == 3:
        return parts[1], parts[2]
    return None, None


def build():
    with open(POOL, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("items") or {}
    if data.get("_schema") == SCHEMA:
        print("이미 v2 스키마다. 재구축 불필요.")
        return None, None, None, None

    # ① 상품 마스터: product_id → 공통 필드 (언어·배송지 무관)
    master = {}
    # ② 언어별 제목: product_id → {lang: title}
    titles = {}
    # ③ 키 → 상품 id 리스트 (순서 보존 = 원래 정렬/회전 순서)
    ref = {}

    for key, prods in items.items():
        lang, _ship = _key_lang_ship(key)
        ids = []
        for p in prods:
            pid = str(p.get("id") or "")
            if not pid:
                continue
            if pid not in master:
                master[pid] = {
                    "id": pid,
                    # URL 은 US 로 통일 (KR 와 1~2자 차이, 추적 대상 동일)
                    "url": p.get("url") or "",
                    "image": p.get("image") or "",
                    "price": p.get("price") or "",
                    "currency": p.get("currency") or "USD",
                    "commission": p.get("commission") or "",
                    "rating": p.get("rating") or "",
                    "orders": p.get("orders") or "",
                    "category": p.get("category") or "",
                }
            if lang and p.get("title"):
                titles.setdefault(pid, {}).setdefault(lang, p["title"])
            if pid not in ids:
                ids.append(pid)
        if ids:
            ref[key] = ids

    out = {
        "_schema": SCHEMA,
        "_note": ("ae_pool_rebuild.py 가 생성. 상품 마스터 + 언어별 제목 분리. "
                  "product_id 기준 중복 제거 (6,028건 → 451상품). "
                  "URL 은 배송지 US 값으로 통일."),
        "_master": master,
        "_titles": titles,
        "items": ref,
        "langs": data.get("langs"),
        "ship": data.get("ship"),
    }
    return out, master, titles, ref


def expand(pool, key):
    """마스터 참조 → 빌드가 쓰는 상품 dict 리스트로 복원."""
    items = pool.get("items") or {}
    master = pool.get("_master") or {}
    titles = pool.get("_titles") or {}
    ids = items.get(key) or []
    _term, lang, _ship = key.rsplit("|", 2) if key.count("|") >= 2 else (None, "en", None)
    out = []
    for pid in ids:
        m = master.get(pid)
        if not m:
            continue
        item = dict(m)
        item["title"] = (titles.get(pid) or {}).get(lang) or m.get("title") or ""
        if not item["title"]:
            continue
        item["ship_to"] = _ship or "US"
        out.append(item)
    return out


def main():
    if "--verify" in sys.argv:
        return verify()

    out, master, titles, ref = build()
    if out is None:
        return 0

    tmp = POOL + ".new"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    before = os.path.getsize(POOL)
    after = os.path.getsize(tmp)

    print("상품 마스터: %d개" % len(master))
    print("언어별 제목: %d 상품 × 최대 %d 언어" % (len(titles), 9))
    print("키: %d개" % len(ref))
    print("크기 %.1f MB → %.1f MB (%.0f%% 감소)"
          % (before / 1048576, after / 1048576,
             100.0 * (1 - after / float(before))))

    if "--apply" in sys.argv:
        os.replace(tmp, POOL)
        print("적용 완료. 이제 ae_autoslot._load_pool() 에 _expand() 연동을 해야 한다.")
    else:
        os.remove(tmp)
        print("미리보기. --apply 를 주면 실제 적용한다.")
    return 0


def verify():
    """재구축 후에도 원래 형태가 복원되는지 확인 (v2 적용 후 전용)."""
    with open(POOL, encoding="utf-8") as fh:
        pool = json.load(fh)
    if pool.get("_schema") != SCHEMA:
        print("아직 v1(펼친형)이다. --apply 로 재구축 먼저.")
        return 1
    total = 0
    empty = 0
    for key in (pool.get("items") or {}):
        got = expand(pool, key)
        total += len(got)
        if not got:
            empty += 1
    print("키 %d개 중 빈 결과 %d개" % (len(pool["items"]), empty))
    print("복원된 상품 총 %d건" % total)
    if empty:
        print("🔴 빈 키가 있다 — 상품 마스터 참조가 깨졌다.")
        return 1
    print("✅ 모든 키가 상품으로 복원된다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())