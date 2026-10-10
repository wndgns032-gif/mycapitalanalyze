"""🔍 어필리에이트 커버리지 검증 (2026-10-10).

"모든 글에 상품 카드가 들어갔는가" 를 **수치로** 증명한다.
로이 지시 "알리익스프레스 제품이 없다면 다 집어넣어줘" 의 수용 기준.

사용법
------
    python scripts/verify_aff_coverage.py           # 요약 + 누락 목록
    python scripts/verify_aff_coverage.py --strict  # 누락 있으면 exit 1

CI 에서 이걸 돌리면 **카드 없는 글이 발행되는 회귀를 잡을 수 있다.**
"""
import glob
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

try:
    import ae_autoslot as _ae
except ImportError:      # 빌드 밖에서 단독 실행해도 검증은 되게
    _ae = None

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLOT_MARK = 'data-affiliate-slot="1"'
AD_MARK = 's.click.aliexpress.com'
# ✅ 링크가 실제로 수수료 추적 URL 인지까지 본다.
LINK_RE = re.compile(r'href="(https://s\.click\.aliexpress\.com/[^"]+)"')
# 카드 안의 상품명 추출
TITLE_RE = re.compile(
    r'rel="sponsored[^"]*"[^>]*href="[^"]*"[^>]*>(.*?)</a>', re.S)

# 언어 루트만 (post/ 하위 디렉터리)
LANG_DIRS = ["ko", "en", "zh", "ja", "es", "fr", "de", "pt", "ru", "ar",
             "hi", "id", "bn", "game"]


# ⚠️ 위치 측정 함정이 세 가지 있다 (2026-10-10 실측).
#   ① <article> 안에는 <aside> 광고 슬롯·공유 위젯·<script> 가 딸려 있다.
#      이걸 본문 길이에 포함하면 분모가 3배로 불어나 카드가 "앞쪽"으로 보인다.
#   ② <header> 에는 제목·설명·메타가 있고 <p> 를 포함한다.
#   ③ 게임 글은 <div class="prose"> 밖에 카드가 있을 수 있다.
#   → 셋 다 제외하고 **순수 리치텍스트 본문**만으로 ���을 잰다.
_NOISE = [
    (re.compile(r'<header.*?</header>', re.S), ''),
    (re.compile(r'<aside.*?</aside>', re.S), ''),
    (re.compile(r'<script.*?</script>', re.S), ''),
    (re.compile(r'<div class="mt-8" data-ad-slot-name.*?</div>', re.S), ''),
    (re.compile(r'<div id="mca-share".*?</div>', re.S), ''),
]


def _article(h):
    m = re.search(r'<article[^>]*>(.*?)</article>', h, re.S)
    return m.group(1) if m else h


def _strip_noise(s):
    for pat, rep in _NOISE:
        s = pat.sub(rep, s)
    return s


def main_text_len(h):
    return len(_strip_noise(_article(h)))


def mid_position_pct(h):
    """카드가 순수 본문의 몇 % 지점에 있는지. 없으면 None.

    ⚠️ 분자/분모는 **같은 스케일**이어야 한다.
       분자만 원본 article 오프셋을 쓰고 분모를 노이즈 제거 길이로 잡으면
       게임 글에서 실제 38% 인 카드가 90%로 보고된다 (2026-10-10 실측).
       → 분자도 "카드 이전 구간"에 같은 노이즈 제거를 적용한다.
    """
    art = _article(h)
    card = art.find(SLOT_MARK)
    if card < 0:
        return None
    total = len(_strip_noise(art))
    if not total:
        return None
    head = len(_strip_noise(art[:card]))
    return round(100.0 * head / total)


def _card_titles(h):
    """카드에 노출된 상품명 리스트."""
    m = re.search(r'<aside data-affiliate-slot="1".*?</aside>', h, re.S)
    if not m:
        return []
    return [re.sub(r"<[^>]+>", "", t).strip()
            for t in TITLE_RE.findall(m.group(0))]


def collect():
    """모든 발행된 포스트 HTML을 긁는다. {path: (lang, n_cards, n_links)}"""
    files = []
    for lang in LANG_DIRS:
        files.extend(glob.glob(os.path.join(BASE, lang, "post", "*.html")))
    files.extend(glob.glob(os.path.join(BASE, "post", "*.html")))

    out = {}
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                html = fh.read()
        except OSError:
            continue
        rel = os.path.relpath(path, BASE)
        parts = rel.split(os.sep)
        lang = parts[0] if len(parts) > 2 else "en"
        cards = html.count(SLOT_MARK)
        links = len(set(LINK_RE.findall(html)))
        out[rel] = (lang, cards, links)
    return out


def main():
    strict = "--strict" in sys.argv
    data = collect()
    if not data:
        print("발행된 포스트를 찾지 못했다. build.py 를 먼저 실행하라.")
        return 1

    by_lang_total = Counter()
    by_lang_ok = Counter()
    missing = []
    bad_link = []
    positions = []
    unfit = []            # 🔴 카테고리에 부적합한 상품(장난감 등)
    all_titles = []
    dup_counter = Counter()

    for rel, (lang, cards, links) in sorted(data.items()):
        by_lang_total[lang] += 1
        if cards >= 1:
            by_lang_ok[lang] += 1
        else:
            missing.append(rel)
        if cards >= 1 and links < 1:
            bad_link.append(rel)
        try:
            with open(os.path.join(BASE, rel), encoding="utf-8") as fh:
                html = fh.read()
            pos = mid_position_pct(html)
            if pos is not None:
                positions.append((pos, rel))
            # 상품 적합성 검사 — 게임 글은 액세서리라 예외(코스프레 허용)
            titles = _card_titles(html)
            all_titles += titles
            for t in set(titles):
                dup_counter[t] += 1
            if _ae and "game" not in rel.split(os.sep)[0]:
                bad = [t for t in titles if _ae._is_unfit(t)]
                if bad:
                    unfit.append((rel, bad))
        except OSError:
            pass

    total = len(data)
    ok = sum(by_lang_ok.values())

    print("=" * 62)
    print("어필리에이트 커버리지 — %d/%d 포스트 (%.1f%%)"
          % (ok, total, 100.0 * ok / total))
    print("=" * 62)
    for lang in sorted(by_lang_total, key=lambda x: -by_lang_total[x]):
        t, o = by_lang_total[lang], by_lang_ok[lang]
        flag = "OK " if o == t else "!! "
        print("  %s%-4s %3d/%-3d" % (flag, lang, o, t))

    if positions:
        vals = sorted(p for p, _ in positions)
        mid = vals[len(vals) // 2]
        print("\n삽입 위치(순수 본문 대비 %%): 중앙값 %d%%  최소 %d%%  최대 %d%%"
              % (mid, vals[0], vals[-1]))
        # "가운데" 정의: 30~80% = 어已经把中段
        inside = sum(1 for p in vals if 30 <= p <= 80)
        print("  가운데(30~80%%) 안에 있음: %d/%d (%.0f%%)"
              % (inside, len(vals), 100.0 * inside / len(vals)))
        off = [(abs(p - 55), p, r) for p, r in positions if not (30 <= p <= 80)]
        if off:
            off.sort(reverse=True)
            print("  벗어난 글 %d개 (상위 5):" % len(off))
            for _, p, r in off[:5]:
                print("    %3d%%  %s" % (p, r[:70]))

    if missing:
        print("\n🔴 카드 없음 %d개:" % len(missing))
        for rel in missing[:40]:
            print("   ", rel)
    else:
        print("\n✅ 모든 포스트에 상품 카드가 있다.")

    if bad_link:
        print("\n🔴 카드는 있으나 추적링크(commission) 없음 %d개:" % len(bad_link))
        for rel in bad_link[:20]:
            print("   ", rel)
    elif ok:
        print("✅ 모든 카드에 s.click.aliexpress.com 추적링크가 실려 있다.")

    # ── 상품 적합성 (AdSense '광고-콘텐츠 관련성' 저위험 방지)
    if _ae:
        if unfit:
            print("\n🔴 카테고리에 부적합한 상품 %d개 글 (장난감·인형류):" % len(unfit))
            for rel, bad in unfit[:10]:
                print("   %s" % rel[:68])
                print("      → %s" % bad[0][:62])
        else:
            print("✅ 경제·금융 글에 부적합한 상품 없음.")

        if all_titles:
            print("\n상품 다양성: 카드 %d개 / 고유 상품 %d개"
                  % (len(all_titles), len(set(all_titles))))
            top = dup_counter.most_common(3)
            print("  가장 많이 쓰인 상품 (반복 노출은 정상, 상위 노출):")
            for t, n in top:
                print("    %3d회  %s" % (n, t[:60]))

    if strict and (missing or bad_link or unfit):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())