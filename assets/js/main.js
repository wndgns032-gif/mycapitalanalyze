/* ============================================
   MyCapital Analyze — 공통 JS
   1) 다크모드 토글 (localStorage + 시스템 설정)
   2) Media.net 광고 로더 (승인 후 cid 설정 시 활성화)
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

  // ---------- Media.net 광고 ----------
  // 승인 후 MEDIANET_CID 값을 실제 cid로 바꾸면 광고가 활성화된다.
  var MEDIANET_CID = ''; // 예: '8CUXXXXXXXXXX'

  if (MEDIANET_CID) {
    window._mNHandle = window._mNHandle || {};
    window._mNHandle.queue = window._mNHandle.queue || [];
    window.medianet_versionId = '3121199';

    var s = document.createElement('script');
    s.src = 'https://contextual.media.net/dmedianet.js?cid=' + MEDIANET_CID;
    s.async = true;
    document.head.appendChild(s);

    // 각 광고 슬롯 로드 함수
    window.loadMediaNetAd = function (slotId, size) {
      window._mNHandle.queue.push(function () {
        window._mNDetails.loadTag(slotId, size, slotId);
      });
    };
  }
})();
