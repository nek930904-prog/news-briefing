# -*- coding: utf-8 -*-
"""
주간 증시 브리핑 (일요일 저녁 6시)
  핵심 주제: "핫 섹터의 변화 흐름"
  - 섹터 ETF·지수의 '주간 등락'을 무료 시세로 받아와 (이번 주 뜬/식은 섹터)
  - 최근 뉴스로 맥락을 붙여
  - Claude가 섹터 흐름 중심의 주간 브리핑을 작성하고
  - 노션에 "주간 증시 브리핑 - YYYY-MM-DD" 하위 페이지로 정리합니다.

일간(main.py)의 공통 기능(뉴스 수집·Claude 호출·노션 작성)을 그대로 재사용합니다.
"""

import sys
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import requests

# 일간 코드(main.py)에서 공통 기능을 가져옵니다.
from main import (
    KST,
    check_secrets,
    collect_news,
    _claude_text,
    parse_briefing,
    create_notion_page,
)

# 윈도우 콘솔 이모지 출력 깨짐 방지
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# 주간 등락을 볼 섹터·지수 (이름, 야후 심볼)
# 미국 섹터 ETF(SPDR) 위주 + 반도체/원자력/주요 지수/코인
SECTOR_SYMBOLS = [
    ("반도체(SOXX)", "SOXX"),
    ("기술(XLK)", "XLK"),
    ("커뮤니케이션(XLC)", "XLC"),
    ("임의소비재(XLY)", "XLY"),
    ("금융(XLF)", "XLF"),
    ("헬스케어(XLV)", "XLV"),
    ("산업재(XLI)", "XLI"),
    ("에너지(XLE)", "XLE"),
    ("소재(XLB)", "XLB"),
    ("필수소비재(XLP)", "XLP"),
    ("유틸리티(XLU)", "XLU"),
    ("부동산(XLRE)", "XLRE"),
    ("원자력·청정에너지(ICLN)", "ICLN"),
    ("금(GLD)", "GLD"),
]

INDEX_SYMBOLS = [
    ("코스피", "^KS11"),
    ("코스닥", "^KQ11"),
    ("S&P500", "^GSPC"),
    ("나스닥 종합", "^IXIC"),
    ("원/달러 환율", "KRW=X"),
    ("비트코인", "BTC-USD"),
    ("이더리움", "ETH-USD"),
]


def _weekly_change(symbol):
    """
    한 종목의 'ISO 주(월~일)' 기준 주간 등락률(%)을 구합니다. (야후 차트, 키 불필요)

      - 현재값 = 이번 주(월~현재)에 속한 거래일들의 '가장 최근 종가'
      - 기준값 = 이번 주 이전 주들 중 '가장 마지막 거래일'의 종가
                 (요일 고정 없이 데이터에 실제 존재하는 직전 거래일 사용
                  → 금요일 휴장이면 목요일, 연휴면 그 앞 거래일로 자동 하향)
      - 등락률 = (현재값 ÷ 기준값 − 1) × 100
      - 이번 주에 거래일이 하나도 없으면(연휴 등) → 최근 두 거래주의 마지막 거래일끼리 비교(fallback)

    반환: (현재값, 등락률%) 또는 (None, None)
    """
    headers = {"User-Agent": "Mozilla/5.0 (briefing-bot)"}
    # 직전 주 거래일이 항상 포함되도록 넉넉히 1개월치를 받습니다.
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1mo"
    r = requests.get(url, headers=headers, timeout=10)
    result = r.json()["chart"]["result"][0]

    timestamps = result.get("timestamp") or []
    closes = result["indicators"]["quote"][0].get("close") or []
    gmtoffset = result.get("meta", {}).get("gmtoffset", 0)  # 거래소 현지시각 보정(초)

    # (거래소 현지 날짜, 종가) 쌍을 시간순으로 정리 (빈 종가는 제외)
    bars = []
    for ts, c in zip(timestamps, closes):
        if c is None or ts is None:
            continue
        local_date = (datetime.fromtimestamp(ts, tz=timezone.utc)
                      + timedelta(seconds=gmtoffset)).date()
        bars.append((local_date, c))
    if len(bars) < 2:
        return None, None
    bars.sort(key=lambda x: x[0])

    def week_key(d):
        iso = d.isocalendar()      # (ISO년, ISO주, 요일)
        return (iso[0], iso[1])    # 같은 ISO 주면 같은 키

    # 각 ISO 주의 '마지막 거래일 종가'를 주 순서대로 보관
    last_close_by_week = OrderedDict()
    for d, c in bars:
        last_close_by_week[week_key(d)] = c
    weeks = list(last_close_by_week.items())   # [(주키, 마지막종가), ...] 오름차순

    # '이번 주'는 실행 시점(거래소 현지시각) 기준 ISO 주
    now_local = (datetime.now(timezone.utc) + timedelta(seconds=gmtoffset))
    cur_wk = week_key(now_local.date())

    if cur_wk in last_close_by_week:
        cur_close = last_close_by_week[cur_wk]
        prior = [(wk, c) for wk, c in weeks if wk < cur_wk]
        if not prior:
            return None, None
        base_close = prior[-1][1]
    else:
        # 이번 주에 거래일이 하나도 없음 → 최근 두 거래주 비교
        if len(weeks) < 2:
            return None, None
        cur_close = weeks[-1][1]
        base_close = weeks[-2][1]

    pct = (cur_close / base_close - 1) * 100
    return cur_close, pct


def fetch_weekly_performance(symbols, label):
    """심볼 목록의 주간 등락을 모아 '내림차순(많이 오른 순)' 텍스트로 만듭니다."""
    print(f"📊 {label} 주간 등락 수집 중...")
    rows = []
    for name, sym in symbols:
        try:
            last, pct = _weekly_change(sym)
            if pct is None:
                continue
            rows.append((name, last, pct))
        except Exception as e:
            print(f"   ⚠️ {name}({sym}) 실패(건너뜀): {e}")
    rows.sort(key=lambda x: x[2], reverse=True)   # 많이 오른 순
    lines = []
    for name, last, pct in rows:
        arrow = "▲" if pct >= 0 else "▼"
        lines.append(f"- {name}: 주간 {arrow}{abs(pct):.2f}% (현재 {last:,.2f})")
    print(f"📊 {label} {len(rows)}건 수집 완료\n")
    return "\n".join(lines)


def build_weekly_prompt(week_label, sector_block, index_block, news_block):
    return f"""당신은 증시 브리핑 작가입니다. 아래 '이번 주({week_label})' 데이터를 바탕으로
'핫 섹터의 변화 흐름'에 초점을 맞춘 한국어 주간 증시 브리핑을 작성하세요.

[작성 규칙]
- 이번 주 핵심은 "어떤 섹터가 뜨고 식었는지, 그리고 자금 흐름이 어떻게 바뀌었는지"입니다. 여기에 집중하세요.
- 아래 [섹터 주간 등락]·[지수 주간 등락] 수치를 본문에 반드시 활용하세요. 가장 많이 오른/내린 섹터를 콕 집어 주세요.
- 수치는 데이터로 주고, '왜 그 섹터가 움직였는지'는 [최근 뉴스]에 근거해 설명하세요. 근거가 약하면 "관련 뉴스가 적었음"이라고 솔직히 쓰세요.
- 절대 사실을 지어내지 마세요.

[본문 작성 스타일 — 아주 중요]
- 줄글 대신 '개조식 글머리표'로 정리: 한 줄에 한 주제. 형식 "- 주제: 핵심 내용"
- 인과관계는 → , 의미 풀이는 = 로 간결하게 연결. 어미는 "~음/~함/~됨".
- 부연/세부는 두 칸 들여쓴 하위 글머리표("  - ")로 1~2개.
- 마크다운/HTML(**굵게**, <br>, # 등) 쓰지 말 것. 글머리표는 "- "만.
- 본문에 "(뉴스 [3])" 같은 번호 표시는 쓰지 마세요. 출처는 오직 @LINK@ 줄로만 표시하세요.
- 정말 중요한 핵심 수치·섹터명은 ==이렇게== 등호 두 개로 감싸면 노란 형광펜이 됩니다. 줄마다 1개 정도만.
  (주제 라벨 전체나 콜론(:)은 ==로 감싸지 말고 짧은 단어/수치만)
- 등락은 ▲/▼ 또는 +/- 부호와 함께 적으면 자동으로 색이 입혀져요(상승=빨강, 하락=파랑). 예: "▲18.1%", "▼7.7%"
- @SUMMARY@ 한 줄 요약은 친근한 한 문장("~예요")으로 먼저 감 잡게 쓰기.

[섹터 주간 등락] (많이 오른 순)
{sector_block if sector_block else "(수집 실패)"}

[지수·환율·코인 주간 등락]
{index_block if index_block else "(수집 실패)"}

[최근 뉴스]
{news_block}

[출력 형식]
표시(@HEADLINE@, @SECTION@, @SUMMARY@, @LINK@)를 정확히 그대로 쓰세요. 코드블록(```)·다른 설명 금지.
링크 줄은 "제목 ||| 주소" 형태. 각 섹션은 @SUMMARY@ 한 줄 뒤에 개조식 본문을 씁니다.

@HEADLINE@
- (이번 주를 관통하는 핵심 한 줄)
- (핵심 한 줄)
- (핵심 한 줄)

@SECTION@ 이번 주 시장 흐름 — 지수 한눈에
@SUMMARY@ (한 문장)
(코스피/코스닥/S&P500/나스닥/환율/코인 주간 등락 정리)
@LINK@ 기사 제목 ||| https://링크주소

@SECTION@ 🔥 핫 섹터 — 이번 주 강세
@SUMMARY@ (한 문장)
(가장 많이 오른 섹터들과 그 이유·흐름)
@LINK@ 기사 제목 ||| https://링크주소

@SECTION@ 🧊 식은 섹터 — 이번 주 약세
@SUMMARY@ (한 문장)
(가장 많이 내린 섹터들과 그 이유)
@LINK@ 기사 제목 ||| https://링크주소

@SECTION@ 🔁 섹터 로테이션 — 자금 흐름의 변화
@SUMMARY@ (한 문장)
(돈이 어느 섹터에서 어느 섹터로 이동했는지, 한 주간 흐름의 변화)
@LINK@ 기사 제목 ||| https://링크주소

@SECTION@ 🔭 다음 주 관심 포인트 — 주목 섹터 & 뉴스
@SUMMARY@ (한 문장)
다음 주에 관심 가져볼 만한 것을 아래 두 묶음으로 구체적으로 정리하세요(개조식):
- 주목 섹터: 다음 주 눈여겨볼 섹터 2~4개 + 각 이유(이번 주 흐름의 연장/반전, 수혜·리스크). 형식 "- 섹터명: 이유 → 체크포인트"
- 주목 뉴스·이벤트: 다음 주 예정·진행 중이라 챙겨야 할 이슈/일정 2~4개 (예: 지표 발표, 실적, 정책, 협상 등). 형식 "- 이벤트: 무엇 → 시장 영향 포인트"
근거가 약하면 "관련 뉴스가 적었음"이라고 솔직히 쓰세요.
@LINK@ 기사 제목 ||| https://링크주소
"""


def main():
    check_secrets()
    today = datetime.now(KST)
    week_start = today - timedelta(days=6)   # 지난 7일(월~일 근사)
    week_label = f"{week_start.strftime('%Y-%m-%d')} ~ {today.strftime('%Y-%m-%d')}"

    # 1) 섹터·지수 주간 등락
    sector_block = fetch_weekly_performance(SECTOR_SYMBOLS, "섹터")
    index_block = fetch_weekly_performance(INDEX_SYMBOLS, "지수")

    # 2) 최근 뉴스(맥락용) — 주말까지 모이도록 넉넉히 72시간
    items = collect_news(hours=72, max_items=70)
    news_lines = []
    for i, it in enumerate(items, 1):
        news_lines.append(
            f"[{i}] ({it['category']}/{it['source']}) {it['title']}\n"
            f"    요약: {it['summary']}\n    링크: {it['link']}"
        )
    news_block = "\n".join(news_lines)

    # 3) Claude로 주간 브리핑 작성
    print("🤖 Claude가 주간 브리핑을 작성하는 중입니다...")
    prompt = build_weekly_prompt(week_label, sector_block, index_block, news_block)
    raw = _claude_text(prompt)
    data = parse_briefing(raw)
    data["headline_label"] = "📌 이번 주 핵심"
    print("🤖 작성 완료!\n")

    # 4) 노션에 작성 (제목·맨 위에 작성 시각 표시)
    now = datetime.now(KST)
    data["generated_at"] = now.strftime("%Y-%m-%d %H:%M (%a) KST")
    title = f"주간 증시 브리핑 - {today.strftime('%Y-%m-%d')} 주간 ({now.strftime('%H:%M')} 작성)"
    create_notion_page(data, title)


if __name__ == "__main__":
    main()
