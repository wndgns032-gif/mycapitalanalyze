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
    for (var d = 1; d <= days; d++) { cells += cell(null, d, false); col++; if (col % 7 === 0) { rows += '<div class="grid grid-cols-7 gap-1">' + cells + '</div>'; cells = ''; } }
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
        .then(function (j) { return (j && j.count) || 0; })
        .catch(function () { return 0; });
    })).then(function (arr) { return [cc, arr.reduce(function (a, b) { return a + b; }, 0)]; });
  })).then(function (pairs) {
    var data = {};
    pairs.forEach(function (p) { if (p[1] > 0) data[p[0]] = p[1]; });
    render(data);
    try { localStorage.setItem(CACHE, JSON.stringify({ t: Date.now(), d: data })); } catch (e) {}
  }).catch(function () {});
})();
