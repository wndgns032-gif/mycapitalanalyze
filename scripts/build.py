#!/usr/bin/env python3
"""
13개 언어 정적 사이트 빌더.
content/posts/*.md (영어 원본) + content/translations/{lang}/{slug}.json → HTML 생성.

출력:
  - 영어(루트): index.html, post/{slug}.html
  - 번역: {lang}/index.html, {lang}/post/{slug}.html
  - sitemap.xml, feed.xml

사용법: python scripts/build.py
"""
import json, os, re, glob, html as htmllib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
DOMAIN = 'https://www.mycapitalanalyze.com'
SITE_NAME = 'MyCapital Analyze'
TAGLINE_EN = 'Global Macro &amp; Economics Analysis'
FOOTER_EN = 'Posts on this blog are AI-curated summaries and independent analysis of public macro and economic data. Content is for informational purposes only and does not constitute investment advice.'

POSTS_DIR = os.path.join(BASE, 'content', 'posts')
TRANS_DIR = os.path.join(BASE, 'content', 'translations')

# (code, 표시명, rtl 여부)
LANG_META = {
    'en': ('English', 'ltr'),
    'zh': ('中文', 'ltr'),
    'hi': ('हिन्दी', 'ltr'),
    'es': ('Español', 'ltr'),
    'ar': ('العربية', 'rtl'),
    'fr': ('Français', 'ltr'),
    'bn': ('বাংলা', 'ltr'),
    'pt': ('Português', 'ltr'),
    'ru': ('Русский', 'ltr'),
    'de': ('Deutsch', 'ltr'),
    'ja': ('日本語', 'ltr'),
    'id': ('Bahasa Indonesia', 'ltr'),
    'ko': ('한국어', 'ltr'),
}

# 카테고리 → 썸네일 gradient
CATEGORY_GRADIENT = {
    'Monetary Policy': 'from-brand-700 to-brand-500',
    'Inflation': 'from-slate-700 to-slate-500',
    'China': 'from-red-800 to-red-600',
    'Bonds': 'from-emerald-800 to-emerald-600',
    'Labor Markets': 'from-violet-800 to-violet-600',
}
DEFAULT_GRADIENT = 'from-brand-700 to-brand-500'

TAILWIND_CONFIG = "darkMode: 'class', theme: { extend: { colors: { brand: { 50:'#eef4ff',100:'#dbe7ff',200:'#b9d0ff',300:'#8fb0ff',400:'#5f86f5',500:'#1e4fd8',600:'#1a3fb0',700:'#142f85',800:'#122560',900:'#0e1a3d' } } } }"


def home_path(lang):
    return '/' if lang == 'en' else f'/{lang}/'

def post_url(lang, slug):
    return f'{DOMAIN}/post/{slug}.html' if lang == 'en' else f'{DOMAIN}/{lang}/post/{slug}.html'

def post_href(lang, slug):
    return f'/post/{slug}.html' if lang == 'en' else f'/{lang}/post/{slug}.html'


# ---------- 프론트매터 ----------
def parse_md(path):
    raw = open(path, encoding='utf-8').read()
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', raw, re.S)
    fm = {}
    for line in m.group(1).splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm, m.group(2).strip()


# ---------- 마크다운 -> HTML ----------
def md_to_html(text):
    text = text.strip()
    out = []
    lines = text.split('\n')
    i = 0
    para = []
    list_buf = None  # 'ul' or 'ol'

    def flush_para():
        nonlocal para
        if para:
            out.append('<p>' + ' '.join(para) + '</p>')
            para = []

    def flush_list():
        nonlocal list_buf
        list_buf = None

    for line in lines:
        s = line.strip()
        if not s:
            flush_para(); flush_list(); continue
        if s.startswith('## '):
            flush_para(); flush_list(); out.append('<h2>' + inline(s[3:]) + '</h2>'); continue
        if s.startswith('### '):
            flush_para(); flush_list(); out.append('<h3>' + inline(s[4:]) + '</h3>'); continue
        if s.startswith('- '):
            flush_para()
            if list_buf != 'ul':
                if list_buf: out.append('</ul>')
                out.append('<ul>'); list_buf = 'ul'
            out.append('<li>' + inline(s[2:]) + '</li>'); continue
        m = re.match(r'^(\d+)\.\s+(.*)', s)
        if m:
            flush_para()
            if list_buf != 'ol':
                if list_buf: out.append('</ol>')
                out.append('<ol>'); list_buf = 'ol'
            out.append('<li>' + inline(m.group(2)) + '</li>'); continue
        if s.startswith('> '):
            flush_para(); flush_list(); out.append('<blockquote>' + inline(s[2:]) + '</blockquote>'); continue
        para.append(inline(s))

    flush_para()
    if list_buf == 'ul': out.append('</ul>')
    if list_buf == 'ol': out.append('</ol>')
    return '\n'.join(out)


def inline(s):
    s = htmllib.escape(s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'\*(.+?)\*', r'<em>\1</em>', s)
    s = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', s)
    return s


# ---------- 레이아웃 ----------
def lang_switcher(current, label='Language:'):
    btns = []
    for code, (name, _) in LANG_META.items():
        active = 'bg-brand-600 text-white' if code == current else 'text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800'
        btns.append(f'<a href="{home_path(code)}" class="px-2 py-0.5 rounded text-sm {active}" title="{name}">{code.upper()}</a>')
    return f'<div class="max-w-5xl mx-auto px-4 pt-3 flex flex-wrap items-center gap-1"><span class="text-slate-500 mr-1 text-sm">{label}</span>' + ''.join(btns) + '</div>'


def header_html(current):
    return f'''<header class="border-b border-slate-200 dark:border-slate-800 sticky top-0 bg-white/80 dark:bg-slate-950/80 backdrop-blur z-10">
    <div class="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between">
      <a class="font-bold text-lg text-slate-900 dark:text-slate-100" href="{home_path(current)}">{SITE_NAME}</a>
      <nav class="flex items-center gap-4 text-sm">
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="{home_path(current)}">Home</a>
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="/about.html">About</a>
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300 hidden sm:block" href="/privacy.html">Privacy</a>
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300 hidden sm:block" href="/contact.html">Contact</a>
        <button type="button" onclick="toggleTheme()" aria-label="Toggle dark mode" class="p-1.5 rounded text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">
          <svg class="w-4 h-4 dark:hidden" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/></svg>
          <svg class="w-4 h-4 hidden dark:block" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path stroke-linecap="round" stroke-linejoin="round" d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>
        </button>
      </nav>
    </div>
  </header>'''


def footer_html():
    return f'''<footer class="border-t border-slate-200 dark:border-slate-800 mt-16">
    <div class="max-w-5xl mx-auto px-4 py-8 text-sm text-slate-500 dark:text-slate-400 space-y-2">
      <p>&copy; 2026 {SITE_NAME}. All rights reserved.</p>
      <p class="text-xs leading-relaxed">{FOOTER_EN}</p>
    </div>
  </footer>'''


def layout(lang, title, description, canonical, content_html, og_type='website', og_url=None):
    dir_ = LANG_META[lang][1]
    og_url = og_url or canonical
    return f'''<!DOCTYPE html>
<html lang="{lang}" dir="{dir_}" class="scroll-smooth">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{htmllib.escape(title)}</title>
  <meta name="description" content="{htmllib.escape(description)}" />
  <link rel="canonical" href="{canonical}" />
  <link rel="alternate" type="application/rss+xml" href="{DOMAIN}/feed.xml" />
  <meta property="og:title" content="{htmllib.escape(title)}" />
  <meta property="og:description" content="{htmllib.escape(description)}" />
  <meta property="og:type" content="{og_type}" />
  <meta property="og:url" content="{og_url}" />
  <meta name="twitter:card" content="summary_large_image" />
  <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9243770518153989" crossorigin="anonymous"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>tailwind.config = {{ {TAILWIND_CONFIG} }};</script>
  <link rel="stylesheet" href="/assets/css/custom.css" />
</head>
<body class="bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100 min-h-screen flex flex-col antialiased">

{header_html(lang)}
{lang_switcher(lang)}

<main class="flex-1 max-w-5xl w-full mx-auto px-4 py-8">
{content_html}
</main>

{footer_html()}
<script src="/assets/js/main.js"></script>
</body>
</html>
'''


def ad_slot(slot_id, label='Ad — 300x600 (Media.net)', style='width:300px;height:600px;margin:0 auto'):
    return f'<div class="my-6 text-center" aria-hidden="true"><div id="{slot_id}" class="ad-slot" style="{style}">{label}</div></div>'


# ---------- 포스트 페이지 ----------
def card_html(lang, slug, title, desc, category, date):
    grad = CATEGORY_GRADIENT.get(category, DEFAULT_GRADIENT)
    return f'''<article class="border border-slate-200 dark:border-slate-800 rounded-lg overflow-hidden hover:shadow-md transition-shadow bg-white dark:bg-slate-900 flex flex-col">
  <a class="block aspect-[16/9] overflow-hidden bg-gradient-to-br {grad} flex items-center justify-center" href="{post_href(lang, slug)}">
    <span class="text-white/90 text-sm font-semibold uppercase tracking-widest px-4 text-center">{htmllib.escape(category)}</span>
  </a>
  <div class="p-5">
    <div class="flex items-center gap-2 text-xs text-slate-500 mb-2">
      <span class="bg-brand-50 text-brand-700 px-2 py-0.5 rounded uppercase tracking-wide">{htmllib.escape(category)}</span>
      <time datetime="{date}">{date}</time>
    </div>
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2 leading-snug"><a class="hover:text-brand-600" href="{post_href(lang, slug)}">{htmllib.escape(title)}</a></h2>
    <p class="text-sm text-slate-600 dark:text-slate-400 line-clamp-3">{htmllib.escape(desc)}</p>
  </div>
</article>'''


def build_post(lang, slug, title, desc, category, date, body_md):
    body_html = md_to_html(body_md)
    canonical = post_url(lang, slug)
    content = f'''<article class="max-w-3xl mx-auto">
  <header class="mb-8">
    <div class="flex items-center gap-2 text-xs text-slate-500 mb-3">
      <span class="bg-brand-50 text-brand-700 px-2 py-0.5 rounded uppercase tracking-wide">{htmllib.escape(category)}</span>
      <time datetime="{date}">Published: {date}</time>
      <span class="text-slate-400">&middot;</span>
      <span>Independent Analysis</span>
    </div>
    <h1 class="text-3xl font-bold leading-tight text-slate-900 dark:text-slate-100 mb-4">{htmllib.escape(title)}</h1>
    <p class="text-slate-600 dark:text-slate-400">{htmllib.escape(desc)}</p>
  </header>
  {ad_slot('medianet-inarticle-1', 'Ad — In-article (Media.net)', 'min-height:120px')}
  <div class="prose">{body_html}</div>
  {ad_slot('medianet-inarticle-2', 'Ad — In-article (Media.net)', 'min-height:120px')}
</article>'''
    html_doc = layout(lang, title + ' — ' + SITE_NAME, desc, canonical, content, 'article', canonical)
    out_path = os.path.join(BASE, 'post', slug + '.html') if lang == 'en' else os.path.join(BASE, lang, 'post', slug + '.html')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, 'w', encoding='utf-8').write(html_doc)
    return out_path


# ---------- 홈 페이지 ----------
def build_index(lang, posts):
    cards = '\n'.join(card_html(lang, p['slug'], p['title'], p['desc'], p['category'], p['date']) for p in posts)
    content = f'''<div class="space-y-6">
  <h1 class="text-2xl font-bold text-slate-900 dark:text-slate-100">Latest Macro &amp; Market Insights</h1>
  {ad_slot('medianet-sidebar-1')}
  <div class="grid gap-4 sm:grid-cols-2">
{cards}
  </div>
</div>'''
    canonical = DOMAIN + home_path(lang)
    html_doc = layout(lang, f'{SITE_NAME} — {TAGLINE_EN}', 'Global macro, markets, and economics insights. Independent analysis of interest rates, inflation, growth, and capital flows.', canonical, content)
    out_path = os.path.join(BASE, 'index.html') if lang == 'en' else os.path.join(BASE, lang, 'index.html')
    open(out_path, 'w', encoding='utf-8').write(html_doc)
    return out_path


# ---------- sitemap / feed ----------
def build_sitemap(all_posts):
    urls = [DOMAIN + '/', DOMAIN + '/about.html', DOMAIN + '/privacy.html', DOMAIN + '/contact.html']
    for lang in LANG_META:
        if lang != 'en':
            urls.append(DOMAIN + home_path(lang))
        for p in all_posts:
            urls.append(post_url(lang, p['slug']))
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml.append(f'  <url><loc>{u}</loc></url>')
    xml.append('</urlset>')
    open(os.path.join(BASE, 'sitemap.xml'), 'w', encoding='utf-8').write('\n'.join(xml) + '\n')


def build_feed(all_posts):
    items = []
    for p in all_posts:
        items.append(f'''  <item>
    <title>{htmllib.escape(p['title'])}</title>
    <link>{post_url('en', p['slug'])}</link>
    <guid>{post_url('en', p['slug'])}</guid>
    <pubDate>{p['date']}T00:00:00Z</pubDate>
    <description>{htmllib.escape(p['desc'])}</description>
  </item>''')
    rss = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{SITE_NAME}</title>
    <link>{DOMAIN}/</link>
    <description>Global macro and economics analysis</description>
    <language>en</language>
    <atom:link href="{DOMAIN}/feed.xml" rel="self" type="application/rss+xml" />
{chr(10).join(items)}
  </channel>
</rss>'''
    open(os.path.join(BASE, 'feed.xml'), 'w', encoding='utf-8').write(rss)


# ---------- 메인 ----------
def main():
    posts = []
    for path in sorted(glob.glob(os.path.join(POSTS_DIR, '*.md'))):
        fm, body = parse_md(path)
        posts.append({'slug': fm['slug'], 'title': fm['title'], 'desc': fm['description'],
                      'category': fm['category'], 'date': fm['date'], 'body': body})
    posts.sort(key=lambda p: p['date'], reverse=True)

    # 영어 포스트 + 홈
    print('[en]')
    for p in posts:
        build_post('en', p['slug'], p['title'], p['desc'], p['category'], p['date'], p['body'])
        print(f'  post/{p["slug"]}.html')
    build_index('en', posts)
    print('  index.html')

    # 번역 포스트 + 홈
    for lang, (lang_name, _) in LANG_META.items():
        if lang == 'en':
            continue
        tdir = os.path.join(TRANS_DIR, lang)
        if not os.path.isdir(tdir):
            continue
        print(f'[{lang}]')
        translated_posts = []
        for p in posts:
            tj = os.path.join(tdir, p['slug'] + '.json')
            if not os.path.exists(tj):
                continue
            t = json.load(open(tj, encoding='utf-8'))
            build_post(lang, p['slug'], t['title'], t['description'], p['category'], p['date'], t['body'])
            print(f'  {lang}/post/{p["slug"]}.html')
            translated_posts.append({'slug': p['slug'], 'title': t['title'], 'desc': t['description'],
                                     'category': p['category'], 'date': p['date']})
        if translated_posts:
            build_index(lang, translated_posts)
            print(f'  {lang}/index.html')

    build_sitemap(posts)
    build_feed(posts)
    print('sitemap.xml, feed.xml OK')


if __name__ == '__main__':
    main()
