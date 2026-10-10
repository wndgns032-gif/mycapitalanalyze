"""🧹 풀 속장난감 상품 제거 (2026-10-10).

왜 필요한가
------------
`ae_prefetch.py` 가 상품 필터를 통과했어도 **풀 파일에는 장난감이 들어있다.**
`_JUNK_PAT` 이 영어 위주라 `target_language=ko` 로 번역된
"장난감·인형·미니어처" 를 걸러내지 못했기 때문이다.

실측 결과: **영미 중앙은행 정책 글 119개**에
  "인형의 집 미니어처 파일폴더", "1 세트 미니 쓰기 보드 … 장난감 … 인형 집 부품"
이 붙어 있었다. → AdSense '광고-콘텐츠 관련성' 저위험.

왜 풀이 아니라 풀을 청소하는가
------------------------------
`render_html` 에서 걸러버리면 **포스트마다 남는 상품 수가 제각각**이 된다
(어떤 글은 4개, 어떤 글은 1개). 그러면:
  ① 카드 높이가 고르게 안 맞아 레이아웃이 깨진다
  ② "상품 4개" 규칙이 무너진다
→ **풀을 한 번 청소**하고 v2 재구축을 다시 돌리는 게 근본 해결이다.

사용법
------
    python scripts/ae_pool_clean.py --dry-run   # 몇 개나 잡히는지 미리보기
    python scripts/ae_pool_clean.py --apply     # 실제 청소 + v2 재구축
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import ae_autoslot as ae                       # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(BASE, "content", "affiliate_pool.json")


def clean_v2(apply_=False):
    """v2 풀의 `_titles` 에서 부적합 상품의 제목을 제거한다.

    마스터(URL·가격·이미지)는 언어 무관하므로 건드리지 않는다.
    어떤 언어의 제목이라도 부적합하면 **그 상품 전체를 키에서 제거**한다.
    한 언어로만 걸리면 나머지 언어로 노출되므로 → 전체 제거가 맞다.
    """
    with io.open(POOL, encoding="utf-8") as fh:
        pool = json.load(fh)

    if pool.get("_schema") != 2:
        print("⚠️ 풀 스키마가 v1이다. ae_pool_rebuild.py --apply 후 다시 실행하라.")
        return 0, 0

    titles = pool.get("_titles") or {}
    bad_ids = set()
    for pid, per_lang in titles.items():
        for lang, title in (per_lang or {}).items():
            if ae._is_unfit(title):
                bad_ids.add(pid)
                break

    items = pool.get("items") or {}
    removed = 0
    for key in list(items):
        before = len(items[key])
        items[key] = [i for i in items[key] if i not in bad_ids]
        removed += before - len(items[key])
    # 비어버린 키 삭제 (빈 리스트로 두면 폴백 경로를 오염시킨다)
    empty = [k for k, v in items.items() if not v]
    for k in empty:
        del items[k]

    for pid in bad_ids:
        titles.pop(pid, None)
        pool.get("_master", {}).pop(pid, None)

    pool["items"] = items
    pool["_titles"] = titles
    pool["_cleaned"] = {
        "date": ae.time.strftime("%Y-%m-%d"),
        "removed_product_ids": len(bad_ids),
        "removed_refs": removed,
        "empty_keys_dropped": len(empty),
        "note": "ae_pool_clean.py — 부적합(장난감/인형) 상품 제거.",
    }

    before_size = os.path.getsize(POOL)
    tmp = POOL + ".clean"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(pool, fh, ensure_ascii=False, separators=(",", ":"))
    after_size = os.path.getsize(tmp)

    print("부적합 상품: %d / %d" % (len(bad_ids), len(titles) + len(bad_ids)))
    print("참조 제거: %d건" % removed)
    print("빈 키 제거: %d개" % len(empty))
    print("남은 키: %d / 남은 상품 ref: %d"
          % (len(items), sum(len(v) for v in items.values())))
    print("크기 %.2f MB → %.2f MB" % (before_size / 1048576,
                                      after_size / 1048576))

    if apply_:
        os.replace(tmp, POOL)
        print("적용 완료.")
    else:
        os.remove(tmp)
        print("미리보기. --apply 를 주면 실제 적용한다.")
    return len(bad_ids), len(empty)


if __name__ == "__main__":
    clean_v2(apply_="--apply" in sys.argv)