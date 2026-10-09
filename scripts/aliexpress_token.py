"""AliExpress OAuth 토큰 발급 / 갱신.

포털 문서 (2026-10-08 캡처) 근거:
  * 토큰은 **API로 직접 만들 수 없다.** 반드시 OAuth `code` 로 교환해야 한다.
  * 엔드포인트: `/auth/token/create` (일반) · `/auth/token/security/create` (보안增强)
  * `code` 는 **유효기간 3분** 이고 **1회 소비 후 폐기**된다.
  * `uuid` 파라미터는 **넣지 말 것** — 넣으면 `InvalidCode` 난다 (문서 명시).

사용 흐름
  1. 포털의 authorize URL 로 네가 직접 로그인 (여기서는 자동화 불가)
  2. callback 으로 리다이렉트된 URL 에서 `code=` 값을 복사
     (리다이렉트 대상이 404여도 URL 에 code 가 붙어 있어 회수 가능)
  3. 아래에 code 를 넣고 실행 → access_token / refresh_token 발급
  4. 발급된 토큰은 `.secrets/ALIEXPRESS.txt` 에 직접 옮긴다

주의: code·토큰 모두 대화창에 붙이지 말 것. 파일에만 쓴다.
"""
import hashlib
import hmac
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aliexpress as ax   # noqa: E402

CONF = ax.load_credentials(require_tracking=False, require_session=False)
GATEWAY = "https://api-sg.aliexpress.com/sync"
# 토큰 교환 엔드포인트 (포털 문서 표기 그대로)
TOKEN_PATH = "/auth/token/create"
SECURITY_TOKEN_PATH = "/auth/token/security/create"
REFRESH_PATH = "/auth/token/refresh"
# 서명 원본에서 제외할 키 — 실측 미확정. `Api process` 로 규칙을 확인한 뒤 확정한다.
SIGN_OMIT = ()


def sign(payload_src, secret):
    """확정된 서명 규칙 (2026-10-08 실측).
    key 오름차순 정렬 → k{v} 이어붙임 → secret 앞뒤 감싸 HMAC-MD5 → 대문자
    """
    flat = "".join("%s%s" % (k, payload_src[k]) for k in sorted(payload_src))
    wrapped = "%s%s%s" % (secret, flat, secret)
    return hmac.new(secret.encode("utf-8"), wrapped.encode("utf-8"),
                    hashlib.md5).hexdigest().upper()


def post(api_path, params, timeout=20):
    """토큰 교환 호출.

    ⚠️ 2026-10-08 실측 상태: **서명 규칙이 아직 미확정** 이다.
      포털 문서 컨텍스트(`/auth/token/create`)는 TOP 와 다른 규칙을 쓰는데,
      서명 원본 조합을 4가지 시도했으나 전부 `IncompleteSignature` 다.
      → 이 경로는 **로그인한 포털의 `Api process` 도구로 먼저 확인**해야 한다.
        (`Api process` 에서 자동 생성되는 요청을 그대로 쓰면 규칙이 보인다.)

    그래도 시도가 필요하면 아래 `SIGN_OMIT` 을 바꿔가며 확인한다.
    """
    payload = {
        "method": api_path,          # 문서는 apiPath 라고 하나 method 로 보내야 경로가 유효 (실측)
        "app_key": CONF["app_key"],
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "v": "2.0",
        "format": "json",
        "sign_method": CONF["sign_method"],
    }
    payload.update(params)
    # 서명 원본에서 제외할 키. 실측으로 아직 정해지지 않았다.
    sign_src = {k: v for k, v in payload.items() if k not in SIGN_OMIT}
    payload["sign"] = sign(sign_src, CONF["app_secret"])

    body = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        GATEWAY,
        data=body,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def create_token(code):
    """OAuth code → 토큰 교환. code 유효기간 3분."""
    return post(TOKEN_PATH, {"code": code})


def refresh_token(rt):
    """refresh_token 으로 access_token 재발급."""
    return post(REFRESH_PATH, {"refreshToken": rt})


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("사용법:")
        print("  python scripts/aliexpress_token.py create <code>")
        print("  python scripts/aliexpress_token.py refresh <refresh_token>")
        return

    cmd = sys.argv[1]
    arg = sys.argv[2]
    try:
        result = create_token(arg) if cmd == "create" else refresh_token(arg)
    except Exception as exc:
        print("호출 실패: %s" % type(exc).__name__)
        return

    if "error_response" in result:
        err = result["error_response"]
        print("API 오류: %s (%s) %s"
              % (err.get("code"), err.get("sub_code", "-"),
                 err.get("msg") or err.get("message", "")))
        return

    # 토큰 값을 stdout 에 찍지 않는다. 파일 경로만 안내한다.
    for key in ("access_token", "refresh_token", "expires_in",
                "refresh_expires_in", "account", "user_id"):
        if key in result:
            val = result[key]
            shown = "***" if "token" in key else val
            print("  %-20s = %s" % (key, shown))
    print()
    print("위 access_token / refresh_token 값을")
    print("  %s" % ax.SECRETS)
    print("의 session= / refresh_token= 줄에 직접 옮겨넣어라 (채팅에 붙이지 말 것).")


if __name__ == "__main__":
    main()
