# 제휴 수익화 실행 가이드 (mycapitalanalyze.com)

> 상태: **코드/인프라는 배포 완료** (커밋 `624506d`, Vercel READY).
> 남은 것은 **로이가 직접 클릭해서 채워야 하는 3가지 값**이다.
> 코드 작업은 더 이상 필요 없다. 아래 순서대로 30~40분이면 끝난다.

---

## 0. 지금 이미 되어 있는 것

| 항목 | 상태 |
|---|---|
| GA4 측정 코드 삽입 지점 | 준비됨 (`ga4_id` 값이 있으면 모든 페이지에 자동 삽입) |
| 제휴 박스 (포스트 하단) | 준비됨 (`affiliates.enabled=true` + URL 있으면 표시) |
| `rel="sponsored nofollow noopener"` | 적용됨 (구글 Thin affiliation 페널티 회피) |
| 제휴 클릭 이벤트 (`affiliate_click`) | 준비됨 (GA4로 전환 추적) |
| 고지 페이지 13개 언어 | 배포됨 (`/disclosure.html`, `/ko/disclosure.html` …) |
| 발행량 브레이크 | 1일 1포스트, 번역 40개/회 |

**지금 당장은 제휴 링크가 비어 있어서 광고가 안 보인다.** → 정상이다. 아래에서 채운다.

---

## 1단계. GA4 측정 ID 만들기 (10분) — 가장 먼저

제휴를 켜기 전에 **측정이 먼저**다. 클릭이 없으면 최적화도 없다.

1. <https://analytics.google.com> 접속 → Google 계정 로그인
2. 왼쪽 아래 **관리(Admin)** → **속성 만들기(Create property)**
3. 속성 이름: `mycapitalanalyze` / 국가: 대한민국 / 통화: USD
4. 데이터 스트림 → **웹** 선택
   - URL: `https://mycapitalanalyze.com`
   - 스트림 이름: `mycapitalanalyze web`
5. 생성 후 표시되는 **측정 ID (`G-XXXXXXXXXX`)** 복사
   - ※ "GTM-XXXX" 아니다. 반드시 **`G-`로 시작**하는 ID.

### GitHub Secret 등록

<https://github.com/wndgns032-gif/mycapitalanalyze/settings/secrets/actions> → **New repository secret**

- Name: `GA4_ID`
- Value: `G-XXXXXXXXXX` (위에서 복사한 값)

저장하면 **다음 자동 발행(CI) 실행 시 모든 페이지에 자동 삽입**된다.
바로 반영하고 싶으면 Actions → `Auto Publish` → Run workflow.

---

## 2단계. 금융 제휴 프로그램 가입 (20분)

트래픽이 글로벌 + 주제가 거시경제/금리/인플레이션이므로,
**차트·브로커·리서치** 3종이 가장 전환이 잘 맞는다.

### 추천 1순위 — TradingView 파트너 프로그램 ⭐

- 신청: <https://www.tradingview.com/referral-program/> → **Become a partner**
- 커미션: 플랜별 정액 (Essential $10~$60 / Plus $20~$80 / Premium $40~$100 / Ultimate $160~$400)
- **쿠키 90일** (업계 최장 수준), EPC $17.5/100클릭, 전 세계 20개 언어 지원
- 승인 조건: "트레이딩/금융 오디언스 + 오리지널 콘텐츠" → 우리 사이트는 **조건 충족**
- 지급: PayPal, 월 1회 (말일 후 30일 내)
- 💡 무료 티어가 있어 전환이 쉽고, "차트로 보는 금리/인플레이션" 같은 상시 콘텐츠와 궁합이 좋다.

### 추천 2순위 — Interactive Brokers

- 신청: Impact 네트워크 경유 (IBKR 공식 "Introducing Broker / Influencer Program")
- 모델: **티어 CPC** (전환 품질에 따라 단가 상승) 또는 정액 $200/건
- 조건: 피추천인 $10,000 예치 + 1년 유지 (정액 트랙 기준)
- ⚠️ 제외 국가 있음(일본·이스라엘·중국·한국 등 일부). 글로벌 트래픽이라 무효 클릭이 섞일 수 있음 → **보조로만**.

### 추천 3순위 — Seeking Alpha / 리서치 구독

- 주식 리서치·레팅 구독형. 개별 종목 분석 글에서 전환.
- 승인이 까다로운 편. **나중에**.

### 🔴 CEO 코멘트 (중요)

지금 트래픽 규모에서는 **제휴보다 디스플레이 광고가 먼저 돈이 된다**.
제휴는 "전환율 0.5~2%"를 요구하는데, 신규 도메인은 아직 거기까지 안 왔다.

- **병행 추천**: Google AdSense를 먼저 붙여서 **트래픽당 기본 수익(바닥 수익)**을 확보하고,
  TradingView 제휴를 **상방 옵션**으로 얹는 구조가 가장 현실적이다.
- AdSense는 1단계 GA4와 같은 계정으로 신청 가능. 승인까지 며칠~수주.
- 제휴만 달아두고 수익 0원이면 "광고 달았는데 왜 안 벌리지?"가 된다. **기대치 관리가 필요하다.**

---

## 3단계. 제휴 링크 등록 (5분)

가입 승인 후 받은 **고유 제휴 링크**를 GitHub Secret에 넣는다.

Settings → Secrets → **New repository secret**

- Name: `AFFILIATES_JSON`
- Value: 아래 JSON을 **한 줄로** 붙여넣기 (URL만 본인 링크로 교체)

```json
{"enabled":true,"offers":[{"id":"tradingview","name":"TradingView","url":"https://www.tradingview.com/?aff_id=본인ID","blurb":"Advanced charting and market analysis tools","badge":"Charting","categories":["*"]},{"id":"interactive-brokers","name":"Interactive Brokers","url":"https://www.interactivebrokers.com/본인링크","blurb":"Global multi-asset brokerage with low margin rates","badge":"Brokerage","categories":["US Economy","Global Economy"]}]}
```

### 필드 설명

| 필드 | 의미 |
|---|---|
| `enabled` | `false`면 광고 박스 전부 미표시 |
| `url` | 비어 있으면 해당 offer는 **자동 스킵** (빈 광고 안 나옴) |
| `categories` | `["*"]` = 전 글에 표시. 특정 카테고리만 넣으면 해당 글에만 표시 |
| `badge` | 박스에 붙는 작은 라벨 (Charting / Brokerage / Research) |

- 현재 사이트 카테고리는 `US Economy`, `Global Economy` 두 개다.
- **JSON 파싱이 실패하면 워크플로우가 자동으로 제휴를 비활성화**한다 → 사이트가 깨질 걱정 없음.

---

## 4단계. 검증 (5분)

배포 후 확인:

1. `https://mycapitalanalyze.com/disclosure.html` → 고지 페이지 열리는지
2. 아무 포스트 하단 → 제휴 박스에 `rel="sponsored"` 붙어 있는지 (소스 보기)
3. GA4 → **실시간(Realtime)** 보고서 → 내 접속이 잡히는지
4. GA4 → **이벤트** → `affiliate_click` 발생하는지 (박스 링크 클릭 후 수 분~24시간 내 반영)

---

## 아직 안 된 것 (구글 색인)

- **IndexNow는 구글을 포함하지 않는다.** 현재 Bing / Naver / Yandex / Seznam 만 자동 제출 중.
- 구글은 별도다: <https://search.google.com/search-console> 에서
  속성 추가 → `sitemap.xml` 제출 → 이 작업은 **한 번만 하면 끝**.
- 이게 빠져 있으면 구글 트래픽(전체의 60~90%)을 못 받는다. **우선순위 최상위.**

---

## AliExpress를 다시 하고 싶다면

- mycapitalanalyze.com에는 **넣지 않는다.** (주제 불일치 → 구글 Thin affiliation 판정 위험)
- 별도 신규 도메인(예: deal/review 성격)으로 분리하면 가능.
- 참고: 쿠키 **3일**(매우 짧음), 커미션 3~9%, 판매자 대부분 중국 본토, EU 부가세 8% 공제, 최소 출금 $16.
