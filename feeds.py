# -*- coding: utf-8 -*-
"""
뉴스 출처(RSS) 목록입니다.
나중에 출처를 더하거나 빼고 싶으면 이 목록만 고치면 됩니다.
( "이름": "RSS 주소" 형태이고, category 로 분류해 둡니다. )
"""

FEEDS = [
    # ── 해외: 미국 증시 종합 ─────────────────────────────
    {"name": "CNBC 마켓",        "category": "해외증시", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"},
    {"name": "CNBC 주요뉴스",    "category": "해외증시", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"},
    {"name": "MarketWatch",      "category": "해외증시", "url": "http://feeds.marketwatch.com/marketwatch/topstories/"},
    {"name": "Yahoo Finance",    "category": "해외증시", "url": "https://finance.yahoo.com/news/rssindex"},

    # ── 해외: 암호화폐 ───────────────────────────────────
    {"name": "CoinDesk",         "category": "암호화폐", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Cointelegraph",    "category": "암호화폐", "url": "https://cointelegraph.com/rss"},

    # ── 국내: 코스피·코스닥·경제 ─────────────────────────
    {"name": "연합뉴스 경제",     "category": "국내증시", "url": "https://www.yna.co.kr/rss/economy.xml"},
    {"name": "한국경제 증권",     "category": "국내증시", "url": "https://www.hankyung.com/feed/finance"},
    {"name": "매일경제 증권",     "category": "국내증시", "url": "https://www.mk.co.kr/rss/50200011/"},
]

# 증시와 관련된 뉴스만 골라내기 위한 키워드입니다.
# 제목/요약에 이 단어 중 하나라도 들어 있으면 "증시 관련"으로 봅니다.
# (해외 피드는 영어, 국내 피드는 한국어라서 둘 다 넣었습니다.)
KEYWORDS = [
    # 한국어
    "증시", "코스피", "코스닥", "주식", "주가", "지수", "상장", "반도체",
    "환율", "금리", "외국인", "기관", "수급", "나스닥", "S&P", "뉴욕증시",
    "비트코인", "이더리움", "코인", "가상자산", "암호화폐", "연준", "Fed",
    # 영어
    "stock", "market", "shares", "nasdaq", "s&p", "dow", "wall street",
    "fed", "rate", "inflation", "bitcoin", "ethereum", "crypto", "earnings",
    "treasury", "yield", "index", "equities",
]
