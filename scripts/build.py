#!/usr/bin/env python3
"""
다국어 정적 사이트 빌더 (SEO 강화版).
content/posts/*.md (영어 원본) + content/translations/{lang}/{slug}.json → HTML 생성.

출력:
  - 영어(루트): index.html, post/{slug}.html
  - 번역: {lang}/index.html, {lang}/post/{slug}.html
  - sitemap.xml (hreflang alternates 포함), feed.xml

SEO: 페이지별 hreflang 상호 링크, NewsArticle/BreadcrumbList/FAQPage JSON-LD,
     출처 표기(isBasedOn), 내부 관련 글 링크, 언어별 홈 메타.

사용법: python scripts/build.py
"""
import json, os, re, glob, html as htmllib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
DOMAIN = 'https://www.mycapitalanalyze.com'
SITE_NAME = 'MyCapital Analyze'

POSTS_DIR = os.path.join(BASE, 'content', 'posts')
TRANS_DIR = os.path.join(BASE, 'content', 'translations')

GA4_ID = ((CONFIG.get('analytics') or {}).get('ga4_id') or '').strip()
AD_CLIENT = 'ca-pub-9243770518153989'

AFF_CFG = CONFIG.get('affiliates') or {}
AFF_ENABLED = bool(AFF_CFG.get('enabled'))
AFF_ALL = AFF_CFG.get('offers') or []
# url이 채워지지 않은 항목은 렌더링하지 않는다 (빈 링크 방지)
AFF_LIVE = [o for o in AFF_ALL if (o.get('url') or '').strip()]

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

# 언어별 UI 문자열: [tagline, home_desc, home_h1, published, source, related, footer]
LANG_STR = {
    'en': ['Global Macro & Economics Analysis',
           'Independent analysis of interest rates, inflation, growth, and capital flows.',
           'Latest Macro & Market Insights', 'Published', 'Source', 'Related Analysis',
           'Posts on this blog are AI-assisted summaries and independent analysis of public macro and economic data. Content is for informational purposes only and does not constitute investment advice.'],
    'ko': ['글로벌 거시경제 · 금융시장 분석',
           '금리, 인플레이션, 성장, 자본 흐름에 대한 독립 분석.',
           '최신 거시경제 · 시장 분석', '발행일', '출처', '관련 분석',
           '이 블로그의 글은 공개된 거시경제 데이터를 AI가 요약·분석한 것입니다. 정보 제공 목적이며 투자 조언이 아닙니다.'],
    'zh': ['全球宏观经济与金融市场分析',
           '利率、通胀、增长与资本流动的独立分析。',
           '最新宏观与市场洞察', '发布日期', '来源', '相关分析',
           '本博客文章为基于公开宏观数据的 AI 辅助摘要与独立分析，仅供参考，不构成投资建议。'],
    'ja': ['グローバルマクロ・金融市場分析',
           '金利、インフレ、成長、資本フローの独自分析。',
           '最新のマクロ・市場分析', '公開日', '出典', '関連分析',
           '本ブログの記事は公開されたマクロ経済データに基づく AI 支援の要約と独自分析です。情報提供のみを目的とし、投資助言ではありません。'],
    'es': ['Análisis macroeconómico y de mercados globales',
           'Análisis independiente de tipos de interés, inflación, crecimiento y flujos de capital.',
           'Últimos análisis macro y de mercados', 'Publicado', 'Fuente', 'Análisis relacionado',
           'Los artículos son resúmenes asistidos por IA y análisis independientes de datos macroeconómicos públicos. Solo informativos, no constituyen asesoramiento de inversión.'],
    'fr': ['Analyse macroéconomique et marchés mondiaux',
           'Analyse indépendante des taux, de l\'inflation, de la croissance et des flux de capitaux.',
           'Dernières analyses macro et marchés', 'Publié le', 'Source', 'Analyses liées',
           'Les articles sont des synthèses assistées par IA et des analyses indépendantes de données macroéconomiques publiques. À visée informative, sans conseil en investissement.'],
    'de': ['Globale Makro- und Marktanalyse',
           'Unabhängige Analyse von Zinsen, Inflation, Wachstum und Kapitalströmen.',
           'Neueste Makro- & Marktanalysen', 'Veröffentlicht', 'Quelle', 'Weitere Analysen',
           'Die Beiträge sind KI-gestützte Zusammenfassungen und unabhängige Analysen öffentlicher Makrodaten. Nur zu Informationszwecken, keine Anlageberatung.'],
    'pt': ['Análise macroeconômica e de mercados globais',
           'Análise independente de juros, inflação, crescimento e fluxos de capital.',
           'Últimas análises macro e de mercados', 'Publicado em', 'Fonte', 'Análises relacionadas',
           'Os artigos são resumos assistidos por IA e análises independentes de dados macroeconômicos públicos. Apenas informativos, não constituem aconselhamento de investimento.'],
    'ru': ['Глобальный макро- и рыночный анализ',
           'Независимый анализ ставок, инфляции, роста и потоков капитала.',
           'Последние макро- и рыночные обзоры', 'Опубликовано', 'Источник', 'Похожие материалы',
           'Статьи — подготовленные с помощью ИИ сводки и независимый анализ открытых макроэкономических данных. Только для информации, не инвестиционная рекомендация.'],
    'hi': ['वैश्विक मैक्रो और बाज़ार विश्लेषण',
           'ब्याज दरों, मुद्रास्फीति, विकास और पूंजी प्रवाह का स्वतंत्र विश्लेषण।',
           'नवीनतम मैक्रो और बाज़ार विश्लेषण', 'प्रकाशित', 'स्रोत', 'संबंधित विश्लेषण',
           'ये लेख सार्वजनिक मैक्रो डेटा के AI-सहायता प्राप्त सारांश और स्वतंत्र विश्लेषण हैं। केवल जानकारी के लिए, निवेश सलाह नहीं।'],
    'id': ['Analisis Makro & Pasar Global',
           'Analisis independen suku bunga, inflasi, pertumbuhan, dan arus modal.',
           'Analisis Makro & Pasar Terbaru', 'Diterbitkan', 'Sumber', 'Analisis Terkait',
           'Artikel adalah ringkasan berbantuan AI dan analisis independen atas data makro publik. Hanya untuk informasi, bukan saran investasi.'],
    'ar': ['تحليل الاقتصاد الكلي والأسواق العالمية',
           'تحليل مستقل لأسعار الفائدة والتضخم والنمو وتدفقات رأس المال.',
           'أحدث تحليلات الاقتصاد الكلي والأسواق', 'نُشر في', 'المصدر', 'تحليلات ذات صلة',
           'المقالات ملخصات بمساعدة الذكاء الاصطناعي وتحليل مستقل لبيانات اقتصادية عامة. لأغراض المعلومات فقط ولا تمثل نصيحة استثمارية.'],
    'bn': ['বৈশ্বিক ম্যাক্রো ও বাজার বিশ্লেষণ',
           'সুদের হার, মুদ্রাস্ফীতি, প্রবৃদ্ধি ও মূলধন প্রবাহের স্বাধীন বিশ্লেষণ।',
           'সর্বশেষ ম্যাক্রো ও বাজার বিশ্লেষণ', 'প্রকাশিত', 'উৎস', 'সম্পর্কিত বিশ্লেষণ',
           'নিবন্ধগুলো প্রকাশ্য ম্যাক্রো তথ্যের এআই-সহায়তাপ্রাপ্ত সারসংক্ষেপ ও স্বাধীন বিশ্লেষণ। শুধুমাত্র তথ্যের জন্য, বিনিয়োগ পরামর্শ নয়।'],
}
DEFAULT_STR = LANG_STR['en']

# 제휴 UI 문자열: [박스제목, 면책문구, 라벨, 페이지제목, 페이지서문]
AFF_STR = {
    'en': ['Recommended resources',
           'Some of the links above are affiliate links. If you sign up through them we may earn a commission at no additional cost to you. This never influences our analysis or rankings.',
           'Sponsored', 'Affiliate Disclosure',
           'We believe in being fully transparent about how this site is funded.'],
    'ko': ['추천 리소스',
           '위 링크 중 일부는 제휴 링크입니다. 이를 통해 가입하시면 추가 비용 없이 저희가 수수료를 받을 수 있습니다. 이는 분석 내용이나 평가에 어떠한 영향도 주지 않습니다.',
           '광고 · 제휴', '제휴 수익 고지',
           '이 사이트가 어떻게 운영 자금을 조달하는지 투명하게 공개합니다.'],
    'zh': ['推荐资源',
           '以上部分链接为联盟链接。通过它们注册时我们可能获得佣金，您无需支付额外费用。这绝不会影响我们的分析或排名。',
           '广告 · 联盟', '联盟营销披露',
           '我们致力于完全公开本网站的资金来源。'],
    'ja': ['おすすめリソース',
           '上記リンクの一部はアフィリエイトリンクです。それらを経由して登録された場合、お客様に追加費用なく当サイトが報酬を受け取ることがあります。分析内容や評価に影響はありません。',
           '広告 · アフィリエイト', 'アフィリエイト開示',
           '本サイトの運営資金について全面的に透明性を確保します。'],
    'es': ['Recursos recomendados',
           'Algunos enlaces son de afiliado. Si te registras a través de ellos podemos recibir una comisión sin coste adicional para ti. Esto nunca influye en nuestro análisis.',
           'Publicidad · Afiliado', 'Divulgación de afiliados',
           'Creemos en la total transparencia sobre cómo se financia este sitio.'],
    'fr': ['Ressources recommandées',
           'Certains liens sont des liens d\'affiliation. Si vous vous inscrivez via ceux-ci, nous pouvons percevoir une commission sans frais supplémentaires pour vous. Cela n\'influence jamais notre analyse.',
           'Publicité · Affiliation', 'Divulgation d\'affiliation',
           'Nous croyons à une transparence totale sur le financement de ce site.'],
    'de': ['Empfohlene Ressourcen',
           'Einige Links sind Affiliate-Links. Wenn Sie sich über sie anmelden, erhalten wir eine Provision ohne zusätzliche Kosten für Sie. Dies beeinflusst unsere Analyse niemals.',
           'Anzeige · Affiliate', 'Affiliate-Offenlegung',
           'Wir setzen auf vollständige Transparenz bei der Finanzierung dieser Website.'],
    'pt': ['Recursos recomendados',
           'Alguns links são de afiliado. Se você se cadastrar por eles, podemos receber uma comissão sem custo adicional para você. Isso nunca influencia nossa análise.',
           'Publicidade · Afiliado', 'Divulgação de afiliados',
           'Acreditamos na total transparência sobre como este site é financiado.'],
    'ru': ['Рекомендуемые ресурсы',
           'Некоторые ссылки являются партнёрскими. Если вы зарегистрируетесь по ним, мы можем получить комиссию без дополнительных затрат для вас. Это никогда не влияет на наш анализ.',
           'Реклама · Партнёрская ссылка', 'Раскрытие партнёрских ссылок',
           'Мы за полную прозрачность в вопросах финансирования сайта.'],
    'hi': ['अनुशंसित संसाधन',
           'ऊपर दिए गए कुछ लिंक एफिलिएट लिंक हैं। इनके माध्यम से साइन अप करने पर हमें कमीशन मिल सकता है, जिसका आप पर कोई अतिरिक्त खर्च नहीं होगा। यह हमारे विश्लेषण को कभी प्रभावित नहीं करता।',
           'विज्ञापन · एफिलिएट', 'एफिलिएट खुलासा',
           'हम इस साइट के वित्तपोषण को लेकर पूर्ण पारदर्शिता में विश्वास करते हैं।'],
    'id': ['Sumber daya yang direkomendasikan',
           'Beberapa tautan adalah tautan afiliasi. Jika Anda mendaftar melaluinya, kami dapat menerima komisi tanpa biaya tambahan bagi Anda. Hal ini tidak pernah memengaruhi analisis kami.',
           'Iklan · Afiliasi', 'Pengungkapan Afiliasi',
           'Kami percaya pada transparansi penuh tentang bagaimana situs ini didanai.'],
    'ar': ['موارد موصى بها',
           'بعض الروابط أعلاه روابط تابعة (أفلييت). إذا سجلت عبرها قد نحصل على عمولة دون أي تكلفة إضافية عليك. هذا لا يؤثر أبداً على تحليلنا أو ترتيبنا.',
           'إعلان · رابط تابع', 'إفصاح الروابط التابعة',
           'نؤمن بالشفافية الكاملة حول كيفية تمويل هذا الموقع.'],
    'bn': ['প্রস্তাবিত সংস্থান',
           'উপরের কিছু লিংক অ্যাফিলিয়েট লিংক। এগুলোর মাধ্যমে সাইন আপ করলে আমরা কমিশন পেতে পারি, আপনার কোনো অতিরিক্ত খরচ হবে না। এটি আমাদের বিশ্লেষণে কখনো প্রভাব ফেলে না।',
           'বিজ্ঞাপন · অ্যাফিলিয়েট', 'অ্যাফিলিয়েট প্রকাশ',
           'এই সাইটের অর্থায়ন সম্পর্কে আমরা সম্পূর্ণ স্বচ্ছতায় বিশ্বাস করি।'],
}
DEFAULT_AFF = AFF_STR['en']

CATEGORY_GRADIENT = {
    'Monetary Policy': 'from-brand-700 to-brand-500',
    'Inflation': 'from-slate-700 to-slate-500',
    'China': 'from-red-800 to-red-600',
    'Bonds': 'from-emerald-800 to-emerald-600',
    'Labor Markets': 'from-violet-800 to-violet-600',
    'Global Macro': 'from-sky-800 to-sky-600',
    'US Economy': 'from-indigo-800 to-indigo-600',
    'Central Banking': 'from-cyan-800 to-cyan-600',
    'Economic Research': 'from-amber-800 to-amber-600',
}
DEFAULT_GRADIENT = 'from-brand-700 to-brand-500'

TAILWIND_CONFIG = "darkMode: 'class', theme: { extend: { colors: { brand: { 50:'#eef4ff',100:'#dbe7ff',200:'#b9d0ff',300:'#8fb0ff',400:'#5f86f5',500:'#1e4fd8',600:'#1a3fb0',700:'#142f85',800:'#122560',900:'#0e1a3d' } } } }"


def home_path(lang):
    return '/' if lang == 'en' else f'/{lang}/'

def post_url(lang, slug):
    return f'{DOMAIN}/post/{slug}.html' if lang == 'en' else f'{DOMAIN}/{lang}/post/{slug}.html'

def post_href(lang, slug):
    return f'/post/{slug}.html' if lang == 'en' else f'/{lang}/post/{slug}.html'

def strs(lang):
    return LANG_STR.get(lang, DEFAULT_STR)


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
    out, lines, para = [], text.split('\n'), []
    list_buf = None

    def flush_para():
        nonlocal para
        if para:
            out.append('<p>' + ' '.join(para) + '</p>')
            para = []

    for line in lines:
        s = line.strip()
        if not s:
            flush_para(); list_buf = None; continue
        if s.startswith('# '):          # H1 중복 방지 — 페이지 H1과 동일하므로 건너뜀
            flush_para(); list_buf = None; continue
        if s.startswith('## '):
            flush_para(); list_buf = None
            out.append('<h2 id="h-%d">%s</h2>' % (len(out), inline(s[3:]))); continue
        if s.startswith('### '):
            flush_para(); list_buf = None
            out.append('<h3>%s</h3>' % inline(s[4:])); continue
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
            flush_para(); list_buf = None
            out.append('<blockquote>' + inline(s[2:]) + '</blockquote>'); continue
        list_buf = None
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


# ---------- FAQ 추출 (FAQPage 스키마용) ----------
def extract_faq(body_md):
    m = re.search(r'^##\s+(FAQ|Frequently Asked Questions|자주 묻는 질문)\s*$', body_md, re.I | re.M)
    if not m:
        return []
    seg = body_md[m.end():]
    seg = re.split(r'^##\s+', seg, flags=re.M)[0]
    out = []
    q = None
    buf = []
    for line in seg.split('\n'):
        s = line.strip()
        if s.startswith('### '):
            if q: out.append((q, ' '.join(buf).strip()))
            q = inline(s[4:])
            buf = []
        elif s and q is not None:
            buf.append(inline(s))
    if q: out.append((q, ' '.join(buf).strip()))
    return [(q, a) for q, a in out if a]


# ---------- 헤더/푸터 ----------
def header_html(current):
    s = strs(current)
    home = home_path(current)
    return f'''<header class="border-b border-slate-200 dark:border-slate-800 sticky top-0 bg-white/80 dark:bg-slate-950/80 backdrop-blur z-10">
    <div class="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between">
      <a class="font-bold text-lg text-slate-900 dark:text-slate-100" href="{home}">{SITE_NAME}</a>
      <nav class="flex items-center gap-4 text-sm">
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="{home}">Home</a>
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


def footer_html(lang):
    return f'''<footer class="border-t border-slate-200 dark:border-slate-800 mt-16">
    <div class="max-w-5xl mx-auto px-4 py-8 text-sm text-slate-500 dark:text-slate-400 space-y-2">
      <p>&copy; 2026 {SITE_NAME}. All rights reserved.</p>
      <p class="text-xs leading-relaxed">{strs(lang)[6]}</p>
      <p class="text-xs pt-2"><a class="underline hover:text-brand-600" href="{(home_path(lang)) + AFF_CFG.get('disclosure_path', 'disclosure.html')}">{htmllib.escape(AFF_STR.get(lang, DEFAULT_AFF)[3])}</a></p>
    </div>
  </footer>'''


def ga_html():
    """GA4 추적 코드. 측정 ID가 설정되지 않았으면 빈 문자열."""
    if not GA4_ID:
        return ''
    return (
        f'  <script async src="https://www.googletagmanager.com/gtag/js?id={GA4_ID}"></script>\n'
        "  <script>\n"
        "    window.dataLayer = window.dataLayer || [];\n"
        "    function gtag(){dataLayer.push(arguments);}\n"
        "    gtag('js', new Date());\n"
        f"    gtag('config', '{GA4_ID}');\n"
        "  </script>"
    )


def conversion_tracking_js():
    """아웃바운드(제휴/출처) 클릭을 GA4 이벤트로 전송."""
    return """  <script>
  (function () {
    if (!document.addEventListener) return;
    function track(node, evt) {
      var id = node.getAttribute('data-affiliate');
      if (!id || typeof window.gtag !== 'function') return;
      try {
        window.gtag('event', evt, {
          affiliate_id: id,
          outbound_url: node.getAttribute('href'),
          transport_type: 'beacon'
        });
      } catch (e) {}
    }
    document.addEventListener('click', function (e) {
      var el = e.target && e.target.closest ? e.target.closest('[data-affiliate]') : null;
      if (el) track(el, 'affiliate_click');
    }, true);
  })();
  </script>"""


def offers_for(category):
    """카테고리에 맞는 실제 제휴 상품. 비활성/빈 링크는 제외."""
    if not AFF_ENABLED or not AFF_LIVE:
        return []
    out = []
    for o in AFF_LIVE:
        cats = o.get('categories') or ['*']
        if '*' in cats or category in cats:
            out.append(o)
    return out


def affiliate_box(lang, category):
    """포스트 하단 제휴 추천 박스 (FTC/표시광고법 준수: rel=sponsored + 고지 동일 화면)."""
    offers = offers_for(category)
    if not offers:
        return ''
    s = AFF_STR.get(lang, DEFAULT_AFF)
    items = '\n'.join(
        '    <li><a class="font-medium underline hover:text-brand-600" '
        'rel="sponsored nofollow noopener" target="_blank" '
        f'data-affiliate="{htmllib.escape(o["id"])}" href="{htmllib.escape(o["url"])}">'
        f'{htmllib.escape(o["name"])}</a>'
        f' — <span class="text-slate-600 dark:text-slate-400">{htmllib.escape(o["blurb"])}</span></li>'
        for o in offers)
    return f'''<aside class="mt-10 border border-slate-200 dark:border-slate-800 rounded-lg p-5 bg-slate-50 dark:bg-slate-900">
  <p class="text-xs uppercase tracking-wide text-slate-500 mb-2">{htmllib.escape(s[2])}</p>
  <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100 mb-3">{htmllib.escape(s[0])}</h2>
  <ul class="space-y-2 text-sm text-slate-700 dark:text-slate-300">
{items}
  </ul>
  <p class="mt-4 text-xs text-slate-500 leading-relaxed">{htmllib.escape(s[1])}</p>
</aside>'''


def build_disclosure(lang, available):
    """제휴 수익 고지 페이지. 모든 언어에 생성 (AdSense 이용자 신뢰 목적)."""
    s = AFF_STR.get(lang, DEFAULT_AFF)
    f = LANG_STR.get(lang, DEFAULT_STR)
    if AFF_LIVE and AFF_ENABLED:
        items = '\n'.join(
            f'    <li><a rel="sponsored nofollow noopener" target="_blank" '
            f'data-affiliate="{htmllib.escape(o["id"])}" href="{htmllib.escape(o["url"])}">'
            f'{htmllib.escape(o["name"])}</a> — {htmllib.escape(o["blurb"])}</li>'
            for o in AFF_LIVE)
        body_list = f'  <ul class="list-disc pl-5 text-sm text-slate-700 dark:text-slate-300 space-y-1">\n{items}\n  </ul>'
    else:
        body_list = ('  <p class="text-sm text-slate-600 dark:text-slate-400">'
                     + htmllib.escape(s[1]) + '</p>')
    content = f'''<article class="max-w-3xl mx-auto">
  <h1 class="text-2xl font-bold text-slate-900 dark:text-slate-100 mb-4">{htmllib.escape(s[3])}</h1>
  <p class="text-slate-600 dark:text-slate-400 mb-6">{htmllib.escape(s[4])}</p>
  <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2">{htmllib.escape(s[3])}</h2>
  <p class="text-sm text-slate-700 dark:text-slate-300 mb-4">{htmllib.escape(s[1])}</p>
{body_list}
  <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mt-8 mb-2">Advertising</h2>
  <p class="text-sm text-slate-700 dark:text-slate-300">This site displays advertising, including Google AdSense. Third-party vendors, including Google, use cookies to serve ads based on a user's prior visits to this or other websites.</p>
  <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mt-8 mb-2">Editorial independence</h2>
  <p class="text-sm text-slate-700 dark:text-slate-300">Commercial relationships never determine which topics we cover or the conclusions of our analysis. All posts are based on publicly available economic data, with original sources credited.</p>
  <p class="mt-6 text-xs text-slate-500 leading-relaxed">{htmllib.escape(f[6])}</p>
</article>'''
    canonical = DOMAIN + (('/' if lang == 'en' else f'/{lang}/')) + AFF_CFG.get('disclosure_path', 'disclosure.html')
    html_doc = layout(lang, s[3] + ' — ' + SITE_NAME, s[4], canonical, content)
    out_path = os.path.join(BASE, AFF_CFG.get('disclosure_path', 'disclosure.html')) if lang == 'en' \
        else os.path.join(BASE, lang, AFF_CFG.get('disclosure_path', 'disclosure.html'))
    os.makedirs(os.path.dirname(out_path) or BASE, exist_ok=True)
    open(out_path, 'w', encoding='utf-8').write(html_doc)
    return out_path


def lang_switcher(current, slug=None, available=None):
    """포스트 페이지에서는 해당 글의 번역 URL로, 없으면 홈으로 링크."""
    btns = []
    for code, (name, _) in LANG_META.items():
        if slug and available and code in available:
            href = post_href(code, slug)
        else:
            href = home_path(code)
        active = 'bg-brand-600 text-white' if code == current else 'text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800'
        btns.append(f'<a href="{href}" class="px-2 py-0.5 rounded text-sm {active}" title="{name}" hreflang="{code}">{code.upper()}</a>')
    return ('<div class="max-w-5xl mx-auto px-4 pt-3 flex flex-wrap items-center gap-1">'
            '<span class="text-slate-500 mr-1 text-sm">Language:</span>' + ''.join(btns) + '</div>')


def alternates_html(slug, available):
    """검색엔진용 hreflang 상호 링크 (존재하는 언어만)."""
    tags = []
    if slug:
        for code in LANG_META:
            if code in available:
                tags.append(f'  <link rel="alternate" hreflang="{code}" href="{post_url(code, slug)}" />')
        tags.append(f'  <link rel="alternate" hreflang="x-default" href="{post_url("en", slug)}" />')
    else:
        for code in LANG_META:
            if code in available:
                tags.append(f'  <link rel="alternate" hreflang="{code}" href="{DOMAIN + home_path(code)}" />')
        tags.append(f'  <link rel="alternate" hreflang="x-default" href="{DOMAIN}/" />')
    return '\n'.join(tags)


def layout(lang, title, description, canonical, content_html, og_type='website',
           jsonld_blocks=None, slug=None, available=None, switcher_slug=None):
    dir_ = LANG_META[lang][1]
    head_extra = alternates_html(slug, available or {lang})
    ld = '\n'.join('  <script type="application/ld+json">' + json.dumps(b, ensure_ascii=False) + '</script>'
                   for b in (jsonld_blocks or []))
    return f'''<!DOCTYPE html>
<html lang="{lang}" dir="{dir_}" class="scroll-smooth">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{htmllib.escape(title)}</title>
  <meta name="description" content="{htmllib.escape(description)}" />
  <link rel="canonical" href="{canonical}" />
{head_extra}
  <link rel="alternate" type="application/rss+xml" href="{DOMAIN}/feed.xml" />
  <meta property="og:title" content="{htmllib.escape(title)}" />
  <meta property="og:description" content="{htmllib.escape(description)}" />
  <meta property="og:type" content="{og_type}" />
  <meta property="og:url" content="{canonical}" />
  <meta property="og:site_name" content="{SITE_NAME}" />
  <meta name="twitter:card" content="summary_large_image" />
  <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9243770518153989" crossorigin="anonymous"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>tailwind.config = {{ {TAILWIND_CONFIG} }};</script>
  <link rel="stylesheet" href="/assets/css/custom.css" />
{ld}
{ga_html()}
</head>
<body class="bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100 min-h-screen flex flex-col antialiased">

{header_html(lang)}
{lang_switcher(lang, switcher_slug, available)}

<main class="flex-1 max-w-5xl w-full mx-auto px-4 py-8">
{content_html}
</main>

{footer_html(lang)}
<script src="/assets/js/main.js"></script>
{conversion_tracking_js()}
</body>
</html>
'''


# ---------- JSON-LD ----------
def news_article_ld(lang, slug, title, desc, date, body_text, source_name, source_url):
    art = {
        '@context': 'https://schema.org',
        '@type': 'NewsArticle',
        'headline': title,
        'description': desc,
        'inLanguage': lang,
        'datePublished': date,
        'dateModified': date,
        'mainEntityOfPage': {'@type': 'WebPage', '@id': post_url(lang, slug)},
        'author': {'@type': 'Organization', 'name': SITE_NAME, 'url': DOMAIN + '/'},
        'publisher': {'@type': 'Organization', 'name': SITE_NAME, 'url': DOMAIN + '/'},
        'articleSection': 'Macroeconomics',
        'wordCount': len(body_text.split()),
    }
    if source_url:
        art['isBasedOn'] = {'@type': 'CreativeWork', 'name': source_name or 'Source', 'url': source_url}
    return art


def breadcrumb_ld(lang, slug, title):
    return {
        '@context': 'https://schema.org',
        '@type': 'BreadcrumbList',
        'itemListElement': [
            {'@type': 'ListItem', 'position': 1, 'name': 'Home', 'item': DOMAIN + home_path(lang)},
            {'@type': 'ListItem', 'position': 2, 'name': title, 'item': post_url(lang, slug)},
        ],
    }


def faq_ld(pairs, lang, slug):
    return {
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        'inLanguage': lang,
        'mainEntity': [{'@type': 'Question', 'name': q, 'acceptedAnswer': {'@type': 'Answer', 'text': a}}
                       for q, a in pairs],
    }


def website_ld(lang):
    return {
        '@context': 'https://schema.org',
        '@type': 'WebSite',
        'name': SITE_NAME,
        'url': DOMAIN + home_path(lang),
        'inLanguage': lang,
        'potentialAction': {
            '@type': 'SearchAction',
            'target': DOMAIN + home_path(lang) + '?q={search_term_string}',
            'query-input': 'required name=search_term_string',
        },
    }


# ---------- 카드 ----------
def card_html(lang, slug, title, desc, category, date):
    grad = CATEGORY_GRADIENT.get(category, DEFAULT_GRADIENT)
    return f'''<article class="border border-slate-200 dark:border-slate-800 rounded-lg overflow-hidden hover:shadow-md transition-shadow bg-white dark:bg-slate-900 flex flex-col">
  <a class="block aspect-[16/9] overflow-hidden bg-gradient-to-br {grad} flex items-center justify-center" href="{post_href(lang, slug)}" aria-label="{htmllib.escape(title)}">
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


def related_html(lang, slug, posts_in_lang, label):
    others = [p for p in posts_in_lang if p['slug'] != slug][:3]
    if not others:
        return ''
    items = '\n'.join(
        f'    <li><a class="hover:text-brand-600 underline-offset-2 hover:underline" href="{post_href(lang, p["slug"])}">{htmllib.escape(p["title"])}</a></li>'
        for p in others)
    return f'''<nav class="mt-12 border-t border-slate-200 dark:border-slate-800 pt-6">
  <h2 class="text-sm font-semibold uppercase tracking-wide text-slate-500 mb-3">{htmllib.escape(label)}</h2>
  <ul class="space-y-2 text-slate-700 dark:text-slate-300">
{items}
  </ul>
</nav>'''


def build_post(lang, slug, title, desc, category, date, body_md, source_name, source_url,
               posts_in_lang, available):
    body_html = md_to_html(body_md)
    canonical = post_url(lang, slug)
    s = strs(lang)
    src = ''
    if source_url:
        src = (f'\n    <p class="mt-4 text-xs text-slate-500">{htmllib.escape(s[4])}: '
               f'<a class="underline hover:text-brand-600" rel="nofollow noopener" target="_blank" '
               f'href="{htmllib.escape(source_url)}">{htmllib.escape(source_name or source_url)}</a></p>')
    content = f'''<article class="max-w-3xl mx-auto">
  <header class="mb-8">
    <div class="flex items-center gap-2 text-xs text-slate-500 mb-3">
      <span class="bg-brand-50 text-brand-700 px-2 py-0.5 rounded uppercase tracking-wide">{htmllib.escape(category)}</span>
      <time datetime="{date}">{htmllib.escape(s[3])}: {date}</time>
      <span class="text-slate-400">&middot;</span>
      <span>Independent Analysis</span>
    </div>
    <h1 class="text-3xl font-bold leading-tight text-slate-900 dark:text-slate-100 mb-4">{htmllib.escape(title)}</h1>
    <p class="text-slate-600 dark:text-slate-400">{htmllib.escape(desc)}</p>{src}
  </header>
  <div class="prose prose-slate dark:prose-invert max-w-none">{body_html}</div>
  {affiliate_box(lang, category)}
  {related_html(lang, slug, posts_in_lang, s[5])}
</article>'''

    blocks = [news_article_ld(lang, slug, title, desc, date, body_md, source_name, source_url),
              breadcrumb_ld(lang, slug, title)]
    faq = extract_faq(body_md)
    if faq:
        blocks.append(faq_ld(faq, lang, slug))

    html_doc = layout(lang, title + ' — ' + SITE_NAME, desc, canonical, content, 'article',
                      blocks, slug=slug, available=available, switcher_slug=slug)
    out_path = os.path.join(BASE, 'post', slug + '.html') if lang == 'en' else os.path.join(BASE, lang, 'post', slug + '.html')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, 'w', encoding='utf-8').write(html_doc)
    return out_path


def build_index(lang, posts, available):
    s = strs(lang)
    cards = '\n'.join(card_html(lang, p['slug'], p['title'], p['desc'], p['category'], p['date']) for p in posts)
    content = f'''<div class="space-y-6">
  <div>
    <h1 class="text-2xl font-bold text-slate-900 dark:text-slate-100">{htmllib.escape(s[2])}</h1>
    <p class="mt-1 text-sm text-slate-600 dark:text-slate-400">{htmllib.escape(s[1])}</p>
  </div>
  <div class="grid gap-4 sm:grid-cols-2">
{cards}
  </div>
</div>'''
    canonical = DOMAIN + home_path(lang)
    title = f'{SITE_NAME} — {s[0]}'
    html_doc = layout(lang, title, s[1], canonical, content, 'website', [website_ld(lang)],
                      slug=None, available=available, switcher_slug=None)
    out_path = os.path.join(BASE, 'index.html') if lang == 'en' else os.path.join(BASE, lang, 'index.html')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, 'w', encoding='utf-8').write(html_doc)
    return out_path


def build_sitemap(posts, avail_by_slug, langs_with_home):
    """실제 존재하는 언어 조합만 sitemap에 넣는다 (404 유도 URL 제거)."""
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    latest = max(p['date'] for p in posts)

    def emit(loc, alternates):
        xml.append('  <url>')
        xml.append(f'    <loc>{loc}</loc>')
        xml.append(f'    <lastmod>{latest}</lastmod>')
        for code, href in alternates:
            xml.append(f'    <xhtml:link rel="alternate" hreflang="{code}" href="{href}" />')
        xml.append('  </url>')

    # 정적 페이지
    for p in ('about.html', 'privacy.html', 'contact.html'):
        emit(DOMAIN + '/' + p, [('en', DOMAIN + '/' + p)])

    # 제휴 고지 페이지 (홈이 존재하는 모든 언어)
    dp = AFF_CFG.get('disclosure_path', 'disclosure.html')
    langs = [c for c in LANG_META if c in langs_with_home or c == 'en']
    dp_alts = [(c, DOMAIN + home_path(c) + dp) for c in langs]
    for c in langs:
        emit(DOMAIN + home_path(c) + dp, dp_alts)

    # 홈 (존재하는 언어만)
    home_alts = [(c, DOMAIN + home_path(c)) for c in LANG_META if c in langs_with_home]
    home_alts.append(('x-default', DOMAIN + '/'))
    for c in LANG_META:
        if c in langs_with_home:
            emit(DOMAIN + home_path(c), home_alts)

    for p in posts:
        avail = avail_by_slug.get(p['slug'], {'en'})
        alts = [(c, post_url(c, p['slug'])) for c in LANG_META if c in avail]
        if 'en' in avail:
            alts.append(('x-default', post_url('en', p['slug'])))
        for c in LANG_META:
            if c in avail:
                emit(post_url(c, p['slug']), alts)

    xml.append('</urlset>')
    open(os.path.join(BASE, 'sitemap.xml'), 'w', encoding='utf-8').write('\n'.join(xml) + '\n')


def build_feed(posts):
    items = []
    for p in posts:
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
                      'category': fm.get('category', 'Global Macro'), 'date': fm['date'],
                      'sourceName': fm.get('sourceName', ''), 'sourceUrl': fm.get('sourceUrl', ''),
                      'body': body})
    posts.sort(key=lambda p: p['date'], reverse=True)

    # 번역 로드: {slug: {lang: {...}}}
    trans = {}
    for lang in LANG_META:
        tdir = os.path.join(TRANS_DIR, lang)
        if not os.path.isdir(tdir):
            continue
        for tj in sorted(glob.glob(os.path.join(tdir, '*.json'))):
            slug = os.path.splitext(os.path.basename(tj))[0]
            try:
                t = json.load(open(tj, encoding='utf-8'))
            except json.JSONDecodeError:
                print(f'  ! 번역 JSON 손상, 건너뜀: {tj}')
                continue
            if not t.get('body'):
                continue
            trans.setdefault(slug, {})[lang] = t

    # 언어별로 실제 존재하는 포스트 목록
    avail_by_slug = {}
    for p in posts:
        langs = set(trans.get(p['slug'], {}).keys()) | {'en'}
        avail_by_slug[p['slug']] = langs

    def posts_for(lang):
        out = []
        for p in posts:
            if lang == 'en':
                out.append({'slug': p['slug'], 'title': p['title'], 'desc': p['desc'],
                            'category': p['category'], 'date': p['date']})
            elif lang in avail_by_slug.get(p['slug'], set()):
                t = trans[p['slug']][lang]
                out.append({'slug': p['slug'], 'title': t['title'], 'desc': t['description'],
                            'category': p['category'], 'date': p['date']})
        return out

    # 홈이 존재하는 언어 집합
    langs_with_home = {c for c in LANG_META if any(c in avail_by_slug.get(p['slug'], {'en'}) for p in posts)}
    home_available = langs_with_home | {'en'}

    total = 0
    for lang in LANG_META:
        plist = posts_for(lang)
        if not plist:
            print(f'[{lang}] 건너뜀 — 번역 없음')
            continue
        print(f'[{lang}] {len(plist)} posts')
        for p in plist:
            if lang == 'en':
                src = next(x for x in posts if x['slug'] == p['slug'])
                build_post(lang, p['slug'], p['title'], p['desc'], p['category'], p['date'],
                           src['body'], src['sourceName'], src['sourceUrl'], plist,
                           avail_by_slug[p['slug']])
            else:
                t = trans[p['slug']][lang]
                src = next(x for x in posts if x['slug'] == p['slug'])
                build_post(lang, p['slug'], t['title'], t['description'], p['category'], p['date'],
                           t['body'], src['sourceName'], src['sourceUrl'], plist,
                           avail_by_slug[p['slug']])
            total += 1
        build_index(lang, plist, home_available)
        build_disclosure(lang, home_available)

    build_sitemap(posts, avail_by_slug, langs_with_home)
    build_feed(posts)
    dp = AFF_CFG.get('disclosure_path', 'disclosure.html')
    print(f'빌드 완료: 포스트 페이지 {total}개 + 홈 {len(LANG_META)}개 + {dp} {len(LANG_META)}개, sitemap.xml, feed.xml OK')


if __name__ == '__main__':
    main()
