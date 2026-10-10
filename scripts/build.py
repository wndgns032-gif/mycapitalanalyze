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
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 오토 어필리에이트 슬롯 모듈을 scripts/ 안에서 찾기 위해 경로 등록
sys.path.insert(0, os.path.join(BASE, 'scripts'))

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

# KO 전용 학습 섹션(/ko/study/). 로이 개인 학습용 → 색인 제외(noindex), 사이트맵 미포함.
STUDY_DIR = os.path.join(BASE, 'content', 'study')
STUDY_LANG = 'ko'
STUDY_TRACK_TITLE = {'history': '한국사(심화)', 'toeic': 'TOEIC Part5·Part7',
                     'psat': 'PSAT 자료해석', 'rest': '주간 복습'}
STUDY_BUDGET = '복습 5분(7일 전) + 복습 5분(어제) + 새 내용 20분 = 30분'

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
ARCHIVE_STR = {
    'en': ('All articles', 'Every analysis we have published, newest first.'),
    'ko': ('전체 글', '발행한 모든 분석 글을 최신순으로 모았습니다.'),
    'zh': ('全部文章', '按发布时间倒序列出所有分析文章。'),
    'ja': ('すべての記事', '公開した分析記事をすべて新しい順に掲載しています。'),
    'es': ('Todos los artículos', 'Todos los análisis publicados, del más reciente al más antiguo.'),
    'de': ('Alle Artikel', 'Alle veröffentlichten Analysen, neueste zuerst.'),
    'fr': ('Tous les articles', 'Toutes les analyses publiées, de la plus récente à la plus ancienne.'),
    'pt': ('Todos os artigos', 'Todas as análises publicadas, da mais recente à la mais antiga.'),
    'ru': ('Все статьи', 'Все опубликованные материалы — от новых к старым.'),
    'id': ('Semua artikel', 'Semua analisis yang diterbitkan, dari yang terbaru.'),
    'hi': ('सभी लेख', 'प्रकाशित सभी विश्लेषण, नवीनतम पहले।'),
    'ar': ('جميع المقالات', 'جميع التحليلات المنشورة، من الأحدث إلى الأقدم.'),
    'bn': ('সব নিবন্ধ', 'প্রকাশিত সব বিশ্লেষণ, নতুন থেকে পুরনো।'),
}
DEFAULT_ARCHIVE = ARCHIVE_STR['en']
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

# 언어별 "페이지 N" 표현 — build_index 가 2페이지 이후 meta description을 고유하게 만들 때 쓴다.
# (홈 1페이지는 기존 home_desc 를 그대로 유지한다)
PAGE_STR = {
    'en': 'page {n}',
    'ko': '{n}페이지',
    'zh': '第{n}页',
    'ja': '{n}ページ',
    'es': 'página {n}',
    'fr': 'page {n}',
    'de': 'Seite {n}',
    'pt': 'página {n}',
    'ru': 'страница {n}',
    'hi': 'पृष्ठ {n}',
    'id': 'halaman {n}',
    'ar': 'صفحة {n}',
    'bn': 'পৃষ্ঠা {n}',
}
DEFAULT_PAGE_STR = PAGE_STR['en']

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

# 출시일 미정(TBA) 표기 — 카드 1행·팩트박스 공용
FACT_TBA_STR = {
    'en': 'TBA', 'ko': '미정', 'ja': '未定', 'zh': '待定', 'es': 'Por confirmar',
    'de': 'Noch offen', 'fr': 'À confirmer', 'pt': 'A definir', 'id': 'Belum ditentukan',
    'ru': 'Уточняется', 'hi': 'अघोषित', 'ar': 'لم يُحدَّد', 'bn': 'নির্ধারিত হয়নি',
}
FACT_TBA_DEFAULT = FACT_TBA_STR['en']

# 지원 언어 / 인앱 결제 / 미지원 라벨 — 팩트박스·카드 공용
FACT_LANGS_STR = {
    'en': 'Languages', 'ko': '지원 언어', 'ja': '対応言語', 'zh': '支持语言', 'es': 'Idiomas',
    'de': 'Sprachen', 'fr': 'Langues', 'pt': 'Idiomas', 'id': 'Bahasa', 'ru': 'Языки',
    'hi': 'भाषाएँ', 'ar': 'اللغات', 'bn': 'ভাষাসমূহ'}
FACT_IAP_STR = {
    'en': 'In-app purchases', 'ko': '앱 내 구매', 'ja': 'アプリ内課金', 'zh': '应用内购买',
    'es': 'Compras en la app', 'de': 'In-App-Käufe', 'fr': 'Achats intégrés', 'pt': 'Compras no app',
    'id': 'Pembelian dalam aplikasi', 'ru': 'Внутриигровые покупки', 'hi': 'इन-ऐप खरीदारी',
    'ar': 'عمليات الشراء داخل التطبيق', 'bn': 'ইন-অ্যাপ কেনাকাটা'}
FACT_NA_STR = {
    'en': 'not supported', 'ko': '미지원', 'ja': '非対応', 'zh': '不支持', 'es': 'no compatible',
    'de': 'nicht unterstützt', 'fr': 'non pris en charge', 'pt': 'não suportado', 'id': 'tidak didukung',
    'ru': 'не поддерживается', 'hi': 'समर्थित नहीं', 'ar': 'غير مدعوم', 'bn': 'সমর্থিত নয়'}

# 목록을 '출시 예정' / '신규 출시' 두 그룹으로 나눌 때의 소제목.
# GAME_STR 튜플 인덱스는 건드리지 않기 위해 별도 dict 를 쓴다.
GAME_GROUP_STR = {
    'en': ('Upcoming', 'New & Available'),
    'ko': ('출시 예정', '신규 출시'),
    'ja': ('配信予定', '新着・配信中'),
    'zh': ('即将上线', '已上线新作'),
    'es': ('Próximamente', 'Novedades'),
    'de': ('Demnächst', 'Neu erschienen'),
    'fr': ('Bientôt disponible', 'Nouveautés'),
    'pt': ('Em breve', 'Novidades'),
    'id': ('Segera hadir', 'Rilis baru'),
    'ru': ('Скоро выйдет', 'Новинки'),
    'hi': ('जल्द आ रहा है', 'नया और उपलब्ध'),
    'ar': ('قريبًا', 'جديد ومتاح'),
    'bn': ('শীঘ্রই আসছে', 'নতুন ও উপলব্ধ'),
}
GAME_GROUP_DEFAULT = GAME_GROUP_STR['en']

# 상세 페이지 빵부스러기 섹션명 (홈 › 앱 & 게임 › 제목)
GAME_CRUMB_STR = {
    'en': 'Apps & Games', 'ko': '앱 & 게임', 'ja': 'アプリ & ゲーム', 'zh': '应用与游戏',
    'es': 'Apps y juegos', 'de': 'Apps & Spiele', 'fr': 'Apps et jeux', 'pt': 'Apps e jogos',
    'id': 'Aplikasi & Game', 'ru': 'Приложения и игры', 'hi': 'ऐप्स और गेम',
    'ar': 'التطبيقات والألعاب', 'bn': 'অ্যাপ ও গেম'}
GAME_CRUMB_DEFAULT = GAME_CRUMB_STR['en']

# 빵부스러기 첫 항목(홈) — 헤더의 'Home' 은 번역이 없어 별도 표기를 쓴다.
HOME_CRUMB_STR = {
    'en': 'Home', 'ko': '홈', 'ja': 'ホーム', 'zh': '首页', 'es': 'Inicio', 'de': 'Start',
    'fr': 'Accueil', 'pt': 'Início', 'id': 'Beranda', 'ru': 'Главная', 'hi': 'होम',
    'ar': 'الرئيسية', 'bn': 'হোম'}
HOME_CRUMB_DEFAULT = HOME_CRUMB_STR['en']

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
def _theme_btn(cls='p-2 rounded text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800'):
    """다크모드 토글 버튼 (헤더 데스크톱/모바일 양쪽에서 재사용)."""
    return (f'<button type="button" onclick="toggleTheme()" aria-label="Toggle dark mode" class="{cls}">'
            '<svg class="w-5 h-5 dark:hidden" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/></svg>'
            '<svg class="w-5 h-5 hidden dark:block" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path stroke-linecap="round" stroke-linejoin="round" d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>'
            '</button>')


def _search_link(current, cls='p-2 rounded text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-brand-600'):
    return (f'<a class="{cls}" href="{search_path(current)}" '
            f'aria-label="{htmllib.escape(UX_STR.get(current, DEFAULT_UX)[0])}">'
            '<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path stroke-linecap="round" d="M21 21l-4.35-4.35"/></svg></a>')


def header_html(current):
    """헤더.

    모바일(640px 미만)에서는 내비 항목이 가로로 넘치므로(13개 언어 + 메뉴 5개)
    데스크톱용 inline nav 를 숨기고, 검색·테마 아이콘 + 햄버거 메뉴(details) 로 바꾼다.
    메뉴 항목은 44px 터치 목표를 지킨다. (2026-10-04 모바일 규격)
    """
    s = strs(current)
    home = home_path(current)
    # KO 전용: 오늘의 학습 바로가기 (로이 학습 섹션 진입점)
    study_link = ('<a class="text-slate-600 hover:text-brand-600 dark:text-slate-300 font-semibold" '
                  'href="/ko/study/">오늘의 학습</a>') if current == 'ko' else ''
    app_lbl = htmllib.escape(TAB_STR.get(current, DEFAULT_TAB)[1])
    game_lbl = htmllib.escape(TAB_STR.get(current, DEFAULT_TAB)[2])
    m_item = ('<a class="block px-3 py-3 rounded text-sm font-medium text-slate-700 dark:text-slate-200 '
              'hover:bg-slate-100 dark:hover:bg-slate-800" href="%s">%s</a>')
    m_study = (m_item % ('/ko/study/', '오늘의 학습')) if current == 'ko' else ''
    menu_items = (m_item % (home, 'Home') + m_study
                  + m_item % (game_home_for(current, 'app'), app_lbl)
                  + m_item % (game_home_for(current, 'game'), game_lbl)
                  + m_item % ('/about.html', 'About')
                  + m_item % ('/privacy.html', 'Privacy')
                  + m_item % ('/contact.html', 'Contact'))
    return f'''<header class="border-b border-slate-200 dark:border-slate-800 sticky top-0 bg-white/80 dark:bg-slate-950/80 backdrop-blur z-20">
    <div class="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between gap-2">
      <a class="font-bold text-base sm:text-lg text-slate-900 dark:text-slate-100 truncate tap px-1" href="{home}">{SITE_NAME}</a>

      <nav class="hidden sm:flex items-center gap-4 text-sm">
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="{home}">Home</a>
        {study_link}
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="{game_home_for(current, 'app')}">{app_lbl}</a>
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="{game_home_for(current, 'game')}">{game_lbl}</a>
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="/about.html">About</a>
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="/privacy.html">Privacy</a>
        <a class="text-slate-600 hover:text-brand-600 dark:text-slate-300" href="/contact.html">Contact</a>
        {_search_link(current, 'p-2 rounded text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-brand-600')}
        {_theme_btn()}
      </nav>

      <div class="flex items-center gap-0.5 sm:hidden">
        {_search_link(current)}
        {_theme_btn()}
        <details class="relative" data-mca-menu>
          <summary class="mca-menu-btn tap w-11 cursor-pointer text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded" aria-label="Menu">
            <svg class="w-5 h-5 mx-auto" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="M4 7h16M4 12h16M4 17h16"/></svg>
          </summary>
          <div class="absolute right-0 top-12 w-48 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-xl p-1.5 z-30">
            {menu_items}
          </div>
        </details>
      </div>
    </div>
  </header>'''


def footer_html(lang):
    # 통계 대시보드(stats.html)는 noindex 개인용 페이지라 한국어에서만foot 링크를 건다.
    # 13개 언어 전체에 노출시키지 않고, 그래도 인바운드 링크 0건인 고아 상태는 막는다.
    stats_link = ('<a class="inline-block py-2.5 underline hover:text-brand-600" '
                  'href="/stats.html">통계</a>') if lang == 'ko' else ''
    stats_line = f'      <p class="text-xs">{stats_link}</p>\n' if stats_link else ''
    return f'''<footer class="border-t border-slate-200 dark:border-slate-800 mt-16">
    <div class="max-w-5xl mx-auto px-4 py-8 text-sm text-slate-500 dark:text-slate-400 space-y-2">
      <p>&copy; 2026 {SITE_NAME}. All rights reserved.</p>
      <p class="text-xs leading-relaxed">{strs(lang)[6]}</p>
      <p class="text-xs pt-2"><a class="inline-block py-2.5 underline hover:text-brand-600" href="{(home_path(lang)) + AFF_CFG.get('disclosure_path', 'disclosure.html')}">{htmllib.escape(AFF_STR.get(lang, DEFAULT_AFF)[3])}</a></p>
{stats_line}    </div>
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
    {"id","format","layout","layout_key"} 객체 지원. (layout_key: 인피드 전용)"""
    ent = AD_SLOTS.get(key)
    if isinstance(ent, dict):
        return ((ent.get('id') or '').strip(),
                (ent.get('format') or 'auto').strip(),
                (ent.get('layout') or '').strip(),
                (ent.get('layout_key') or '').strip())
    return ((ent or '').strip() if isinstance(ent, str) else '', 'auto', '', '')


def ad_unit(key, wrap_class='my-6'):
    """AdSense 광고 유닛. 슬롯 ID가 설정되지 않았으면 빈 문자열.

    애드센스는 각 <ins> 마다 한 번의 push가 필요하므로 유닛마다 스크립트를 붙인다.
    """
    slot, fmt, layout, layout_key = _slot_entry(key)
    if not slot or not AD_CLIENT:
        return ''
    layout_attr = f' data-ad-layout="{htmllib.escape(layout)}"' if layout else ''
    lkey_attr = (f' data-ad-layout-key="{htmllib.escape(layout_key)}"' if layout_key else '')
    # 인아티클은 구글 예시 코드처럼 가운데 정렬을 명시한다
    style = 'display:block; text-align:center;' if layout == 'in-article' else 'display:block'
    return (
        f'<div class="{wrap_class}" data-ad-slot-name="{htmllib.escape(key)}">\n'
        f'  <ins class="adsbygoogle" style="{style}" '
        f'data-ad-client="{AD_CLIENT}" data-ad-slot="{htmllib.escape(slot)}" '
        f'data-ad-format="{htmllib.escape(fmt)}" data-full-width-responsive="true"{layout_attr}{lkey_attr}></ins>\n'
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


def ae_autoslot_html(lang, category, title, body_md, kind=None):
    """🤖 오토 어필리에이트 슬롯 (2026-10-08, 로이 지시).

    포스트 본문 중간에 카테고리 적합 상품을 자동 삽입한다.
    config.public.json 의 `auto_affiliate.enabled` 가 true 일 때만 동작한다.
    API 세션이 없거나 실패하면 빈 문자열을 반환하므로 **사이트는 절대 죽지 않는다.**

    왜 하단 박스가 아니라 본문 중간인가
      * AdSense 정책상 본문 중간 삽입이 하단 배너보다 위험이 낮다
      * 독자가 "이 정보 쓰려면 이게 필요하다"고 느끼는 지점에 놓여 CTR 이 높다

    `kind` = 'app' | 'game'. 게임 리뷰면 게임 액세서리(패드/지문링커)로 조회한다.
    """
    cfg = CONFIG.get('auto_affiliate') or {}
    if not cfg.get('enabled'):
        return ''

    # md frontmatter 의 category 값이 '"Apps & Games"' 처럼 따옴표가 붙어 오는 경우가
    # 있다 (2026-10-08 실측). 정규화 없이 비교하면 화이트리스트를 통과하지 못한다.
    category = (category or '').strip().strip('"\'').strip()

    # 카테고리 화이트리스트 — 지정된 카테고리에만 넣는다.
    # "*" 면 전 카테고리 허용 (2026-10-09 로이 지시 "다 넣으라고").
    only = cfg.get('only_categories')
    if only and '*' not in only \
            and category not in [c.strip().strip('"\'') for c in only]:
        return ''

    # 화이트리스트가 "*" 이고 매핑이 없는 카테고리면 넣지 않는다.
    # (임의 카테고리에 기본 앱용 상품을 넣으면 맥락이 안 맞는다)
    # ⚠️ 2026-10-10 로이 지시 "다 집어넣어" — 이 **차단 로직을 제거했다.**
    #   매핑 없는 카테고리를 건너뛰는 게 "카드 없는 글"의 주원인이었다.
    #   이제는 어떤 카테고리든 넣고, 상품 선택은 ae_autoslot 안에서
    #   주제어 → 카테고리 기본값 → 전역 풀 순으로 폴백한다.

    try:
        import ae_autoslot
    except ImportError:
        return ''

    # 🔴 2026-10-10: 짧은 글 제외 로직 삭제.
    #   `min_body_chars` 로 되돌아가지 않는다. 로이가 "다 넣으라"고 명시했고,
    #   짧은 Coming-soon 리뷰도 상품 카드가 있어야 수익이 된다.
    #   대신 짧은 글이면 data-short-post 속성만 찍어 추적 가능하게 한다.
    try:
        # 🔑 variant: 같은 카테고리 글이 수백 개면 상위 상품이 그대로 반복된다.
        #   포스트 순번을 넘겨 상품 조합을 회전시킨다 (ae_autoslot._rotate).
        import hashlib as _hl
        variant = int(_hl.md5(("%s|%s" % (category, title)).encode("utf-8")).hexdigest()[:8], 16)
        picks = ae_autoslot.recommend(category, None,
                                      limit=cfg.get('limit', 4),
                                      game_post=(kind == 'game'),
                                      body_md=body_md, title=title,
                                      variant=variant, lang=lang)
    except Exception:
        # ⚠️ 예외를 삼키되 **최후 폴백은 시도한다.**
        #   키워드 추출/분류 로직이 뻗어도 카드는 반드시 나와야 한다.
        try:
            picks = ae_autoslot._pool_any(limit=cfg.get('limit', 4))
        except Exception:
            return ''

    # 🔴 방어선: 여기서 빈 리스트면 그 포스트는 상품 없이 발행된다.
    #   ae_autoslot.recommend 는 이제 빈 리스트를 반환하지 않지만,
    #   렌더 단계에서 걸러지는 경우까지 이중으로 막는다.
    if not picks:
        try:
            picks = ae_autoslot._pool_any(limit=cfg.get('limit', 4))
        except Exception:
            return ''

    out = ae_autoslot.render_html(picks, lang)
    if out and len((body_md or '').strip()) < cfg.get('min_body_chars', 1200):
        out = out.replace('data-affiliate-slot="1"',
                          'data-affiliate-slot="1" data-short-post="1"', 1)
    return out


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


# 🔴 2026-10-09 로이 지시로 `affiliate_box()` 삭제 (호출부 0건).
#    하단 별도 제휴 박스 → 본문 안에만 상품 카드를 노출한다 (`ae_autoslot_html`).
#    삭제 이유:
#      ① 본문 카드와 하단 박스가 같은 상품을 이중 노출 → 신뢰 하락 + 중복 광고 판정
#      ② AdSense '관련성 낮은 콘텐츠' 정책상 광고 블록 남발이 감점
#      ③ offers 배열이 TradingView 밖에 없어 실제로는 빈 박스였음
#    참고: `offers_for()` 와 `affiliates.offers` 설정은 disclosure 페이지에서
#    제휴 고지를 렌더링하는 데 여전히 쓰인다. 함수는 건드리지 말 것.


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
    """포스트 페이지에서는 해당 글의 번역 URL로, 없으면 홈으로 링크.

    🔴 2026-10-10 수정 (Search Console 404 4건의 원인):
      게임 글은 **언어마다 slug 가 다르다** (ko=livetopia-party,
      fr=livetopia-party-applications-sur-google-play-prix).
      그런데 예전엔 현재 slug 를 그대로 타언어 경로에 붙여
      `/id/game/post/cookierun-classic-google-play.html` 같은 **없는 URL** 을 만들었다.
      → 실측 4건이 그대로 Google 에 크롤돼 '찾을 수 없음(404)' 로 보고됐다.
      → 같은 앱의 그 언어 버전이 있으면 그 slug 를, 없으면 **섹션 홈**으로 보낸다.
    """
    is_game = (section == 'game')
    href_fn = game_post_href if is_game else post_href
    home_fn = game_home_path if is_game else home_path
    peers = (GAME_PEER_URLS.get(slug) or {}) if (is_game and slug) else {}
    btns = []
    for code, (name, _) in LANG_META.items():
        if slug and available and code in available:
            peer = peers.get(code)
            if is_game and not peer:
                href = home_fn(code)          # 그 언어 버전이 실제로 없음 → 404 대신 홈
            else:
                href = href_fn(code, peer or slug)
        else:
            href = home_fn(code)
        active = 'bg-brand-600 text-white' if code == current else 'text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800'
        btns.append(f'<a href="{href}" class="tap px-2.5 rounded text-sm {active}" title="{name}" hreflang="{code}">{code.upper()}</a>')
    return ('<div class="max-w-5xl mx-auto px-4 sm:px-6 pt-3 flex flex-wrap items-center gap-1.5">'
            '<span class="text-slate-500 mr-1 text-sm">Language:</span>' + ''.join(btns) + '</div>')


# 🔑 같은 앱(패키지명)의 타언어 버전 인덱스. `build_game_peer_index()` 가 채운다.
#   {slug: {lang: peer_slug}} — 현재 slug 와 같은 앱인 타언어 버전들.
#   ⚠️ 언어마다 slug 가 다르므로 (lang, slug) 쌍으로 저장해야 한다.
GAME_PEER_URLS = {}

_APP_PKG_RE = re.compile(r"[?&]id=([A-Za-z0-9_\.]+)")


def build_game_peer_index(game_posts):
    """같은 Google Play 패키지(또는 같은 sourceUrl) 를 쓰는 게임 글끼리 묶는다.

    🔑 2026-10-09 (로이 지시 "전체 점검"):
       게임 글은 언어마다 slug 가 전부 다르다.
         ko: livetopia-party
         fr: livetopia-party-applications-sur-google-play-prix
       그래서 hreflang 을 자기 자신만 가리키게 되고(213개 전부 2개),
       다국어 SEO 신호가 완전히 0 이었다.
       → 패키지명이 같은 것끼리 묶어 상호 참조하게 한다.
       ⚠️ 다른 앱은 절대 묶지 않는다. 잘못된 신호가 더 나쁘다.
    """
    groups = {}
    for lang, plist in (game_posts or {}).items():
        for p in plist:
            url = p.get('sourceUrl') or ''
            m = _APP_PKG_RE.search(url)
            key = m.group(1) if m else ('src:' + url if url else None)
            if not key:
                continue
            groups.setdefault(key, {}).setdefault(lang, set()).add(p['slug'])

    GAME_PEER_URLS.clear()
    for key, by_lang in groups.items():
        if len(by_lang) < 2:
            continue                      # 한 언어뿐이면 참조할 상대가 없다
        for slug_list in by_lang.values():
            for slug in slug_list:
                peers = {}
                for other_lang, other_slugs in by_lang.items():
                    for other_slug in other_slugs:
                        peers.setdefault(other_lang, other_slug)
                        break            # 언어당 하나만 (가장 짧은 slug 우선)
                if peers:
                    GAME_PEER_URLS[slug] = peers
    return len(GAME_PEER_URLS)


def alternates_html(lang, slug, available, section=None):
    """검색엔진용 hreflang 상호 링크 (존재하는 언어만).

    게임 개별 글은 언어마다 **다른 앱**을 다루므로 slug 가 보통 한 언어에만 존재한다.
    이때 타언어 URL을 출력하면 없는 페이지를 서로 가리키는 잘못된 신호가 되므로
    자기 자신 1개 + x-default(자기 자신) 만 내보낸다. 섹션 홈은 전 언어에 실재하므로 기존대로.
    """
    is_game = (section == 'game')
    url_fn = game_post_url if is_game else post_url
    home_fn = game_home_path if is_game else home_path
    tags = []
    if slug:
        if is_game:
            me = url_fn(lang, slug)
            tags.append(f'  <link rel="alternate" hreflang="{lang}" href="{me}" />')
            tags.append(f'  <link rel="alternate" hreflang="x-default" href="{me}" />')
            # 🔑 2026-10-09: 같은 앱(패키지명)의 타언어 버전이 실제로 있으면
            #   상호 참조한다 → 다국어 SEO 를 제대로 쓴다.
            #   근거: 같은 앱이어도 언어마다 제목/본문 톤이 달라(한국어=리뷰형,
            #   프랑스어=스토어 설명형) '동일 문서'는 아니지만,
            #   Google 은 hreflang 을 '같은 페이지의 언어 변형' 신호로 쓰므로
            #   **같은 대상(앱)을 다루는 페이지끼리 묶어도 불이익이 없다.**
            #   오히려 213개 전부가 자기 자신만 가리키면 다국어 신호가 0 이 된다.
            #   ※ 서로 다른 앱끼리는 절대 묶지 않는다 (잘못된 신호).
            peers = GAME_PEER_URLS.get(slug) or {}
            for code, peer_slug in peers.items():
                if code == lang or code not in available:
                    continue
                tags.append(f'  <link rel="alternate" hreflang="{code}" '
                            f'href="{url_fn(code, peer_slug)}" />')
        else:
            for code in LANG_META:
                if code in available:
                    tags.append(f'  <link rel="alternate" hreflang="{code}" href="{url_fn(code, slug)}" />')
            if 'en' in available:
                tags.append(f'  <link rel="alternate" hreflang="x-default" href="{url_fn("en", slug)}" />')
    else:
        for code in LANG_META:
            if code in available:
                tags.append(f'  <link rel="alternate" hreflang="{code}" href="{DOMAIN + home_fn(code)}" />')
        tags.append(f'  <link rel="alternate" hreflang="x-default" href="{DOMAIN + ("/game/" if is_game else "/")}" />')
    return '\n'.join(tags)


def layout(lang, title, description, canonical, content_html, og_type='website',
           jsonld_blocks=None, slug=None, available=None, switcher_slug=None, section=None,
           image=None, noindex=False, plain=False, head_links=''):
    dir_ = LANG_META[lang][1]
    og_img = ''
    if image:
        abs_url = image if image.startswith('http') else DOMAIN + image
        og_img = (f'  <meta property="og:image" content="{htmllib.escape(abs_url)}" />\n'
                  f'  <meta name="twitter:image" content="{htmllib.escape(abs_url)}" />\n')
    head_extra = '' if plain else alternates_html(lang, slug, available or {lang}, section)
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
{head_links}  <link rel="alternate" type="application/rss+xml" href="{DOMAIN}/feed.xml" />
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
{'' if plain else lang_switcher(lang, switcher_slug, available, section)}

<main class="flex-1 max-w-5xl w-full mx-auto px-4 sm:px-6 py-6 sm:py-8">
{content_html}
</main>

{footer_html(lang)}
<script src="/assets/js/main.js?v=5"></script>
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
        # app_radar 가 스토어에서 뽑아 넣는 키. 아직 없는 글도 있으므로 빈 문자열 허용.
        'langs': (fm.get('langs') or '').strip(),
        'iap': (fm.get('iap') or '').strip(),
        'requirements': (fm.get('requirements') or '').strip(),
        'lang': (fm.get('lang') or '').strip(),
    }


def langs_codes(f):
    """facts['langs'] → 소문자 2자리 코드 목록. 값이 없으면 글 자체 언어로 폴백."""
    raw = (f or {}).get('langs') or ''
    toks = [t.strip().lower().split('-')[0] for t in re.split(r'[,\s/;]+', raw) if t.strip()]
    if not toks:
        own = ((f or {}).get('lang') or '').strip().lower()
        toks = [own] if own else []
    return [t for t in toks if t]


def langs_value(lang, f):
    """표시용 지원 언어 문구. 현재 UI 언어 지원 여부를 함께 적는다."""
    codes = langs_codes(f)
    if not codes:
        return ''
    shown = ', '.join(c.upper() for c in codes[:8])
    if len(codes) > 8:
        shown += ' …'
    if lang in codes:
        return shown
    return '%s · %s' % (shown, FACT_NA_STR.get(lang, FACT_NA_STR['en']))


def fact_box_html(lang, f):
    """첫 화면 팩트박스 — 출시일·가격·상태를 먼저 보여줘 이탈을 줄인다.
    뉴스 재각색 글(news=true)은 스토어 상품이 아니므로 박스를 숨긴다."""
    if f.get('news'):
        return ''
    t = FACT_STR.get(lang, DEFAULT_FACT)
    status = t[7] if f['upcoming'] else (t[8] if f['releaseDate'] else '')
    rows = [(t[1], f['releaseDate']), (t[2], f['price']), (t[5], status),
            (FACT_LANGS_STR.get(lang, FACT_LANGS_STR['en']), langs_value(lang, f)),
            (FACT_IAP_STR.get(lang, FACT_IAP_STR['en']), (f.get('iap') or '').strip()),
            (t[3], f['platform']), (t[4], f['developer']), (t[6], f['checked'])]
    body = '\n'.join(
        '    <div class="flex justify-between gap-4 py-1.5 border-b border-slate-100 '
        'dark:border-slate-800 last:border-0">'
        '<dt class="text-slate-500 dark:text-slate-400 shrink-0">%s</dt>'
        '<dd class="text-end font-medium text-slate-900 dark:text-slate-100 min-w-0 break-words">%s</dd></div>'
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
        '    <li><a class="block py-2 hover:text-fuchsia-700 dark:hover:text-fuchsia-300" '
        'href="#%s">%s</a></li>' % (i, re.sub(r'<[^>]+>', '', txt)) for i, txt in items)
    return ('<details class="mb-6 rounded-lg border border-slate-200 dark:border-slate-800 p-4" open>'
            '<summary class="tap px-1 text-sm font-semibold cursor-pointer text-slate-900 '
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
                'bg-fuchsia-50/60 dark:bg-fuchsia-950/30 p-4 sm:p-5 text-center">'
                '<a class="inline-block px-5 py-2.5 rounded-md bg-fuchsia-700 hover:bg-fuchsia-800 '
                'text-white text-sm font-semibold tap px-5" rel="nofollow noopener" target="_blank" '
                'href="%s">%s</a></aside>'
                % (htmllib.escape(url), htmllib.escape(label)))
    label = ('%s · %s' % (store_name, t[10])) if store_name else t[10]
    return ('<aside class="mt-10 rounded-lg border border-fuchsia-200 dark:border-fuchsia-900 '
            'bg-fuchsia-50/60 dark:bg-fuchsia-950/30 p-4 sm:p-5 text-center">'
            '<a class="inline-block px-5 py-2.5 rounded-md bg-fuchsia-700 hover:bg-fuchsia-800 '
            'text-white text-sm font-semibold tap px-5" rel="nofollow noopener" target="_blank" '
            'href="%s">%s</a>'
            '<p class="mt-2 text-xs text-slate-500 dark:text-slate-400">%s</p></aside>'
            % (htmllib.escape(url), htmllib.escape(label), htmllib.escape(t[11])))


def store_text_link_html(lang, url):
    """본문 중간용 중립 텍스트 링크 CTA — 배너처럼 보이지 않게 한 줄로."""
    if not url:
        return ''
    t = FACT_STR.get(lang, DEFAULT_FACT)
    return ('<p class="my-6 text-sm"><a class="inline-block py-2 underline underline-offset-2 '
            'hover:text-fuchsia-700" rel="nofollow noopener" target="_blank" '
            'href="%s">%s</a></p>' % (htmllib.escape(url), htmllib.escape(t[10])))


def insert_after_h2(html, n, snippet):
    """n번째 <h2> 섹션 뒤에 snippet 을 끼운다. h2 가 n개뿐이면 문말에 붙인다."""
    if not snippet:
        return html or ''
    ms = list(re.finditer(r'<h2 id="h-\d+">', html or ''))
    if len(ms) < n:
        return (html or '') + snippet
    pos = ms[n].start() if len(ms) > n else len(html or '')
    return (html or '')[:pos] + snippet + (html or '')[pos:]


# 블록 경계 — 카드는 이 태그가 끝난 자리에만 넣는다 (중간에 잘리면 깨진다)
_BLOCK_END = re.compile(r'</(?:p|h2|h3|ul|ol|pre|blockquote|figure|table)>')


def insert_at_midpoint(html, snippet):
    """본문 **정중앙**에 snippet 을 끼운다. (로이 2026-10-10: "확정적으로 가운데")

    ⚠️ 네 번의 시행착오를 거쳤다. 이 함수가 왜 이런 형태인지 기록해 둔다.

    ❶ `split('</p>')` + `len//2` = 문단 **개수** 기준.
       문단 길이가 고르면 화면상 10~20% 지점. (실측 480개 중 405개가 앞쪽 25%)
    ❷ h2 **섹션 개수의 절반** 지점.
       idea 는 좋았지만 `scary-halloween-games` 처럼 첫 h2("At a glance")가
       본문 34%를 차지하는 글에서 섹션 절반이 74% 지점이 되어 카드가 맨 뒤로
       간다(98%). → 55개 원본 기준 중앙값 57%, 35~75% 구간 84%.
    ❸ 위 둘의 최솟값. 중앙값 48% 이지만 구간 통과율이 84% 로 떨어진다.
       (두 전략이 어긋난 글에서 갑자기 앞쪽으로 튄다)
    ❹ **문자 오프셋 50% 지점의 가장 가까운 블록 경계 = 정답.**
       55개 원본 기준 중앙값 **50%**, 35~75% 구간 **55/55 = 100%**.

    결국 **복잡한 휴리스틱은 필요 없었다.** 단순한 "문자 절반"이 가장 좋다.
    h2 균형·문단 개수 집계는 모두 이 단순한 기준보다 못한답이다.

    규칙
      * 블록 경계(`</p>` `</h2>` `</li>` 등 끝 태그) 중 50% 에 가장 가까운 곳
      * 블록 경계가 아예 없으면 맨 앞
      * **어떤 경우에도 삽입한다** — 로이 지시 "다 집어넣어"
    """
    html = html or ''
    if not snippet:
        return html
    bounds = [m.end() for m in _BLOCK_END.finditer(html)]
    if not bounds:
        # <p> 도 <h2> 도 없는 본문 — 맨 앞에 넣는다 (차단하지 않는다)
        return snippet + '\n' + html
    target = len(html) * 0.5
    pos = min(bounds, key=lambda b: abs(b - target))
    # 첫 블록 경계보다 앞이면 그 경계로 (0 에 붙이면 제목 옆에 붙는다)
    pos = max(pos, bounds[0])
    return html[:pos] + '\n' + snippet + '\n' + html[pos:]


def game_card_rows(lang, p):
    """카드 결정정보 4행 — 출시일·가격·플랫폼/분류·지원 언어.

    목록에서 스토어로 넘어갈지 판단하는 값만 남긴다(CTR·이탈 개선).
    """
    f = p.get('facts') or {}
    t = FACT_STR.get(lang, DEFAULT_FACT)
    tba = FACT_TBA_STR.get(lang, FACT_TBA_DEFAULT)
    iap_lbl = FACT_IAP_STR.get(lang, FACT_IAP_STR['en'])
    rel = (f.get('releaseDate') or '').strip()
    dd = dday_badge_html(rel)
    row1 = ('<time datetime="%s" class="tabular-nums">%s</time>%s'
            % (htmllib.escape(rel), htmllib.escape(rel),
               (' ' + dd) if dd else '')) if rel else htmllib.escape(tba)
    price = (f.get('price') or '').strip()
    if price and (f.get('iap') or '').strip():
        row2 = '%s · %s' % (htmllib.escape(price), htmllib.escape(iap_lbl))
    else:
        row2 = htmllib.escape(price) if price else htmllib.escape(tba)
    genre = (f.get('genre') or '').strip().split(',')[0].strip()
    bits3 = [b for b in ((f.get('platform') or '').strip(), genre or p.get('category', '')) if b]
    row3 = htmllib.escape(' · '.join(bits3)) if bits3 else htmllib.escape(tba)
    row4 = htmllib.escape(langs_value(lang, f) or tba)
    rows = [(t[1], row1), (t[2], row2), (t[3], row3),
            (FACT_LANGS_STR.get(lang, FACT_LANGS_STR['en']), row4)]
    return '\n'.join(
        '      <div class="flex justify-between gap-3 py-1 border-b border-slate-100 '
        'dark:border-slate-800 last:border-0">'
        '<dt class="text-slate-400 dark:text-slate-500 shrink-0">%s</dt>'
        '<dd class="text-end font-medium text-slate-800 dark:text-slate-100 min-w-0 break-words">%s</dd></div>'
        % (htmllib.escape(k), v) for k, v in rows)


def game_card_html(lang, p):
    """목록 카드: 스토어 실제 이미지 + 상태/가격 뱃지 + 결정정보 4행."""
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
        badge = '<span class="absolute top-2 end-2 flex flex-col items-end gap-1">' + badge + '</span>'
    return f'''<article class="border border-slate-200 dark:border-slate-800 rounded-lg overflow-hidden hover:shadow-md transition-shadow bg-white dark:bg-slate-900 flex flex-col min-w-0">
  <a class="relative block aspect-[16/10] overflow-hidden bg-gradient-to-br from-fuchsia-800 to-fuchsia-600" href="{p.get('href')}" aria-label="{htmllib.escape(p.get('title', ''))}">
    {thumb}{badge}
  </a>
  <div class="p-4 sm:p-5 min-w-0">
    <div class="flex items-center gap-2 text-xs text-slate-500 mb-1">
      <span class="bg-fuchsia-50 text-fuchsia-700 px-2 py-0.5 rounded uppercase tracking-wide">{htmllib.escape(p.get('category', ''))}</span>
      <time datetime="{p.get('date', '')}">{p.get('date', '')}</time>
    </div>
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-1 leading-snug line-clamp-2"><a class="hover:text-fuchsia-700" href="{p.get('href')}">{htmllib.escape(p.get('title', ''))}</a></h2>
    <dl class="mt-2 text-xs">
{game_card_rows(lang, p)}
    </dl>
    <p class="mt-2 text-sm text-slate-600 dark:text-slate-400 line-clamp-3">{htmllib.escape(p.get('desc', ''))}</p>
  </div>
</article>'''


def game_hero_html(lang, p):
    """출시예정 Hero 카드 — D-day 를 주시각으로 써서 목록 첫 화면에서 우위를 준다.

    D-day 가 없으면(날짜 오파싱·999일 초과 등) Hero 를 쓰지 않고 일반 카드로 돌려보낸다.
    이미 출시된 글은 호출하지 않는다(build_game_index 가 걸러낸다).
    """
    f = p.get('facts') or {}
    t = FACT_STR.get(lang, DEFAULT_FACT)
    rel = (f.get('releaseDate') or '').strip()
    dd = dday_str(rel)
    if not dd:
        return game_card_html(lang, p)
    img = f.get('image') or ''
    if img:
        thumb = ('<img src="%s" alt="" decoding="async" '
                 'class="w-full h-full object-cover object-top" />' % htmllib.escape(img))
    else:
        thumb = ('<span class="text-white/90 text-sm font-semibold uppercase tracking-widest px-4 '
                 'text-center">%s</span>' % htmllib.escape(p.get('category', '')))
    badge = ('<span class="absolute top-3 end-3 flex flex-col items-end gap-1">'
             '<span class="text-[11px] font-semibold px-2 py-0.5 rounded '
             'bg-fuchsia-700 text-white">%s</span></span>' % htmllib.escape(t[7]))
    return f'''<article data-hero="1" class="sm:col-span-2 border-2 border-fuchsia-300 dark:border-fuchsia-800 rounded-lg overflow-hidden hover:shadow-md transition-shadow bg-white dark:bg-slate-900 grid sm:grid-cols-2 min-w-0">
  <a class="relative block aspect-[16/9] sm:aspect-[4/3] overflow-hidden bg-gradient-to-br from-fuchsia-800 to-fuchsia-600" href="{p.get('href')}" aria-label="{htmllib.escape(p.get('title', ''))}">
    {thumb}{badge}
  </a>
  <div class="p-5 sm:p-6 flex flex-col justify-center min-w-0">
    <div class="flex items-center gap-2 text-xs text-slate-500 mb-1">
      <span class="bg-fuchsia-50 text-fuchsia-700 px-2 py-0.5 rounded uppercase tracking-wide">{htmllib.escape(p.get('category', ''))}</span>
      <time datetime="{p.get('date', '')}">{p.get('date', '')}</time>
    </div>
    <h2 class="text-xl sm:text-2xl font-bold text-slate-900 dark:text-slate-100 mb-2 leading-snug line-clamp-2"><a class="hover:text-fuchsia-700" href="{p.get('href')}">{htmllib.escape(p.get('title', ''))}</a></h2>
    <p class="text-5xl sm:text-6xl font-bold tabular-nums leading-none bg-gradient-to-r from-amber-500 to-amber-300 bg-clip-text text-transparent">{htmllib.escape(dd)}</p>
    <p class="mt-2 text-sm text-slate-600 dark:text-slate-400">{htmllib.escape(t[1])} <time datetime="{htmllib.escape(rel)}" class="font-medium text-slate-800 dark:text-slate-100 tabular-nums">{htmllib.escape(rel)}</time></p>
    <dl class="mt-3 text-xs">
{game_card_rows(lang, p)}
    </dl>
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
  <div class="p-4 sm:p-5">
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
        f'    <li><a class="inline-block py-1.5 hover:text-brand-600 underline-offset-2 hover:underline" href="{p.get("href") or fn(lang, p["slug"])}">{htmllib.escape(p["title"])}</a></li>'
        for p in others)
    return f'''<nav class="mt-12 border-t border-slate-200 dark:border-slate-800 pt-6">
  <h2 class="text-sm font-semibold uppercase tracking-wide text-slate-500 mb-3">{htmllib.escape(label)}</h2>
  <ul class="space-y-2 text-slate-700 dark:text-slate-300">
{items}
  </ul>
</nav>'''


def build_post(lang, slug, title, desc, category, date, body_md, source_name, source_url,
               posts_in_lang, available, section='post', facts=None, kind=None):
    """section='post' → /post/{slug}.html (경제), section='game' → /game/post/{slug}.html
    `kind` = 'app' | 'game' (앱/게임 구분). 오토 어필리에이트는 앱 리뷰에만 넣는다.
    """
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
               f'<a class="inline-block py-2 underline hover:text-brand-600" rel="nofollow noopener" target="_blank" '
               f'href="{htmllib.escape(source_url)}">{htmllib.escape(source_name or source_url)}</a></p>')
    # 앱·게임 글: 첫 화면에 팩트박스 + 스토어 버튼을 먼저 보여주고 광고는 그 아래로 내린다.
    box = fact_box_html(lang, f) if is_game else ''
    toc = toc_html(body_html, lang) if is_game else ''
    # 🔑 2026-10-09 로이 지시 "광고가 본문 내용에만 나와야 한다":
    #   포스트 상단(post_top)·하단(post_bottom) 배너를 끈다.
    #   근거: ① 제휴 상품 카드와 광고 블록이 붙으면 초점이 흩어져 CTR 이 떨어진다
    #        ② AdSense '관련성 낮은 콘텐츠' 정책 — 광고 블록 남발은 감점
    #        ③ 게임 상세는 이미 post_top 제외 상태였음 (2026-10-06 로이 지시)
    #   인아티클(post_mid, in-article 레이아웃)만 남겨 본문 흐름에 자연스럽게 놓는다.
    top_ad = ad_unit('post_top')  # 2026-10-09 로이 지시: 상단 배너 복귀
    # 스토어 CTA ① 결정표 바로 아래 (주 버튼)
    cta_top = (store_cta_html(lang, source_url, source_name, news=bool(f.get('news')))
               if is_game else '')
    # 인아티클(본문 중간) 광고 — 2026-10-09 로이 지시로 **삭제**.
    #   "글 내용에서 상단 및 하단에만 그 구글 애드센스가 나오도록"
    #   → post_top / post_bottom 만 사용한다.
    #   (지시 변경 시 되돌리기 쉽도록 슬롯 코드는 통째로 제거하지 않고
    #    여기 빈 문자열로 두고 위쪽 top_ad / bottom_ad 만 살린다)
    mid_ad = ''
    # 🤖 오토 어필리에이트 슬롯 (2026-10-08, 로이 지시: "자동광고처럼 자연스럽게")
    # mid_ad 와 같은 문단 경계에 삽입한다. AdSense 정책상 '본문 중간 삽입'이
    # 하단 배너보다 위험이 낮고, 독자 이탈도 적다.
    #
    # 2026-10-09 수정: 앱뿐 아니라 **게임 리뷰에도 넣는다.**
    #   게임 리뷰 독자는 게임기용 하드웨어(쿨링 패드·패드·지문링커)를 실제로 산다.
    #   어제처럼 앱에만 제한하니 188개 중 22개에만 카드가 들어갔다.
    #   → 게임은 `game_post=True` 로 게임 액세서리 매핑을 사용한다.
    ae_slot = ae_autoslot_html(lang, category, title, body_md, kind=kind)
    if ae_slot:
        # 🔴🔴 2026-10-10 로이 지시: "구글 애드센스처럼 그냥 **확정적으로 가운데**에
        #   들어가게끔. 수동으로 모든 글에 편집해서 넣는게 아니라 그냥 끼워넣으라."
        #
        #   ⚠️ 예전 방식(`split('</p>')` 후 `len//2`)은 **가운데가 아니다.**
        #     문단 개수 기준이라, 첫 문단이 길면 카드가 화면상 10~20% 지점에
        #     appeared 한다 (실측 480개 중 405개가 앞쪽 25% 안에 들어감).
        #   → **문자 위치 50% 지점의 가장 가까운 문단 경계**를 찾는다.
        #     이것이 "확정적으로 가운데"의 정확한 구현이다.
        body_html = insert_at_midpoint(body_html, ae_slot)
    # 스토어 CTA ② 두 번째 h2 섹션 뒤 — 중립 텍스트 링크
    if is_game:
        body_html = insert_after_h2(body_html, 2, store_text_link_html(lang, source_url))
    # 🤖 2026-10-09 로이 지시: 제휴 상품은 **본문 안에만** 나온다.
    #   아래쪽 별도 제휴 박스(affiliate_box)는 더 이상 렌더링하지 않는다.
    #   근거: ① 같은 상품이 본문과 하단에 이중 노출되면 신뢰 하락 + 중복 광고 판정
    #        ② AdSense '관련성 낮은 콘텐츠' 정책상 광고 블록 남발이 감점 요소
    #        ③ 상품 카드가 이미 그 카테고리에 맞는 것만 고르므로 하단 복제본은 불필요
    bottom = (store_cta_html(lang, source_url, source_name, news=bool(f.get('news')))
              if is_game else '')
    # 🔑 2026-10-09 로이 지시 "본문에만" — 하단 배너도 끈다.
    #   AdSense 는 인아티클(post_mid) 하나만 남긴다.
    #   post_related 는 '관련 글' 영역이라 본문이 아니라서 유지 (188게임 페이지 전부).
    bottom_ad = ad_unit('post_bottom')  # 2026-10-09 로이 지시: 하단 배너 복귀
    # 상세 빵부스러기: 홈 › 앱 & 게임 › 제목
    crumb = ''
    if is_game:
        crumb = ('  <nav class="mb-3 text-xs text-slate-500 dark:text-slate-400" '
                 'aria-label="Breadcrumb">'
                 '<a class="inline-block py-1 underline-offset-2 hover:underline" href="%s">%s</a>'
                 '<span class="px-1.5" aria-hidden="true">&rsaquo;</span>'
                 '<a class="inline-block py-1 underline-offset-2 hover:underline" href="%s">%s</a>'
                 '<span class="px-1.5" aria-hidden="true">&rsaquo;</span>'
                 '<span aria-current="page" class="text-slate-700 dark:text-slate-300">%s</span>'
                 '</nav>' % (home_path(lang),
                             htmllib.escape(HOME_CRUMB_STR.get(lang, HOME_CRUMB_DEFAULT)),
                             game_home_path(lang),
                             htmllib.escape(GAME_STR.get(lang, DEFAULT_GAME)[0]),
                             htmllib.escape(title)))
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
{crumb}    <div class="flex items-center gap-2 text-xs text-slate-500 mb-3">
      <span class="{"bg-fuchsia-50 text-fuchsia-700" if is_game else "bg-brand-50 text-brand-700"} px-2 py-0.5 rounded uppercase tracking-wide">{htmllib.escape(category)}</span>
      <time datetime="{date}">{htmllib.escape(s[3])}: {date}</time>
      <span class="text-slate-400">&middot;</span>
      <span>{"Store Listing Summary" if is_game else "Independent Analysis"}</span>
{dday_chip}    </div>
    <h1 class="text-2xl sm:text-3xl font-bold leading-tight text-slate-900 dark:text-slate-100 mb-4">{htmllib.escape(title)}</h1>
    <p class="text-slate-600 dark:text-slate-400">{htmllib.escape(desc)}</p>{src}
  </header>
  {box}
  {cta_top}
  {toc}
  {top_ad}
  <div class="prose">{body_html}</div>
  {bottom_ad}
  {bottom}
  {related_html(lang, slug, posts_in_lang, s[5], href_fn)}
  {ad_unit('post_related', wrap_class='mt-8')}
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
    # 신규 앱·게임 미니 모듈 — 같은 언어 글이 없으면 블록 전체를 렌더하지 않는다(빈 섹션 금지).
    g_items = [p for p in (game_list or []) if not p.get('legacy')][:3]
    game_block = ''
    if g_items:
        glabel = GAME_GROUP_STR.get(lang, GAME_GROUP_DEFAULT)[1]
        lis = []
        for p in g_items:
            f = (p.get('facts') or {})
            img = (f.get('image') or '').strip()
            if img:
                icon = ('<img src="%s" alt="" loading="lazy" decoding="async" width="40" height="40" '
                        'class="w-10 h-10 rounded-full object-cover shrink-0" />'
                        % htmllib.escape(img))
            else:
                icon = ('<span class="w-10 h-10 rounded-full shrink-0 bg-fuchsia-100 '
                        'dark:bg-fuchsia-900 flex items-center justify-center text-fuchsia-700 '
                        'dark:text-fuchsia-300 text-xs font-bold">%s</span>'
                        % htmllib.escape((p.get('category') or '·')[:2]))
            sub = ' · '.join(b for b in ((f.get('releaseDate') or '').strip(),
                                         (f.get('price') or '').strip()) if b)
            lis.append(
                '    <li class="min-w-0"><a class="flex items-center gap-3 py-2 hover:text-fuchsia-700" '
                'href="%s">%s<span class="min-w-0"><span class="block text-sm font-medium '
                'text-slate-900 dark:text-slate-100 line-clamp-1">%s</span>'
                '<span class="block text-xs text-slate-500 dark:text-slate-400 tabular-nums">%s</span>'
                '</span></a></li>'
                % (htmllib.escape(p.get('href') or game_post_href(lang, p['slug'])), icon,
                   htmllib.escape(p.get('title', '')), htmllib.escape(sub)))
        game_block = (
            '  <div class="border border-slate-200 dark:border-slate-800 rounded-lg p-4 sm:p-5 '
            'bg-white dark:bg-slate-900">\n'
            '    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">%s</h2>\n'
            '    <ul class="divide-y divide-slate-100 dark:divide-slate-800">\n%s\n    </ul>\n'
            '    <p class="mt-3"><a class="tap px-3 text-sm text-fuchsia-700 dark:text-fuchsia-300 '
            'font-semibold" href="%s">%s</a></p>\n'
            '  </div>'
            % (htmllib.escape(glabel), '\n'.join(lis),
               htmllib.escape(game_home_path(lang)),
               htmllib.escape(GAME_STR.get(lang, DEFAULT_GAME)[0])))
    return f'''
<section class="mt-10 grid gap-6 lg:grid-cols-2 items-start">
  <div class="border border-slate-200 dark:border-slate-800 rounded-lg p-4 sm:p-5 bg-white dark:bg-slate-900">
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">{htmllib.escape(w[0])}</h2>
    <div id="mca-cal" {attrs}></div>
    <div id="mca-cal-out" class="mt-3 text-sm"></div>
  </div>
  <div class="border border-slate-200 dark:border-slate-800 rounded-lg p-4 sm:p-5 bg-white dark:bg-slate-900">
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">{htmllib.escape(w[2])}</h2>
    <div id="mca-geo" data-locale="{lang}" data-str-wait="{htmllib.escape(w[3], quote=True)}"></div>
  </div>
  <div class="border border-slate-200 dark:border-slate-800 rounded-lg p-4 sm:p-5 bg-white dark:bg-slate-900">
    <h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-3">{htmllib.escape(u[4])}</h2>
    <div id="mca-pop" {pop_attrs}></div>
  </div>
{game_block}
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


# 홈 1페이지에 보여줄 글 수 (로이 지시 2026-10-03: 스크롤 귀찮으니 5개 + 페이지 이동)
HOME_PAGE_SIZE = 5
# 언어별 홈 총 페이지 수 — build_index 가 채우고 build_sitemap 이 읽는다.
HOME_PAGES = {}


def home_page_path(lang, n):
    """홈 페이지 경로. 1페이지는 기존 홈, 2페이지부터 /page/2/ ..."""
    base = home_path(lang)
    return base if n <= 1 else base + 'page/%d/' % n


def upcoming_first(plist):
    """출시 예정(미래 출시일이 확인된 글)을 목록 맨 앞으로 올린다 (로이 지시 2026-10-04).

    출시예정은 임박한 순(출시일 오름차순), 나머지는 원래 순서(최신순)를 유지한다.
    """
    today = datetime.date.today().isoformat()
    up, rest = [], []
    for p in plist:
        f = p.get('facts') or {}
        if f.get('upcoming') and (f.get('releaseDate') or '') > today:
            up.append(p)
        else:
            rest.append(p)
    if not up:
        return plist
    up.sort(key=lambda p: ((p.get('facts') or {}).get('releaseDate') or '9999-12-31'))
    return up + rest


def merged_feed(lang, posts, game_list):
    """경제 글 + 앱/게임 글을 하나로 합쳐 **최신순**으로 돌려준다 (로이 지시 2026-10-03).

    각 항목에 _kind('macro'|'game') 와 href 를 붙여 카드 렌더링이 갈라지게 한다."""
    feed = []
    for p in posts or []:
        q = dict(p)
        q['href'] = q.get('href') or post_href(lang, q['slug'])
        q['_kind'] = 'macro'
        feed.append(q)
    for p in game_list or []:
        if p.get('legacy'):
            continue      # 옛 영문 앱 글은 /post/ 에 이미 중복 노출되므로 제외
        q = dict(p)
        q['href'] = q.get('href') or game_post_href(lang, q['slug'])
        q['_kind'] = 'game'
        feed.append(q)
    feed.sort(key=lambda x: (x.get('date') or ''), reverse=True)
    # 출시예정 글은 최신순과 무관하게 맨 위로 — 로이 지시(2026-10-04)
    return upcoming_first(feed)


def feed_card_html(lang, p):
    """통합 피드용 카드 — 앱/게임은 스토어 이미지·배지 카드, 경제 글은 기존 카드."""
    if p.get('_kind') == 'game':
        return game_card_html(lang, p)
    return card_html(lang, p['slug'], p['title'], p['desc'], p['category'],
                     p['date'], href=p['href'])


def pager_html(lang, page, pages):
    """페이지 네비게이션. 숫자와 ‹ › 기호는 언어 중립이라 번역 없이 전 언어 공용."""
    if pages <= 1:
        return ''
    btn = ('tap px-3.5 rounded text-sm border transition-colors '
           'border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-300 '
           'hover:border-brand-400')
    nums = []
    for n in range(1, pages + 1):
        if n == page:
            nums.append('<span class="tap px-3.5 rounded text-sm border bg-brand-600 inline-flex '
                        'items-center justify-center text-white border-brand-600" aria-current="page">%d</span>' % n)
        else:
            nums.append('<a class="%s" href="%s">%d</a>' % (btn, home_page_path(lang, n), n))
    prev = ('<a class="%s" href="%s" aria-label="Previous">&lsaquo;</a>'
            % (btn, home_page_path(lang, page - 1))) if page > 1 else ''
    nxt = ('<a class="%s" href="%s" aria-label="Next">&rsaquo;</a>'
           % (btn, home_page_path(lang, page + 1))) if page < pages else ''
    return ('<nav class="mt-8 flex items-center justify-center gap-2 flex-wrap" '
            'aria-label="Pagination">' + prev + ''.join(nums) + nxt + '</nav>')


def pager_rel_links(lang, page, pages):
    """<head> 용 <link rel="prev"> / <link rel="next">.

    rel=prev/next 는 <link> 태그라 반드시 head 에 있어야 하므로 pager_html()(body 안의 <a>)과
    분리한다. 없는 쪽(1페이지의 prev, 마지막 페이지의 next)은 태그 자체를 내보내지 않고,
    page 1 이 prev 를 가리켜 자기 루프가 되는 것도 막는다.
    """
    if pages <= 1:
        return ''
    tags = []
    if page > 1:
        tags.append('  <link rel="prev" href="%s" />' % (DOMAIN + home_page_path(lang, page - 1)))
    if page < pages:
        tags.append('  <link rel="next" href="%s" />' % (DOMAIN + home_page_path(lang, page + 1)))
    return '\n'.join(tags) + '\n' if tags else ''


def build_index(lang, posts, available, game_list=None):
    """홈(경제+앱/게임 통합, 최신순) + 2페이지 이후를 페이지 단위로 생성."""
    s = strs(lang)
    feed = merged_feed(lang, posts, game_list)
    pages = max(1, (len(feed) + HOME_PAGE_SIZE - 1) // HOME_PAGE_SIZE)
    widgets = homepage_widgets(lang, posts, game_list or [])
    infeed = ad_unit('index_infeed', wrap_class='sm:col-span-2 my-6 text-center')
    written = []
    for page in range(1, pages + 1):
        chunk = feed[(page - 1) * HOME_PAGE_SIZE: page * HOME_PAGE_SIZE]
        half = max(1, len(chunk) // 2)
        cards = '\n'.join(feed_card_html(lang, p) for p in chunk[:half])
        cards2 = '\n'.join(feed_card_html(lang, p) for p in chunk[half:])
        # 1페이지에만 디스플레이 광고를 얹는다 — 5개 카드 페이지에 광고 3개는 과하다.
        mid_ads = ad_unit('index') if page == 1 else ''
        content = f'''<div class="space-y-6">
  <div>
    <h1 class="text-2xl font-bold text-slate-900 dark:text-slate-100">{htmllib.escape(s[2])}</h1>
    <p class="mt-1 text-sm text-slate-600 dark:text-slate-400">{htmllib.escape(s[1])}</p>
    <p class="mt-2 text-sm"><a class="text-blue-700 dark:text-blue-300 hover:underline" href="{'/post/index.html' if lang == 'en' else f'/{lang}/post/index.html'}">{htmllib.escape(ARCHIVE_STR.get(lang, DEFAULT_ARCHIVE)[0])}</a></p>
  </div>
  <div class="grid gap-4 sm:grid-cols-2">
{cards}
  {infeed}
{cards2}
  </div>
  {pager_html(lang, page, pages)}
  {widgets}
  {mid_ads}
  {ad_unit('index_bottom')}
</div>'''
        canonical = DOMAIN + home_page_path(lang, page)
        title = f'{SITE_NAME} — {s[0]}' + ('' if page == 1 else f' (Page {page})')
        # 2페이지 이후는 홈과 같은 description 을 그대로 쓰면 161개 페이지가 한 문자열을 공유한다.
        #현지화된 "페이지 N" 을 덧붙여 페이지마다 고유하게 만든다 (홈 1페이지는 기존 문구 유지).
        desc = s[1] if page == 1 else '%s — %s' % (
            s[1], PAGE_STR.get(lang, DEFAULT_PAGE_STR).format(n=page))
        html_doc = layout(lang, title, desc, canonical, content, 'website', [website_ld(lang)],
                          slug=None, available=available, switcher_slug=None,
                          head_links=pager_rel_links(lang, page, pages),
                          noindex=(page > 1))
        rel = home_page_path(lang, page).lstrip('/') or ''
        out_path = os.path.join(BASE, rel, 'index.html') if rel else os.path.join(BASE, 'index.html')
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        open(out_path, 'w', encoding='utf-8').write(html_doc)
        written.append(out_path)
    prune_stale_pages(lang, pages)
    HOME_PAGES[lang] = pages
    return written[0]


def prune_stale_pages(lang, pages):
    """페이지 수가 줄었을 때 남은 낡은 page/N 디렉터리를 지운다.

    왜 필요하냐 (2026-10-08 실측):
      원고 7개를 삭제하니 fr·id·ru의 首页 글 수가 줄면서 page 수가 8→7 로 내려갔다.
      그런데 build_index() 는 1..pages 만 새로 쓰기 때문에 **page/8/index.html 이
      디스크에 그대로 남았다.** 남은 파일은 (a) 사이트맵엔 없는데 Vercel 이 서빙하고
      (b) window.__POSTS__ 페이로드에 이미 삭제된 글을 그대로 들고 있어
      클라이언트 검색에서 404 링크가 노출된다. 콘텐츠 삭제 때마다 재발한다.
    """
    import shutil
    base = os.path.join(BASE, 'page') if lang == 'en' else os.path.join(BASE, lang, 'page')
    if not os.path.isdir(base):
        return
    removed = []
    for name in sorted(os.listdir(base)):
        if not name.isdigit():
            continue
        if int(name) > pages:
            shutil.rmtree(os.path.join(base, name), ignore_errors=True)
            removed.append('%s/page/%s' % (lang, name))
    if removed:
        print('   [stale] 삭제된 페이지 정리: %s' % ', '.join(removed))


def game_tabs(lang, active):
    """섹션 상단 탭 — 전체/앱/게임. active: ''|'app'|'game'."""
    t = TAB_STR.get(lang, DEFAULT_TAB)
    base = game_home_path(lang)
    items = [('', base, t[0]), ('app', base + 'apps/', t[1]), ('game', base + 'games/', t[2])]
    cls = ('tap px-4 rounded-full text-sm border transition-colors')
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
    # 999일을 넘는 날짜는 오파싱(연도 오독 등)일 수 있어 표시하지 않는다.
    if n > 999:
        return ''
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
    return ('<span class="text-[11px] font-semibold px-2 py-0.5 rounded tabular-nums '
            'bg-amber-500 text-white%s">%s</span>' % (cls, htmllib.escape(dd)))


def is_upcoming_future(p):
    """출시 예정 + 미래 출시일이 확인된 글 (Hero 자격 — D-day 가 있어야 한다)."""
    f = p.get('facts') or {}
    if not f.get('upcoming'):
        return False
    rel = (f.get('releaseDate') or '').strip()
    return bool(rel) and rel > datetime.date.today().isoformat()


def is_upcoming_group(p):
    """'출시 예정' 그룹 편성용. 출시일이 이미 지난 사전예약 글은 출시된 것으로 본다."""
    f = p.get('facts') or {}
    if not f.get('upcoming'):
        return False
    rel = (f.get('releaseDate') or '').strip()
    return (not rel) or rel > datetime.date.today().isoformat()


def build_game_index(lang, plist, available, kind=''):
    """/game/ (전체·앱·게임) 인덱스. 기존 /game/ URL 은 그대로 두고 apps/ games/ 를 추가."""
    g = GAME_STR.get(lang, DEFAULT_GAME)
    shown = [p for p in plist if not kind or p.get('kind') == kind] if kind else plist
    infeed = ad_unit('index_infeed', wrap_class='sm:col-span-2 my-6 text-center')
    empty = (f'<p class="text-sm text-slate-500 dark:text-slate-400 py-8 text-center">'
             f'{htmllib.escape(TAB_STR.get(lang, DEFAULT_TAB)[0])} — 0</p>')
    # 목록 첫 항목이 "미래 출시예정"일 때만 Hero 를 쓴다. 이미 출시된 글은 Hero 자격 없음.
    blocks = []
    rest = shown
    if shown and is_upcoming_future(shown[0]):
        blocks.append('<div class="grid gap-4 sm:grid-cols-2">\n%s\n</div>'
                      % game_hero_html(lang, shown[0]))
        rest = shown[1:]
    up_list = [p for p in rest if is_upcoming_group(p)]
    new_list = [p for p in rest if not is_upcoming_group(p)]
    labels = GAME_GROUP_STR.get(lang, GAME_GROUP_DEFAULT)
    for i, (label, items) in enumerate(((labels[0], up_list), (labels[1], new_list))):
        if not items:
            continue      # 빈 그룹은 제목까지 통째로 렌더하지 않는다
        grid = ('<div class="grid gap-4 sm:grid-cols-2">\n%s\n%s\n</div>'
                % ('\n'.join(game_card_html(lang, p) for p in items), infeed if i == 1 else ''))
        blocks.append('<section class="space-y-3">\n'
                      '<h2 class="text-lg font-semibold text-slate-900 dark:text-slate-100">'
                      '%s</h2>\n%s\n</section>' % (htmllib.escape(label), grid))
    body = '\n'.join(blocks) if blocks else empty
    content = f'''<div class="space-y-6">
  <div>
    <h1 class="text-2xl font-bold text-slate-900 dark:text-slate-100">{htmllib.escape(g[1])}</h1>
    <p class="mt-1 text-sm text-slate-600 dark:text-slate-400">{htmllib.escape(g[2])}</p>
  </div>
  {game_tabs(lang, kind)}
  {body}
  {ad_unit('index')}
  {ad_unit('index_bottom')}
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


def build_post_archive(lang, plist, available):
    """/post/index.html — 경제 포스트 전체 목록(크롤 허브).

    왜 필요한가 (2026-10-10 실측):
      홈은 **최신 3~8편만** 링크한다. 포스트끼리는 '관련 글' 4개로 연결돼 있지만,
      그 체인에서 밀려난 오래된 글은 **어느 HTML 페이지에서도 링크되지 않는 고아 URL** 이 된다.
      sitemap 이 URL 을 알려주긴 하지만, 내부 링크가 없으면 Googlebot 이 중요도를 낮게 잡는다.
      → 모든 포스트가 **한 번의 클릭**으로 도달 가능한 허브를 만든다. 881개 중 상당수가
      이 페이지 하나로 크롤러에게 노출된다.
    """
    a = ARCHIVE_STR.get(lang, DEFAULT_ARCHIVE)
    items = sorted(plist, key=lambda p: p['date'], reverse=True)
    rows, cur = [], None
    for p in items:
        ym = p['date'][:7]
        if ym != cur:
            if cur is not None:
                rows.append('</ul>')
            cur = ym
            rows.append('<h2 class="mt-6 mb-2 text-base font-semibold '
                        'text-slate-900 dark:text-slate-100">%s</h2>' % htmllib.escape(ym))
            rows.append('<ul class="archive-list space-y-2.5">')
        rows.append(
            '<li>'
            '<a class="text-brand-600 dark:text-blue-300 hover:underline break-words" href="%s">%s</a>'
            ' <span class="text-slate-500 dark:text-slate-400 whitespace-nowrap">· %s</span>'
            '</li>' % (post_href(lang, p['slug']), htmllib.escape(p['title']),
                       htmllib.escape(p['date'])))
    if rows:
        rows.append('</ul>')
    body = '\n'.join(rows)
    content = f'''<div class="space-y-6">
  <div>
    <h1 class="text-2xl font-bold text-slate-900 dark:text-slate-100">{htmllib.escape(a[0])}</h1>
    <p class="mt-1 text-sm text-slate-600 dark:text-slate-400">{htmllib.escape(a[1])}</p>
    <p class="mt-1 text-xs text-slate-500 dark:text-slate-400">{len(items)}</p>
  </div>
  {body}
  {ad_unit('index')}
</div>'''
    rel = '/post/index.html' if lang == 'en' else f'/{lang}/post/index.html'
    canonical = DOMAIN + rel
    html_doc = layout(lang, f'{a[0]} — {SITE_NAME}', a[1], canonical, content, 'website',
                      [website_ld(lang, canonical)],
                      slug=None, available=available)
    out_path = os.path.join(BASE, rel.lstrip('/'))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, 'w', encoding='utf-8').write(html_doc)
    return out_path


def build_sitemap(posts, avail_by_slug, langs_with_home, game=None):
    """실제 존재하는 언어 조합만 sitemap에 넣는다 (404 유도 URL 제거).

    🔑 2026-10-09 수정 (로이: "검색 색인이 안 된다"):
       예전엔 **전체 글 중 가장 최근 날짜**를 모든 URL 에 썼다.
       → sitemap 867개 전부 `<lastmod>2026-10-09</lastmod>` 로 동일했다.
       → Google 은 "이 사이트의 모든 페이지가 동시에 갱신됐다"로 판단하고
          크롤 예산을 극히 적게 배분한다. 신규 사이트 색인 지연의 대표적 원인.
       → **페이지마다 실제 발행일**을 쓴다.
    """
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    latest = max(p['date'] for p in posts)
    # 수정 시점 = 빌드 시각. 실제 변경이 없으면 sitemap 이 불필요하게 갱신되므로
    # '오늘' 만 쓴다 (어제 이전 글은 실제 발행일을 유지).
    build_day = time.strftime('%Y-%m-%d')

    def emit(loc, alternates, lastmod=None):
        xml.append('  <url>')
        xml.append(f'    <loc>{loc}</loc>')
        xml.append(f'    <lastmod>{lastmod or build_day}</lastmod>')
        # 🔑 2026-10-09 수정 (Search Console "기록을 수정" 경고 해결).
        #   Google 은 sitemap 안의 xhtml:link 를 **hreflang annotation** 으로 해석한다.
        #   그런데 자기 자신만 가리키는 링크(self + x-default)를 넣으면
        #   "이 페이지에 진짜 다른 언어 버전이 없다" 는 뜻이 되어
        #   오히려 오류로 본다 → 155개 URL 이 '기록을 수정' 로 떠 있었다.
        #   → 실제 다국어 버전이 2개 이상일 때만 xhtml:link 를 출력한다.
        if len(alternates) >= 2:
            for code, href in alternates:
                xml.append(f'    <xhtml:link rel="alternate" '
                           f'hreflang="{code}" href="{href}" />')
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

    # 포스트 아카이브 허브 (/post/index.html) — build_post_archive 가 생성.
    #   내부 링크 허브라서 크롤 도달성이 높다. lastmod 는 해당 언어의 최신 발행일.
    for c in LANG_META:
        if c not in langs_with_home:
            continue
        loc = DOMAIN + ('/post/index.html' if c == 'en' else f'/{c}/post/index.html')
        alts = [(x, DOMAIN + ('/post/index.html' if x == 'en' else f'/{x}/post/index.html'))
                for x in LANG_META if x in langs_with_home]
        alts.append(('x-default', DOMAIN + '/post/index.html'))
        emit(loc, alts)

    # 🔴 2026-10-10 제거: 페이지네이션(홈 2페이지 이후)을 sitemap 에서 뺀다.
    #   Search Console 실측에서 `/hi/page/5/` 가 "크롤링됨 - 현재 색인 생성되지 않음"
    #   으로 잡혀 있었다. 목록 페이지는 색인 대상이 아니고, 13개 언어 × 최대 10페이지
    #   = **128개 URL** 이 sitemap 을 채워 실제 글의 크롤 예산을 잡아먹고 있었다.
    #   → build_index() 에서 `noindex, follow` 를 걸었으므로 sitemap 에서도 뺀다.
    #     (sitemap 등록 + noindex 는 서로 충돌하는 신호라 둘 중 하나만 해야 한다)

    for p in posts:
        avail = avail_by_slug.get(p['slug'], {'en'})
        alts = [(c, post_url(c, p['slug'])) for c in LANG_META if c in avail]
        if 'en' in avail:
            alts.append(('x-default', post_url('en', p['slug'])))
        for c in LANG_META:
            if c in avail:
                # 🔑 페이지별 실제 발행일 (위 build_sitemap docstring 참고)
                emit(post_url(c, p['slug']), alts, lastmod=p['date'])

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
                # 🔑 2026-10-09: HTML head 와 동일한 hreflang 을 sitemap 에도 쓴다.
                #   sitemap 의 xhtml:link 와 head 의 link rel=alternate 가
                #   어긋나면 Google 이 둘 중 하나를 무시한다.
                g_post_alts = [(c, p['url']), ('x-default', p['url'])]
                _peers = GAME_PEER_URLS.get(p['slug']) or {}
                for _code, _pslug in _peers.items():
                    if _code == c or _code not in LANG_META:
                        continue
                    g_post_alts.append((_code, game_post_url(_code, _pslug)))
                emit(p['url'], g_post_alts, lastmod=p.get('date'))

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
# ---------- KO 학습 섹션 (/ko/study/) ----------
STUDY_MARK = '①②③④⑤⑥⑦⑧⑨⑩'
STUDY_MARK_RE = re.compile(r'^\s*(?:[①②③④⑤⑥⑦⑧⑨⑩]|[A-Ea-e][).、.,]|[0-9][).])\s*')

STUDY_JS = '''<script>
(function () {
  var page = document.querySelector('[data-study-page]');
  if (!page) return;
  var key = 'study:' + page.getAttribute('data-study-page');
  function load() { try { return JSON.parse(localStorage.getItem(key) || '{}'); } catch (e) { return {}; } }
  function paint(box, d) {
    var k = box.getAttribute('data-mark');
    box.querySelectorAll('button[data-ok]').forEach(function (b) {
      var on = (d[k] === b.getAttribute('data-ok'));
      b.style.backgroundColor = on ? '#dbeafe' : '';
      b.style.borderColor = on ? '#2563eb' : '';
    });
  }
  function render() {
    var d = load(), ks = Object.keys(d), ok = ks.filter(function (k) { return d[k] === '1'; }).length;
    var el = document.getElementById('study-progress');
    if (el) el.textContent = ks.length
      ? ('오늘 체크 ' + ks.length + '문항 · 정답 ' + ok + '개 (정답률 ' + Math.round(ok * 100 / Math.max(1, ks.length)) + '%)')
      : '아직 체크한 문항이 없습니다. 풀고 맞음/틀림을 눌러주세요.';
  }
  document.querySelectorAll('[data-mark]').forEach(function (box) {
    var d0 = load();
    paint(box, d0);
    box.querySelectorAll('button[data-ok]').forEach(function (b) {
      b.addEventListener('click', function () {
        var d = load(), k = box.getAttribute('data-mark'), v = b.getAttribute('data-ok');
        if (d[k] === v) delete d[k]; else d[k] = v;
        localStorage.setItem(key, JSON.stringify(d));
        paint(box, d);
        render();
      });
    });
  });
  render();
})();
</script>'''


def study_load(lang=STUDY_LANG):
    out = []
    d = os.path.join(STUDY_DIR, lang)
    if not os.path.isdir(d):
        return out
    for fn in sorted(glob.glob(os.path.join(d, '*.json'))):
        try:
            out.append(json.load(open(fn, encoding='utf-8')))
        except Exception:
            print(f'  ! 학습 JSON 손상, 건너뜀: {fn}')
    out.sort(key=lambda x: x.get('date', ''))
    return out


def study_questions(item):
    """세트에서 (라벨, 문항) 목록을 뽑는다."""
    out = []
    for q in item.get('questions') or []:
        out.append(('한국사', q))
    for q in item.get('part5') or []:
        out.append(('TOEIC', q))
    for q in ((item.get('part7') or {}).get('questions') or []):
        out.append(('TOEIC', q))
    for q in item.get('problems') or []:
        out.append(('PSAT', q))
    return out


def study_q_html(q, idx, key):
    ch = q.get('choices') or []
    # 선택지에 ①/A) 같은 표기가 남아 있어도 벗겨내고 ①②③… 를 직접 붙인다
    # → "정답 ②" 기호가 목록 표기와 항상 일치한다.
    lis = '\n'.join(
        f'<li>{STUDY_MARK[min(i, len(STUDY_MARK) - 1)]} {htmllib.escape(STUDY_MARK_RE.sub("", str(c)).strip())}</li>'
        for i, c in enumerate(ch))
    a = q.get('answer')
    sym = STUDY_MARK[a] if isinstance(a, int) and 0 <= a < len(STUDY_MARK) else '?'
    ev = (f'<p class="mt-1 text-slate-600 dark:text-slate-400"><b>근거</b> “{htmllib.escape(str(q["evidence"]))}”</p>'
          if q.get('evidence') else '')
    trap = (f'<p class="mt-1 text-amber-700 dark:text-amber-400"><b>함정</b> {htmllib.escape(str(q["trap"]))}</p>'
            if q.get('trap') else '')
    return f'''<div class="rounded-xl border border-slate-200 dark:border-slate-800 p-4">
  <p class="font-semibold text-slate-900 dark:text-slate-100">{idx}. {htmllib.escape(str(q.get('q', '')))}</p>
  <ul class="mt-2 space-y-1 text-sm text-slate-700 dark:text-slate-300 list-none">{lis}</ul>
  <details class="mt-3">
    <summary class="tap px-1 cursor-pointer text-sm font-semibold text-brand-600">정답·해설 보기</summary>
    <div class="mt-2 text-sm text-slate-700 dark:text-slate-300 space-y-1">
      <p><b>정답</b> {sym}</p>
      <p>{htmllib.escape(str(q.get('explain', '')))}</p>{ev}{trap}
    </div>
  </details>
  <div class="mt-3 flex gap-2" data-mark="{key}">
    <button type="button" data-ok="1" class="tap flex-1 px-4 rounded-lg text-sm font-semibold border border-slate-300 dark:border-slate-700 hover:border-brand-500 hover:text-brand-600">맞음</button>
    <button type="button" data-ok="0" class="tap flex-1 px-4 rounded-lg text-sm font-semibold border border-slate-300 dark:border-slate-700 hover:border-brand-500 hover:text-brand-600">틀림</button>
  </div>
</div>'''


def study_table_html(t):
    if not t:
        return ''
    hs = ''.join('<th class="px-3 py-2 text-left border-b border-slate-300 dark:border-slate-700">'
                 f'{htmllib.escape(str(h))}</th>' for h in (t.get('headers') or []))
    rows = ''
    for r in (t.get('rows') or []):
        cells = ''.join('<td class="px-3 py-2 border-b border-slate-100 dark:border-slate-800">'
                        f'{htmllib.escape(str(c))}</td>' for c in r)
        rows += f'<tr>{cells}</tr>'
    return ('<div class="overflow-x-auto my-3"><table class="min-w-full text-sm '
            f'text-slate-800 dark:text-slate-200"><thead><tr>{hs}</tr></thead><tbody>{rows}</tbody></table></div>')


def study_review_html(label, mins, item, date_str):
    head = (f'<h3 class="text-base font-bold text-slate-900 dark:text-slate-100">{label} '
            f'<span class="text-xs font-normal text-slate-500">약 {mins}분</span></h3>')
    if not item:
        return (f'<section class="rounded-2xl border border-dashed border-slate-300 dark:border-slate-700 p-4">'
                f'{head}<p class="mt-2 text-sm text-slate-500">해당 세트 없음({date_str}). '
                '시작 첫 주에는 비어 있는 게 정상입니다.</p></section>')
    qs = study_questions(item)
    body = '\n'.join(study_q_html(q, i + 1, f'{date_str}-{i}') for i, (_, q) in enumerate(qs))
    topic = item.get('topic') or item.get('psat_type') or item.get('track_title') or ''
    return (f'<section class="space-y-3">{head}'
            f'<p class="text-sm text-slate-600 dark:text-slate-400">{htmllib.escape(str(topic))} · '
            f'{len(qs)}문항 — 문제를 다시 풀고 정답을 확인하세요.</p>'
            f'<div class="space-y-3">{body}</div></section>')


def study_today_html(item):
    t = item.get('track')
    date = item.get('date', '')
    if t == 'history':
        c = item.get('concept') or {}
        pts = ''.join(f'<li>{htmllib.escape(str(p))}</li>' for p in (c.get('points') or []))
        mn = (f'<p class="mt-3 text-sm text-slate-800 dark:text-slate-200"><b>암기</b> '
              f'{htmllib.escape(str(c.get("mnemonic", "")))}</p>' if c.get('mnemonic') else '')
        qs = '\n'.join(study_q_html(q, i + 1, f'{date}-h{i}')
                       for i, q in enumerate(item.get('questions') or []))
        return (f'<div class="rounded-2xl border-2 border-brand-600/30 bg-slate-50 dark:bg-slate-900 p-5">'
                f'<h2 class="text-lg font-bold">{htmllib.escape(str(c.get("title") or item.get("topic", "")))}</h2>'
                f'<ul class="mt-3 space-y-1 text-sm text-slate-700 dark:text-slate-300 list-disc pl-5">{pts}</ul>{mn}'
                f'</div><div class="mt-4 space-y-3">{qs}</div>')
    if t == 'toeic':
        p7 = item.get('part7') or {}
        psg = p7.get('passage') or {}
        p5 = '\n'.join(study_q_html(q, i + 1, f'{date}-p5{i}')
                       for i, q in enumerate(item.get('part5') or []))
        p7q = '\n'.join(study_q_html(q, i + 1, f'{date}-p7{i}')
                        for i, q in enumerate(p7.get('questions') or []))
        return (f'<div class="rounded-2xl border border-slate-200 dark:border-slate-800 p-5">'
                f'<h2 class="text-lg font-bold">Part 5 — {htmllib.escape(str(item.get("part5_point", "")))}</h2>'
                f'<div class="mt-3 space-y-3">{p5}</div></div>'
                f'<div class="rounded-2xl border border-slate-200 dark:border-slate-800 p-5 mt-4">'
                f'<h2 class="text-lg font-bold">Part 7 — {htmllib.escape(str(item.get("part7_type", "")))}</h2>'
                f'<p class="mt-2 text-sm font-semibold">{htmllib.escape(str(psg.get("title", "")))}</p>'
                f'<div class="mt-2 text-sm leading-relaxed whitespace-pre-line text-slate-700 dark:text-slate-300">'
                f'{htmllib.escape(str(psg.get("body", "")))}</div>'
                f'<div class="mt-3 space-y-3">{p7q}</div></div>')
    if t == 'psat':
        tc = item.get('type_card') or {}
        steps = ''.join(f'<li>{htmllib.escape(str(s))}</li>' for s in (tc.get('steps') or []))
        extra = []
        if tc.get('shortcut'):
            extra.append(f'<p class="mt-2 text-sm"><b>암기 요약</b> {htmllib.escape(str(tc["shortcut"]))}</p>')
        if tc.get('pitfall'):
            extra.append('<p class="mt-1 text-sm text-amber-700 dark:text-amber-400"><b>함정</b> '
                         f'{htmllib.escape(str(tc["pitfall"]))}</p>')
        probs = ''
        for i, p in enumerate(item.get('problems') or []):
            probs += (f'<div class="rounded-xl border border-slate-200 dark:border-slate-800 p-4">'
                      f'<p class="font-semibold">{htmllib.escape(str(p.get("title", "")))}</p>'
                      f'{study_table_html(p.get("table"))}'
                      f'{study_q_html(p, i + 1, f"{date}-s{i}")}</div>')
        return (f'<div class="rounded-2xl border-2 border-brand-600/30 bg-slate-50 dark:bg-slate-900 p-5">'
                f'<h2 class="text-lg font-bold">유형 카드 · {htmllib.escape(str(tc.get("name") or item.get("psat_type", "")))}</h2>'
                f'<ol class="mt-3 space-y-1 text-sm text-slate-700 dark:text-slate-300 list-decimal pl-5">{steps}</ol>'
                f'{"".join(extra)}</div><div class="mt-4 space-y-3">{probs}</div>')
    return (f'<div class="rounded-2xl border border-slate-200 dark:border-slate-800 p-5 text-sm '
            f'text-slate-700 dark:text-slate-300">{htmllib.escape(str(item.get("rest_note", "이번 주 세트를 다시 봅니다.")))}</div>')


def study_vocab_html(v):
    """매일 최하단 TOEIC 단어 카드 (트랙 무관 · LLM 호출 없이 정적 로테이션)."""
    if not v:
        return ''
    words = v.get('words') or []
    if not words:
        return ''
    lis = ''
    for w in words:
        lis += (f'<li class="rounded-lg border border-slate-200 dark:border-slate-800 p-3">'
                f'<p class="font-semibold text-slate-900 dark:text-slate-100">'
                f'{htmllib.escape(str(w.get("w", "")))}'
                f'<span class="ml-1 rounded bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 text-xs font-normal text-slate-500 dark:text-slate-400">'
                f'{htmllib.escape(str(w.get("pos", "")))}</span></p>'
                f'<p class="text-sm text-slate-700 dark:text-slate-300">{htmllib.escape(str(w.get("mean", "")))}</p>'
                f'<p class="mt-1 text-xs italic text-slate-500 dark:text-slate-400">{htmllib.escape(str(w.get("ex", "")))}</p>'
                f'</li>')
    conf = v.get('confusable') or {}
    conf_html = ''
    if conf.get('a'):
        conf_html = (f'<div class="mt-3 rounded-lg bg-slate-50 dark:bg-slate-800 p-3">'
                     f'<p class="text-xs font-bold text-brand-600">헷갈리는 표현</p>'
                     f'<p class="mt-1 text-sm text-slate-800 dark:text-slate-200">'
                     f'<span class="font-semibold">{htmllib.escape(str(conf.get("a", "")))}</span>'
                     f' <span class="text-slate-400">vs</span> '
                     f'<span class="font-semibold">{htmllib.escape(str(conf.get("b", "")))}</span></p>'
                     f'<p class="mt-1 text-xs text-slate-600 dark:text-slate-400">'
                     f'{htmllib.escape(str(conf.get("diff", "")))}</p></div>')
    tip = v.get('tip')
    tip_html = (f'<p class="mt-3 text-sm text-slate-700 dark:text-slate-300">'
                f'<span class="font-bold text-brand-600">암기 팁</span> · '
                f'{htmllib.escape(str(tip))}</p>') if tip else ''
    return f'''<section class="space-y-3">
    <h3 class="text-base font-bold text-slate-900 dark:text-slate-100">④ 오늘의 토익 단어 <span class="text-xs font-normal text-slate-500">매일 3개</span></h3>
    <ul class="space-y-2">{lis}</ul>
    {conf_html}
    {tip_html}
  </section>'''


def study_page_html(item, prev7, prev1, next_item=None):
    date = item.get('date', '')
    d = datetime.date.fromisoformat(date)
    d7 = (d - datetime.timedelta(days=7)).isoformat()
    d1 = (d - datetime.timedelta(days=1)).isoformat()
    prev_link = ''
    next_link = ''
    pd = item.get('_prev_day')
    nd = next_item or item.get('_next_day')
    if pd:
        prev_link = (f'<a class="tap px-3 rounded-lg border border-slate-300 dark:border-slate-700 hover:border-brand-500" href="/ko/study/day/{pd}/">← Day {pd}</a>')
    if nd:
        next_link = (f'<a class="tap px-3 rounded-lg border border-slate-300 dark:border-slate-700 hover:border-brand-500" href="/ko/study/day/{nd["day"]}/">Day {nd["day"]} →</a>')
    nav = (f'<nav class="flex items-center justify-between border-t border-slate-200 dark:border-slate-800 pt-4 text-sm">'
           f'<span>{prev_link or "·"}</span><span>{next_link or "·"}</span></nav>'
           if (prev_link or next_link) else '')
    return f'''<div data-study-page="{date}" class="space-y-8">
  <div>
    <p class="text-xs font-bold tracking-widest text-brand-600">KO STUDY · 7급 워밍업</p>
    <h1 class="mt-1 text-2xl font-bold text-slate-900 dark:text-slate-100">Day {item.get('day', '')} · {htmllib.escape(str(item.get('track_title', '')))}</h1>
    <p class="mt-1 text-sm text-slate-600 dark:text-slate-400">{date} · {STUDY_BUDGET}</p>
    <p id="study-progress" class="mt-2 text-sm text-slate-500"></p>
    <p class="mt-3"><a class="tap px-3 rounded-lg border border-slate-300 dark:border-slate-700 text-sm font-semibold" href="/ko/study/">← 학습 목록으로</a></p>
  </div>
  {study_review_html('① 복습 — 7일 전', 5, prev7, d7)}
  {study_review_html('② 복습 — 어제', 5, prev1, d1)}
  <section class="space-y-4">
    <h3 class="text-base font-bold text-slate-900 dark:text-slate-100">③ 오늘 새 내용 <span class="text-xs font-normal text-slate-500">약 20분</span></h3>
    {study_today_html(item)}
  </section>
  {study_vocab_html(item.get('vocab'))}
  {nav}
  <p class="text-xs text-slate-400 dark:text-slate-600">※ 본 학습 문항은 AI가 생성 후 자동 검증(계산 재확인·근거 일치 확인)을 거쳤습니다. 오답이나 오류가 있으면 무시하고 넘어가 주세요.</p>
  {STUDY_JS}
</div>'''


def study_index_html(items, today_str):
    """학습 진입 페이지 = Day 목록.

    로이 지시 2026-10-03: 학습 메뉴를 누르면 '바로 어떤 Day'가 뜨는 게 아니라
    목록이 먼저 나오고 거기서 골라 들어가야 한다. (예전엔 최신 Day가 바로 떠서
    "왜 Day 5지?" 같은 혼란이 생겼다.)
    """
    cards = ''
    for it in items:
        day = it.get('day')
        date = it.get('date', '')
        tt = htmllib.escape(str(it.get('track_title', '')))
        topic = htmllib.escape(str(it.get('topic') or it.get('psat_type')
                                   or it.get('part7_type') or ''))
        nq = len(study_questions(it))
        badge = ('<span class="ml-2 rounded bg-brand-600 px-1.5 py-0.5 text-xs font-bold '
                 'text-white">오늘</span>' if date == today_str else '')
        cards += (
            f'<a class="block rounded-lg border border-slate-200 dark:border-slate-800 p-4 '
            f'hover:border-brand-600 hover:bg-slate-50 dark:hover:bg-slate-800" '
            f'href="/ko/study/day/{day}/">'
            f'<div class="flex items-baseline justify-between">'
            f'<span class="font-semibold text-slate-900 dark:text-slate-100">Day {day}</span>'
            f'<span class="text-xs text-slate-500">{date}{badge}</span></div>'
            f'<p class="mt-1 text-sm text-slate-700 dark:text-slate-300">{tt}'
            f' <span class="text-xs text-slate-400">· {nq}문항</span></p>'
            f'<p class="mt-0.5 text-xs text-slate-500">{topic}</p></a>')
    total_q = sum(len(study_questions(x)) for x in items)
    has_today = any(x.get('date') == today_str for x in items)
    note = ('' if has_today else
            f'<p class="rounded-lg bg-slate-50 dark:bg-slate-800 p-3 text-sm text-slate-600 '
            f'dark:text-slate-400">오늘({today_str}) 세트는 아직 없습니다 — 매일 21:05에 '
            f'자동으로 만들어집니다.</p>')
    return f'''<div class="space-y-4">
  <div>
    <p class="text-xs font-bold tracking-widest text-brand-600">KO STUDY · 7급 워밍업</p>
    <h1 class="mt-1 text-2xl font-bold text-slate-900 dark:text-slate-100">학습 목록</h1>
    <p class="mt-1 text-sm text-slate-600 dark:text-slate-400">누적 {len(items)}일 · 총 {total_q}문항 · 하루 30분(복습 10분 + 새 내용 20분)</p>
  </div>
  {note}
  <div class="space-y-2">{cards}</div>
  <p><a class="tap px-3 rounded-lg border border-slate-300 dark:border-slate-700 text-sm font-semibold" href="/ko/study/archive/">표 형태 전체 목록 →</a></p>
</div>'''


def build_study():
    """KO 학습 세트 → /ko/study/ (목록) + /ko/study/day/{n}/ + /ko/study/archive/."""
    items = study_load()
    if not items:
        print('[study] 학습 세트 없음 — 건너뜀')
        return 0
    by_date = {x.get('date'): x for x in items}
    by_day = {x.get('day'): x for x in items}
    written = 0
    for it in items:
        d = datetime.date.fromisoformat(it['date'])
        prev7 = by_date.get((d - datetime.timedelta(days=7)).isoformat())
        prev1 = by_date.get((d - datetime.timedelta(days=1)).isoformat())
        dn = it.get('day')
        it['_prev_day'] = dn - 1 if isinstance(dn, int) and (dn - 1) in by_day else None
        nxt = by_day.get(dn + 1) if isinstance(dn, int) else None
        content = study_page_html(it, prev7, prev1, nxt)
        canon = f'{DOMAIN}/ko/study/day/{it["day"]}/'
        title = f'Day {it["day"]} · {it.get("track_title", "")} — KO 학습 | {SITE_NAME}'
        doc = layout('ko', title, f'{it["date"]} 30분 학습 세트 — 복습(7일 전·어제) + 새 내용 20분.',
                     canon, content, 'website', [], slug=None, available={'ko'},
                     noindex=True, plain=True)
        out = os.path.join(BASE, 'ko', 'study', 'day', str(it['day']), 'index.html')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, 'w', encoding='utf-8').write(doc)
        written += 1

    # /ko/study/ = 목록(선택 화면). 예전엔 최신 Day를 바로 붙였는데,
    # 오늘이 Day 1인데 Day 5가 떠서 "왜 Day 5지?" 하는 혼란이 있었다(로이 지적).
    today_str = datetime.date.today().isoformat()
    lp = os.path.join(BASE, 'ko', 'study', 'index.html')
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    open(lp, 'w', encoding='utf-8').write(
        layout('ko', f'학습 목록 (Day 1~{items[-1]["day"]}) — {SITE_NAME}',
               '원하는 Day를 골라 30분 학습 — 복습 10분 + 새 내용 20분.',
               f'{DOMAIN}/ko/study/',
               study_index_html(items, today_str),
               'website', [], slug=None, available={'ko'}, noindex=True, plain=True))
    written += 1

    rows = ''
    for it in reversed(items):
        rows += (f'<tr><td class="px-3 py-2 border-b border-slate-100 dark:border-slate-800">'
                 f'<a class="text-brand-600 underline" href="/ko/study/day/{it["day"]}/">Day {it["day"]}</a></td>'
                 f'<td class="px-3 py-2 border-b border-slate-100 dark:border-slate-800">{it.get("date", "")}</td>'
                 f'<td class="px-3 py-2 border-b border-slate-100 dark:border-slate-800">'
                 f'{htmllib.escape(str(it.get("track_title", "")))}</td>'
                 f'<td class="px-3 py-2 border-b border-slate-100 dark:border-slate-800">'
                 f'{htmllib.escape(str(it.get("topic") or it.get("psat_type") or ""))}</td></tr>')
    ap = os.path.join(BASE, 'ko', 'study', 'archive', 'index.html')
    os.makedirs(os.path.dirname(ap), exist_ok=True)
    open(ap, 'w', encoding='utf-8').write(
        layout('ko', f'학습 전체 목록 (Day 1~{items[-1]["day"]}) — {SITE_NAME}',
               '지금까지의 학습 세트 목록.',
               f'{DOMAIN}/ko/study/archive/',
               f'''<div class="space-y-4">
  <h1 class="text-2xl font-bold text-slate-900 dark:text-slate-100">학습 전체 목록</h1>
  <p class="text-sm text-slate-600 dark:text-slate-400">누적 {len(items)}일 · 총 {sum(len(study_questions(x)) for x in items)}문항</p>
  <p><a class="tap px-3 rounded-lg border border-slate-300 dark:border-slate-700 text-sm font-semibold" href="/ko/study/">오늘의 학습으로 →</a></p>
  <div class="overflow-x-auto"><table class="min-w-full text-sm text-slate-800 dark:text-slate-200">
    <thead><tr><th class="px-3 py-2 text-left border-b border-slate-300 dark:border-slate-700">Day</th>
    <th class="px-3 py-2 text-left border-b border-slate-300 dark:border-slate-700">날짜</th>
    <th class="px-3 py-2 text-left border-b border-slate-300 dark:border-slate-700">트랙</th>
    <th class="px-3 py-2 text-left border-b border-slate-300 dark:border-slate-700">주제/유형</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
</div>''', 'website', [], slug=None, available={'ko'}, noindex=True, plain=True))
    written += 1
    print(f'[study] KO 학습 페이지 {written}개 생성 (Day 1~{items[-1]["day"]})')
    return written


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
            game_posts[lang] = upcoming_first(items)
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
        # 레거시 글을 합치면서 최신순 정렬을 다시 걸면 출시예정이 밀려난다 → 재정렬로 복구.
        game_posts['en'] = upcoming_first(game_posts['en'])

    # 🔑 2026-10-09: 같은 앱의 타언어 버전을 인덱싱한다 (다국어 hreflang 용).
    #   게임 글은 언어마다 slug 가 다르므로(ko=livetopia-party, fr=livetopia-party-applications-sur-google-play-prix)
    #   단순 slug 비교로는 짝을 찾을 수 없다.
    #   → Google Play 패키지명(`com.xxx.yyy`) 또는 sourceUrl 이 같은 것끼리 묶는다.
    #   → 이 인덱스가 없으면 hreflang 이 자기 자신만 가리켜 다국어 신호가 0 이 된다.
    build_game_peer_index(game_posts)

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
        build_post_archive(lang, plist, home_available)
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
                # 🔴 2026-10-10 수정: legacy 앱 글도 **HTML 은 렌더링한다.**
                #   예전엔 URL 유지를 위해 아예 건너뛰었는데, 그 결과
                #   /post/*.html 55개 중 3개가 **상품 카드 없이** 발행되고 있었다
                #   (실측: followers-tracker / mendazzle / tideward).
                #   로이 지시 "알리익스프레스 제품이 없다면 다 집어넣어줘" →
                #   이 경로에도 카드를 넣는다. 단 kind 를 넘겨 게임 액세서리로 조회.
                build_post(lang, p['slug'], p['title'], p['desc'], p['category'],
                           p['date'], p['body'], p['sourceName'], p['sourceUrl'],
                           plist, {lang}, kind=p.get('kind'))
                continue
            # 🔑 2026-10-09: peer 언어를 available 에 포함한다.
            #   예전엔 {lang} 하나만 넘겨서 alternates_html 의
            #   `code not in available` 조건이 전부 걸려 hreflang 이 자기 자신뿐이었다.
            _peers = GAME_PEER_URLS.get(p['slug']) or {}
            _avail = {lang} | {c for c in _peers if c in LANG_META}
            build_post(lang, p['slug'], p['title'], p['desc'], p['category'], p['date'],
                       p['body'], p['sourceName'], p['sourceUrl'], plist, _avail,
                       section='game', facts=p.get('facts'), kind=p.get('kind'))
            game_total += 1
        build_game_index(lang, plist, set(game_langs))
        build_game_index(lang, plist, set(game_langs), 'app')
        build_game_index(lang, plist, set(game_langs), 'game')

    build_study()
    build_sitemap(macro_posts, avail_by_slug, langs_with_home, game_posts)
    build_feed(macro_posts)
    dp = AFF_CFG.get('disclosure_path', 'disclosure.html')
    print(f'빌드 완료: 경제 포스트 {total}개 + 앱/게임 {game_total}개 + 홈 {len(LANG_META)}개, sitemap.xml, feed.xml OK')


if __name__ == '__main__':
    main()
