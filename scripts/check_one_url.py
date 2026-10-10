"""🔎 임의 URL 1회성 구조 검사 (2026-10-10, 로이 지시).

용도
----
"아무 URL이나 봤는데 문제가 있으면 안 된다" 는 요구를 매번 수동으로
하지 않도록, **URL 을 넣으면 광고 배치 구조를 즉시 검증**한다.

확인하는 것
------------
1. 페이지에 `<article>` 이 있는가
2. 광고 순서가 맞나:  post_top < 알리익스프레스 < post_bottom < post_related
3. 알리익스프레스 카드에 **수수료 추적링크**(`s.click.aliexpress.com`) 가 있는가
4. `rel="sponsored nofollow"` 가 붙었는가 (표시광고법 고지)
5. 카드가 부적합 상품(장난감 등) 을 포함하지 않는가
6. HTML 태그 균형 (aside / p)

사용법
------
    python scripts/check_one_url.py https://www.mycapitalanalyze.com/ko/post/fed-rate-outlook-2026.html
    python scripts/check_one_url.py --local post/yield-curve-2026.html     # 로컬 산출물
    python scripts/check_one_url.py --sample                              # 무작위 20개
"""
import glob
import os
import random
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

try:
    import ae_autoslot as _ae
except ImportError:
    _ae = None

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGIN = "https://www.mycapitalanalyze.com/"
LOCAL = "http://127.0.0.1:8901/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

OK = "OK  "
NG = "FAIL"


def fetch(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def slot(h, name):
    m = re.search(r'data-ad-slot-name="%s"' % name, h)
    return m.start() if m else -1


def check(url, label=""):
    """URL 1개를 검증. (통과여부, 보고용 행 리스트)"""
    rows = []
    try:
        h = fetch(url)
    except Exception as exc:
        return False, [(label or url, "HTTP %s" % type(exc).__name__, "")]

    m = re.search(r"<article[^>]*>(.*?)</article>", h, re.S)
    if not m:
        return False, [(label or url, "<article> 없음 — 포스트 페이지 아님", "")]
    a = m.group(1)

    top, bot, rel = slot(a, "post_top"), slot(a, "post_bottom"), slot(a, "post_related")
    aff = a.find('data-affiliate-slot="1"')
    links = len(re.findall(r"s\.click\.aliexpress\.com", a))

    rows.append(("상단 AdSense (post_top)", OK if top > 0 else NG,
                 "offset %d" % top if top > 0 else "없음"))
    rows.append(("알리익스프레스 카드", OK if aff > 0 else NG,
                 "offset %d" % aff if aff > 0 else "없음"))
    rows.append(("하단 AdSense (post_bottom)", OK if bot > 0 else NG,
                 "offset %d" % bot if bot > 0 else "없음"))
    rows.append(("최하단 AdSense (post_related)", OK if rel > 0 else NG,
                 "offset %d" % rel if rel > 0 else "없음"))
    rows.append(("배치 순서", OK if 0 < top < aff < bot < rel else NG,
                 "top < 카드 < bottom < related"))

    rows.append(("수수료 추적링크", OK if links >= 1 else NG, "%d개" % links))
    rows.append(("rel=sponsored nofollow",
                 OK if 'rel="sponsored nofollow noopener"' in a else NG, ""))
    rows.append(("태그 균형 (aside)",
                 OK if a.count("<aside") == a.count("</aside>") else NG,
                 "%d/%d" % (a.count("<aside"), a.count("</aside>"))))

    if _ae and "game" not in url:
        bad = [re.sub(r"<[^>]+>", "", t).strip()
               for t in re.findall(
                   r'rel="sponsored[^"]*"[^>]*href="[^"]*"[^>]*>(.*?)</a>',
                   re.search(r'<aside data-affiliate-slot="1".*?</aside>', a, re.S).group(0)
                   if re.search(r'<aside data-affiliate-slot="1".*?</aside>', a, re.S) else "", re.S)
               if _ae._is_unfit(re.sub(r"<[^>]+>", "", t))]
        rows.append(("상품 적합성", OK if not bad else NG,
                     "부적합 %d개" % len(bad) if bad else "모두 적합"))

    return all(r[1] == OK for r in rows), rows


def run_url(url, label=""):
    good, rows = check(url, label)
    print("=" * 66)
    print("%s" % (label or url))
    print("=" * 66)
    for name, mark, detail in rows:
        print("  [%s] %-30s %s" % (mark, name, detail))
    print("  → %s" % ("문제 없음" if good else "문제 있음 ★ 로이에게 보고"))
    print()
    return good


def sample(n=20, seed=5):
    files = []
    for lang in ["ko", "ja", "de", "es", "fr", "zh", "pt", "ru", "ar", "hi", "id", "bn", "game"]:
        files += glob.glob(os.path.join(BASE, lang, "post", "*.html"))
    files += glob.glob(os.path.join(BASE, "post", "*.html"))
    picks = random.Random(seed).sample(files, min(n, len(files)))
    good = 0
    fails = []
    for f in picks:
        # ⚠️ BASE 가 절대경로라 os.path.join 결과도 절대경로가 된다.
        #    → 반드시 BASE 로부터의 상대 경로로 바꿔야 URL 이 된다.
        #    (실측: `os.path.join(BASE, 'ko/post/x.html')` → 'D:/.../ko/post/x.html'
        #     를 그대로 ORIGIN 뒤에 붙여 전부 404 = "정상 0 / 문제 20" 이었다)
        rel = os.path.relpath(f, BASE).replace(os.sep, "/")
        g, rows = check(ORIGIN + rel, rel)
        if g:
            good += 1
        else:
            fails.append((rel, [r for r in rows if r[1] == NG]))
    print("=" * 66)
    print("실서비스 무작위 %d개 검사 — 정상 %d / 문제 %d"
          % (len(picks), good, len(fails)))
    print("=" * 66)
    for rel in fails:
        print("  ❌ %s" % rel)
        for name, _mark, detail in bad:
            print("       %s — %s" % (name, detail))
    return len(fails) == 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--sample" in argv:
        sys.exit(0 if sample() else 1)
    if "--local" in argv:
        target = argv[argv.index("--local") + 1]
        sys.exit(0 if run_url(LOCAL + target, "LOCAL " + target) else 1)
    if argv:
        ok = True
        for u in argv:
            ok = run_url(u) and ok
        sys.exit(0 if ok else 1)
    print(__doc__)
    sys.exit(0)