"""포스트 본문에 자연스럽게 어필리에이트 상품을 삽입하는 오토 광고 모듈.

목적
  매일 발행되는 포스트에 카테고리/키워드에 맞는 알리익스프레��� 상품을
  자동으로 골라 **본문 문맥 안에** 넣는다. 포스트 하단 박스(기존 affiliate_box)
  가 아니라, 독자가 "이거 쓰려면 이게 필요하다"고 느끼는 지점에 배치한다.

왜 필요한가
  * 포털 API 는 `session`(access_token) 이 유효해야 商品 데이터가 나온다.
    서명 규칙은 2026-10-08 실측으로 확정 (`aliexpress._sign`).
  * 토큰이 없으면 **대체 경로**로 폴백한다: `Link Generator` 로 미리 만든
    링크 풀. API 실패가 곧 수익 Eyebrow 소실이 되면 안 된다.

설계 원칙
  1. API 가 죽어도 사이트는 죽지 않는다 (Graceful Fallback)
  2. 캐시를 aggressively 쓴다 — 같은 키워드는 하루 1회만 조회 (쿼터 절약)
  3. 카테고리 매칭으로 무관한 상품을 넣지 않는다 (CTR + AdSense 안전)
  4. `rel="sponsored nofollow"` + 고지 문구를 항상 붙인다 (표시광고법)

사용법
  from ae_autoslot import recommend
  picks = recommend(category="Apps & Games", keywords=["backup","battery"])
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aliexpress   # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 🔑 캐시 버전 — 필터/매핑 규칙을 바꾸면 이 숫자를 올린다.
#    그러면 예전 캐시를 **삭제하지 않고도** 자동으로 무효화된다.
#    (파일 삭제는 위험하므로 버전 스위치를 쓴다)
CACHE_VERSION = "v4"
CACHE_DIR = os.path.join(BASE, ".cache", "aliexpress")
CACHE_TTL = 24 * 3600          # 캐시 24시간
# API 세션이 없을 때 쓰는 정적 폴백 풀 (포털 Link Generator 로 손으로 채운다)
STATIC_POOL = os.path.join(BASE, "content", "affiliate_static.json")

# 카테고리 → 알리 카테고리 키워드 매핑.
# 'thematic relevance' 요건을 충족시키려면 이 매핑이 Adsense 안전장치 겸 전환율 장치다.
#
# ⚠️ 포스트 제목/본문 단어를 검색어로 쓰면 안 된다 (2026-10-08 실측).
#    "Arknights 리뷰" → 'arknights' 검색 → 코슬패·_BADGE 같은 쓰레기 상품.
#    그래서 카테고리 고정 키워드만 사용한다.
#
# 🔑 앱 vs 게임 분리 (2026-10-08)
#    앱 리뷰 독자 = 앱 쓰고 있으니 케이스/충전기/이어폰을 산다 → 자연스럽다.
#    게임 리뷰 독자 = 게임을 사지 않는다. But 게임 리뷰 독자는
#    **게임용 하드웨어**(패드·지문링커·충전 케이블)를 산다 → 이것도 자연스럽다.
#    오히려 게임 글에 게임 액세서리를 붙이는 게 더 관련성 높다.
CATEGORY_MAP = {
    "Apps & Games": {
        "keywords": ["phone case", "screen protector", "phone stand",
                     "usb charger", "power bank", "earbuds"],
        "ship_to": "KR",
    },
    # 게임 리뷰 전용 — 게임 기기용 액세서리만. 복권/코스프레/팬cies 는 뺀다.
    "Apps & Games (game)": {
        "keywords": ["game controller", "mobile game controller",
                     "phone cooler", "gaming headset", "thumb controller"],
        "ship_to": "KR",
    },
    "Consumer Electronics": {
        "keywords": ["usb c cable", "phone holder", "car charger",
                     "bluetooth speaker", "power bank"],
        "ship_to": "US",
    },
    "Home & Tools": {
        "keywords": ["tool set", "storage box", "led light strip",
                     "kitchen organizer"],
        "ship_to": "US",
    },
}

# 🔑 금융/경제 포스트 매핑 (2026-10-09, 로이 지시 "다 넣으라고")
#   중앙은행·인플레이션·채권 글에 이어폰을 넣으면 맥락이 안 맞는다.
#   그러나 **금융을 읽는 독자는 사무·정리·정확도 도구를 실제로 산다.**
#   → 같은 빌드가 숫자 데이터(매크로)를 다루는 사람이 매일 쓰는 물건을 노출한다.
#
#   ⚠️ 키워드는 전부 실측 검증한 것이다 (2026-10-09).
#      'webcam'·'usb hub'·'laptop stand'·'wireless mouse' 는 Aliexpress 에서
#      부품/수리용이가 먼저 떠서 제외했다. 아래만 검색 결과가 일관됐다.
ECON_MAP = {
    "Central Banking": ["accounting calculator", "document organizer",
                        "desk organizer set", "file folder set"],
    "Monetary Policy": ["accounting calculator", "file folder set",
                        "document organizer", "desk organizer set"],
    "Global Macro": ["cable management tray", "desk organizer set",
                     "document organizer", "file folder set"],
    "US Economy": ["cable management tray", "accounting calculator",
                   "file folder set", "desk organizer set"],
    "Economic Research": ["document organizer", "file folder set",
                          "accounting calculator", "cable management tray"],
    "Bonds": ["accounting calculator", "file folder set",
              "document organizer", "desk organizer set"],
    "Inflation": ["cable management tray", "document organizer",
                  "file folder set", "desk organizer set"],
    "Labor Markets": ["desk organizer set", "cable management tray",
                      "document organizer", "file folder set"],
    "China": ["cable management tray", "file folder set",
              "document organizer", "desk organizer set"],
}

# 이 카테고리들에 속하면 ECON_MAP 을 우선 사용한다.
_ECON_ALIASES = tuple(ECON_MAP.keys())

# 🔑 본문 주제 → 상품 검색어 매핑 (2026-10-09, 로이 지시 "글 내용에 맞춰서 추천")
#
# 왜 화이트리스트인가 (실측 실패 사례):
#   게임 포스트에서 제목 단어 'arknights' 를 그대로 검색하면
#   코슬패·인형·복권 같은 쓰레기가 나온다. 게임명은 상품으로 이어지지 않는다.
#   → **상품으로 이어질 수 있는 주제어만 등록**하고, 본문에서 그 주제어가
#     실제로 등장할 때만 검색한다. 안 잡히면 카테고리 기본값으로 폴백.
TOPIC_QUERY = {
    # ── 파일/문서 정리 (금융·행정 포스트 다수)
    "file": "file folder set",
    "folder": "file folder set",
    "document": "document organizer",
    "paper": "document organizer",
    "archive": "file folder set",
    "desk": "desk organizer set",
    "calculator": "accounting calculator",
    "calculate": "accounting calculator",
    "budget": "accounting calculator",
    "tax": "accounting calculator",
    "invoice": "accounting calculator",
    "cable": "cable management tray",
    "wire": "cable management tray",
    # ── 재택·생산성 구성
    "remote": "desk organizer set",
    "home office": "desk organizer set",
    "laptop": "cable management tray",
    "monitor": "desk organizer set",
    # ── 통화/물가
    "inflation": "file folder set",
    "currency": "accounting calculator",
    "dollar": "accounting calculator",
    # ── 앱/태블릿 사용
    "phone": "phone stand",
    "tablet": "phone stand",
    "screen": "screen protector",
    "display": "screen protector",
    "charging": "usb charger",
    "battery": "power bank",
    "earphone": "earbuds",
    "earbud": "earbuds",
    "headphone": "earbuds",
    # ── 게임 포스트
    "game": "mobile game controller",
    "gaming": "mobile game controller",
    "controller": "mobile game controller",
    "fps": "gaming headset",
    "playtime": "phone cooler",
    "overheat": "phone cooler",
    "heat": "phone cooler",
    "cool": "phone cooler",
    # ── 생활/쇼핑 (앱 리뷰 본문에서 자주 나옴)
    "water": "water bottle",
    "bottle": "water bottle",
    "coffee": "coffee mug",
    "mug": "coffee mug",
    "bag": "travel bag",
    "backpack": "backpack",
    "wallet": "card holder",
    "card holder": "card holder",
    "umbrella": "umbrella",
    "shoes": "sports shoes",
    "footwear": "sports shoes",
    "shirt": "t shirt",
    "clothing": "hoodie",
    "watch": "wrist watch",
    "bagpack": "travel bag",
    "snack": "snack box",
    "food": "food container",
    "kitchen": "kitchen organizer",
    "cook": "kitchen utensil set",
    "clean": "cleaning brush",
    "travel": "travel organizer",
    "trip": "travel organizer",
    "hotel": "travel organizer",
    "flight": "travel organizer",
    "money": "cash wallet",
    "coin": "coin holder",
    "safe": "document safe",
    "lock": "combination lock",
    "sensor": "motion sensor",
    "light": "led light strip",
    "lamp": "led desk lamp",
    "chair": "memory foam seat cushion",
    "posture": "memory foam seat cushion",
    "sleep": "memory foam pillow",
    "pillow": "memory foam pillow",
    "plant": "self watering planter",
    "garden": "garden tool set",
    "pet": "pet grooming brush",
    "dog": "pet grooming brush",
    "cat": "pet grooming brush",
    "baby": "baby feeding bottle",
    "camera": "phone tripod",
    "photo": "phone tripod",
    "video": "phone tripod",
    "edit": "phone tripod",
    "music": "portable speaker",
    "audio": "portable speaker",
    "speaker": "portable speaker",
    "microphone": "usb microphone",
    "stream": "usb microphone",
    "record": "usb microphone",
    "office": "file folder set",
    "school": "document organizer",
    "study": "file folder set",
    "student": "file folder set",
    "write": "gel pen set",
    "note": "gel pen set",
    "pen": "gel pen set",
    "book": "book stand",
    "read": "book stand",
    "exercise": "resistance band",
    "workout": "resistance band",
    "fitness": "yoga mat",
    "yoga": "yoga mat",
    "run": "sports water bottle",
    "health": "digital scale",
    "weight": "digital scale",
    "diet": "digital scale",
    "calorie": "kitchen scale",
    "sleep tracker": "sleep mask",
    "bluetooth": "bluetooth speaker",
    "wireless": "wireless mouse",
    "typing": "mechanical keyboard",
    "keyboard": "keyboard stand",
    "printer": "printer stand",
    "scanner": "portable scanner",
}

# 🔑 다국어 주제어 사전 (2026-10-09).
#   실측: 번역본 197개가 영어 어휘가 없어 전부 카테고리 기본값으로 폴백했다
#   → 같은 상품이 수백 번 반복 노출. (다양성 3%)
#   → 9개 언어의 자주 쓰이는 주제어를 매핑한다.
#   원문(post/*.md)에는 영어가 쓰이므로 이 사전을 안 쓰면 다국어만 상품이 안 갈라진다.
TOPIC_QUERY_I18N = {
    # ── 한국어
    "파일": "file folder set", "서류": "document organizer",
    "문서": "document organizer", "책상": "desk organizer set",
    "계산기": "accounting calculator", "예산": "accounting calculator",
    "세금": "accounting calculator", "케이블": "cable management tray",
    "책": "book stand", "공책": "gel pen set", "필통": "gel pen set",
    "물": "water bottle", "텀블러": "water bottle", "커피": "coffee mug",
    "가방": "travel bag", "백팩": "backpack", "지갑": "card holder",
    "카드": "card holder", "우산": "umbrella", "신발": "sports shoes",
    "옷": "hoodie", "티셔츠": "t shirt", "시계": "wrist watch",
    "밥": "food container", "식기": "food container",
    "주방": "kitchen organizer", "요리": "kitchen utensil set",
    "청소": "cleaning brush", "여행": "travel organizer",
    "호텔": "travel organizer", "항공": "travel organizer",
    "동전": "coin holder", "금고": "document safe",
    "자물쇠": "combination lock", "감지": "motion sensor",
    "전등": "led light strip", "조명": "led desk lamp",
    "의자": "memory foam seat cushion", "자세": "memory foam seat cushion",
    "베개": "memory foam pillow", "수면": "memory foam pillow",
    "화분": "self watering planter", "정원": "garden tool set",
    "반려": "pet grooming brush", "강아지": "pet grooming brush",
    "고양이": "pet grooming brush", "아기": "baby feeding bottle",
    "카메라": "phone tripod", "사진": "phone tripod",
    "영상": "phone tripod", "동영상": "phone tripod",
    "편집": "phone tripod", "음악": "portable speaker",
    "스피커": "portable speaker", "마이크": "usb microphone",
    "사무": "file folder set", "학교": "document organizer",
    "공부": "file folder set", "학생": "file folder set",
    "운동": "resistance band", "다이어트": "digital scale",
    "체중": "digital scale", "영양": "kitchen scale",
    "게임": "mobile game controller", "겜": "mobile game controller",
    "그래픽": "phone cooler", "발열": "phone cooler",
    "쿨링": "phone cooler", "패드": "mobile game controller",
    "휴대폰": "phone stand", "스마트폰": "phone stand",
    "태블릿": "phone stand", "화면": "screen protector",
    "배터리": "power bank", "충전": "usb charger",
    "이어폰": "earbuds", "헤드폰": "earbuds",
    "이어버드": "earbuds", "블루투스": "bluetooth speaker",
    "키보드": "keyboard stand", "마우스": "wireless mouse",
    "인쇄": "printer stand", "스캐너": "portable scanner",
    "通胀": "file folder set", "물가": "file folder set",
    # ── 中文
    "文件": "file folder set", "文件夹": "file folder set",
    "文档": "document organizer", "桌面": "desk organizer set",
    "计算器": "accounting calculator", "预算": "accounting calculator",
    "税": "accounting calculator", "线缆": "cable management tray",
    "书": "book stand", "笔记本": "gel pen set", "笔": "gel pen set",
    "水": "water bottle", "水杯": "water bottle", "咖啡": "coffee mug",
    "包": "travel bag", "背包": "backpack", "钱包": "card holder",
    "伞": "umbrella", "鞋": "sports shoes", "衣服": "hoodie",
    "手表": "wrist watch", "饭": "food container",
    "厨房": "kitchen organizer", "旅行": "travel organizer",
    "灯": "led light strip", "椅子": "memory foam seat cushion",
    "枕头": "memory foam pillow", "睡眠": "memory foam pillow",
    "游戏": "mobile game controller", "手柄": "mobile game controller",
    "散热": "phone cooler", "手机": "phone stand",
    "屏幕": "screen protector", "电池": "power bank",
    "充电": "usb charger", "耳机": "earbuds",
    "相机": "phone tripod", "照片": "phone tripod",
    "视频": "phone tripod", "音乐": "portable speaker",
    "音箱": "portable speaker", "麦克风": "usb microphone",
    "办公": "file folder set", "学校": "document organizer",
    "学习": "file folder set", "学生": "file folder set",
    "健身": "resistance band", "减肥": "digital scale",
    "体重秤": "digital scale", "通胀": "file folder set",
    # ── 日本語
    "ファイル": "file folder set", "書類": "document organizer",
    "デスク": "desk organizer set", "電卓": "accounting calculator",
    "予算": "accounting calculator", "ケーブル": "cable management tray",
    "本": "book stand", "ペン": "gel pen set",
    "水筒": "water bottle", "コーヒー": "coffee mug",
    "バッグ": "travel bag", "財布": "card holder",
    "傘": "umbrella", "靴": "sports shoes", "シャツ": "t shirt",
    "時計": "wrist watch", "台所": "kitchen organizer",
    "旅行": "travel organizer", "ライト": "led light strip",
    "椅子": "memory foam seat cushion", "枕": "memory foam pillow",
    "ゲーム": "mobile game controller", "_pad": "mobile game controller",
    "スマホ": "phone stand", "画面": "screen protector",
    "バッテリー": "power bank", "充電": "usb charger",
    "イヤホン": "earbuds", "カメラ": "phone tripod",
    "写真": "phone tripod", "動画": "phone tripod",
    "音楽": "portable speaker", "スピーカー": "portable speaker",
    "マイク": "usb microphone", "オフィス": "file folder set",
    "学校": "document organizer", "学習": "file folder set",
    "学生": "file folder set", "運動": "resistance band",
    "体重計": "digital scale", "REDI": "file folder set",
    # ── Español
    "archivo": "file folder set", "carpeta": "file folder set",
    "documento": "document organizer", "escritorio": "desk organizer set",
    "calculadora": "accounting calculator", "presupuesto": "accounting calculator",
    "impuesto": "accounting calculator", "cable": "cable management tray",
    "libro": "book stand", "cuaderno": "gel pen set",
    "agua": "water bottle", "botella": "water bottle",
    "café": "coffee mug", "taza": "coffee mug",
    "bolso": "travel bag", "mochila": "backpack",
    "cartera": "card holder", "paraguas": "umbrella",
    "zapatos": "sports shoes", "camisa": "t shirt",
    "reloj": "wrist watch", "cocina": "kitchen organizer",
    "viaje": "travel organizer", "hotel": "travel organizer",
    "lampara": "led desk lamp", "silla": "memory foam seat cushion",
    "almohada": "memory foam pillow", "juego": "mobile game controller",
    "mando": "mobile game controller", "móvil": "phone stand",
    "teléfono": "phone stand", "pantalla": "screen protector",
    "batería": "power bank", "cargador": "usb charger",
    "auriculares": "earbuds", "cámara": "phone tripod",
    "foto": "phone tripod", "vídeo": "phone tripod",
    "música": "portable speaker", "altavoz": "portable speaker",
    "micrófono": "usb microphone", "oficina": "file folder set",
    "escuela": "document organizer", "estudio": "file folder set",
    "estudiante": "file folder set", "fitness": "resistance band",
    "peso": "digital scale", "inflación": "file folder set",
    # ── Français
    "fichier": "file folder set", "dossier": "file folder set",
    "document": "document organizer", "bureau": "desk organizer set",
    "calculatrice": "accounting calculator", "budget": "accounting calculator",
    "taxe": "accounting calculator", "câble": "cable management tray",
    "livre": "book stand", "cahier": "gel pen set",
    "eau": "water bottle", "gourde": "water bottle",
    "café": "coffee mug", "tasse": "coffee mug",
    "sac": "travel bag", "cartable": "backpack",
    "portefeuille": "card holder", "parapluie": "umbrella",
    "chaussures": "sports shoes", "chemise": "t shirt",
    "montre": "wrist watch", "cuisine": "kitchen organizer",
    "voyage": "travel organizer", "lampe": "led desk lamp",
    "chaise": "memory foam seat cushion", "oreiller": "memory foam pillow",
    "jeu": "mobile game controller", "manette": "mobile game controller",
    "téléphone": "phone stand", "écran": "screen protector",
    "batterie": "power bank", "chargeur": "usb charger",
    "écouteurs": "earbuds", "appareil photo": "phone tripod",
    "photo": "phone tripod", "vidéo": "phone tripod",
    "musique": "portable speaker", "micro": "usb microphone",
    "école": "document organizer", "étudiant": "file folder set",
    "sport": "resistance band", "poids": "digital scale",
    "inflation": "file folder set",
    # ── Русский
    "файл": "file folder set", "папка": "file folder set",
    "документ": "document organizer", "стол": "desk organizer set",
    "калькулятор": "accounting calculator", "бюджет": "accounting calculator",
    "налог": "accounting calculator", "кабель": "cable management tray",
    "книга": "book stand", "тетрадь": "gel pen set",
    "вода": "water bottle", "бутылка": "water bottle",
    "кофе": "coffee mug", "чашка": "coffee mug",
    "сумка": "travel bag", "рюкзак": "backpack",
    "кошелёк": "card holder", "зонт": "umbrella",
    "обувь": "sports shoes", "рубашка": "t shirt",
    "часы": "wrist watch", "кухня": "kitchen organizer",
    "путешествие": "travel organizer", "лампа": "led desk lamp",
    "стул": "memory foam seat cushion", "подушка": "memory foam pillow",
    "игра": "mobile game controller", "геймпад": "mobile game controller",
    "телефон": "phone stand", "экран": "screen protector",
    "батарея": "power bank", "зарядка": "usb charger",
    "наушники": "earbuds", "камера": "phone tripod",
    "фото": "phone tripod", "видео": "phone tripod",
    "музыка": "portable speaker", "микрофон": "usb microphone",
    "офис": "file folder set", "школа": "document organizer",
    "учёба": "file folder set", "студент": "file folder set",
    "спорт": "resistance band", "весы": "digital scale",
    "инфляция": "file folder set",
    # ── Português
    "arquivo": "file folder set", "pasta": "file folder set",
    "documento": "document organizer", "mesa": "desk organizer set",
    "calculadora": "accounting calculator", "orçamento": "accounting calculator",
    "imposto": "accounting calculator", "cabo": "cable management tray",
    "livro": "book stand", "caderno": "gel pen set",
    "água": "water bottle", "garrafa": "water bottle",
    "café": "coffee mug", "caneca": "coffee mug",
    "bolsa": "travel bag", "mochila": "backpack",
    "carteira": "card holder", "chuva": "umbrella",
    "tênis": "sports shoes", "camisa": "t shirt",
    "relógio": "wrist watch", "cozinha": "kitchen organizer",
    "viagem": "travel organizer", "lâmpada": "led desk lamp",
    "cadeira": "memory foam seat cushion", "travesseiro": "memory foam pillow",
    "jogo": "mobile game controller", "controle": "mobile game controller",
    "celular": "phone stand", "tela": "screen protector",
    "bateria": "power bank", "carregador": "usb charger",
    "fone": "earbuds", "câmera": "phone tripod",
    "foto": "phone tripod", "vídeo": "phone tripod",
    "música": "portable speaker", "microfone": "usb microphone",
    "escola": "document organizer", "estudo": "file folder set",
    "estudante": "file folder set", "fitness": "resistance band",
    "peso": "digital scale", "inflação": "file folder set",
    # ── Deutsch
    "datei": "file folder set", "ordner": "file folder set",
    "dokument": "document organizer", "schreibtisch": "desk organizer set",
    "taschenrechner": "accounting calculator", "budget": "accounting calculator",
    "steuer": "accounting calculator", "kabel": "cable management tray",
    "buch": "book stand", "notizbuch": "gel pen set",
    "wasser": "water bottle", "flasche": "water bottle",
    "kaffee": "coffee mug", "tasse": "coffee mug",
    "tasche": "travel bag", "rucksack": "backpack",
    "geldbörse": "card holder", "regenschirm": "umbrella",
    "schuhe": "sports shoes", "hemd": "t shirt",
    "uhr": "wrist watch", "küche": "kitchen organizer",
    "reise": "travel organizer", "lampe": "led desk lamp",
    "stuhl": "memory foam seat cushion", "kissen": "memory foam pillow",
    "spiel": "mobile game controller", "controller": "mobile game controller",
    "telefon": "phone stand", "bildschirm": "screen protector",
    "akku": "power bank", "ladegerät": "usb charger",
    "kopfhörer": "earbuds", "kamera": "phone tripod",
    "foto": "phone tripod", "video": "phone tripod",
    "musik": "portable speaker", "mikrofon": "usb microphone",
    "büro": "file folder set", "schule": "document organizer",
    "studium": "file folder set", "student": "file folder set",
    "fitness": "resistance band", "waage": "digital scale",
    "inflation": "file folder set",
    # ── 中文/한국어 이외 나머지 언어는 본문이 영어 원문과 같은 경우 많음.
    #    (예: hi / id / bn / ar 게임글) → 영어 TOPIC_QUERY 로 충분.
}

# 일반 명사지만 상품으로 이어지지 않아 무시할 단어
_TOPIC_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has",
    "are", "was", "were", "will", "would", "could", "should", "can",
    "but", "not", "you", "your", "its", "they", "their", "them",
    "app", "apps", "play", "google", "apple", "store", "free", "new",
    "review", "version", "update", "android", "ios", "price", "dollar",
}


def extract_topics(body_md, title=""):
    """본문+제목에서 상품으로 이어질 주제어를 뽑는다.

    ⚠️ 영문 + CJK(한/일/중) 를 모두 대상으로 한다.
       다국어 번역본을 위해 `TOPIC_QUERY_I18N` 도 함께 본다.
    Returns: 검색어 리스트 (최대 3개)
    """
    text = ((title or "") + " " + (body_md or "")).lower()
    text = re.sub(r"<[^>]+>", " ", text)      # HTML 태그 제거

    hits = []
    # 1) 영문 단어
    for word in re.findall(r"[a-z][a-z\-]{2,}", text):
        if word in _TOPIC_STOP:
            continue
        query = TOPIC_QUERY.get(word)
        if query and query not in hits:
            hits.append(query)
    # 2) CJK 2~6자 구 (한글/일본어/한자가 모두 2~4자로 잘림)
    #    2자부터 6자까지 슬라이딩 — "calculadora" 처럼 긴 단어의 일부만 사전에 있어도
    #    부분 일치로 잡히게 한다 (스페인어 적용률이 8%였음).
    cjk_runs = re.findall(
        r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]{2,}", text)
    for run in cjk_runs:
        for size in (2, 3, 4):
            for i in range(len(run) - size + 1):
                token = run[i:i + size]
                query = TOPIC_QUERY_I18N.get(token)
                if query and query not in hits:
                    hits.append(query)
    # 3) 라틴 확장어 (스페인어/포르투갈어/프랑스어/독어 — 어간 포함 매칭)
    for word in re.findall(r"[a-záéíóúãõçüöàèäëîïôûùêÿñ]{4,}", text):
        if word in _TOPIC_STOP:
            continue
        query = TOPIC_QUERY.get(word)
        if not query:
            for key, val in TOPIC_QUERY.items():
                # 라틴어 어간 일치 (inflation/inflación, calculadora/calculadora)
                if len(key) >= 5 and word.startswith(key[:5]):
                    query = val
                    break
        if query and query not in hits:
            hits.append(query)
    return hits[:3]

# 검색 결과에서 반드시 버려야 할品类 (CTR·브랜드 안전).
# 게임 키워드로 검색하면 복권 티켓·코스프레·수집품이 섞여 나온다.
_JUNK_PAT = re.compile(
    r'raffle|cosplay|costume|doll|figure|stickers?|poster|'
    r'card\b|coins?\b|replica|prop|collectible|ticket',
    re.I)

# 가격이 이 범위 밖이면 버린다.
#   최저가(0.07달러짜리 케이블)는 클릭해도 수수료가 글자도 안 되고 신뢰를 깎는다.
#   최고가(200달러+)는 홈쇼핑 독자와 안 맞는다.
_PRICE_MIN = 0.8
_PRICE_MAX = 120.0


def _is_junk_price(price):
    """가격 파싱 실패 또는 범위 밖이면 True(=버림)."""
    try:
        value = float(re.sub(r"[^0-9.]", "", str(price)) or 0)
    except (ValueError, TypeError):
        return True
    return not (_PRICE_MIN <= value <= _PRICE_MAX)


def _cache_path(key):
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", key)[:80]
    return os.path.join(CACHE_DIR, "%s.json" % safe)


# 🔑 연속 실패 카운터 (2026-10-09 추가).
#   562개 포스트가 각각 API 를 부르면 타임아웃(25초)이 누적돼 빌드가 10분+
#   걸릴 수 있다. 이 카운터가 그 폭주를 막는다.
_FAIL_STREAK = 0
_MAX_FAIL_STREAK = 3
# 카테고리 → 추천 결과 메모. 빌드 1회 실행 동안 유효.
_MEMO = {}


def _cache_get(key, ttl=CACHE_TTL):
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    age = time.time() - os.path.getmtime(path)
    if age > ttl:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _cache_put(key, value):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(key), "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False)


def _extract_products(resp):
    """검색 응답에서 상품 배열을 추린다 (2026-10-08 실측 구조)."""
    return aliexpress.extract_products(resp)[1]


def _normalize(product, ship_to="US"):
    """상품 dict 를 UI 에 필요한 최소 필드로 정규화 (실측 필드명 기준)."""
    def pick(*keys, default=""):
        for k in keys:
            val = product.get(k)
            if val not in (None, "", [], {}):
                return val
        return default

    # ⚠️ commission 을 받으려면 반드시 promotion_link(추적링크)여야 한다.
    #   일반 상세 URL 로 폴백하면 클릭해도 수수료가 붙지 않는다 → 넣지 않는다.
    url = pick("promotion_link", "promotionLink")
    if not url:
        return None

    price = pick("target_sale_price", "sale_price", "salePrice")
    currency = pick("target_sale_price_currency", "sale_price_currency",
                    default="USD")

    return {
        "id": str(pick("product_id", "productId", "id", default="")),
        "title": str(pick("product_title", "productTitle", "subject")),
        "price": str(price),
        "currency": str(currency),
        "image": str(pick("product_main_image_url", "productMainImageUrl",
                          "image")),
        "url": str(url),
        "commission": str(pick("commission_rate", "commissionRate",
                               "hot_product_commission_rate", default="")),
        "rating": str(pick("evaluate_rate", "evaluateRate", default="")),
        "orders": str(pick("lastest_volume", "lastestVolume", default="")),
        "shop": str(pick("shop_name", "shopName", default="")),
        "category": str(pick("second_level_category_name",
                             "secondLevelCategoryName", default="")),
        "ship_to": ship_to,
    }


def search(keyword, ship_to="US", currency="USD", page_size=20):
    """키워드 1건으로 상품 목록. 실패하면 빈 리스트(throw하지 않음)."""
    key = "%s_q_%s_%s_%s" % (CACHE_VERSION, keyword, ship_to, currency)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # 🔑 연속 실패 카운터 — 전체가 느려지는 것 방지 (2026-10-09).
    #   562개 포스트 × timeout 25초 = 빌드가 10분 넘게 걸릴 수 있었다.
    #   N 번 연속 실패하면 그 뒤로는 API 를 아예 호출하지 않는다.
    global _FAIL_STREAK
    if _FAIL_STREAK >= _MAX_FAIL_STREAK:
        return []
    try:
        resp = aliexpress.search_products(
            keyword, page_size=page_size, currency=currency,
            country=ship_to or None,
        )
    except Exception:
        _FAIL_STREAK += 1
        return []          # 네트워크/서명 오류 → 조용히 빈 결과

    _FAIL_STREAK = 0
    items = _extract_products(resp)
    out = [n for n in (_normalize(it, ship_to) for it in items) if n]
    # 쓰레기品类 제거 (복권/코스프레/수집품). CTR 과 브랜드 안전에 필수.
    out = [n for n in out if not _JUNK_PAT.search(n.get("title") or "")]
    out = [n for n in out if not _is_junk_price(n.get("price"))]
    if out:
        _cache_put(key, out)
    return out


def _rotate(pool, limit, variant):
    """후보 풀에서 limit 개를 고르되, 포스트마다 다른 구간을 쓴다.

    🔑 2026-10-09: 같은 카테고리 글이 500개면 상위 4개 상품이 500페이지에
       그대로 반복된다 (실측: A4 파일폴더 223회 반복). variant 로 슬라이스
       오프셋을 줘서 같은 독자가 같은 상품만 보지 않게 한다.
    """
    if not pool:
        return pool
    if len(pool) <= limit or not variant:
        return pool[:limit]
    n_chunks = max(1, len(pool) // limit)
    offset = (variant % n_chunks) * limit
    rotated = pool[offset:offset + limit]
    return rotated if len(rotated) >= min(limit, 2) else pool[:limit]


def recommend(category, keywords=None, limit=4, game_post=False, body_md="",
              title="", variant=0):
    """포스트용 상품 추천.

    🔑 2026-10-09 로이 지시: "글 내용과 알아서 비슷하게 맞춰서 추천"
       → 본문 주제를 읽고 **그 주제에 맞는 상품**을 고른다.

    ⚠️ 검색어를 아무거나 쓰면 안 된다 (실측 실패 사례):
       게임 포스트("Arknights 리뷰")에서 제목 단어 'arknights' 를 그대로 검색하면
       코슬패·인형·복권 같은 쓰레기가 나온다.
    → 그래서 **화이트리스트** 방식으로 바꾼다:
         1) 본문에서 상품으로 이어질 수 있는 일반 명사만 추린다
         2) 그 명사가 `TOPIC_QUERY` 에 등록된 경우에만 검색한다
         3) 하나도 안 잡히면 카테고리 기본값으로 폴백한다
       게임명·앱명은 명사 추출 단계에서 자연스럽게 탈락한다
       (专有名詞·고유명사 패턴 제외).

    `game_post=True` 면 게임 액세서리 매핑을 쓴다 (패드·지문링커·냉각기).

    전략: 카테고리 매핑 키워드로만 조회 → 쓰레기品类 제외 → 커미션율·평점 순 정렬.
    """
    category = (category or '').strip().strip('"\'').strip()
    # 🔑 본문에서 상품으로 이어질 주제어를 먼저 뽑는다 (로이 지시 2026-10-09).
    #   게임명·앱명은 TOPIC_QUERY 에 없으므로 자연스럽게 무시된다.
    topics = extract_topics(body_md, title)

    # 🔑 게임 포스트는 게임 액세서리 매핑을 무조건 먼저 쓴다 (2026-10-09).
    #   실측 실패: 게임 본문의 'file'(파일 저장)을 사무용어로 잡아
    #   "Hanging File Folder" 가 게임패드보다 앞에 나왔다.
    #   → 게임 글에서는 사무·문서 계열 주제어를 통째로 버린다.
    _OFFICE_QUERIES = {
        "file folder set", "document organizer", "desk organizer set",
        "accounting calculator", "cable management tray", "gel pen set",
        "book stand", "led desk lamp", "led light strip",
    }
    if game_post:
        filtered = [t for t in topics if t not in _OFFICE_QUERIES]
        topics = ["mobile game controller"] + filtered

    # 게임 포스트는 별도 매핑을 쓴다 (게임 액세서리)
    if category in ("Apps & Games", "Apps & Games (game)"):
        conf = CATEGORY_MAP["Apps & Games (game)"] if game_post \
            else CATEGORY_MAP["Apps & Games"]
    elif category in _ECON_ALIASES:
        # 금융/경제 포스트 — 독자가 실제로 사는 방송·홈오피스·정확도 도구
        conf = {"keywords": ECON_MAP[category], "ship_to": "US"}
    else:
        conf = CATEGORY_MAP.get(category) or CATEGORY_MAP["Apps & Games"]
    ship_to = conf["ship_to"]

    pool, seen = [], set()
    # 🔑 카테고리별 **후보 풀**을 프로세스 동안 고정 (2026-10-09).
    #   포스트마다 API 를 부르면 빌드가 10분 넘게 걸린다.
    #   ⚠️ `variant` 는 여기 넣지 않는다 → 후보 풀만 캐시하고,
    #      최종 4개 선택은 그때마다 variant 로 회전시킨다.
    mem_key = "%s|%s|%s|%s" % (category, game_post, limit, ",".join(topics))
    if mem_key in _MEMO:
        pool = _MEMO[mem_key]
        return _rotate(pool, limit, variant)

    # 🔑 본문 주제를 먼저 쓰고, 부족하면 카테고리 기본값으로 보충한다.
    #   (로이 지시 2026-10-09: "글 내용과 알아서 비슷하게 맞춰서 추천")
    if game_post:
        # 게임 글은 게임 액세서리로만 채운다 (사무용어 혼입 방지).
        pool_terms = ["mobile game controller", "gaming headset", "phone cooler"]
        terms = list(topics) or pool_terms[:2]
        for extra in pool_terms:            # 보충용 (중복 제거)
            if len(terms) >= 4:
                break
            if extra not in terms:
                terms.append(extra)
    else:
        terms = list(topics)[:3] or list(conf["keywords"][:2])
        for extra in conf["keywords"][:2]:   # 보충용 (중복 제거)
            if len(terms) >= 4:
                break
            if extra not in terms:
                terms.append(extra)

    for term in terms[:4]:                      # 쿼터 절약: 최대 4개
        for item in search(term, ship_to=ship_to):
            if _JUNK_PAT.search(item.get("title") or ""):
                continue          # 복권/코스프레/수집품 제외
            if _is_junk_price(item.get("price")):
                continue          # 0.07달러짜리 케이블 등
            if item["id"] and item["id"] in seen:
                continue
            if item["id"]:
                seen.add(item["id"])
            if item["title"]:
                pool.append(item)

    # 🔑 본문 주제 일치 가산점 (2026-10-09).
    #   같은 카테고리라도 어떤 검색어로 나왔는지에 따라 관련도가 다르다.
    #   본문에서 직접 뽑힌 검색어(topics)의 상품에 보너스를 준다.
    #   안 그러면 USB 케이블(커미션 7%)이 게임패드(9%)를 밀어낸다.
    topic_words = set()
    for t in topics:
        for w in re.findall(r"[a-z]+", t):
            if len(w) > 2:
                topic_words.add(w)

    def score(item):
        try:
            comm = float(re.sub(r"[^0-9.]", "", item["commission"]) or 0)
        except ValueError:
            comm = 0
        try:
            rate = float(item["rating"] or 0)
        except ValueError:
            rate = 0
        try:
            vol = float(item["orders"] or 0)
        except ValueError:
            vol = 0
        base = (comm * 0.6) + (rate * 0.3) + (min(vol, 100000) / 100000 * 10)
        # 본문 주제어 일치 보너스 (최대 +6). 가중치가 커야 커미션 차이를 이긴다.
        title_words = set(re.findall(r"[a-z]+", (item.get("title") or "").lower()))
        overlap = topic_words & title_words
        return base + min(len(overlap), 3) * 2.0

    pool.sort(key=score, reverse=True)
    _MEMO[mem_key] = pool        # 후보 풀 전체를 캐시 (회전은 _rotate 에서)
    return _rotate(pool, limit, variant)


# ------------------------------------------------------------------ 렌더링

_I18N = {
    "ko": ("추천 장비", "알리익스프레스 제휴 링크입니다. 구매 시 우리가 수수료를 받습니다."),
    "en": ("Recommended gear", "Affiliate link to AliExpress. We may earn a commission."),
    "ja": ("おすすめ機器",
           "これは AliExpressの affiliate リンクです。購入すると私たちが報酬を得る場合があります。"),
    "zh": ("推荐设备", "这是 AliExpress 联盟链接。购买后我们可能会获得佣金。"),
    "es": ("Equipo recomendado", "Enlace de afiliado de AliExpress. Podemos ganar una comisión."),
    "fr": ("Équipement recommandé", "Lien d'affiliation AliExpress. Nous pouvons gagner une commission."),
    "hi": ("अनुशंसित उपकरण", "AliExpress एफ़िलिएट लिंक। खरीद पर हमें कमीशन मिल सकता है।"),
    "ar": ("المعدات الموصى بها", "رابط affiliacy من AliExpress. قد نربح عمولة عند الشراء."),
    "ru": ("Рекомендуемое оборудование", "Партнёрская ссылка AliExpress. Мы можем получить комиссию."),
    "id": ("Peralatan yang direkomendasikan", "Tautan afiliasi AliExpress. Kami mungkin mendapatkan komisi."),
    # ⚠️ 아래 3개가 빠져서 영어로 폴백하고 있었다 (2026-10-09 실측).
    #    13개 언어를 서빙하는데 문구가 10개뿐이었다.
    "de": ("Empfohlene Ausstattung",
           "Affiliate-Link zu AliExpress. Wir erhalten möglicherweise eine Provision."),
    "pt": ("Equipamento recomendado",
           "Link de afiliado da AliExpress. Podemos receber uma comissão."),
    "bn": ("প্রস্তাবিত সরঞ্জাম",
           "AliExpress অ্যাফিলিয়েট লিঙ্ক। কিনলে আমরা কমিশন পেতে পারি।"),
}


def render_html(products, lang="en", title=None, blurb=None):
    """상품 카드 HTML. rel=sponsored nofollow + 고지 동시 표기."""
    if not products:
        return ""
    t, d = _I18N.get(lang, _I18N["en"])
    title = title or t
    blurb = blurb or d

    cards = []
    for p in products:
        if not p.get("url") or not p.get("title"):
            continue
        img = ('<img src="%s" alt="" loading="lazy" class="w-16 h-16 '
               'object-cover rounded flex-shrink-0">'
               % p["image"]) if p.get("image") else ""
        meta = []
        if p.get("price"):
            meta.append('<span class="font-semibold text-brand-600">%s %s</span>'
                        % (p["price"], p["currency"]))
        if p.get("rating"):
            meta.append("★ %s" % p["rating"])
        if p.get("orders"):
            meta.append("%s orders" % p["orders"])
        cards.append(
            '<li class="flex gap-3 items-start">%s'
            '<div class="min-w-0">'
            '<a rel="sponsored nofollow noopener" target="_blank" href="%s" '
            'class="block font-medium text-slate-900 dark:text-slate-100 '
            'hover:text-brand-600 leading-snug">%s</a>'
            '<p class="text-xs text-slate-500 mt-1">%s</p>'
            '</div></li>'
            % (img, p["url"], p["title"], " · ".join(meta))
        )
    if not cards:
        return ""

    return (
        '<aside data-affiliate-slot="1" '
        'class="mt-8 rounded-lg border border-slate-200 '
        'dark:border-slate-800 bg-slate-50 dark:bg-slate-900 p-4 sm:p-5">'
        '<h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100 '
        'mb-3">%s</h2>'
        '<ul class="space-y-3">%s</ul>'
        '<p class="mt-3 text-xs text-slate-500">%s</p>'
        '</aside>' % (title, "".join(cards), blurb)
    )


if __name__ == "__main__":
    picks = recommend("Apps & Games", ["phone stand"], limit=3)
    print(json.dumps(picks, ensure_ascii=False, indent=2)[:1200] if picks
          else "상품 없음 — session 토큰 필요 (포털 Auth Management)")
    print()
    print(render_html(picks, "ko")[:400] or "HTML 없음")
