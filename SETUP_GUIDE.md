# 로이(Roy)가 직접 해야 하는 설정 — 단계별 가이드

총 소요 시간 약 **5분**. 순서대로 하면 된다.

---

## ✅ 1단계. GitHub Actions 시크릿 등록 (2분)

자동 발행 파이프라인이 GLM API를 쓰려면 키를 넣어줘야 한다.

1. 브라우저에서 **https://github.com/wndgns032-gif/mycapitalanalyze** 접속 (로그인 필요)
2. 리포 페이지 **오른쪽 위의 ⚙️ Settings** 탭 클릭
   - (브라우저 폭이 좁으면 "⋯" 더보기 메뉴 안에 있다)
3. 왼쪽 사이드바에서 **Secrets and variables** 클릭 → **Actions** 클릭
4. 오른쪽 위 초록 버튼 **New repository secret** 클릭
5. 아래 두 칸 입력:
   - **Name**: `GLM_API_KEY` ← 이 철자 그대로, 대문자
   - **Secret**: `***REMOVED***`
6. **Add secret** 클릭

> **GH_PAT은 이제 필요 없다.** 워크플로를 기본 `GITHUB_TOKEN`으로 고쳐서 푸시 권한 문제를 없앴다.

### 동작 확인
1. 리포 상단 **Actions** 탭 클릭
2. 왼쪽 목록에서 **Auto Publish (daily)** 선택
3. 오른쪽 위 **Run workflow** 드롭다운 → 초록 **Run workflow** 버튼
4. 2~3분 후 목록에 뜨는 실행 하나를 클릭 → `Create config.json` → `Run publish pipeline` 스텝이 초록 체크면 성공
   - 빨간 X면 그 스텝을 클릭해서 로그를 캡처해 나한테 보내줘

---

## ✅ 2단계. GitHub Pages 켜기 (1분)

정비된 사이트를 실제로 볼 수 있는 공개 URL을 만든다.

1. 같은 리포 → **Settings** 탭
2. 왼쪽 사이드바 **Pages** 클릭
3. **Source** 항목에서:
   - **Deploy from a branch** 선택 (GitHub Actions가 아니라 이쪽)
   - **Branch**: `gh-pages` 선택, 폴더는 `/ (root)`
4. **Save** 클릭
5. 1~2분 기다린 뒤 **https://wndgns032-gif.github.io/mycapitalanalyze/** 접속

### 여기서 막히는 경우
`gh-pages` 브랜치가 목록에 안 보이면 → `pages.yml` 워크플로가 아직 안 돌았다는 뜻이다.
**Actions** 탭 → **Deploy to GitHub Pages** 워크플로가 초록 체크인지 확인. 빨간 X면 나한테 알려줘.

---

## 🔶 3단계. 라이브 도메인 접근권 (선택 — 도메인을 건드리고 싶을 때만)

`https://mycapitalanalyze.com` 은 지금 **별도의 Next.js 앱(Vercel)** 이 서비스 중이다.
내가 가진 코드(위 정적 사이트)와 다른 프로그램이라, 도메인 쪽을 만지려면 아래 둘 중 **하나**가 필요하다.

### 방법 A. Vercel 토큰 발급 (추천)
1. **https://vercel.com** 로그인
2. 오른쪽 위 프로필 아이콘 → **Settings**
3. 왼쪽 메뉴 **Tokens**
4. **Create Token** 클릭
   - Token Name: 아무거나 (`workbuddy`)
   - Scope: **Full Account**
   - Expiration: **No Expiration**
5. 생성된 토큰 문자열 복사 → **나한테 전달**

### 방법 B. 라이브 사이트의 GitHub 리포 주소 찾기
1. Vercel 대시보드에서 **mycapitalanalyze** 프로젝트 클릭
2. **Settings** 탭 → 왼쪽 **Git**
3. **Connected Git Repository** 옆에 있는 링크(예: `github.com/xxx/yyy`) → 그 URL을 **나한테 전달**

> 방법 B로 리포 주소만 알려주면, 내가 클론해서 코드를 직접 고칠 수 있다.
> A·B 둘 다 어려우면 **안 해도 된다.** 대신 정적 사이트(GitHub Pages) 쪽만 키우는 방향으로 간다.

---

## 체크리스트

- [ ] `GLM_API_KEY` 시크릿 등록
- [ ] Actions 수동 실행 1회 → 초록 체크 확인
- [ ] Settings → Pages → `gh-pages` / root → Save
- [ ] https://wndgns032-gif.github.io/mycapitalanalyze/ 접속 확인
- [ ] (선택) Vercel 토큰 또는 라이브 리포 URL 전달

---

## 참고: 각 단계를 안 하면 생기는 일

| 안 한 것 | 결과 |
|---|---|
| 1단계 | 자동 발행이 매일 실패한다. 글은 안 늘어난다. 사이트 자체는 정상. |
| 2단계 | 사이트를 볼 수 있는 공개 URL이 없다. 도메인만 기존 Next.js 앱이 서비스. |
| 3단계 | 기존 라이브 도메인은 계속 예전 방식(속보 뉴스 대량 발행)으로 돌아간다. 내 진단대로 고칠 수 없다. |
