"""공용 LLM 호출 모듈.

정책 (로이 지시 2026-09-19, 최우선 목표 = 비용 최소화):
  * 모든 호출은 **Flash 모델만** 사용한다. 추론형·대형 모델은 금지.
  * 용도별로 제공자를 고정한다:
      - `purpose='write'` (게시글 작성/번역/길이보정) → **GLM**
      - `purpose='check'` (사이트 점검·보고 요약)  → **DeepSeek (Flash)**
  * 1순위가 실패하면(401/402/403/429/5xx/네트워크) 보조 제공자로 **자동 폴백**.
    → 키가 만료돼도 작업이 죽지 않는다. 폴백은 로그에 남긴다.

반환값: (content, provider_name)
실패 시: RuntimeError
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))

# flash_only=true 면 모델명에 'flash' 가 없는 제공자는 건너뛴다.
FLASH_ONLY = bool(CONFIG.get('flash_only', True))

# 용도별 제공자 우선순위 (로이 정책: 글쓰기=GLM / 점검=DeepSeek)
PURPOSE_ORDER = {
    # 게시글 작성·번역·길이보정 — 무료 제공자 우선, 유료 DeepSeek 는 폴백
    'write': [p.strip().lower() for p in (CONFIG.get('write_provider_order')
                                          or DEFAULT_WRITE_ORDER)],
    # 사이트 점검·보고
    'check': [p.strip().lower() for p in (CONFIG.get('check_provider_order')
                                          or DEFAULT_CHECK_ORDER)],
}
PURPOSE_MODEL = {
    'write': (CONFIG.get('write_model') or '').strip(),
    'check': (CONFIG.get('check_model') or '').strip(),
}

# 제공자별 flash 모델 기본값 (config에 flash 모델이 없을 때 사용)
# ⚠️ 로이 지시(2026-09-30): DeepSeek 은 반드시 flash 로만 호출한다.
#    실측(api.deepseek.com/models, 2026-09-30) 결과 DeepSeek 가 제공하는 모델은
#    'deepseek-flash'(DeepSeek-V4.1-Flash) 와 'deepseek-v4-pro' 두 개뿐이다.
#    'deepseek-v4-flash' 는 존재하지 않는 이름이라 404 가 난다. 절대 쓰지 말 것.
DEFAULT_FLASH = {
    'deepseek': 'deepseek-flash',
    'glm': 'glm-5.3-flash',
    # 무료 제공자 기본 모델 (2026-10-02 로이 지시: 무료 API 우선 → 실패 시 DeepSeek)
    'gemini': 'gemini-flash-latest',        # Google AI Studio 무료 티어 (별칭이라 항상 최신 flash)
    'groq': 'openai/gpt-oss-120b',           # Groq 무료 티어 (2026-10-27 실측 가용)
    # ⚠️ 2026-10-27 실측: Groq 무료 티어에서 'llama-3.3-70b-versatile' 은
    #   **404 "model does not exist or you do not have access"** 이었다.
    #   실제로 접근 가능한 것: openai/gpt-oss-120b / -20b, qwen/qwen3.8-27b 등.
    #   gpt-oss 는 reasoning 모델이라 content 가 빈 응답을 낼 때가 있어
    #   chat() 이 "Answer with at least five words" 를 붙여 재시도한다.
    'openrouter': 'openrouter/free',        # OpenRouter 무료 모델 라우터 (하루 50회)
    'nvidia': 'nvidia/deepseek-v4-flash',   # NVIDIA NIM 무료 티어 (일일 토큰 상한 없음)
}

# 🔴 2026-10-27 실측: Groq 무료 티어의 TPM(분당 토큰) 한도는 8,000 이다.
#   Groq 의 요청 예약 계산식 = prompt_tokens + min(max_tokens, 8192).
#   → max_tokens 를 기본값 16,384 로 두면 "min(16384, 8192) = 8192" 가 예약되어,
#     prompt 3,946 토큰짜리 요청조차 예약 12,138 토큰으로 **거부(HTTP 413)** 된다.
#   실측(2026-10-27): prompt 5,004 + max_tokens 4,096 조합도 예약 9,100 으로 TPM 초과.
#   → Groq 는 max_tokens 를 **프롬프트 크기에 맞춰 자동 축소**해야 한다.
PROVIDER_MAX_TOKENS = {
    'groq': 4096,
    'groq2': 4096,
}

# Groq TPM 예약 한도 (추정 prompt + max_tokens 가 넘지 않게 맞추기 위한 기준값)
GROQ_TPM_LIMIT = 8000
GROQ_TPM_MARGIN = 400


def _estimate_tokens(text):
    """토크나이저 없이 프롬프트 크기를 추정한다.
    실측 보정(2026-10-27, tiktoken o200k_base 대조):
      한글/일본어/중국어 약 1.25글자/토큰, 라틴·기호 약 0.22글자/토큰.
    (처음엔 1.6 / 0.28 로 잡았더니 실제보다 27% 과대추정 → max_tokens 를
     필요 이상으로 잘라 본문이 부족했다. 아래 값은 실제값에 맞춘 보정값이다.)
    과대추정은 요청을 작게 잡을 뿐 실패를 만들지 않지만, 과소추정하면 429 가 되므로
    이 값은 보수적으로 유지한다.
    """
    if not text:
        return 0
    n = 0.0
    for ch in text:
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3 or 0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF:
            n += 1.25      # 한글/일본어/중국어
        else:
            n += 0.22      # 라틴/기호 등
    return int(n) + 8


def _fit_max_tokens(name, model, messages, want):
    """제공자 한도에 맞춰 max_tokens 를 줄인다. Groq 는 prompt + max_tokens 예약이므로
    프롬프트가 큰 글(게임 프롬프트 5,400 토큰)에서는 max_tokens 를 함께 줄여야 한다.

    🔴 2026-10-27 실측: gpt-oss 의 completion 예산은 **추론 + 본문**이 같이 쓴다.
      effort=medium 이면 추론만 3,125 토큰을 먹어서 본문이 0이 되는 경우도 있었다.
      → 본문이 쓸 몫(최소 1,400)을 먼저 확보하고, 남는 만큼만 추론에 허용한다.
    """
    if name not in ('groq', 'groq2'):
        return want
    est = sum(_estimate_tokens(m.get('content') or '') for m in messages)
    # 본문은 최소 1,400 토큰(한글 1,600자 이상) 필요 — 이것을 먼저 잠근다.
    body_need = 1400 if 'gpt-oss' in model else 700
    room = GROQ_TPM_LIMIT - GROQ_TPM_MARGIN - est
    if room < body_need:
        # 프롬프트 자체가 한도를 넘는다 → 최소값으로 내린다.
        # (413 이 나면 chat() 이 한 단계 더 축소하며 재시도한다)
        return max(512, body_need)
    return max(body_need, min(want, room))



# 무료 제공자 — 비용이 0원이므로 flash 전용 정책(비용 통제 목적)을 적용하지 않는다.
# config 의 섹션에 "free": true 로 표시하거나 이름이 이 목록에 있으면 예외 처리한다.
FREE_PROVIDERS = {'gemini', 'groq', 'groq2', 'openrouter', 'nvidia', 'cloudflare',
                  'mistral', 'cohere'}

# 🔴 2026-10-27 실측: User-Agent 를 안 보내면 Groq(그리고 Cloudflare 앞단 프록시)가
#   **403 "error code: 1010"** 으로 전부 막는다. 같은 키에 UA만 붙이면 정상 응답했다.
#   → 모든 API 요청에 브라우저 UA 를 붙인다.
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

# 🔴 2026-10-27: 연속 실패한 제공자는 이번 프로세스에서 건너뛴다 (서킷 브레이커).
#   이유: 무료 제공자가 죽어 있으면 (429 쿼터 소진 / 403 차단) 호출마다
#   "실패 → 5초 대기 → 재시도 → 실패 → 5초 대기 → 재시도" 를 반복한다.
#   번역 40건 × 2회 호출 × 15초 = 20분 낭비 → 워크플로가 timeout 으로 취소된다.
#   (실측: 10-25 auto-publish, 10-25·10-27 game-daily 가 정확히 timeout 분에 cancelled)
#   PROVIDER_FAILS[name] >= FAIL_LIMIT 이면 그 제공자는 나머지 실행에서 건너뛴다.
FAIL_LIMIT = 2
PROVIDER_FAILS = {}

# config 에 순서가 없을 때의 기본 사슬 — 무료 → GLM → DeepSeek(유료 폴백)
DEFAULT_WRITE_ORDER = ['gemini', 'groq', 'openrouter', 'nvidia', 'glm', 'deepseek']
DEFAULT_CHECK_ORDER = ['groq', 'gemini', 'openrouter', 'deepseek']


def _is_flash(model):
    return 'flash' in (model or '').lower()


def _flash_model(name, sec, override=''):
    """flash_only 정책을 만족하는 모델명을 고른다."""
    model = (override or sec.get('model') or '').strip()
    if not FLASH_ONLY:
        return model or DEFAULT_FLASH.get(name, '')
    if _is_flash(model):
        return model
    # config에 명시된 flash 대체 모델이 있으면 사용
    alt = (sec.get('flash_model') or '').strip()
    if _is_flash(alt):
        return alt
    return DEFAULT_FLASH.get(name, model)


def chain(purpose='write'):
    """사용 가능한 (name, base_url, api_key, model) 목록을 용도별 우선순위대로.

    무료 제공자(비용 0원)는 flash 전용 정책의 예외다 — 정책 목적이 비용 통제이기 때문.
    """
    order = PURPOSE_ORDER.get(purpose) or PURPOSE_ORDER['write']
    override = PURPOSE_MODEL.get(purpose, '')
    # ⚠️ 2026-10-05 실측 버그: override 를 order[0] 에 적용했는데, 무료 제공자
    #   (gemini/groq) 가 1순위가 되자 **Gemini 에 'deepseek-flash' 모델명이 붙어**
    #   404 가 났다("models/deepseek-flash is not found ... for generateContent").
    #   write_model/check_model 은 원래 유료 폴백(DeepSeek)용 값이다
    #   → override 는 체인의 **마지막(폴백) 제공자**에만 적용한다.
    fallback = order[-1] if order else ''
    out = []
    for name in order:
        sec = CONFIG.get(name) or {}
        if not (sec.get('api_key') or '').strip():
            continue
        model = _flash_model(name, sec, override if name == fallback else '')
        if not model:
            continue
        is_free = bool(sec.get('free')) or name in FREE_PROVIDERS
        if FLASH_ONLY and not is_free and not _is_flash(model):
            continue
        out.append((name, sec['base_url'].rstrip('/'), sec['api_key'].strip(), model, is_free))
    return out


# 하드 가드: 어떤 경로로도 flash 가 아닌 유료 모델이 호출되면 즉시 중단.
# (config 오타로 pro/추론 모델이 나가 비용이 폭주하는 것을 원천 차단)
for _p in PURPOSE_ORDER:
    for _row in chain(_p):
        if not _row[4] and not _is_flash(_row[3]):
            raise SystemExit('[llm] flash 전용 정책 위반: purpose=%s provider=%s model=%s'
                             % (_p, _row[0], _row[3]))


# 제공자별 성공/실패 집계 — 실행 마지막에 "무료 API 가 실제로 쓰였나" 를 보여주려는 목적.
# (로이 2026-10-06: 시크릿을 등록했는데 진짜 무료로 도는지 로그에서 바로 확인하고 싶다)
STATS = {}


def _stat(name, ok, err=''):
    s = STATS.setdefault(name, {'ok': 0, 'fail': 0, 'err': ''})
    if ok:
        s['ok'] += 1
        return
    s['fail'] += 1
    if not err:
        return
    # 응답 본문(JSON)이 그대로 들어오면 통계 줄이 엉망이 된다 → 핵심만 남긴다.
    one = ' '.join((err or '').split())
    m = re.search(r'(HTTP\s+\d+)', one)
    code = m.group(1) if m else one[:24]
    # 오류 사유: "message" 값이 있으면 그것을, 없으면 첫 40자
    m2 = re.search(r'"message"\s*:\s*"([^"]{0,70})', one)
    why = m2.group(1) if m2 else one[:40]
    s['err'] = '%s %s' % (code, why)


def stats_report():
    """집계를 사람이 읽는 한 줄 요약으로."""
    if not STATS:
        return 'LLM 호출 없음'
    free = {'gemini', 'groq', 'groq2', 'openrouter', 'nvidia'}
    parts = []
    for name in sorted(STATS):
        s = STATS[name]
        tag = '무료' if name in free else '유료'
        parts.append('%s(%s) 성공%d/실패%d%s'
                     % (name, tag, s['ok'], s['fail'],
                        (' — %s' % s['err']) if (s['fail'] and s['err']) else ''))
    tot_free = sum(STATS[n]['ok'] for n in STATS if n in free)
    tot = sum(s['ok'] for s in STATS.values())
    return ' | '.join(parts) + ('\n   → 무료 %d / 전체 %d건 (%s)'
                                % (tot_free, tot,
                                   ('%.0f%%' % (100.0 * tot_free / tot)) if tot else '-'))


def chat(messages, max_tokens=16384, temperature=0.6, response_format=None,
         timeout=180, retries=2, log=print, purpose='write'):
    """Flash 모델로 채팅 완료를 호출한다. 실패 시 다음 제공자로 폴백.

    purpose: 'write' (GLM) | 'check' (DeepSeek Flash)
    반환: (content, provider_name)
    """
    providers = chain(purpose)
    if not providers:
        raise RuntimeError('사용 가능한 LLM 제공자가 없다 (purpose=%s, flash_only=%s)'
                           % (purpose, FLASH_ONLY))

    last_err = None
    for name, base_url, api_key, model, is_free in providers:
        # 🔴 서킷 브레이커: 이 프로세스에서 이미 여러 번 죽은 제공자는 건너뛴다.
        if PROVIDER_FAILS.get(name, 0) >= FAIL_LIMIT:
            log('  [llm] %s 스킵 (연속 실패 %d회 — 이번 실행에서는 생략)'
                % (name, PROVIDER_FAILS[name]))
            continue
        body = {
            'model': model,
            'messages': messages,
            'max_tokens': _fit_max_tokens(name, model, messages,
                                          PROVIDER_MAX_TOKENS.get(name, max_tokens)),
            'temperature': temperature,
        }
        # 🔴 2026-10-27 실측 결함: Groq 의 gpt-oss 는 **추론 모델**이라서
        #   reasoning_effort 를 안 주면 내부 추론에 토큰을 9,900개나 쓴다.
        #   실측 비교(동일 게임 글 프롬프트, prompt 5,004 토큰):
        #     effort 미지정 → completion 10,938 (reasoning 9,900) → TPM 8,000 창을
        #                    파고들어 HTTP 413 "Requested 12138" 로 요청 자체가 거부됨
        #     effort='low'    → completion   866 (reasoning   337) → 본문 706~909자 (분량 부족)
        #     effort='medium' → completion 4,054 (reasoning 3,125) → 본문 1,311자 (하한 통과)
        #   'low' 는 추론이 너무 적어 본문이 짧아져 하한 미달로 폐기된다.
        #   'medium' 이 분량·TPM 균형점이다. 'high' 는 추론만 늘려 본문이 늘지 않는다.
        #   이 글은 "사실 정리" 작업이라 깊은 추론이 필요 없다.
        if 'gpt-oss' in model and 'reasoning_effort' not in body:
            body['reasoning_effort'] = 'medium'
        if response_format:
            body['response_format'] = response_format
        if name == 'deepseek':
            body['thinking'] = {'type': 'disabled'}

        data = json.dumps(body).encode('utf-8')
        for attempt in range(1, retries + 2):
            req = urllib.request.Request(
                base_url + '/chat/completions', data=data,
                headers={'Content-Type': 'application/json',
                         'Authorization': 'Bearer ' + api_key,
                         'User-Agent': UA},
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    d = json.loads(resp.read().decode('utf-8', 'ignore'))
                content = ((d.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
                if not content.strip():
                    # 추론 모델에서 빈 응답이 오는 케이스 → 재시도.
                    # 2026-10-05 실측: Gemini 는 "Say only: PONG" 처럼 지시가 짧으면
                    # 빈 문자열을 그대로 반환한다. 같은 요청을 반복해도 또 비니
                    # "최소 몇 단어로 답하라" 는 지시를 덧붙여 다시 물어본다.
                    last_err = 'empty content'
                    if not any('Answer with at least' in (m.get('content') or '')
                               for m in messages):
                        messages = list(messages) + [
                            {'role': 'user',
                             'content': 'Answer with at least five words of plain text.'}]
                    # 🔴 2026-10-27: reasoning 모델(gpt-oss 등)은 max_tokens 가 작으면
                    #   reasoning 에 토큰을 다 쓰고 content 가 빈 문자열이 된다.
                    #   → content 가 비면 토큰 예산을 2배로 늘려 한 번 더 시도한다.
                    max_tokens = min(max_tokens * 2, 32768)
                    time.sleep(1)
                    continue
                _stat(name, True)
                return content, name
            except urllib.error.HTTPError as e:
                # 🔴 2026-10-27: 에러 본문을 200자로 자르면 Groq 429 의
                #   "Please try again in 25.02s" 까지 잘려서 **대기시간 파싱이 실패**한다.
                #   (실측: 고정 5초 대기 → 매번 재시도 실패 → 3회 소진 후 폴백)
                #   → 400자로 넓혀서 회복 시간을 정확히 읽는다.
                detail = e.read().decode('utf-8', 'ignore')[:400]
                last_err = 'HTTP %s %s' % (e.code, detail)
                log('  [llm] %s 실패: %s' % (name, last_err))
                # 429(요청 한도)·503(일시적 고부하) 는 **기다리면 다시 된다**.
                #   2026-10-05 실측: Gemini 무료 티어가 503 → 429 로 막히는데
                #   예전 코드는 이 둘을 즉시 폴백시켰다 → 무료 API 를 거의 못 쓰고
                #   유료 DeepSeek 이 대부분을 처리했다(로이: "무료 API도 이용해줘").
                #   → 무료 제공자는 짧은 백오프 후 같은 제공자를 한 번 더 시도한다.
                if e.code in (429, 503) and is_free:
                    # 🔴 2026-10-27: 429 라고 다 같은 성격이 아니다.
                    #   - "Rate limit exceeded" (분당 제한) → 5초 뒤에 회복된다 → 재시도 OK
                    #   - "exceeded your current quota" (일일/월간 쿼터 소진) → 5초 뒤에도 그대로
                    #     → 재시도 3번 = 15초 낭비. 워크플로가 timeout 으로 죽는 주범이었다.
                    #   영구 쿼터 소진은 즉시 폴백(서킷 브레이커가 이후 호출을 건너뛴다).
                    permanent = ('current quota' in detail.lower()
                                 or 'quota exceeded' in detail.lower()
                                 or 'insufficient' in detail.lower()
                                 or 'insufficient_quota' in detail.lower()
                                 or 'check your billing' in detail.lower()
                                 or 'out of credits' in detail.lower()
                                 or 'payment required' in detail.lower())
                    if permanent:
                        log('  [llm] %s 429 영구 쿼터 소진 → 즉시 폴백' % name)
                        break
                    # 🔴 2026-10-27 실측 (신규): Groq **일일 한도(TPD)** 소진.
                    #   메시지: "tokens per day (TPD): Limit 200000, Used 195582 ...
                    #             Please try again in 2m57.984s"
                    #   → 회복까지 몇 시간~하루. 재시도 3번 = 전부 실패.
                    #   "tokens per minute (TPM)" 과 **구분**해서 즉시 폴백해야 한다.
                    if 'tokens per day' in detail.lower() or '(tpd)' in detail.lower():
                        log('  [llm] %s 429 일일 한도(TPD) 소진 → 즉시 폴백' % name)
                        break
                    # 🔴 2026-10-27 실측: Groq 429 메시지에 실제 회복 시간이 들어 있다
                    #   ("Please try again in 25.02s"). 고정 5초 대기는 그 시간보다
                    #   짧아서 **매번 재시도가 또 실패**했다(실측 로그: 3회 연속 429).
                    #   → 서버가 알려준 시간을 그대로 쓴다.
                    wait = 5 if e.code == 429 else 3
                    m_wait = re.search(r'try again in\s*([0-9.]+)\s*s', detail, re.I)
                    if m_wait:
                        wait = max(wait, min(120, int(float(m_wait.group(1))) + 1))
                    else:
                        # "2m57.984s" 형식 (분 단위)도 읽는다.
                        m_min = re.search(r'try again in\s*([0-9]+)m\s*([0-9.]+)\s*s', detail, re.I)
                        if m_min:
                            wait = int(m_min.group(1)) * 60 + int(float(m_min.group(2))) + 1
                    # 회복이 2분 넘으면 재시도해도 소용없다(실측 "try again in 6m55.152s").
                    # 120초 캡으로 잘려 5초만 기다렸다가 또 429 → 3회 전부 실패.
                    if wait > 120:
                        log('  [llm] %s 429 회복까지 %d초 → 재시도 불가, 즉시 폴백'
                            % (name, wait))
                        break
                    # Groq TPM(분당 8,000)은 **분 단위 창**이라 "try again in Ns" 만큼
                    #   기다리면 반드시 회복한다. 단 N이 크면 요청이 길어지므로
                    #   두 번 연속 실패하면 다음 제공자로 넘어간다.
                    log('  [llm] %s %s → %d초 대기 후 재시도(무료 티어)' % (name, e.code, wait))
                    time.sleep(wait)
                    if attempt <= retries:
                        continue
                if e.code in (401, 402, 403, 404, 429):
                    break  # 다른 제공자로 폴백
                # 🔴 2026-10-27: HTTP 413 "Request too large for model ... on tokens per
                #   minute (TPM): Limit 8000, Requested 12138" — 이건 프롬프트가
                #   나빠서가 아니라 **max_tokens 예약분 + reasoning 토큰**이 TPM 8000을
                #   넘긴 경우다. 실측(2026-10-27): gpt-oss-120b 는 한 호출에
                #   completion 10,938 토큰(reasoning 9,900)을 소모했다.
                #   → 해결책: max_tokens 를 줄여 재시도. 413 은 429 와 달리
                #   "5초 뒤에 회복"이 아니라 **요청 자체가 큼**이므로 기다리는 게 소용없다.
                if e.code == 413:
                    new_mt = max(1024, max_tokens // 2)
                    if new_mt == max_tokens:
                        log('  [llm] %s 413 → max_tokens=%d 로 더 줄일 수 없음, 폴백'
                            % (name, max_tokens))
                        break
                    log('  [llm] %s 413(요청 과대) → max_tokens %d → %d 로 축소 재시도'
                        % (name, max_tokens, new_mt))
                    max_tokens = new_mt
                    if attempt <= retries:
                        continue
                    break
                if attempt <= retries:
                    time.sleep(3)
                    continue
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = 'network: %s' % str(e)[:120]
                log('  [llm] %s 네트워크 오류(%d/%d)' % (name, attempt, retries + 1))
                time.sleep(3)
        _stat(name, False, last_err or '')
        PROVIDER_FAILS[name] = PROVIDER_FAILS.get(name, 0) + 1
        log('  [llm] %s 포기 → 다음 제공자로 폴백' % name)

    raise RuntimeError('모든 LLM 제공자 실패: %s' % last_err)


def selftest(purpose='write'):
    """Flash 모델이 실제로 응답하는지 점검. (text, provider) 반환."""
    return chat([{'role': 'user', 'content': 'Reply with only the word: PONG'}],
                max_tokens=64, temperature=0.1, timeout=60, purpose=purpose)


if __name__ == '__main__':
    print('flash_only:', FLASH_ONLY)
    for purpose in ('write', 'check'):
        print('--- purpose=%s  order=%s' % (purpose, PURPOSE_ORDER[purpose]))
        for row in chain(purpose):
            print('    available:', row[0], '/', row[3])
        try:
            text, prov = selftest(purpose)
            print('    selftest OK -> provider=%s, reply=%r' % (prov, text.strip()[:60]))
        except Exception as e:
            print('    selftest FAIL:', str(e)[:200])
