/* ============================================
   MyCapital Analyze — 공통 JS
   1) 다크모드 토글 (localStorage + 시스템 설정)
   2) 방문자 카운터 (1일 1회, KST 기준) + 국가 코드 집계
   3) 메인 위젯: 일별 발행 달력 / 국가별 방문자 표
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
   목적: "하루에 몇 명이, 어느 나라에서 들어왔는지"만 집계한다.
   - localStorage에 마지막 집계 날짜를 저장 → 같은 날 재방문은 1명으로 처리.
   - 날짜 기준은 KST(UTC+9). 쿠키·IP는 저장하지 않고 국가 코드만 7일간 캐시한다.
   - 집계 서버가 응답하지 않아도 사이트 동작에는 영향이 없다(실패 무시).
*/
(function () {
  var NS = 'mca-visit-7f3q';
  var API = 'https://tallywire.cronpulse.workers.dev';
  try {
    var d = new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10).replace(/-/g, '');
    if (localStorage.getItem('mcv-day') !== d) {
      fetch(API + '/hit/' + NS + '/v' + d, { mode: 'cors', cache: 'no-store' })
        .then(function () { try { localStorage.setItem('mcv-day', d); } catch (e) {} })
        .catch(function () {});
      hitCountry(d);
    }
  } catch (e) {}

  function hitCountry(d) {
    var cached = null;
    try { cached = JSON.parse(localStorage.getItem('mcv-geo') || 'null'); } catch (e) {}
    if (cached && cached.cc && Date.now() - cached.t < 7 * 864e5) {
      send(cached.cc); return;
    }
    // 국가 코드 조회 — 무료 공개 API 2개를 차례로 시도한다 (둘 다 실패해도 무시).
    fetch('https://ipwho.is/').then(function (r) { return r.json(); }).then(function (j) {
      var cc = ((j && j.country_code) || '').toUpperCase();
      if (!/^[A-Z]{2}$/.test(cc)) throw 0;
      remember(cc);
    }).catch(function () {
      fetch('https://ipapi.co/json/').then(function (r) { return r.json(); }).then(function (j) {
        var cc = ((j && j.country_code) || '').toUpperCase();
        if (/^[A-Z]{2}$/.test(cc)) remember(cc);
      }).catch(function () {});
    });
  }
  function remember(cc) {
    try { localStorage.setItem('mcv-geo', JSON.stringify({ cc: cc, t: Date.now() })); } catch (e) {}
    send(cc);
  }
  function send(cc) {
    try {
      fetch(API + '/hit/' + NS + '/v' + dKey() + '-' + cc, { mode: 'cors', cache: 'no-store' })
        .catch(function () {});
    } catch (e) {}
  }
  function dKey() {
    return new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10).replace(/-/g, '');
  }
})();

/* ============================================
   메인 위젯 1) 일별 발행 달력
   - window.__POSTS__ = [{t:제목, d:YYYY-MM-DD, h:링크, k:'econ'|'app'|'game'}]
   - 달의 각 칸에 그 날 발행된 글 수를 배지로, 클릭하면 목록이 아래에 펼쳐진다.
   ============================================ */
(function () {
  var mount = document.getElementById('mca-cal');
  if (!mount || !window.__POSTS__) return;
  var out = document.getElementById('mca-cal-out');
  var LOCALE = { zh: 'zh-CN', pt: 'pt-BR' }[mount.dataset.locale] || (mount.dataset.locale || 'en');
  var none = mount.dataset.strNone || 'No posts.';
  var unit = mount.dataset.strUnit || 'posts';

  var byDate = {};
  window.__POSTS__.forEach(function (p) {
    (byDate[p.d] = byDate[p.d] || []).push(p);
  });
  var dates = Object.keys(byDate).sort();
  var min = dates.length ? new Date(dates[0]) : new Date();
  var view = new Date();
  view.setDate(1);

  var wnames = [];
  for (var i = 0; i < 7; i++) {
    var dt = new Date(2017, 0, 1 + i); // 2017-01-01 = 일요일
    wnames.push(new Intl.DateTimeFormat(LOCALE, { weekday: 'narrow' }).format(dt));
  }
  var fmtM = new Intl.DateTimeFormat(LOCALE, { year: 'numeric', month: 'long' });
  var today = new Date(); today.setHours(0, 0, 0, 0);

  function tagOf(k) {
    var c = k === 'game' ? 'bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-900/40 dark:text-fuchsia-300'
                         : k === 'app' ? 'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300'
                                       : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300';
    return k === 'econ' ? '' : '<span class="ml-1 px-1.5 py-0.5 rounded text-[10px] ' + c + '">' + k.toUpperCase() + '</span>';
  }

  function render() {
    var y = view.getFullYear(), m = view.getMonth();
    var first = new Date(y, m, 1);
    var days = new Date(y, m + 1, 0).getDate();
    var lead = first.getDay();
    var rows = '', col = 0, cells = '';

    function cell(day) {
      var key = y + '-' + String(m + 1).padStart(2, '0') + '-' + String(day).padStart(2, '0');
      var n = (byDate[key] || []).length;
      var isToday = (today.getFullYear() === y && today.getMonth() === m && today.getDate() === day);
      var base = 'w-9 h-9 rounded-full text-sm flex items-center justify-center transition-colors ';
      var cls = n ? 'bg-brand-600 text-white hover:bg-brand-500 cursor-pointer font-semibold'
        : isToday ? 'ring-1 ring-brand-400 text-slate-700 dark:text-slate-300'
        : 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800';
      return '<button type="button" data-day="' + (n ? key : '') + '" class="' + base + cls + '"'
        + (n ? ' aria-label="' + n + '"' : ' disabled') + '>' + day + (n ?
        '<span class="ml-0.5 text-[10px] opacity-90">' + n + '</span>' : '') + '</button>';
    }

    if (lead) { for (var i = 0; i < lead; i++) { cells += '<div></div>'; col++; } }
    for (var d = 1; d <= days; d++) { cells += cell(d); col++; if (col % 7 === 0) { rows += '<div class="grid grid-cols-7 gap-1">' + cells + '</div>'; cells = ''; } }
    if (cells) rows += '<div class="grid grid-cols-7 gap-1">' + cells + '</div>';

    mount.innerHTML =
      '<div class="flex items-center justify-between mb-3">' +
      '<button type="button" id="mca-cal-prev" class="p-1.5 rounded text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="prev">&#9664;</button>' +
      '<div class="text-sm font-semibold text-slate-800 dark:text-slate-200">' + fmtM.format(first) + '</div>' +
      '<button type="button" id="mca-cal-next" class="p-1.5 rounded text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="next">&#9654;</button>' +
      '</div>' +
      '<div class="grid grid-cols-7 gap-1 mb-1 text-center text-[11px] text-slate-400">' + wnames.map(function (w) { return '<div>' + w + '</div>'; }).join('') + '</div>' +
      rows;

    var prev = document.getElementById('mca-cal-prev');
    var next = document.getElementById('mca-cal-next');
    var canPrev = view > new Date(min.getFullYear(), min.getMonth(), 1);
    prev.disabled = !canPrev; prev.style.opacity = canPrev ? 1 : 0.3;
    next.disabled = view >= new Date(today.getFullYear(), today.getMonth(), 1);
    next.style.opacity = next.disabled ? 0.3 : 1;
    prev.onclick = function () { view = new Date(y, m - 1, 1); render(); };
    next.onclick = function () { view = new Date(y, m + 1, 1); render(); };

    mount.querySelectorAll('button[data-day]').forEach(function (b) {
      b.onclick = function () {
        var list = byDate[b.dataset.day] || [];
        out.innerHTML = '<div class="font-medium text-slate-700 dark:text-slate-300 mb-1">' + b.dataset.day +
          ' · ' + list.length + ' ' + unit + '</div>' +
          '<ul class="space-y-1">' + list.map(function (p) {
            return '<li class="truncate"><a class="text-brand-600 dark:text-brand-400 hover:underline" href="' + p.h + '">' +
              p.t.replace(/</g, '&lt;') + '</a>' + tagOf(p.k) + '</li>';
          }).join('') + '</ul>';
      };
    });
  }
  render();
})();

/* ---------- 공용 헬퍼 (위젯 공통) ---------- */
var MCA_API = 'https://tallywire.cronpulse.workers.dev';
var MCA_NS = 'mca-visit-7f3q';
function mcaKstDate(off) {
  var t = new Date(Date.now() + 9 * 3600 * 1000 - (off || 0) * 864e5);
  return t.toISOString().slice(0, 10).replace(/-/g, '');
}
function mcaPidOf(h) {
  var m = String(h || '').split('/').pop().replace(/\.html$/, '');
  return (m || '').replace(/[^a-zA-Z0-9-]/g, '').slice(0, 60);
}

/* ============================================
   메인 위젯 3) 글별 인기도 추적 (포스트 페이지)
   - /post/*.html, /game/post/*.html (+ /{lang}/...) 에서만 동작.
   - 같은 글은 하루 1회만 카운트 (KST 기준, localStorage).
   ============================================ */
(function () {
  var m = location.pathname.match(/\/post\/([^\/]+)\.html$/);
  if (!m) return;
  var pid = m[1].replace(/[^a-zA-Z0-9-]/g, '').slice(0, 60);
  if (!pid) return;
  var d = mcaKstDate(0);
  try { if (localStorage.getItem('mcv-p-' + pid + '-' + d)) return; } catch (e) {}
  fetch(MCA_API + '/hit/' + MCA_NS + '/p' + d + '-' + pid, { mode: 'cors', cache: 'no-store' })
    .then(function () { try { localStorage.setItem('mcv-p-' + pid + '-' + d, '1'); } catch (e) {} })
    .catch(function () {});
})();

/* ============================================
   메인 위젯 4) 공유 버튼 (포스트 페이지)
   - X + WhatsApp + 링크 복사 (공통)
   - ko=카카오톡, ja=LINE, zh=Weibo, ru=Telegram
   ============================================ */
(function () {
  var mount = document.getElementById('mca-share');
  if (!mount) return;
  var lang = mount.dataset.shareLang || 'en';
  var u = encodeURIComponent(location.href);
  var t = encodeURIComponent(document.title);
  var copyLbl = mount.dataset.strCopy || 'Copy link';
  var copiedLbl = mount.dataset.strCopied || 'Copied!';
  var chan = {
    ko: ['\uCE74\uCE74\uC624\uD1A1', 'https://sharer.kakao.com/talk/sharer/link?url=' + u],
    ja: ['LINE', 'https://social-plugins.line.me/lineit/share?url=' + u],
    zh: ['\u5FAE\u535A', 'https://service.weibo.com/share/share.php?url=' + u + '&title=' + t],
    ru: ['Telegram', 'https://t.me/share/url?url=' + u + '&text=' + t]
  }[lang];
  var links = [['X', 'https://twitter.com/intent/tweet?url=' + u + '&text=' + t]];
  if (chan) links.push(chan);
  links.push(['WhatsApp', 'https://wa.me/?text=' + t + '%20' + u]);
  var html = links.map(function (l) {
    return '<a class="tap inline-flex items-center gap-1 px-4 rounded-full border '
      + 'border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-300 '
      + 'hover:border-brand-400 hover:text-brand-600 text-xs font-medium transition-colors" '
      + 'target="_blank" rel="noopener" href="' + l[1] + '">' + l[0] + '</a>';
  }).join('');
  html += '<button type="button" id="mca-share-copy" class="tap inline-flex items-center gap-1 px-4 '
    + 'rounded-full border border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-300 '
    + 'hover:border-brand-400 hover:text-brand-600 text-xs font-medium transition-colors">'
    + copyLbl + '</button>';
  mount.innerHTML = html;
  var btn = document.getElementById('mca-share-copy');
  btn.onclick = function () {
    try {
      navigator.clipboard.writeText(location.href).then(function () {
        btn.textContent = copiedLbl;
        setTimeout(function () { btn.textContent = copyLbl; }, 2000);
      });
    } catch (e) {}
  };
})();

/* ============================================
   메인 위젯 5) 이번 주 인기 글 (홈)
   - 최근 7일 KST × 이 언어의 글 최대 30개 조회 히트를 합산해 TOP 5.
   - 1시간 로컬 캐시. 데이터가 없으면 안내 문구만.
   ============================================ */
(function () {
  var mount = document.getElementById('mca-pop');
  if (!mount || !window.__POSTS__) return;
  var wait = mount.dataset.strWait || '…';
  var views = mount.dataset.strViews || 'views';
  // ⚠️ 2026-10-07 실측 버그: __POSTS__ 는 **오래된 글부터** 들어있다.
  //   그냥 slice(0,30) 하면 최신 글이 아니라 8월 긁 việt만 재게 되어
  //   "이번 주 인기 글" 이 영원히 집계중으로 남는다 → 최근 7일 글만, 최신순으로.
  var cutoff = new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10);
  var posts = window.__POSTS__
    .filter(function (p) { return (p.d || '') >= cutoff; })
    .sort(function (a, b) { return (a.d < b.d) ? 1 : (a.d > b.d) ? -1 : 0; })
    .slice(0, 30);

  function tagOf(k) {
    var c = k === 'game' ? 'bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-900/40 dark:text-fuchsia-300'
                         : k === 'app' ? 'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300'
                                       : '';
    return k && k !== 'econ' ? '<span class="ml-1 px-1.5 py-0.5 rounded text-[10px] ' + c + '">' + k.toUpperCase() + '</span>' : '';
  }

  function render(rows) {
    if (!rows.length) {
      mount.innerHTML = '<p class="text-sm text-slate-500 dark:text-slate-400 py-4">' + wait + '</p>';
      return;
    }
    mount.innerHTML = '<ol class="space-y-2">' + rows.map(function (r, i) {
      return '<li class="flex items-baseline gap-2.5 text-sm">'
        + '<span class="w-5 h-5 shrink-0 rounded-full bg-brand-600 text-white text-[11px] font-bold flex items-center justify-center">' + (i + 1) + '</span>'
        + '<a class="truncate hover:underline text-slate-800 dark:text-slate-200" href="' + r.p.h + '">'
        + String(r.p.t).replace(/</g, '&lt;') + '</a>' + tagOf(r.p.k)
        + '<span class="ml-auto shrink-0 text-xs text-slate-500 tabular-nums">' + r.n + ' ' + views + '</span></li>';
    }).join('') + '</ol>';
  }

  var cached = null;
  try { cached = JSON.parse(localStorage.getItem('mcv-popstats') || 'null'); } catch (e) {}
  if (cached && Date.now() - cached.t < 3600 * 1000) { render(cached.r); return; }
  mount.innerHTML = '<p class="text-sm text-slate-500 dark:text-slate-400 py-4">' + wait + '</p>';

  var days = [mcaKstDate(0), mcaKstDate(1), mcaKstDate(2), mcaKstDate(3),
              mcaKstDate(4), mcaKstDate(5), mcaKstDate(6)];
  Promise.all(posts.map(function (p) {
    var pid = mcaPidOf(p.h);
    if (!pid) return Promise.resolve(null);
    return Promise.all(days.map(function (d) {
      return fetch(MCA_API + '/get/' + MCA_NS + '/p' + d + '-' + pid, { mode: 'cors', cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (j) { return (j && (j.value != null ? j.value : j.count)) || 0; })
        .catch(function () { return 0; });
    })).then(function (arr) {
      return arr.reduce(function (a, b) { return a + b; }, 0);
    }).then(function (n) { return n > 0 ? { p: p, n: n } : null; });
  })).then(function (res) {
    var rows = res.filter(Boolean).sort(function (a, b) { return b.n - a.n; }).slice(0, 5);
    render(rows);
    try { localStorage.setItem('mcv-popstats', JSON.stringify({ t: Date.now(), r: rows })); } catch (e) {}
  }).catch(function () {});
})();

/* ============================================
   메인 위젯 6) 사이트 내 검색 (/search/)
   - 제목 + 설명 실시간 필터. window.__POSTS__ 사용 (요청 0건).
   ============================================ */
(function () {
  var input = document.getElementById('mca-search');
  var out = document.getElementById('mca-search-out');
  var cnt = document.getElementById('mca-search-count');
  if (!input || !out || !window.__POSTS__) return;
  var none = out.dataset.strNone || 'No results found.';
  var unit = out.dataset.strUnit || 'results';
  var posts = window.__POSTS__;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function tagOf(k) {
    var c = k === 'game' ? 'bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-900/40 dark:text-fuchsia-300'
                         : k === 'app' ? 'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300'
                                       : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300';
    return '<span class="shrink-0 px-1.5 py-0.5 rounded text-[10px] ' + c + '">' + (k === 'econ' ? 'ECON' : k.toUpperCase()) + '</span>';
  }

  function render() {
    var q = input.value.trim().toLowerCase();
    var list = q
      ? posts.filter(function (p) { return (p.t + ' ' + (p.x || '')).toLowerCase().indexOf(q) !== -1; })
      : posts;
    cnt.textContent = list.length + ' ' + unit;
    if (!list.length) {
      out.innerHTML = '<p class="text-sm text-slate-500 dark:text-slate-400 py-8 text-center">' + esc(none) + '</p>';
      return;
    }
    out.innerHTML = list.map(function (p) {
      return '<article class="border border-slate-200 dark:border-slate-800 rounded-lg p-4 bg-white dark:bg-slate-900">'
        + '<div class="flex items-center gap-2 text-xs text-slate-500 mb-1"><time>' + esc(p.d) + '</time>' + tagOf(p.k) + '</div>'
        + '<h2 class="text-base font-semibold leading-snug"><a class="text-brand-600 dark:text-brand-400 hover:underline" href="' + esc(p.h) + '">' + esc(p.t) + '</a></h2>'
        + (p.x ? '<p class="mt-1 text-sm text-slate-600 dark:text-slate-400 line-clamp-2">' + esc(p.x) + '</p>' : '')
        + '</article>';
    }).join('');
  }
  var tm = null;
  input.addEventListener('input', function () {
    clearTimeout(tm); tm = setTimeout(render, 120);
  });
  render();
})();

/* ============================================
   메인 위젯 2) 국가별 방문자 표 (최근 7일, tallywire)
   ============================================ */
(function () {
  var mount = document.getElementById('mca-geo');
  if (!mount) return;
  var NS = 'mca-visit-7f3q';
  var API = 'https://tallywire.cronpulse.workers.dev';
  var wait = mount.dataset.strWait || '…';
  var loc = { zh: 'zh-CN', pt: 'pt-BR' }[mount.dataset.locale] || (mount.dataset.locale || 'en');
  var CCS = ['US', 'KR', 'JP', 'CN', 'TW', 'HK', 'SG', 'DE', 'FR', 'GB', 'ES', 'RU', 'IN', 'BR', 'ID', 'VN'];
  var CACHE = 'mcv-cstats';

  function kstDates(n) {
    var out = [], t = new Date(Date.now() + 9 * 3600 * 1000);
    for (var i = 0; i < n; i++) {
      out.push(t.toISOString().slice(0, 10).replace(/-/g, ''));
      t = new Date(t.getTime() - 864e5);
    }
    return out;
  }

  function flag(cc) {
    try {
      return String.fromCodePoint.apply(null, cc.split('').map(function (c) {
        return 127397 + c.charCodeAt(0);
      }));
    } catch (e) { return ''; }
  }

  function nameOf(cc) {
    try {
      return new Intl.DisplayNames([loc], { type: 'region' }).of(cc) || cc;
    } catch (e) { return cc; }
  }

  function render(data) {
    var rows = Object.keys(data).map(function (cc) { return [cc, data[cc]]; })
      .filter(function (r) { return r[1] > 0; }).sort(function (a, b) { return b[1] - a[1]; });
    var total = rows.reduce(function (s, r) { return s + r[1]; }, 0);
    if (!total) {
      mount.innerHTML = '<p class="text-sm text-slate-500 dark:text-slate-400 py-4">' + wait + '</p>';
      return;
    }
    var max = rows[0][1];
    mount.innerHTML = '<table class="w-full text-sm"><tbody>' + rows.map(function (r) {
      var pct = Math.round(r[1] / total * 100);
      return '<tr class="border-b border-slate-100 dark:border-slate-800">' +
        '<td class="py-1.5 whitespace-nowrap"><span class="mr-2">' + flag(r[0]) + '</span><span class="text-slate-700 dark:text-slate-300">' +
        nameOf(r[0]) + '</span></td>' +
        '<td class="py-1.5 w-1/2"><div class="h-2 rounded bg-slate-100 dark:bg-slate-800"><div class="h-2 rounded bg-brand-500" style="width:' +
        Math.max(3, Math.round(r[1] / max * 100)) + '%"></div></div></td>' +
        '<td class="py-1.5 text-right tabular-nums text-slate-600 dark:text-slate-400">' + r[1] + ' <span class="text-xs text-slate-400">(' + pct + '%)</span></td>' +
        '</tr>';
    }).join('') + '</tbody></table>';
  }

  var cached = null;
  try { cached = JSON.parse(localStorage.getItem(CACHE) || 'null'); } catch (e) {}
  if (cached && Date.now() - cached.t < 6 * 3600 * 1000) { render(cached.d); return; }

  mount.innerHTML = '<p class="text-sm text-slate-500 dark:text-slate-400 py-4">' + wait + '</p>';
  var days = kstDates(7);
  Promise.all(CCS.map(function (cc) {
    return Promise.all(days.map(function (d) {
      return fetch(API + '/get/' + NS + '/v' + d + '-' + cc, { mode: 'cors', cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (j) { return (j && (j.value != null ? j.value : j.count)) || 0; })
        .catch(function () { return 0; });
    })).then(function (arr) { return [cc, arr.reduce(function (a, b) { return a + b; }, 0)]; });
  })).then(function (pairs) {
    var data = {};
    pairs.forEach(function (p) { if (p[1] > 0) data[p[0]] = p[1]; });
    render(data);
    try { localStorage.setItem(CACHE, JSON.stringify({ t: Date.now(), d: data })); } catch (e) {}
  }).catch(function () {});
})();

/* ---------- 모바일 헤더 메뉴 ---------- */
(function () {
  function closeAll(except) {
    document.querySelectorAll("details[data-mca-menu][open]").forEach(function (d) {
      if (d !== except) d.removeAttribute("open");
    });
  }
  document.addEventListener("click", function (e) {
    var d = e.target.closest ? e.target.closest("details[data-mca-menu]") : null;
    if (d) { closeAll(d); if (e.target.closest("a")) d.removeAttribute("open"); return; }
    closeAll(null);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeAll(null); });
  window.addEventListener("resize", function () { if (window.innerWidth >= 640) closeAll(null); });
})();
