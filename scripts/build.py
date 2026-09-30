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
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _load_config():
    """공개 설정(config.public.json)을 기본으로 하고, 시크릿 설정(config.json)이
    비어 있지 않은 값일 때만 덮어쓴다.

    GA4 측정 ID / 서치콘솔 인증 값은 페이지에 그대로 노출되는 공개값이므로
    config.public.json(커밋 대상)에 둔다. API 키는 여전히 config.json에만 둔다.
    """
    cfg = {}
    pub = os.path.join(BASE, 'config.public.json')
    if os.path.exists(pub):
        try:
            cfg = json.load(open(pub, encoding='utf-8'))
        except Exception:
            cfg = {}

    def _is_empty(v):
        return v is None or v == '' or v == [] or v == {}

    sec = os.path.join(BASE, 'config.json')
    if os.path.exists(sec):
        try:
            s = json.load(open(sec, encoding='utf-8'))
        except Exception:
            s = {}
        for k, v in (s or {}).items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                merged = dict(cfg[k])
                for kk, vv in v.items():
                    if not _is_empty(vv):
                        merged[kk] = vv
                cfg[k] = merged
            elif not _is_empty(v):
                cfg[k] = v
    return cfg


CONFIG = _load_config()
DOMAIN = 'https://www.mycapitalanalyze.com'
SITE_NAME = 'MyCapital Analyze'

POSTS_DIR = os.path.join(BASE, 'content', 'posts')
TRANS_DIR = os.path.join(BASE, 'content', 'translations')
# 앱·게임 섹션(/game/). 경제 메인과 섞이지 않도록 별도 디렉터리·별도 URL을 쓴다.
# content/game/{lang}/*.md 는 "해당 언어 스토어에서 수집한 네이티브 글"이라 번역 단계를 거치지 않는다.
GAME_DIR = os.path.join(BASE, 'content', 'game')
GAME_CATEGORY = 'Apps & Games'

# 앱/게임 구분 — app_radar 가 찍은 kind 우선, 없으면 장르 문자열로 판정(백필 호환).
GAME_KIND_PAT = re.compile(
    r'(^|\s|/|，|、)(games?|spiele|juegos|jeux|jogos|игры)'
    r'|(^|\s)(游戏|ゲーム|게임)'
    r'|GAME_[A-Z]+', re.I)


def game_kind(fm):
    k = (fm.get('kind') or '').strip()
    if k in ('game', 'app'):
        return k
    return 'game' if GAME_KIND_PAT.search(fm.get('genre', '') or '') else 'app'


# 레거시 앱 글 3개(기존 /post/ URL 유지)의 앱/게임 구분 — 장르 정보가 없어 고정 매핑.
LEGACY_KIND = {
    'tideward-upcoming-idle-rpg-release-date-and-features': 'game',
    'mendazzle-release-date-features-and-price-of-the': 'game',
    'followers-tracker-for-insta-review-features-price-release': 'app',
}

# 상단 메뉴 + 섹션 탭 문구 (전체 / 앱 / 게임) — 13개 언어.
TAB_STR = {
    'en': ('All', 'Apps', 'Games'), 'ko': ('전체', '앱', '게임'),
    'zh': ('全部', '应用', '游戏'), 'ja': ('すべて', 'アプリ', 'ゲーム'),
    'es': ('Todo', 'Apps', 'Juegos'), 'de': ('Alle', 'Apps', 'Spiele'),
    'fr': ('Tout', 'Apps', 'Jeux'), 'pt': ('Tudo', 'Apps', 'Jogos'),
    'ru': ('Все', 'Приложения', 'Игры'), 'id': ('Semua', 'Aplikasi', 'Game'),
    'hi': ('सभी', 'ऐप्स', 'गेम्स'), 'ar': ('الكل', 'تطبيقات', 'ألعاب'),
    'bn': ('সব', 'অ্যাপ', 'গেম'),
}
DEFAULT_TAB = TAB_STR['en']

# 홈 화면 위젯 문구 (달력 / 국가별 방문자).
WIDGET_STR = {
    'en': ('Daily posts', 'No posts were published on this day.',
           'Visitors by country — last 7 days', 'Collecting…', 'posts'),
    'ko': ('일별 발행', '이 날에 올라온 글이 없습니다.',
           '국가별 방문자 — 최근 7일', '집계 중…', '편'),
    'zh': ('每日发布', '这一天没有发布文章。',
           '各国访客 — 最近 7 天', '统计中…', '篇'),
    'ja': ('日別の発行', 'この日に公開された記事はありません。',
           '国別訪問者 — 直近7日', '集計中…', '件'),
    'es': ('Publicaciones diarias', 'No se publicaron artículos ese día.',
           'Visitantes por país — últimos 7 días', 'Recopilando…', 'artículos'),
    'de': ('Tägliche Beiträge', 'An diesem Tag wurden keine Beiträge veröffentlicht.',
           'Besucher nach Land — letzte 7 Tage', 'Wird erfasst…', 'Beiträge'),
    'fr': ('Publications quotidiennes', 'Aucun article publié ce jour-là.',
           'Visiteurs par pays — 7 derniers jours', 'Collecte…', 'articles'),
    'pt': ('Publicações diárias', 'Nenhum artigo publicado neste dia.',
           'Visitantes por país — últimos 7 dias', 'Coletando…', 'artigos'),
    'ru': ('Публикации по дням', 'В этот день статей не публиковалось.',
           'Посетители по странам — за 7 дней', 'Сбор данных…', 'статей'),
    'id': ('Kiriman harian', 'Tidak ada artikel yang terbit pada hari ini.',
           'Pengunjung per negara — 7 hari terakhir', 'Mengumpulkan…', 'artikel'),
    'hi': ('दैनिक पोस्ट', 'इस दिन कोई लेख प्रकाशित नहीं हुआ।',
           'देश अनुसार आगंतुक — अंतिम 7 दिन', 'एकत्र किया जा रहा है…', 'लेख'),
    'ar': ('منشورات يومية', 'لم تُنشر مقالات في هذا اليوم.',
           'الزوار حسب الدولة — آخر 7 أيام', 'جارٍ التجميع…', 'مقالات'),
    'bn': ('দৈনিক পোস্ট', 'এই দিনে কোনো নিবন্ধ প্রকাশিত হয়নি।',
           'দেশ অনুযায়ী দর্শক — শেষ ৭ দিন', 'সংগ্রহ চলছে…', 'টি'),
}
DEFAULT_WIDGET = WIDGET_STR['en']

# 검색/인기글/공유 위젯 문구.
# (검색 제목, 검색 placeholder, 결과 없음, 결과 단위, 인기글 제목, 조회 단위, 링크 복사, 복사됨)
UX_STR = {
    'en': ('Search', 'Search posts…', 'No results found.', 'results',
           'Popular this week', 'views', 'Copy link', 'Copied!'),
    'ko': ('검색', '글 검색…', '결과가 없습니다.', '개 결과',
           '이번 주 인기 글', '회', '링크 복사', '복사됐습니다!'),
    'zh': ('搜索', '搜索文章…', '没有找到结果。', '条结果',
           '本周热门文章', '次浏览', '复制链接', '已复制！'),
    'ja': ('検索', '記事を検索…', '結果が見つかりません。', '件',
           '今週の人気記事', '回', 'リンクをコピー', 'コピーしました！'),
    'es': ('Buscar', 'Buscar artículos…', 'No se encontraron resultados.', 'resultados',
           'Populares de esta semana', 'vistas', 'Copiar enlace', '¡Copiado!'),
    'de': ('Suche', 'Beiträge suchen…', 'Keine Ergebnisse gefunden.', 'Ergebnisse',
           'Diese Woche beliebt', 'Aufrufe', 'Link kopieren', 'Kopiert!'),
    'fr': ('Recherche', 'Rechercher des articles…', 'Aucun résultat trouvé.', 'résultats',
           'Populaires cette semaine', 'vues', 'Copier le lien', 'Copié !'),
    'pt': ('Busca', 'Buscar artigos…', 'Nenhum resultado encontrado.', 'resultados',
           'Populares desta semana', 'visualizações', 'Copiar link', 'Copiado!'),
    'ru': ('Поиск', 'Поиск статей…', 'Ничего не найдено.', 'результатов',
           'Популярное за неделю', 'просмотров', 'Копировать ссылку', 'Скопировано!'),
    'id': ('Pencarian', 'Cari artikel…', 'Tidak ada hasil.', 'hasil',
           'Populer minggu ini', 'kali dilihat', 'Salin tautan', 'Tersalin!'),
    'hi': ('खोज', 'लेख खोजें…', 'कोई परिणाम नहीं मिला।', 'परिणाम',
           'इस सप्ताह लोकप्रिय', 'बार देखा गया', 'लिंक कॉपी करें', 'कॉपी हो गया!'),
    'ar': ('بحث', 'ابحث في المقالات…', 'لا توجد نتائج.', 'نتائج',
           'الأكثر رواجًا هذا الأسبوع', 'مشاهدات', 'نسخ الرابط', 'تم النسخ!'),
    'bn': ('অনুসন্ধান', 'নিবন্ধ খুঁজুন…', 'কোনো ফলাফল পাওয়া যায়নি।', 'টি ফলাফল',
           'এই সপ্তাহে জনপ্রিয়', 'বার দেখা', 'লিংক কপি করুন', 'কপি হয়েছে!'),
}
DEFAULT_UX = UX_STR['en']

GA4_ID = ((CONFIG.get('analytics') or {}).get('ga4_id') or '').strip()
# Google Search Console HTML 태그 방식 인증 값 (없으면 메타 태그 미삽입)
GSC_VERIFY = ((CONFIG.get('analytics') or {}).get('google_site_verification') or '').strip()
# 언어권별 대표 포털(Naver/Baidu/Yandex/Bing) 소유권 확인 값. 비어 있으면 태그 미삽입.
# 값은 각 포털 웹마스터 도구에서 발급받아 config.public.json > analytics 에 넣는다.
PORTAL_VERIFY = {
    'naver-site-verification': ((CONFIG.get('analytics') or {}).get('naver_site_verification') or '').strip(),
    'baidu-site-verification': ((CONFIG.get('analytics') or {}).get('baidu_site_verification') or '').strip(),
    'yandex-verification':     ((CONFIG.get('analytics') or {}).get('yandex_verification') or '').strip(),
    'msvalidate.01':           ((CONFIG.get('analytics') or {}).get('bing_site_verification') or '').strip(),
}
ADS_CFG = CONFIG.get('adsense') or {}
# 사이트가 애드센스에 승인되기 전에는 광고 코드를 아예 넣지 않는다.
# 승인 전 광고 코드 삽입은 빈 박스만 노출시키고 계정 정책 리스크를 만든다.
ADS_ENABLED = bool(ADS_CFG.get('enabled'))
AD_CLIENT = ((ADS_CFG.get('client') or '').strip() if ADS_ENABLED else '')
# 광고 슬롯 ID. 값이 비어 있으면 해당 광고 유닛은 렌더링하지 않는다.
AD_SLOTS = ADS_CFG.get('slots') or {}

AFF_CFG = CONFIG.get('affiliates') or {}
AFF_ALL = AFF_CFG.get('offers') or []
# url이 채워지지 않은 항목은 렌더링하지 않는다 (빈 링크 방지)
AFF_LIVE = [o for o in AFF_ALL if (o.get('url') or '').strip()]
# 실제 URL이 채워진 오퍼가 하나라도 있으면 활성으로 본다.
# config.json / CI 시크릿의 enabled:false가 공개 설정(config.public.json)에
# 들어있는 실제 제휴 링크를 지워버리는 것을 막기 위한 처리다.
# 숨기려면 offers의 url을 비우면 된다.
AFF_ENABLED = bool(AFF_LIVE)

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

# 앱·게임 섹션(/game/) 문구: [네비 라벨, 섹션 제목, 섹션 설명]
GAME_STR = {
    'en': ('Games & Apps', 'New & Upcoming Apps and Games',
           'Fresh releases and pre-registration titles, picked up from App Store and Google Play listings.'),
    'ko': ('게임·앱', '신규 · 출시 예정 앱과 게임',
           '앱스토어와 구글플레이에 막 올라온 신작과 사전예약 타이틀을 소개합니다.'),
    'zh': ('游戏·应用', '新上架与即将推出的应用和游戏',
           '来自 App Store 与 Google Play 的新作及预约上线作品介绍。'),
    'ja': ('ゲーム・アプリ', '新作・配信予定のアプリとゲーム',
           'App Store と Google Play に登場した新作・事前登録タイトルを紹介します。'),
    'es': ('Juegos y apps', 'Apps y juegos nuevos y próximos',
           'Lanzamientos recientes y títulos en prerregistro de App Store y Google Play.'),
    'de': ('Games & Apps', 'Neue und kommende Apps und Spiele',
           'Frische Releases und Vorregistrierungs-Titel aus dem App Store und bei Google Play.'),
    'fr': ('Jeux & apps', 'Nouveautés et sorties à venir',
           'Nouveaux titres et pré-inscriptions repérés sur l\'App Store et Google Play.'),
    'pt': ('Jogos e apps', 'Apps e jogos novos e em pré-registro',
           'Lançamentos recentes e títulos em pré-registro da App Store e do Google Play.'),
    'ru': ('Игры и приложения', 'Новые и ожидаемые приложения и игры',
           'Свежие релизы и предварительная регистрация в App Store и Google Play.'),
    'hi': ('गेम और ऐप', 'नए और आने वाले ऐप व गेम',
           'App Store और Google Play की नई रिलीज़ और प्री-रजिस्ट्रेशन टाइटल।'),
    'id': ('Game & aplikasi', 'Aplikasi dan game baru serta yang akan rilis',
           'Rilisan terbaru dan pra-registrasi dari App Store dan Google Play.'),
    'ar': ('ألعاب وتطبيقات', 'تطبيقات وألعاب جديدة وقريبة الإصدار',
           'إصدارات جديدة وعناوين قابلة للتسجيل المسبق من App Store وGoogle Play.'),
    'bn': ('গেম ও অ্যাপ', 'নতুন ও আসন্ন অ্যাপ এবং গেম',
           'App Store ও Google Play থেকে নতুন রিলিজ এবং প্রি-রেজিস্ট্রেশন শিরোনাম।'),
}
DEFAULT_GAME = GAME_STR['en']

# 앱·게임 글 전용 UI 문자열:
# [박스제목, 출시일, 가격, 플랫폼, 개발사, 상태, 확인일, 출시예정값, 출시됨값, 목차, 스토어CTA, CTA부가설명]
FACT_STR = {
    'en': ('At a glance', 'Release date', 'Price', 'Platform', 'Developer', 'Status',
           'Store listing checked', 'Upcoming (pre-order)', 'Released', 'On this page',
           'View on the store', 'Final price and availability are shown on the store listing.',
           'Read the original article'),
    'ko': ('한눈에 보기', '출시일', '가격', '플랫폼', '개발사', '상태', '스토어 정보 확인일',
           '출시 예정(사전예약)', '출시됨', '이 글의 목차', '스토어에서 자세히 보기',
           '최종 가격과 출시 여부는 스토어 페이지가 기준입니다.',
           '원문 기사 보기'),
    'ja': ('ひと目でわかる', '配信日', '価格', 'プラットフォーム', '開発元', 'ステータス',
           'ストア情報の確認日', '配信予定（事前登録）', '配信中', 'この記事の目次',
           'ストアで詳しく見る', '最終的な価格と配信状況はストアの記載が最新です。',
           '原文記事を読む'),
    'zh': ('速览', '上线时间', '价格', '平台', '开发商', '状态', '商店信息核对日期',
           '即将上线（可预约）', '已上线', '本文目录', '前往商店查看',
           '最终价格与上架状态以商店页面为准。',
           '阅读原文报道'),
    'es': ('De un vistazo', 'Fecha de lanzamiento', 'Precio', 'Plataforma', 'Desarrollador',
           'Estado', 'Información de la tienda verificada', 'Próximo (prerregistro)',
           'Ya disponible', 'En esta página', 'Ver en la tienda',
           'El precio y la disponibilidad definitivos se muestran en la tienda.',
           'Leer el artículo original'),
    'de': ('Auf einen Blick', 'Release-Datum', 'Preis', 'Plattform', 'Entwickler', 'Status',
           'Store-Informationen geprüft am', 'Demnächst (Vorbestellung)', 'Veröffentlicht',
           'Auf dieser Seite', 'Im Store ansehen',
           'Endgültiger Preis und Verfügbarkeit stehen im Store-Eintrag.',
           'Originalartikel lesen'),
    'fr': ('En un coup d’œil', 'Date de sortie', 'Prix', 'Plateforme', 'Développeur', 'Statut',
           'Infos boutique vérifiées le', 'Prochainement (précommande)', 'Disponible',
           'Sur cette page', 'Voir sur le store',
           'Le prix et la disponibilité définitifs sont indiqués sur la boutique.',
           "Lire l'article original"),
    'pt': ('Em resumo', 'Data de lançamento', 'Preço', 'Plataforma', 'Desenvolvedor', 'Status',
           'Informação da loja verificada em', 'Em breve (pré-registro)', 'Lançado',
           'Nesta página', 'Ver na loja', 'O preço e a disponibilidade finais constam na loja.',
           'Ler o artigo original'),
    'ru': ('Кратко', 'Дата выхода', 'Цена', 'Платформа', 'Разработчик', 'Статус',
           'Данные магазина проверены', 'Скоро (предзаказ)', 'Выпущено',
           'На этой странице', 'Смотреть в магазине',
           'Итоговая цена и доступность указаны в магазине.',
           'Читать оригинальную статью'),
    'hi': ('संक्षेप में', 'रिलीज़ तिथि', 'कीमत', 'प्लेटफ़ॉर्म', 'डेवलपर', 'स्थिति',
           'स्टोर जानकारी जाँची गई', 'जल्द ही (प्री-ऑर्डर)', 'जारी', 'इस पृष्ठ पर',
           'स्टोर पर देखें', 'अंतिम कीमत और उपलब्धता स्टोर पृष्ठ पर ही दिखाई गई है।',
           'मूल लेख पढ़ें'),
    'id': ('Sekilas', 'Tanggal rilis', 'Harga', 'Platform', 'Pengembang', 'Status',
           'Info toko diperiksa', 'Segera (pre-order)', 'Dirilis', 'Di halaman ini',
           'Lihat di toko', 'Harga dan ketersediaan final ada di halaman toko.',
           'Baca artikel asli'),
    'ar': ('نظرة سريعة', 'تاريخ الإصدار', 'السعر', 'المنصة', 'المطور', 'الحالة',
           'آخر تحقق من بيانات المتجر', 'قريبًا (طلب مسبق)', 'تم الإصدار', 'في هذه الصفحة',
           'عرض في المتجر', 'السعر والتوفر النهائيان معروضان في صفحة المتجر.',
           'اقرأ المقال الأصلي'),
    'bn': ('এক নজরে', 'প্রকাশের তারিখ', 'মূল্য', 'প্ল্যাটফর্ম', 'ডেভেলপার', 'অবস্থা',
           'স্টোর তথ্য যাচাই', 'শীঘ্রই (প্রি-অর্ডার)', 'প্রকাশিত', 'এই পাতায়',
           'স্টোরে দেখুন', 'চূড়ান্ত মূল্য ও প্রাপ্যতা স্টোর পাতায় দেখানো হয়।',
           'মূল নিবন্ধটি পড়ুন'),
}
DEFAULT_FACT = FACT_STR['en']

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
    'Apps & Games': 'from-fuchsia-800 to-fuchsia-600',
}
DEFAULT_GRADIENT = 'from-brand-700 to-brand-500'

TAILWIND_CONFIG = "darkMode: 'class', theme: { extend: { colors: { brand: { 50:'#eef4ff',100:'#dbe7ff',200:'#b9d0ff',300:'#8fb0ff',400:'#5f86f5',500:'#1e4fd8',600:'#1a3fb0',700:'#142f85',800:'#122560',900:'#0e1a3d' } } } }"


def home_path(lang):
    return '/' if lang == 'en' else f'/{lang}/'

def post_url(lang, slug):
    return f'{DOMAIN}/post/{slug}.html' if lang == 'en' else f'{DOMAIN}/{lang}/post/{slug}.html'

def post_href(lang, slug):
    return f'/post/{slug}.html' if lang == 'en' else f'/{lang}/post/{slug}.html'

def game_home_path(lang):
    return '/game/' if lang == 'en' else f'/{lang}/game/'

def game_post_url(lang, slug):
    return f'{DOMAIN}/game/post/{slug}.html' if lang == 'en' else f'{DOMAIN}/{lang}/game/post/{slug}.html'

def game_post_href(lang, slug):
    return f'/game/post/{slug}.html' if lang == 'en' else f'/{lang}/game/post/{slug}.html'

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
    # 이미지 먼저 치환 — 아래 링크 정규식이 이미지의 ](...) 를 잘못 물지 않게 한다
    s = re.sub(r'!\[(.+?)\]\((.+?)\)',
               r'<img src="\2" alt="\1" loading="lazy" decoding="async" />', s)
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
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="{game_home_for(current, 'app')}">{htmllib.escape(TAB_STR.get(current, DEFAULT_TAB)[1])}</a>
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="{game_home_for(current, 'game')}">{htmllib.escape(TAB_STR.get(current, DEFAULT_TAB)[2])}</a>
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="/about.html">About</a>
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300 hidden sm:block" href="/privacy.html">Privacy</a>
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300 hidden sm:block" href="/contact.html">Contact</a>
        <a class="p-1.5 rounded text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-brand-600" href="{search_path(current)}" aria-label="{htmllib.escape(UX_STR.get(current, DEFAULT_UX)[0])}">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path stroke-linecap="round" d="M21 21l-4.35-4.35"/></svg>
        </a>
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


def _slot_entry(key):
    """슬롯 설정 파싱. "1234567890" 문자열 또는
    {"id": "...", "format": "auto|autorelaxed|fluid", "layout": "in-feed"} 객체 지원."""
    ent = AD_SLOTS.get(key)
    if isinstance(ent, dict):
        return ((ent.get('id') or '').strip(),
                (ent.get('format') or 'auto').strip(),
                (ent.get('layout') or '').strip())
    return ((ent or '').strip() if isinstance(ent, str) else '', 'auto', '')


def ad_unit(key, wrap_class='my-6'):
    """AdSense 디스플레이 광고 유닛. 슬롯 ID가 설정되지 않았으면 빈 문자열.

    애드센스는 각 <ins> 마다 한 번의 push가 필요하므로 유닛마다 스크립트를 붙인다.
    """
    slot, fmt, layout = _slot_entry(key)
    if not slot or not AD_CLIENT:
        return ''
    layout_attr = f' data-ad-layout="{htmllib.escape(layout)}"' if layout else ''
    return (
        f'<div class="{wrap_class}" data-ad-slot-name="{htmllib.escape(key)}">\n'
        f'  <ins class="adsbygoogle" style="display:block" '
        f'data-ad-client="{AD_CLIENT}" data-ad-slot="{htmllib.escape(slot)}" '
        f'data-ad-format="{htmllib.escape(fmt)}" data-full-width-responsive="true"{layout_attr}></ins>\n'
        '  <script>(adsbygoogle = window.adsbygoogle || []).push({});</script>\n'
        '</div>'
    )


def adsense_script_html():
    """애드센스 로더 스크립트. 승인 전(AD_CLIENT 없음)에는 삽입하지 않는다."""
    if not AD_CLIENT:
        return ''
    return ('  <script async src="https://pagead2.googlesyndication.com/pagead/js/'
            f'adsbygoogle.js?client={htmllib.escape(AD_CLIENT)}" crossorigin="anonymous"></script>')


def verify_html():
    """검색 포털 소유권 확인 메타 태그. 값이 없는 포털은 태그를 넣지 않는다."""
    tags = []
    if GSC_VERIFY:
        tags.append(f'  <meta name="google-site-verification" content="{htmllib.escape(GSC_VERIFY)}" />')
    for name, val in PORTAL_VERIFY.items():
        if val:
            tags.append(f'  <meta name="{name}" content="{htmllib.escape(val)}" />')
    return '\n'.join(tags)


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


def lang_switcher(current, slug=None, available=None, section=None):
    """포스트 페이지에서는 해당 글의 번역 URL로, 없으면 홈으로 링크."""
    is_game = (section == 'game')
    href_fn = game_post_href if is_game else post_href
    home_fn = game_home_path if is_game else home_path
    btns = []
    for code, (name, _) in LANG_META.items():
        if slug and available and code in available:
            href = href_fn(code, slug)
        else:
            href = home_fn(code)
        active = 'bg-brand-600 text-white' if code == current else 'text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800'
        btns.append(f'<a href="{href}" class="px-2 py-0.5 rounded text-sm {active}" title="{name}" hreflang="{code}">{code.upper()}</a>')
    return ('<div class="max-w-5xl mx-auto px-4 pt-3 flex flex-wrap items-center gap-1">'
            '<span class="text-slate-500 mr-1 text-sm">Language:</span>' + ''.join(btns) + '</div>')


def alternates_html(slug, available, section=None):
    """검색엔진용 hreflang 상호 링크 (존재하는 언어만)."""
    is_game = (section == 'game')
    url_fn = game_post_url if is_game else post_url
    home_fn = game_home_path if is_game else home_path
    tags = []
    if slug:
        for code in LANG_META:
            if code in available:
                tags.append(f'  <link rel="alternate" hreflang="{code}" href="{url_fn(code, slug)}" />')
        tags.append(f'  <link rel="alternate" hreflang="x-default" href="{url_fn("en", slug)}" />')
    else:
        for code in LANG_META:
            if code in available:
                tags.append(f'  <link rel="alternate" hreflang="{code}" href="{DOMAIN + home_fn(code)}" />')
        tags.append(f'  <link rel="alternate" hreflang="x-default" href="{DOMAIN + ("/game/" if is_game else "/")}" />')
    return '\n'.join(tags)


def layout(lang, title, description, canonical, content_html, og_type='website',
           jsonld_blocks=None, slug=None, available=None, switcher_slug=None, section=None,
           image=None, noindex=False):
    dir_ = LANG_META[lang][1]
    og_img = ''
    if image:
        abs_url = image if image.startswith('http') else DOMAIN + image
        og_img = (f'  <meta property="og:image" content="{htmllib.escape(abs_url)}" />\n'
                  f'  <meta name="twitter:image" content="{htmllib.escape(abs_url)}" />\n')
    head_extra = alternates_html(slug, available or {lang}, section)
    robots = ('  <meta name="robots" content="noindex, follow" />\n' if noindex else '')
    ld = '\n'.join('  <script type="application/ld+json">' + json.dumps(b, ensure_ascii=False) + '</script>'
                   for b in (jsonld_blocks or []))
    return f'''<!DOCTYPE html>
<html lang="{lang}" dir="{dir_}" class="scroll-smooth">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{htmllib.escape(title)}</title>
  <meta name="description" content="{htmllib.escape(description)}" />
{robots}  <link rel="canonical" href="{canonical}" />
{verify_html()}
{head_extra}
  <link rel="alternate" type="application/rss+xml" href="{DOMAIN}/feed.xml" />
  <meta property="og:title" content="{htmllib.escape(title)}" />
  <meta property="og:description" content="{htmllib.escape(description)}" />
  <meta property="og:type" content="{og_type}" />
  <meta property="og:url" content="{canonical}" />
  <meta property="og:site_name" content="{SITE_NAME}" />
  <meta name="twitter:card" content="summary_large_image" />
{og_img}
{adsense_script_html()}
  <script src="https://cdn.tailwindcss.com"></script>
  <script>tailwind.config = {{ {TAILWIND_CONFIG} }};</script>
  <link rel="stylesheet" href="/assets/css/custom.css" />
{ld}
{ga_html()}
</head>
<body class="bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100 min-h-screen flex flex-col antialiased">

{header_html(lang)}
{lang_switcher(lang, switcher_slug, available, section)}

<main class="flex-1 max-w-5xl w-full mx-auto px-4 py-8">
{content_html}
</main>

{footer_html(lang)}
<script src="/assets/js/main.js?v=3"></script>
{conversion_tracking_js()}
</body>
</html>
'''


# ---------- JSON-LD ----------
def software_app_ld(lang, slug, title, desc, f, source_url, url_fn=None):
    """앱·게임 글용 스키마 — 검색 결과에 가격·개발사·썸네일이 붙으면 CTR 이 올라간다."""
    fn = url_fn or post_url
    genre = (f.get('genre') or '').lower()
    obj = {
        '@context': 'https://schema.org',
        '@type': 'SoftwareApplication',
        'name': title,
        'description': desc,
        'inLanguage': lang,
        'url': fn(lang, slug),
        'applicationCategory': 'GameApplication' if 'game' in genre else 'MobileApplication',
        'operatingSystem': 'iOS' if (f.get('platform') or '') == 'App Store' else 'Android',
    }
    if f.get('image'):
        obj['image'] = DOMAIN + f['image'] if f['image'].startswith('/') else f['image']
    if f.get('developer'):
        obj['author'] = {'@type': 'Organization', 'name': f['developer']}
    # 무료로 확인된 경우만 0 으로 명시한다. 통화가 불확실한 유료 가격은 넣지 않는다(추정 금지).
    price = (f.get('price') or '').strip()
    if price and re.match(r'^(0|free|무료|gratis|gratuit|gratis|kostenlos|бесплатно|免费|無料|免费)$',
                          price, re.I):
        obj['offers'] = {'@type': 'Offer', 'price': '0', 'priceCurrency': 'USD'}
    if source_url:
        obj['sameAs'] = source_url
    return obj


def news_article_ld(lang, slug, title, desc, date, body_text, source_name, source_url,
                    url_fn=None, article_section=None, image=None):
    fn = url_fn or post_url
    art = {
        '@context': 'https://schema.org',
        '@type': 'NewsArticle',
        'headline': title,
        'description': desc,
        'inLanguage': lang,
        'datePublished': date,
        'dateModified': date,
        'mainEntityOfPage': {'@type': 'WebPage', '@id': fn(lang, slug)},
        'author': {'@type': 'Organization', 'name': SITE_NAME, 'url': DOMAIN + '/'},
        'publisher': {'@type': 'Organization', 'name': SITE_NAME, 'url': DOMAIN + '/'},
        'articleSection': article_section or 'Macroeconomics',
        'wordCount': len(body_text.split()),
    }
    if image:
        art['image'] = DOMAIN + image if image.startswith('/') else image
    if source_url:
        art['isBasedOn'] = {'@type': 'CreativeWork', 'name': source_name or 'Source', 'url': source_url}
    return art


def breadcrumb_ld(lang, slug, title, url_fn=None, section_home=None):
    fn = url_fn or post_url
    items = [{'@type': 'ListItem', 'position': 1, 'name': 'Home',
              'item': DOMAIN + home_path(lang)}]
    if section_home:
        items.append({'@type': 'ListItem', 'position': 2, 'name': section_home,
                      'item': DOMAIN + game_home_path(lang)})
        items.append({'@type': 'ListItem', 'position': 3, 'name': title,
                      'item': fn(lang, slug)})
    else:
        items.append({'@type': 'ListItem', 'position': 2, 'name': title,
                      'item': fn(lang, slug)})
    return {
        '@context': 'https://schema.org',
        '@type': 'BreadcrumbList',
        'itemListElement': items,
    }


def faq_ld(pairs, lang, slug):
    return {
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        'inLanguage': lang,
        'mainEntity': [{'@type': 'Question', 'name': q, 'acceptedAnswer': {'@type': 'Answer', 'text': a}}
                       for q, a in pairs],
    }


def website_ld(lang, url=None):
    return {
        '@context': 'https://schema.org',
        '@type': 'WebSite',
        'name': SITE_NAME,
        'url': url or (DOMAIN + home_path(lang)),
        'inLanguage': lang,
        'potentialAction': {
            '@type': 'SearchAction',
            'target': DOMAIN + home_path(lang) + '?q={search_term_string}',
            'query-input': 'required name=search_term_string',
        },
    }


# ---------- 앱·게임 전용 컴포넌트 (CTR · 체류시간 개선) ----------

def first_image(md):
    """본문 첫 이미지 경로 (app_radar 가 넣은 스토어 대표 이미지)."""
    m = re.search(r'!\[[^\]]*\]\(([^)]+)\)', md or '')
    return m.group(1).strip() if m else ''


def game_facts(fm, md=''):
    """프론트매터 → 팩트박스용 값. 없는 값은 빈 문자열(행 생략)."""
    rel = (fm.get('releaseDate') or '').strip()
    if not rel:
        m = re.search(r'(20\d\d-\d\d-\d\d)', md or '')
        rel = m.group(1) if m else ''
    upcoming = str(fm.get('upcoming', '')).strip().lower() in ('1', 'true', 'yes', 'y')
    return {
        'releaseDate': rel,
        'price': (fm.get('price') or '').strip(),
        'platform': (fm.get('sourceName') or '').strip(),
        'developer': (fm.get('developer') or '').strip(),
        'genre': (fm.get('genre') or '').strip(),
        'upcoming': upcoming,
        'news': str(fm.get('news', '')).strip().lower() in ('1', 'true', 'yes', 'y'),
        'checked': (fm.get('date') or '').strip(),
        'image': (fm.get('image') or '').strip() or first_image(md),
    }


def fact_box_html(lang, f):
    """첫 화면 팩트박스 — 출시일·가격·상태를 먼저 보여줘 이탈을 줄인다.
    뉴스 재각색 글(news=true)은 스토어 상품이 아니므로 박스를 숨긴다."""
    if f.get('news'):
        return ''
    t = FACT_STR.get(lang, DEFAULT_FACT)
    status = t[7] if f['upcoming'] else (t[8] if f['releaseDate'] else '')
    rows = [(t[1], f['releaseDate']), (t[2], f['price']), (t[5], status),
            (t[3], f['platform']), (t[4], f['developer']), (t[6], f['checked'])]
    body = '\n'.join(
        '    <div class="flex justify-between gap-4 py-1.5 border-b border-slate-100 '
        'dark:border-slate-800 last:border-0">'
        '<dt class="text-slate-500 dark:text-slate-400 shrink-0">%s</dt>'
        '<dd class="text-right font-medium text-slate-900 dark:text-slate-100">%s</dd></div>'
        % (htmllib.escape(k), htmllib.escape(v))
        for k, v in rows if v)
    if not body:
        return ''
    return ('<section class="mb-6 rounded-lg border border-fuchsia-200 dark:border-fuchsia-900 '
            'bg-fuchsia-50/50 dark:bg-fuchsia-950/20 p-4">'
            '<h2 class="text-sm font-semibold uppercase tracking-wide text-fuchsia-800 '
            'dark:text-fuchsia-300 mb-2">%s</h2>'
            '<dl class="text-sm">\n%s\n</dl></section>'
            % (htmllib.escape(t[0]), body))


def toc_html(body_html, lang):
    """본문 h2 → 목차. (모바일은 details 로 접힘, 데스크톱은 펼친 상태)"""
    t = FACT_STR.get(lang, DEFAULT_FACT)
    items = re.findall(r'<h2 id="(h-\d+)">(.*?)</h2>', body_html or '')
    if len(items) < 3:
        return ''
    lis = '\n'.join(
        '    <li><a class="block py-0.5 hover:text-fuchsia-700 dark:hover:text-fuchsia-300" '
        'href="#%s">%s</a></li>' % (i, re.sub(r'<[^>]+>', '', txt)) for i, txt in items)
    return ('<details class="mb-6 rounded-lg border border-slate-200 dark:border-slate-800 p-4" open>'
            '<summary class="text-sm font-semibold cursor-pointer text-slate-900 '
            'dark:text-slate-100">%s</summary>'
            '<ul class="mt-3 text-sm text-slate-600 dark:text-slate-400 space-y-1">\n%s\n</ul>'
            '</details>' % (htmllib.escape(t[9]), lis))


def store_cta_html(lang, url, store_name, news=False):
    """앱·게임 글 하단 카드 — 스토어 링크(기본) 또는 뉴스 원문 링크."""
    t = FACT_STR.get(lang, DEFAULT_FACT)
    if not url:
        return ''
    if news:
        # 뉴스 재각색 글: '스토어에서 보기'가 아니라 '원문 기사 보기'로 안내한다.
        label = ('%s · %s' % (store_name, t[12])) if store_name else t[12]
        return ('<aside class="mt-10 rounded-lg border border-fuchsia-200 dark:border-fuchsia-900 '
                'bg-fuchsia-50/60 dark:bg-fuchsia-950/30 p-5 text-center">'
                '<a class="inline-block px-5 py-2.5 rounded-md bg-fuchsia-700 hover:bg-fuchsia-800 '
                'text-white text-sm font-semibold" rel="nofollow noopener" target="_blank" '
                'href="%s">%s</a></aside>'
                % (htmllib.escape(url), htmllib.escape(label)))
    label = ('%s · %s' % (store_name, t[10])) if store_name else t[10]
    return ('<aside class="mt-10 rounded-lg border border-fuchsia-200 dark:border-fuchsia-900 '
            'bg-fuchsia-50/60 dark:bg-fuchsia-950/30 p-5 text-center">'
            '<a class="inline-block px-5 py-2.5 rounded-md bg-fuchsia-700 hover:bg-fuchsia-800 '
            'text-white text-sm font-semibold" rel="nofollow noopener" target="_blank" '
            'href="%s">%s</a>'
            '<p class="mt-2 text-xs text-slate-500 dark:text-slate-400">%s</p></aside>'
            % (htmllib.escape(url), htmllib.escape(label), htmllib.escape(t[11])))


def game_card_html(lang, p):
    """목록 카드: 스토어 실제 이미지 + 상태/가격 뱃지 + 출시일·가격 한 줄."""
    f = p.get('facts') or game_facts({}, p.get('body', ''))
    t = FACT_STR.get(lang, DEFAULT_FACT)
    img = f.get('image') or ''
    if img:
        thumb = ('<img src="%s" alt="" loading="lazy" decoding="async" '
                 'class="w-full h-full object-cover object-top" />' % htmllib.escape(img))
    else:
        thumb = ('<span class="text-white/90 text-sm font-semibold uppercase tracking-widest px-4 '
                 'text-center">%s</span>' % htmllib.escape(p.get('category', '')))
    badge = ''
    if f.get('upcoming'):
        badge = ('<span class="text-[11px] font-semibold px-2 py-0.5 '
                 'rounded bg-fuchsia-700 text-white">%s</span>' % htmllib.escape(t[7]))
        dday = dday_badge_html(f.get('releaseDate', ''))
        if dday:
            badge += dday
    elif f.get('price'):
        badge = ('<span class="text-[11px] font-semibold px-2 py-0.5 '
                 'rounded bg-slate-800/90 text-white">%s</span>' % htmllib.escape(f['price']))
    if badge:
        badge = '<span class="absolute top-2 right-2 flex flex-col items-end gap-1">' + badge + '</span>'
    bits = [b for b in (f.get('releaseDate'), f.get('price'), f.get('platform')) if b]
    meta = ('<p class="mt-1 text-xs text-fuchsia-700 dark:text-fuchsia-300 font-medium">%s</p>'
            % htmllib.escape(' · '.join(bits))) if bits else ''
    return f'''<article class="border border-slate-200 dark:border-slate-800 rounded-lg overflow-hidden hover:shadow-md transition-shadow bg-white dark:bg-slate-900 flex flex-col">
  <a class="relative block aspect-[16/10] overflow-hidden bg-gradient-to-br from-fuchsia-800 to-fuchsia-600" href="{p.get('href')}" aria-label="{htmllib.escape(p.get('title', ''))}">
    {thumb}{badge}
  </a>
  <div class="p-5">
    <div class="flex items-center gap-2 text-xs text-slate-500 mb-1">
      <span class="bg-fuchsia-50 text-fuchsia-700 px-2 py-0.5 rounded uppercase tracking-wide">{htmllib.escape(p.get('category', ''))}</span>
      <time datetime="{p.get('date', '')}">{p.get('date', '')}</time>
    </div>
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-1 leading-snug"><a class="hover:text-fuchsia-700" href="{p.get('href')}">{htmllib.escape(p.get('title', ''))}</a></h2>
    {meta}
    <p class="mt-2 text-sm text-slate-600 dark:text-slate-400 line-clamp-3">{htmllib.escape(p.get('desc', ''))}</p>
  </div>
</article>'''


# ---------- 카드 ----------
def card_html(lang, slug, title, desc, category, date, href_fn=None, href=None):
    grad = CATEGORY_GRADIENT.get(category, DEFAULT_GRADIENT)
    href = href or (href_fn or post_href)(lang, slug)
    return f'''<article class="border border-slate-200 dark:border-slate-800 rounded-lg overflow-hidden hover:shadow-md transition-shadow bg-white dark:bg-slate-900 flex flex-col">
  <a class="block aspect-[16/9] overflow-hidden bg-gradient-to-br {grad} flex items-center justify-center" href="{href}" aria-label="{htmllib.escape(title)}">
    <span class="text-white/90 text-sm font-semibold uppercase tracking-widest px-4 text-center">{htmllib.escape(category)}</span>
  </a>
  <div class="p-5">
    <div class="flex items-center gap-2 text-xs text-slate-500 mb-2">
      <span class="bg-brand-50 text-brand-700 px-2 py-0.5 rounded uppercase tracking-wide">{htmllib.escape(category)}</span>
      <time datetime="{date}">{date}</time>
    </div>
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-2 leading-snug"><a class="hover:text-brand-600" href="{href}">{htmllib.escape(title)}</a></h2>
    <p class="text-sm text-slate-600 dark:text-slate-400 line-clamp-3">{htmllib.escape(desc)}</p>
  </div>
</article>'''


def related_html(lang, slug, posts_in_lang, label, href_fn=None):
    others = [p for p in posts_in_lang if p['slug'] != slug][:3]
    if not others:
        return ''
    fn = href_fn or post_href
    items = '\n'.join(
        f'    <li><a class="hover:text-brand-600 underline-offset-2 hover:underline" href="{p.get("href") or fn(lang, p["slug"])}">{htmllib.escape(p["title"])}</a></li>'
        for p in others)
    return f'''<nav class="mt-12 border-t border-slate-200 dark:border-slate-800 pt-6">
  <h2 class="text-sm font-semibold uppercase tracking-wide text-slate-500 mb-3">{htmllib.escape(label)}</h2>
  <ul class="space-y-2 text-slate-700 dark:text-slate-300">
{items}
  </ul>
</nav>'''


def build_post(lang, slug, title, desc, category, date, body_md, source_name, source_url,
               posts_in_lang, available, section='post', facts=None):
    """section='post' → /post/{slug}.html (경제), section='game' → /game/post/{slug}.html"""
    is_game = (section == 'game')
    url_fn = game_post_url if is_game else post_url
    href_fn = game_post_href if is_game else post_href
    body_html = md_to_html(body_md)
    canonical = url_fn(lang, slug)
    s = strs(lang)
    f = facts or (game_facts({}, body_md) if is_game else {})
    src = ''
    if source_url:
        src = (f'\n    <p class="mt-4 text-xs text-slate-500">{htmllib.escape(s[4])}: '
               f'<a class="underline hover:text-brand-600" rel="nofollow noopener" target="_blank" '
               f'href="{htmllib.escape(source_url)}">{htmllib.escape(source_name or source_url)}</a></p>')
    # 앱·게임 글: 첫 화면에 팩트박스 + 목차를 먼저 보여주고 광고는 그 아래로 내린다.
    box = fact_box_html(lang, f) if is_game else ''
    toc = toc_html(body_html, lang) if is_game else ''
    top_ad = '' if is_game else ad_unit('post_top')
    bottom = (store_cta_html(lang, source_url, source_name, news=bool(f.get('news')))
              if is_game else affiliate_box(lang, category))
    # 사전예약 글: 헤더에 D-데이 칩. 공유 버튼 마운트(렌더는 main.js).
    dday_chip = ''
    if is_game and f.get('upcoming'):
        dd = dday_str(f.get('releaseDate', ''))
        if dd:
            dday_chip = ('  <span class="bg-amber-500 text-white px-2 py-0.5 rounded '
                         'text-xs font-semibold tracking-wide">%s</span>' % htmllib.escape(dd))
    u = UX_STR.get(lang, DEFAULT_UX)
    share_row = ('\n  <div id="mca-share" data-share-lang="%s" '
                 'data-str-copy="%s" data-str-copied="%s" '
                 'class="mt-5 flex flex-wrap items-center gap-2 text-sm"></div>'
                 % (lang, htmllib.escape(u[6], quote=True), htmllib.escape(u[7], quote=True)))
    content = f'''<article class="max-w-3xl mx-auto">
  <header class="mb-6">
    <div class="flex items-center gap-2 text-xs text-slate-500 mb-3">
      <span class="{"bg-fuchsia-50 text-fuchsia-700" if is_game else "bg-brand-50 text-brand-700"} px-2 py-0.5 rounded uppercase tracking-wide">{htmllib.escape(category)}</span>
      <time datetime="{date}">{htmllib.escape(s[3])}: {date}</time>
      <span class="text-slate-400">&middot;</span>
      <span>{"Store Listing Summary" if is_game else "Independent Analysis"}</span>
{dday_chip}    </div>
    <h1 class="text-3xl font-bold leading-tight text-slate-900 dark:text-slate-100 mb-4">{htmllib.escape(title)}</h1>
    <p class="text-slate-600 dark:text-slate-400">{htmllib.escape(desc)}</p>{src}
  </header>
  {box}
  {toc}
  {top_ad}
  <div class="prose prose-slate dark:prose-invert max-w-none">{body_html}</div>
  {ad_unit('post_bottom')}
  {bottom}
  {related_html(lang, slug, posts_in_lang, s[5], href_fn)}
{share_row}</article>'''

    gnav = GAME_STR.get(lang, DEFAULT_GAME)[0] if is_game else None
    art_ld = news_article_ld(lang, slug, title, desc, date, body_md, source_name, source_url,
                             url_fn,
                             article_section=(GAME_CATEGORY if is_game else None),
                             image=(f.get('image') or None))
    blocks = [art_ld, breadcrumb_ld(lang, slug, title, url_fn, gnav)]
    if is_game:
        blocks.append(software_app_ld(lang, slug, title, desc, f, source_url, url_fn))
    faq = extract_faq(body_md)
    if faq:
        blocks.append(faq_ld(faq, lang, slug))

    html_doc = layout(lang, title + ' — ' + SITE_NAME, desc, canonical, content, 'article',
                      blocks, slug=slug, available=available, switcher_slug=slug,
                      section=section, image=(f.get('image') or None))
    if is_game:
        out_path = (os.path.join(BASE, 'game', 'post', slug + '.html') if lang == 'en'
                    else os.path.join(BASE, lang, 'game', 'post', slug + '.html'))
    else:
        out_path = os.path.join(BASE, 'post', slug + '.html') if lang == 'en' else os.path.join(BASE, lang, 'post', slug + '.html')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, 'w', encoding='utf-8').write(html_doc)
    return out_path


def posts_payload(lang, posts, game_list):
    """홈/검색 위젯 공용 데이터 — {t:제목, d:날짜, h:링크, k:종류, x:설명}."""
    data = []
    for p in posts:
        data.append({'t': p['title'], 'd': p['date'], 'h': post_href(lang, p['slug']),
                     'k': 'econ', 'x': (p.get('desc') or '')[:200]})
    for p in game_list:
        data.append({'t': p['title'], 'd': p['date'],
                     'h': p.get('href') or game_post_href(lang, p['slug']),
                     'k': p.get('kind', 'app'), 'x': (p.get('desc') or '')[:200]})
    return json.dumps(data, ensure_ascii=False).replace('</', '<\\/')


def homepage_widgets(lang, posts, game_list):
    """메인 화면 위젯 3개 — 일별 발행 달력 + 국가별 방문자 표 + 이번 주 인기 글.

    렌더링은 assets/js/main.js 가 한다. 여기선 데이터(전체 글 목록)와 마운트만 심는다.
    """
    w = WIDGET_STR.get(lang, DEFAULT_WIDGET)
    u = UX_STR.get(lang, DEFAULT_UX)
    payload = posts_payload(lang, posts, game_list)
    attrs = (f'data-locale="{lang}" data-str-none="{htmllib.escape(w[1], quote=True)}" '
             f'data-str-wait="{htmllib.escape(w[3], quote=True)}" data-str-unit="{htmllib.escape(w[4], quote=True)}"')
    pop_attrs = (f'data-locale="{lang}" data-str-wait="{htmllib.escape(w[3], quote=True)}" '
                 f'data-str-views="{htmllib.escape(u[5], quote=True)}"')
    return f'''
<section class="mt-10 grid gap-6 lg:grid-cols-2 items-start">
  <div class="border border-slate-200 dark:border-slate-800 rounded-lg p-5 bg-white dark:bg-slate-900">
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">{htmllib.escape(w[0])}</h2>
    <div id="mca-cal" {attrs}></div>
    <div id="mca-cal-out" class="mt-3 text-sm"></div>
  </div>
  <div class="border border-slate-200 dark:border-slate-800 rounded-lg p-5 bg-white dark:bg-slate-900">
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">{htmllib.escape(w[2])}</h2>
    <div id="mca-geo" data-locale="{lang}" data-str-wait="{htmllib.escape(w[3], quote=True)}"></div>
  </div>
  <div class="border border-slate-200 dark:border-slate-800 rounded-lg p-5 bg-white dark:bg-slate-900">
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">{htmllib.escape(u[4])}</h2>
    <div id="mca-pop" {pop_attrs}></div>
  </div>
</section>
<script>window.__POSTS__={payload};</script>'''


def build_search_page(lang, posts, game_list, available):
    """사이트 내 검색 — /search/, /{lang}/search/. noindex (검색 결과 페이지는 색인 대상이 아니다)."""
    u = UX_STR.get(lang, DEFAULT_UX)
    s = strs(lang)
    payload = posts_payload(lang, posts, game_list)
    content = f'''<div class="max-w-3xl mx-auto">
  <h1 class="text-2xl font-bold text-slate-900 dark:text-slate-100">{htmllib.escape(u[0])}</h1>
  <p class="mt-1 text-sm text-slate-600 dark:text-slate-400">{htmllib.escape(s[1])}</p>
  <div class="mt-5">
    <input id="mca-search" type="search" autocomplete="off" autofocus
           placeholder="{htmllib.escape(u[1], quote=True)}" aria-label="{htmllib.escape(u[0])}"
           class="w-full px-4 py-3 rounded-lg border border-slate-300 dark:border-slate-700
                  bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100
                  focus:outline-none focus:ring-2 focus:ring-brand-500" />
  </div>
  <p id="mca-search-count" class="mt-3 text-sm text-slate-500 dark:text-slate-400"></p>
  <div id="mca-search-out" class="mt-4 space-y-4"
       data-str-none="{htmllib.escape(u[2], quote=True)}" data-str-unit="{htmllib.escape(u[3], quote=True)}"></div>
</div>
<script>window.__POSTS__={payload};</script>'''
    canonical = DOMAIN + search_path(lang)
    title = f'{u[0]} — {SITE_NAME}'
    html_doc = layout(lang, title, s[1], canonical, content, 'website', [website_ld(lang, canonical)],
                      slug=None, available=available, switcher_slug=None, noindex=True)
    out_path = (os.path.join(BASE, 'search', 'index.html') if lang == 'en'
                else os.path.join(BASE, lang, 'search', 'index.html'))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, 'w', encoding='utf-8').write(html_doc)
    return out_path


def build_index(lang, posts, available, game_list=None):
    s = strs(lang)
    card_list = [card_html(lang, p['slug'], p['title'], p['desc'], p['category'], p['date']) for p in posts]
    half = max(1, len(card_list) // 2)
    cards = '\n'.join(card_list[:half])
    cards2 = '\n'.join(card_list[half:])
    infeed = ad_unit('index_infeed', wrap_class='sm:col-span-2 my-6 text-center')
    widgets = homepage_widgets(lang, posts, game_list or [])
    content = f'''<div class="space-y-6">
  <div>
    <h1 class="text-2xl font-bold text-slate-900 dark:text-slate-100">{htmllib.escape(s[2])}</h1>
    <p class="mt-1 text-sm text-slate-600 dark:text-slate-400">{htmllib.escape(s[1])}</p>
  </div>
  <div class="grid gap-4 sm:grid-cols-2">
{cards}
  {infeed}
{cards2}
  </div>
  {widgets}
  {ad_unit('index')}
</div>'''
    canonical = DOMAIN + home_path(lang)
    title = f'{SITE_NAME} — {s[0]}'
    html_doc = layout(lang, title, s[1], canonical, content, 'website', [website_ld(lang)],
                      slug=None, available=available, switcher_slug=None)
    out_path = os.path.join(BASE, 'index.html') if lang == 'en' else os.path.join(BASE, lang, 'index.html')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, 'w', encoding='utf-8').write(html_doc)
    return out_path


def game_tabs(lang, active):
    """섹션 상단 탭 — 전체/앱/게임. active: ''|'app'|'game'."""
    t = TAB_STR.get(lang, DEFAULT_TAB)
    base = game_home_path(lang)
    items = [('', base, t[0]), ('app', base + 'apps/', t[1]), ('game', base + 'games/', t[2])]
    cls = ('px-3 py-1.5 rounded-full text-sm border transition-colors')
    out = []
    for key, href, label in items:
        on = (key == active)
        style = ('bg-brand-600 text-white border-brand-600' if on
                 else 'border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:border-brand-400')
        out.append(f'<a class="{cls} {style}" href="{href}">{htmllib.escape(label)}</a>')
    return '<div class="flex items-center gap-2 flex-wrap">' + ''.join(out) + '</div>'


def game_home_for(lang, kind):
    """/game/ (전체) 또는 /game/apps/ · /game/games/ 경로."""
    base = game_home_path(lang)
    return base if not kind else base + ('apps/' if kind == 'app' else 'games/')


def search_path(lang):
    """사이트 내 검색 페이지 경로 (noindex — 사이트맵 미포함)."""
    return '/search/' if lang == 'en' else '/' + lang + '/search/'


def dday_str(release_date, today=None):
    """출시 D-데이 배지 문구. 미래면 'D-23'/오늘이면 'D-Day', 지났거나 무효면 ''."""
    try:
        d = datetime.date.fromisoformat((release_date or '').strip()[:10])
    except ValueError:
        return ''
    t = today or datetime.date.today()
    n = (d - t).days
    if n > 0:
        return 'D-%d' % n
    if n == 0:
        return 'D-Day'
    return ''


def dday_badge_html(release_date, cls=''):
    """출시까지 남은 날짜 배지 (사전예약 글 카드/포스트 공용)."""
    dd = dday_str(release_date)
    if not dd:
        return ''
    return ('<span class="text-[11px] font-semibold px-2 py-0.5 rounded '
            'bg-amber-500 text-white%s">%s</span>' % (cls, htmllib.escape(dd)))


def build_game_index(lang, plist, available, kind=''):
    """/game/ (전체·앱·게임) 인덱스. 기존 /game/ URL 은 그대로 두고 apps/ games/ 를 추가."""
    g = GAME_STR.get(lang, DEFAULT_GAME)
    shown = [p for p in plist if not kind or p.get('kind') == kind] if kind else plist
    cards = '\n'.join(game_card_html(lang, p) for p in shown)
    infeed = ad_unit('index_infeed', wrap_class='sm:col-span-2 my-6 text-center')
    empty = (f'<p class="text-sm text-slate-500 dark:text-slate-400 py-8 text-center">'
             f'{htmllib.escape(TAB_STR.get(lang, DEFAULT_TAB)[0])} — 0</p>')
    content = f'''<div class="space-y-6">
  <div>
    <h1 class="text-2xl font-bold text-slate-900 dark:text-slate-100">{htmllib.escape(g[1])}</h1>
    <p class="mt-1 text-sm text-slate-600 dark:text-slate-400">{htmllib.escape(g[2])}</p>
  </div>
  {game_tabs(lang, kind)}
  <div class="grid gap-4 sm:grid-cols-2">
{cards if shown else empty}
  {infeed}
  </div>
  {ad_unit('index')}
</div>'''
    canonical = DOMAIN + game_home_for(lang, kind)
    title = f'{g[1]} — {SITE_NAME}'
    html_doc = layout(lang, title, g[2], canonical, content, 'website',
                      [website_ld(lang, canonical)],
                      slug=None, available=available, section='game')
    rel = 'index.html' if not kind else ('apps/index.html' if kind == 'app' else 'games/index.html')
    out_path = (os.path.join(BASE, 'game', rel) if lang == 'en'
                else os.path.join(BASE, lang, 'game', rel))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, 'w', encoding='utf-8').write(html_doc)
    return out_path


def build_sitemap(posts, avail_by_slug, langs_with_home, game=None):
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

    # 앱·게임 섹션(/game/) — 언어별 네이티브 글
    if game:
        game_langs = [c for c in LANG_META if game.get(c)]
        g_alts = [(c, DOMAIN + game_home_path(c)) for c in game_langs]
        if 'en' in game_langs:
            g_alts.append(('x-default', DOMAIN + '/game/'))
        for c in game_langs:
            emit(DOMAIN + game_home_path(c), g_alts)
            # 앱/게임 분리 인덱스 (신규 URL — 기존 URL 은 건드리지 않는다)
            emit(DOMAIN + game_home_path(c) + 'apps/', [(c, DOMAIN + game_home_path(c) + 'apps/')])
            emit(DOMAIN + game_home_path(c) + 'games/', [(c, DOMAIN + game_home_path(c) + 'games/')])
            for p in game[c]:
                emit(p['url'], [(c, p['url'])])

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

    # 앱·게임 섹션 분리: 경제 메인에는 경제 글만 남긴다.
    # (content/posts 에 옛날에 섞여 들어간 앱 글은 URL 유지 상태로 /game/ 목록에만 노출)
    macro_posts = [p for p in posts if p['category'] != GAME_CATEGORY]
    legacy_app = [p for p in posts if p['category'] == GAME_CATEGORY]

    # 앱·게임 네이티브 글: content/game/{lang}/*.md (번역 파이프라인을 타지 않는다)
    game_posts = {}
    for lang in LANG_META:
        gdir = os.path.join(GAME_DIR, lang)
        if not os.path.isdir(gdir):
            continue
        items = []
        for path in sorted(glob.glob(os.path.join(gdir, '*.md'))):
            fm, body = parse_md(path)
            slug = fm.get('slug') or os.path.splitext(os.path.basename(path))[0]
            items.append({'slug': slug, 'title': fm.get('title', slug),
                          'desc': fm.get('description', ''),
                          'category': fm.get('category', GAME_CATEGORY),
                          'date': fm.get('date', ''),
                          'sourceName': fm.get('sourceName', ''),
                          'sourceUrl': fm.get('sourceUrl', ''),
                          'body': body,
                          'facts': game_facts(fm, body),
                          'genre': fm.get('genre', ''),
                          'kind': game_kind(fm),
                          'href': game_post_href(lang, slug),
                          'url': game_post_url(lang, slug)})
        if items:
            items.sort(key=lambda p: p['date'], reverse=True)
            game_posts[lang] = items
    if legacy_app:
        en = game_posts.setdefault('en', [])
        for p in legacy_app:
            en.append({'slug': p['slug'], 'title': p['title'], 'desc': p['desc'],
                       'category': p['category'], 'date': p['date'], 'body': p['body'],
                       'sourceName': p['sourceName'], 'sourceUrl': p['sourceUrl'],
                       'href': post_href('en', p['slug']),
                       'url': post_url('en', p['slug']), 'legacy': True,
                       'kind': LEGACY_KIND.get(p['slug'], 'app'),
                       'facts': game_facts({}, p['body'])})
        game_posts['en'].sort(key=lambda p: p['date'], reverse=True)

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

    # 언어별로 실제 존재하는 포스트 목록 (경제 글만)
    avail_by_slug = {}
    for p in macro_posts:
        langs = set(trans.get(p['slug'], {}).keys()) | {'en'}
        avail_by_slug[p['slug']] = langs

    def posts_for(lang):
        out = []
        for p in macro_posts:
            if lang == 'en':
                out.append({'slug': p['slug'], 'title': p['title'], 'desc': p['desc'],
                            'category': p['category'], 'date': p['date']})
            elif lang in avail_by_slug.get(p['slug'], set()):
                t = trans[p['slug']][lang]
                out.append({'slug': p['slug'], 'title': t['title'], 'desc': t['description'],
                            'category': p['category'], 'date': p['date']})
        return out

    # 홈이 존재하는 언어 집합
    langs_with_home = {c for c in LANG_META if any(c in avail_by_slug.get(p['slug'], {'en'}) for p in macro_posts)}
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
                src = next(x for x in macro_posts if x['slug'] == p['slug'])
                build_post(lang, p['slug'], p['title'], p['desc'], p['category'], p['date'],
                           src['body'], src['sourceName'], src['sourceUrl'], plist,
                           avail_by_slug[p['slug']])
            else:
                t = trans[p['slug']][lang]
                src = next(x for x in macro_posts if x['slug'] == p['slug'])
                build_post(lang, p['slug'], t['title'], t['description'], p['category'], p['date'],
                           t['body'], src['sourceName'], src['sourceUrl'], plist,
                           avail_by_slug[p['slug']])
            total += 1
        build_index(lang, plist, home_available, game_posts.get(lang, []))
        build_search_page(lang, plist, game_posts.get(lang, []), home_available)
        build_disclosure(lang, home_available)

    # 앱·게임 섹션(/game/) — 언어별 네이티브 글. 번역이 아니라 각 스토어에서 직접 수집한 글.
    game_langs = sorted(game_posts.keys())
    game_total = 0
    for lang in game_langs:
        plist = game_posts[lang]
        print(f'[game/{lang}] {len(plist)} posts')
        for p in plist:
            if p.get('legacy'):
                continue  # 옛 영문 앱 글은 기존 URL(/post/) 유지
            build_post(lang, p['slug'], p['title'], p['desc'], p['category'], p['date'],
                       p['body'], p['sourceName'], p['sourceUrl'], plist, {lang},
                       section='game', facts=p.get('facts'))
            game_total += 1
        build_game_index(lang, plist, set(game_langs))
        build_game_index(lang, plist, set(game_langs), 'app')
        build_game_index(lang, plist, set(game_langs), 'game')

    build_sitemap(macro_posts, avail_by_slug, langs_with_home, game_posts)
    build_feed(macro_posts)
    dp = AFF_CFG.get('disclosure_path', 'disclosure.html')
    print(f'빌드 완료: 경제 포스트 {total}개 + 앱/게임 {game_total}개 + 홈 {len(LANG_META)}개, sitemap.xml, feed.xml OK')


if __name__ == '__main__':
    main()
