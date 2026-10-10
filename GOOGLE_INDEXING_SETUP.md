# Google Indexing API 자동 색인 설정 (최초 1회, 수동)

## 왜 필요한가

`index-submit.yml` 은 매일 두 번 돈다.

| 단계 | 스크립트 | 대상 엔진 |
|---|---|---|
| 1 | `scripts/submit_index.py` (IndexNow) | Bing · Naver · Yandex · Seznam · Yep |
| 2 | `scripts/submit_google_index.py` | **Google** |

IndexNow 에 Google 은 참여하지 않는다. 그래서 2단계가 필요하고,
2단계는 **Google 서비스 계정 키**가 있어야 동작한다. 없으면 조용히 건너뛴다(워크플로 실패 아님).

---

## 4단계 설정

### 1) GCP 프로젝트 준비

1. https://console.cloud.google.com 접속 → 프로젝트 선택(또는 새로 만들기)
2. **API 및 서비스 → 라이브러리** → `Indexing API` 검색 → **사용 설정**
3. (선택) OAuth 동의 화면은 서비스 계정만 쓸 거면 건드릴 필요 없음

### 2) 서비스 계정 + JSON 키 발급

1. **API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → 서비스 계정**
2. 이름 아무거나 (예: `indexing-bot`) → 만들기 → 권한은 **빈칸으로 둬도 됨**
3. 생성된 서비스 계정 클릭 → **키** 탭 → **키 추가 → 새 키 만들기 → JSON**
4. JSON 파일이 다운로드된다. 이 안의 `client_email` 값을 메모해 둔다.
   ```
   "client_email": "indexing-bot@프로젝트ID.iam.gserviceaccount.com"
   ```

### 3) Search Console 에 그 계정을 '소유자'로 추가 ← **제일 많이 빠뜨리는 단계**

1. https://search.google.com/search-console 접속
2. `www.mycapitalanalyze.com` 속성 선택
   - 도메인 속성(`mycapitalanalyze.com`)이 아니라 **URL 접두어 속성**이 안전하다
3. 왼쪽 하단 **설정 → 사용자 및 권한 → 사용자 추가**
4. 위에서 메모한 `client_email` 전체를 붙여넣고 권한 **소유자**
5. (없으면) **색인 → Sitemap** 에 `sitemap.xml` 이 등록돼 있는지도 확인

이 단계를 빼면 API 가 `403 The caller does not have permission` 을 돌려준다.

### 4) GitHub Secret 등록

리포 `wndgns032-gif/mycapitalanalyze` → **Settings → Secrets and variables → Actions → New repository secret**

- Name: `GOOGLE_SA_JSON`
- Value: 2단계에서 받은 **JSON 파일 내용 전체** (`{ "type": "service_account", ... }` 처음부터 끝까지)

---

## 확인 방법

워크플로를 **Actions → Submit URLs to IndexNow → Run workflow** 로 수동 실행하고 로그를 본다.

정상일 때:
```
[auth] 서비스 계정: indexing-bot@xxx.iam.gserviceaccount.com
[done] 27개 제출 완료 (누적 27개)
```

시크릿이 없을 때 (정상 — 그냥 건너뜀):
```
[skip] GOOGLE_SA_JSON 시크릿이 없다 — Google Indexing 생략
```

권한 문제일 때:
```
인증/권한 오류 — Search Console에 서비스 계정을 소유자로 추가했는지 확인 필요. 중단.
```
→ 3단계를 다시 확인.

---

## 🔴 알아둘 점 — 기대치를 낮춰라 (2026-10-10 Google 공식 문서 확인 후 수정)

**Google Search Central 「Indexing API 사용」 원문 (최종수정 2026-07-17):**

> "Indexing API는 **JobPosting 또는 VideoObject에 삽입된 BroadcastEvent가 포함된 페이지를
>  크롤링하는 데만** 사용할 수 있습니다."
> "Indexing API는 **테스트를 위한 기본 할당량**을 제공합니다. 사용하려면 **승인 및 할당량을 요청**하세요."

즉:

- **일반 블로그 글은 공식 대상이 아니다.** 요청 자체는 HTTP 200 으로 받아들여질 수 있지만,
  크롤 우선순위가 실제로 오르는지는 **보장되지 않는다.**
- JobPosting 스키마를 억지로 넣어 대상으로 만드는 건 **스팸 정책 위반**. 절대 하지 않는다.
- 기본 할당량은 테스트용이라, 200 개/일 을 기대하면 안 된다. 증설은 승인 심사 대상이다.

**→ 이 스크립트의 위치: "있으면 좋고 없어도 그만."**
부작용은 0 이고 유지비도 0 이라 남겨둔다. 하지만 **구글 색인의 본류는 Search Console 이다.**
이거 설정하느라 15분 쓰기 전에 Search Console 등록부터 끝내라. 순서가 뒤바뀌면 손해다.

## 기타

- 상태 파일은 두 개다. 헷갈리지 말 것:
  - `content/indexnow_state.json` — IndexNow(Bing 등)용
  - `content/google_index_state.json` — Google용
- 소유권 인증 메타 태그는 **이미 실서비스에 전 페이지 삽입돼 있다**
  (`config.public.json` 의 `analytics.google_site_verification` → `build.py:783` `verify_html()`).
  값: `DSgNHo4SPmiOp6akCru8PRqv1Mpmvk4vyVCPX2AII6A`
  Naver / Yandex / Bing 인증 태그도 함께 세팅돼 있다.
  → Search Console 에 속성만 추가하면 **HTML 태그 인증이 즉시 통과**한다.
