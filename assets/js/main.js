/* ============================================
   MyCapital Analyze — 공통 JS
   1) 다크모드 토글 (localStorage + 시스템 설정)
   2) 방문자 카운터 (1일 1회, KST 기준)
   ============================================ */

(function () {
  // ---------- 다크모드 ----------
  const KEY = 'mycapital-theme';
  const stored = localStorage.getItem(KEY);
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const isDark = stored ? stored === 'dark' : prefersDark;

  function applyTheme(dark) {
    document.documentElement.classList.toggle('dark', dark);
  }
  applyTheme(isDark);

  // 헤더의 토글 버튼에서 호출
  window.toggleTheme = function () {
    const next = !document.documentElement.classList.contains('dark');
    applyTheme(next);
    localStorage.setItem(KEY, next ? 'dark' : 'light');
  };
})();

/* ---------- 방문자 카운터 ----------
   목적: "하루에 몇 명이 들어왔는지"만 집계한다 (누가/어디서/어떻게 는 수집하지 않음).
   - localStorage에 마지막 집계 날짜를 저장 → 같은 날 재방문은 1명으로 처리.
   - 날짜 기준은 KST(UTC+9). 쿠키·IP·개인정보는 저장하지 않는다.
   - 집계 서버가 응답하지 않아도 사이트 동작에는 영향이 없다(실패 무시).
*/
(function () {
  var NS = 'mca-visit-7f3q';
  var API = 'https://tallywire.cronpulse.workers.dev';
  try {
    var d = new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10).replace(/-/g, '');
    if (localStorage.getItem('mcv-day') === d) return;
    fetch(API + '/hit/' + NS + '/v' + d, { mode: 'cors', cache: 'no-store' })
      .then(function () { try { localStorage.setItem('mcv-day', d); } catch (e) {} })
      .catch(function () {});
  } catch (e) {}
})();
