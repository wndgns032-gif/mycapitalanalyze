/* ============================================
   MyCapital Analyze — 공통 JS
   1) 다크모드 토글 (localStorage + 시스템 설정)
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
