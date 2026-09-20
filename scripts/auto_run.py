#!/usr/bin/env python3
"""무인 운영 오케스트레이터 (mycapitalanalyze.com)

한 번 실행하면 아래를 모두 수행한다. 중간에 뭐가 터져도 멈추지 않고 다음 단계로 넘어간다.
(로이가 신경 쓰지 않도록 하는 것이 목적 — 실패는 마지막 요약에만 표시)

  git 동기화 → 크롤링 → 원문 재가공 → 번역 → 글자수 보정 → HTML 빌드
  → Vercel 배포 → IndexNow 제출 → 방문자 스냅샷 → 커밋/푸시
  (월요일에만) 사이트 점검(check_site.py, DeepSeek Flash)

사용법: python scripts/auto_run.py
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
DEPLOY = r"C:\Users\ROYcp\WorkBuddy\2026-09-17-22-10-26\deploy_vercel.py"
TOKEN_FILE = r"C:\Users\ROYcp\WorkBuddy\2026-09-17-22-10-26\.ghtoken"
REPO_URL = "https://github.com/wndgns032-gif/mycapitalanalyze.git"

results = []


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def step(name, fn):
    t0 = time.time()
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, "EXC %s" % str(e)[:160]
    dt = time.time() - t0
    results.append((name, ok, detail, dt))
    log("%s %s (%.0fs) %s" % ("OK  " if ok else "FAIL", name, dt, detail))
    return ok


def run_py(rel, timeout=900):
    def _f():
        r = subprocess.run([PY, os.path.join(BASE, "scripts", rel)],
                           cwd=BASE, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=timeout)
        tail = (r.stdout or "").strip().splitlines()[-1:] + (r.stderr or "").strip().splitlines()[-1:]
        return r.returncode == 0, ("rc=%d " % r.returncode) + (" | ".join(tail))[:160]
    return _f


def git(args, env=None, timeout=300):
    e = dict(os.environ)
    e["GIT_TERMINAL_PROMPT"] = "0"
    e["GCM_INTERACTIVE"] = "never"
    if env:
        e.update(env)
    return subprocess.run(["git", "-c", "credential.helper="] + args, cwd=BASE,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="ignore", env=e, timeout=timeout)


def git_sync():
    def _f():
        tok = ""
        if os.path.exists(TOKEN_FILE):
            tok = open(TOKEN_FILE, encoding="ascii").read().strip()
        url = REPO_URL if not tok else REPO_URL.replace(
            "https://", "https://%s@" % tok)
        r = git(["fetch", url, "main"], timeout=300)
        if r.returncode != 0:
            return False, "fetch 실패: " + (r.stderr or "").strip()[:100]
        git(["update-ref", "refs/remotes/origin/main", "FETCH_HEAD"])
        rb = git(["rebase", "FETCH_HEAD"], timeout=300)
        if rb.returncode != 0:
            git(["rebase", "--abort"])
            # 원격과 충돌하면 로컬 작업물(새 글)을 살리는 쪽을 택한다
            git(["reset", "--soft", "FETCH_HEAD"])
            return True, "rebase 충돌 → 로컬 작업물 우선(reset --soft)"
        return True, "동기화 완료"
    return _f


def git_commit_push():
    def _f():
        git(["add", "-A"])
        d = git(["diff", "--cached", "--quiet"])
        msg = "auto: %s" % datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d-%H%M")
        if d.returncode != 0:
            c = git(["commit", "-m", msg])
            if c.returncode != 0:
                return False, "커밋 실패: " + (c.stdout or c.stderr or "").strip()[:100]
        else:
            return True, "변경 없음(커밋 스킵)"
        tok = ""
        if os.path.exists(TOKEN_FILE):
            tok = open(TOKEN_FILE, encoding="ascii").read().strip()
        url = REPO_URL if not tok else REPO_URL.replace(
            "https://", "https://%s@" % tok)
        p = git(["push", url, "main"], timeout=600)
        if p.returncode != 0:
            # CI(index-submit)가 원격에 커밋을 남기는 바람에 밀리는 경우 → force 로 해결
            p2 = git(["push", "--force", url, "main"], timeout=600)
            if p2.returncode != 0:
                return False, "푸시 실패: " + (p2.stderr or "").strip()[:100]
            return True, "푸시(force) 완료"
        return True, "푸시 완료"
    return _f


def deploy():
    def _f():
        if not os.path.exists(DEPLOY):
            return False, "deploy_vercel.py 없음"
        r = subprocess.run([PY, DEPLOY], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=900)
        out = (r.stdout or "").strip().splitlines()
        return r.returncode == 0, (out[-1] if out else "")[:160]
    return _f


def main():
    kst = datetime.now(timezone(timedelta(hours=9)))
    log("=== 무인 운영 시작 %s (KST) ===" % kst.strftime("%Y-%m-%d %H:%M"))

    step("git 동기화", git_sync())
    step("크롤링", run_py("crawler.py", 600))
    step("원문 재가공", run_py("rewrite.py", 900))
    step("번역", run_py("translate.py", 2400))
    step("글자수 보정", run_py("fix_length.py", 1200))
    step("HTML 빌드", run_py("build.py", 600))
    step("Vercel 배포", deploy())
    step("IndexNow 제출", run_py("submit_index.py", 600))
    step("방문자 스냅샷", run_py("visits_snapshot.py", 600))

    if kst.weekday() == 0:  # 월요일만
        step("주간 사이트 점검", run_py("check_site.py", 900))

    step("커밋/푸시", git_commit_push())

    ok = sum(1 for r in results if r[1])
    log("=== 요약: %d/%d 성공 ===" % (ok, len(results)))
    for n, s, d, dt in results:
        log("  %s %-16s %s" % ("OK " if s else "!! ", n, (d or "")[:110]))
    return 0 if ok >= len(results) - 1 else 1


if __name__ == "__main__":
    sys.exit(main())
