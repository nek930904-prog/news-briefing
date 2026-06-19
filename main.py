# -*- coding: utf-8 -*-
"""
증시 뉴스 브리핑 자동 작성기
  1) 무료 RSS에서 증시 뉴스를 모으고 (제목·요약·링크만)
  2) Claude(Sonnet)로 7개 구조에 맞게 요약한 뒤
  3) 노션 "증시 브리핑" 페이지 아래에 새 하위 페이지로 작성합니다.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta

# 윈도우 한글 콘솔(cp949)에서 이모지가 출력되다 멈추는 것을 막기 위해
# 화면 출력 글자를 UTF-8로 맞춥니다.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import feedparser
import requests
from anthropic import Anthropic
from dotenv import load_dotenv

from feeds import FEEDS, KEYWORDS

# .env 파일에 적어둔 비밀값들을 불러옵니다.
load_dotenv()

# ── 환경변수(비밀값) 읽기 ───────────────────────────────
# .strip(): 복사할 때 끝에 딸려온 공백·줄바꿈을 제거합니다.
# (키에 줄바꿈이 끼면 인증 헤더가 깨져 'Connection error'가 날 수 있어요)
ANTHROPIC_API_KEY = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
NOTION_TOKEN = (os.environ.get("NOTION_TOKEN") or "").strip()
NOTION_PARENT_PAGE_ID = (os.environ.get("NOTION_PARENT_PAGE_ID") or "").strip()

# 한국 시간(KST = UTC+9)
KST = timezone(timedelta(hours=9))

# Claude 모델 (요청하신 Sonnet)
CLAUDE_MODEL = "claude-sonnet-4-6"


def check_secrets():
    """비밀값 3개가 다 있는지 먼저 확인합니다. 없으면 친절히 알려주고 멈춥니다."""
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY (Claude API 키)")
    if not NOTION_TOKEN:
        missing.append("NOTION_TOKEN (노션 토큰)")
    if not NOTION_PARENT_PAGE_ID:
        missing.append("NOTION_PARENT_PAGE_ID (노션 페이지 ID)")
    if missing:
        print("❌ 다음 비밀값이 비어 있어요. .env 파일(또는 GitHub Secrets)을 확인하세요:")
        for m in missing:
            print("   -", m)
        sys.exit(1)


# ══════════════════════════════════════════════════════════
# 1단계: RSS 뉴스 수집
# ══════════════════════════════════════════════════════════
def collect_news(hours=24, max_items=60):
    """
    모든 RSS에서 최근 'hours'시간 안의 증시 관련 뉴스를 모읍니다.
    기사 전문은 가져오지 않고 제목·요약·링크만 담습니다.
    """
    print("📰 1단계: 뉴스 수집을 시작합니다...")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    headers = {"User-Agent": "Mozilla/5.0 (briefing-bot)"}  # 일부 사이트가 막지 않도록
    items = []
    seen_titles = set()  # 같은 제목 중복 제거용

    for feed in FEEDS:
        try:
            # requests로 먼저 받아오고 feedparser로 해석 (타임아웃 10초)
            resp = requests.get(feed["url"], headers=headers, timeout=10)
            parsed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"   ⚠️  '{feed['name']}' 가져오기 실패(건너뜀): {e}")
            continue

        count = 0
        for entry in parsed.entries:
            title = (entry.get("title") or "").strip()
            summary = (entry.get("summary") or entry.get("description") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue

            # 발행 시간이 있으면 최근 것만, 없으면 일단 포함
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue

            # 증시 관련 키워드가 들어있는지 확인
            text = (title + " " + summary).lower()
            if not any(kw.lower() in text for kw in KEYWORDS):
                continue

            # 중복 제목 제거
            key = title.lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)

            # HTML 태그가 섞인 요약을 간단히 정리하고 길이 제한
            summary = _clean_text(summary)[:300]

            items.append({
                "source": feed["name"],
                "category": feed["category"],
                "title": title,
                "summary": summary,
                "link": link,
            })
            count += 1

        print(f"   ✅ {feed['name']}: {count}건")

    # 너무 많으면 토큰 절약을 위해 앞에서부터 max_items개만 사용
    items = items[:max_items]
    print(f"📰 총 {len(items)}건의 증시 뉴스를 모았습니다.\n")
    return items


def _clean_text(html_text):
    """요약에 섞인 HTML 태그를 대충 제거합니다."""
    import re
    text = re.sub(r"<[^>]+>", " ", html_text)   # 태그 제거
    text = re.sub(r"\s+", " ", text)            # 공백 정리
    return text.strip()


# 받아올 시세 목록 (이름, 야후 파이낸스 심볼)
QUOTE_SYMBOLS = [
    ("코스피", "^KS11"),
    ("코스닥", "^KQ11"),
    ("S&P500", "^GSPC"),
    ("나스닥 종합", "^IXIC"),
    ("원/달러 환율", "KRW=X"),
    ("비트코인", "BTC-USD"),
    ("이더리움", "ETH-USD"),
]


def fetch_market_quotes():
    """
    주요 지수·환율·코인의 '현재가'와 '전일 대비 등락률'을 무료로 받아옵니다.
    (야후 파이낸스 차트 API, API 키 불필요. 실패한 항목은 그냥 건너뜁니다.)
    """
    print("📊 시세(지수·환율·코인) 수집 중...")
    headers = {"User-Agent": "Mozilla/5.0 (briefing-bot)"}
    lines = []
    for name, sym in QUOTE_SYMBOLS:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=2d"
            r = requests.get(url, headers=headers, timeout=10)
            meta = r.json()["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            # 진짜 '전일 종가'를 우선 사용 (없으면 차트 기준 전일 종가)
            prev = meta.get("previousClose") or meta.get("chartPreviousClose")
            if price is None:
                continue
            if prev:
                pct = (price / prev - 1) * 100
                arrow = "▲" if pct >= 0 else "▼"
                lines.append(f"- {name}: {price:,.2f} ({arrow}{abs(pct):.2f}%)")
            else:
                lines.append(f"- {name}: {price:,.2f}")
        except Exception as e:
            print(f"   ⚠️ {name}({sym}) 시세 실패(건너뜀): {e}")
    print(f"📊 시세 {len(lines)}건 수집 완료\n")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# 2단계: Claude로 요약
# ══════════════════════════════════════════════════════════
def _claude_text(prompt, max_tokens=12000):
    """프롬프트를 Claude(스트리밍)로 보내고 결과 텍스트를 받아옵니다. (일간·주간 공용)"""
    client = Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=3)
    try:
        chunks = []
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                chunks.append(text)
        return "".join(chunks).strip()
    except Exception as e:
        print("❌ Claude 호출 실패:", type(e).__name__, "-", e)
        cause = getattr(e, "__cause__", None)
        if cause is not None:
            print("   ↳ 실제 원인:", type(cause).__name__, "-", cause)
        print("   진단정보: 키 길이", len(ANTHROPIC_API_KEY),
              "/ sk-ant- 시작:", ANTHROPIC_API_KEY.startswith("sk-ant-"),
              "/ 모델:", CLAUDE_MODEL)
        raise


def parse_briefing(raw):
    """
    Claude가 보낸 표시(@HEADLINE@/@SECTION@/@SUMMARY@/@LINK@) 텍스트를
    {headline, sections} 구조로 해석합니다. (일간·주간 공용)
    """
    headline = []
    sections = []
    cur = None        # 현재 작성 중인 섹션
    mode = None       # 'headline' 또는 'section'
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("@HEADLINE@"):
            mode = "headline"
            continue
        if s.startswith("@SECTION@"):
            title = s[len("@SECTION@"):].strip()
            cur = {"title": title, "summary": "", "body_lines": [], "links": []}
            sections.append(cur)
            mode = "section"
            continue
        if s.startswith("@SUMMARY@"):
            if cur is not None:
                cur["summary"] = s[len("@SUMMARY@"):].strip()
            continue
        if s.startswith("@LINK@"):
            rest = s[len("@LINK@"):].strip()
            t, _, u = rest.partition("|||")
            if cur is not None and u.strip():
                cur["links"].append({"title": t.strip() or "기사", "url": u.strip()})
            continue
        if mode == "headline":
            if s:
                headline.append(s.lstrip("-•").strip())
        elif mode == "section" and cur is not None:
            cur["body_lines"].append(line)

    for sec in sections:
        sec["body"] = "\n".join(sec.pop("body_lines")).strip()

    if not sections:
        print("   ⚠️ 형식을 못 읽어서 원문을 그대로 한 섹션에 담아요.")
        sections = [{"title": "브리핑", "summary": "", "body": raw, "links": []}]
    if not headline:
        headline = ["증시 브리핑"]
    return {"headline": headline, "sections": sections}


def summarize_with_claude(items, date_label, quotes=""):
    """
    모은 뉴스와 시세를 Claude에게 주고, 정해진 7개 구조의 브리핑을
    표시(@SECTION@ 등) 형식으로 받아옵니다.
    """
    print("🤖 2단계: Claude가 뉴스를 요약하는 중입니다...")

    # 뉴스 목록을 번호 붙여 텍스트로 정리 (Claude가 링크를 인용할 수 있게)
    news_lines = []
    for i, it in enumerate(items, 1):
        news_lines.append(
            f"[{i}] ({it['category']}/{it['source']}) {it['title']}\n"
            f"    요약: {it['summary']}\n"
            f"    링크: {it['link']}"
        )
    news_block = "\n".join(news_lines)

    # Claude에게 줄 지시문(프롬프트)
    # JSON 대신 '표시(@SECTION@ 등)' 형식을 쓰는 이유: 본문에 따옴표·줄바꿈이 들어가도
    # 절대 깨지지 않아서 매일 도는 자동화에 훨씬 안정적이에요.
    prompt = f"""당신은 친근한 증시 브리핑 작가입니다. 아래 오늘({date_label})의 뉴스 목록을 바탕으로
한국어 증시 브리핑을 작성하세요.

[작성 규칙]
- 아래 뉴스와 시세에 근거해서 쓰고, 근거가 부족한 항목은 솔직히 "관련 뉴스가 적었음"이라고 쓰세요.
- 절대 사실을 지어내지 마세요.
- 각 섹션마다 참고한 뉴스 링크를 @LINK@ 줄로 넣으세요.
- [오늘의 주요 시세]의 지수·환율·코인 수치(등락률 포함)를 해당 섹션 본문에 반드시 활용하세요.
  (해외 증시 섹션엔 S&P500/나스닥/코인, 국내 증시 섹션엔 코스피/코스닥/환율 수치를 꼭 넣기)

[본문 작성 스타일 — 아주 중요]
- 줄글(긴 문단)로 풀어 쓰지 말고, '개조식 글머리표'로 정리하세요. 다음 규칙을 지키세요:
  · 한 줄에 한 주제. 형식은 "- 주제키워드: 핵심 내용" (주제 뒤에 콜론:)
  · 인과관계는 화살표 → , 의미 풀이는 등호 = 로 간결하게 연결
    (예: "- 국고채 금리: 3년물 ==연 3.784%==까지 상승 → 채권가격 하락 = 금리 인상 반영 신호")
  · 부연/세부는 그 아래 두 칸 들여쓴 하위 글머리표("  - ")로 1~2개 덧붙일 수 있음
  · 문장은 "~음/~함/~됨" 같은 간결한 개조식 어미로 끝맺기 (긴 설명체 금지)
- 마크다운/HTML(**굵게**, <br>, # 등)은 쓰지 마세요. 글머리표는 위 규칙대로 "- "만 사용.
- 본문에 "(뉴스 [3])" 같은 번호 표시는 쓰지 마세요. 출처는 오직 @LINK@ 줄로만 표시하세요.
- 정말 중요한 핵심 수치·키워드는 ==이렇게== 등호 두 개로 감싸면 노란 형광펜이 됩니다.
  남용하지 말고 줄마다 핵심 1개 정도만. (주제 라벨 전체나 콜론(:)을 ==로 감싸지 말고, 짧은 단어/수치만 감싸세요)
- 등락은 ▲/▼ 또는 +/- 부호와 함께 적으면 자동으로 색이 입혀져요(상승=빨강, 하락=파랑). 예: "▲18.1%", "▼7.7%"
- @SUMMARY@ 한 줄 요약은 친근한 한 문장("~예요")으로 부드럽게 써서 먼저 감을 잡게 하세요.

[오늘의 주요 시세] (전일 대비)
{quotes if quotes else "(시세 수집 실패 — 뉴스 내용 기반으로만 작성)"}

[뉴스 목록]
{news_block}

[출력 형식]
아래 형식을 "그대로" 지켜서 작성하세요. 표시(@HEADLINE@, @SECTION@, @SUMMARY@, @LINK@)는 정확히 그대로 쓰고,
코드블록(```)이나 다른 설명은 넣지 마세요. 링크 줄은 "제목 ||| 주소" 형태로 쓰세요.

각 섹션은 반드시 이 순서로 씁니다:
  @SUMMARY@ 한 문장으로 끝내는 그 섹션의 핵심 (먼저 읽고 바로 이해되게)
  그 아래에 "왜 그런지"까지 풀어쓴 상세 본문 (지금처럼 충분히 길고 친절하게)

@HEADLINE@
- (오늘 전체를 관통하는 핵심 한 줄)
- (핵심 한 줄)
- (핵심 한 줄)

@SECTION@ 거시경제 — 큰 그림
@SUMMARY@ (이 섹션 핵심 한 문장)
(상세 본문. 여러 문단 가능. 따옴표·줄바꿈 자유롭게 사용해도 됩니다.)
@LINK@ 기사 제목 ||| https://링크주소

@SECTION@ 해외 증시 — 미국 & 암호화폐
@SUMMARY@ (이 섹션 핵심 한 문장)
(상세 본문)
@LINK@ 기사 제목 ||| https://링크주소

@SECTION@ 국내 증시 — 코스피 & 코스닥
@SUMMARY@ (이 섹션 핵심 한 문장)
(상세 본문)
@LINK@ 기사 제목 ||| https://링크주소

@SECTION@ 섹터 — 오늘 움직인 업종
@SUMMARY@ (이 섹션 핵심 한 문장)
(상세 본문)
@LINK@ 기사 제목 ||| https://링크주소

@SECTION@ 개별 주식 — 눈에 띈 종목
@SUMMARY@ (이 섹션 핵심 한 문장)
(상세 본문)
@LINK@ 기사 제목 ||| https://링크주소

@SECTION@ 관전 포인트 — 오늘·이번 주 챙길 것
@SUMMARY@ (이 섹션 핵심 한 문장)
(상세 본문)
@LINK@ 기사 제목 ||| https://링크주소
"""

    raw = _claude_text(prompt)
    data = parse_briefing(raw)
    print("🤖 요약 완료!\n")
    return data


# ══════════════════════════════════════════════════════════
# 3단계: 노션에 작성
# ══════════════════════════════════════════════════════════
NOTION_VERSION = "2022-06-28"


def _heading(text):
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}


def _run(content, highlight=False, bold=False, color=None):
    """글자 한 토막. color가 있으면 글자색, 아니면 highlight=True일 때 노란 형광펜."""
    content = content.replace("==", "")   # 짝이 안 맞아 남은 강조 기호는 제거
    rt = {"type": "text", "text": {"content": content[:2000]}}
    ann = {}
    if color:
        ann["color"] = color                 # "red"(상승) / "blue"(하락) 글자색
    elif highlight:
        ann["color"] = "yellow_background"    # 노란 형광펜
    if bold:
        ann["bold"] = True
    if ann:
        rt["annotations"] = ann
    return rt


# 등락 표시를 찾는 패턴: ==강조== | ▲16.7% 같은 화살표 수치 | +17%/-7.7% 같은 부호 수치
_MARK_PATTERN = None


def _direction_color(seg):
    """등락 토막이 상승이면 'red'(빨강), 하락이면 'blue'(파랑)."""
    s = seg.strip()
    if "▲" in s or s.startswith("+"):
        return "red"
    if "▼" in s or s.startswith("-"):
        return "blue"
    return None


def _rich_runs(text, bold=False):
    """
    노션 rich_text 배열을 만듭니다.
    - ==이렇게==  : 노란 형광펜 (단, 안에 ▲/▼가 있으면 상승=빨강/하락=파랑 글자색)
    - ▲16.7% / ▼7.7% / +17% / -3.2% : 상승=빨강, 하락=파랑 (굵게)
    - 나머지 : 일반 글자
    """
    import re
    global _MARK_PATTERN
    if _MARK_PATTERN is None:
        _MARK_PATTERN = re.compile(r"==(.+?)==|([▲▼]\s?[\d.,]+%?|[+\-]\d[\d.,]*%)")
    runs = []
    pos = 0
    for m in _MARK_PATTERN.finditer(text):
        if m.start() > pos:
            runs.append(_run(text[pos:m.start()], bold=bold))
        if m.group(1) is not None:           # ==강조==
            seg = m.group(1)
            color = _direction_color(seg)
            if color:
                runs.append(_run(seg, color=color, bold=True))
            else:
                runs.append(_run(seg, highlight=True, bold=bold))
        else:                                # 등락 수치(▲▼ 또는 +/-)
            seg = m.group(2)
            runs.append(_run(seg, color=_direction_color(seg), bold=True))
        pos = m.end()
    if pos < len(text):
        runs.append(_run(text[pos:], bold=bold))
    if not runs:
        runs.append(_run(text, bold=bold))
    return runs


def _paragraph(text):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": _rich_runs(text)}}


def _bullet(text, url=None):
    if url:
        rich = {"type": "text", "text": {"content": text[:2000], "link": {"url": url}}}
        return {"object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [rich]}}
    # 링크가 없는 일반 항목(예: 오늘의 핵심)은 형광펜 표시를 살립니다.
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rich_runs(text)}}


def _label_runs(content):
    """'주제: 내용' 형태면 주제(콜론 앞)를 굵게, 내용은 형광펜 파싱합니다."""
    if ":" in content:
        label, rest = content.split(":", 1)
        return [_run(label.strip() + ": ", bold=True)] + _rich_runs(rest.strip())
    return _rich_runs(content)


def _bullets_from_body(text):
    """
    개조식 본문을 글머리표 블록으로 변환합니다.
    '- '로 시작하면 상위 항목, '  - '(두 칸 들여쓰기)면 직전 상위의 하위 항목.
    글머리표가 전혀 없으면 그냥 문단으로 처리합니다.
    """
    blocks = []
    last_top = None
    for line in text.splitlines():
        if not line.strip():
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        # 맨 앞 글머리 기호 제거
        content = stripped
        for p in ("- ", "• ", "* ", "-", "•", "*"):
            if content.startswith(p):
                content = content[len(p):].strip()
                break
        block = {"object": "block", "type": "bulleted_list_item",
                 "bulleted_list_item": {"rich_text": _label_runs(content)}}
        if indent >= 2 and last_top is not None:
            # 하위 항목 → 직전 상위 항목의 자식으로 넣음
            last_top["bulleted_list_item"].setdefault("children", []).append(block)
        else:
            blocks.append(block)
            last_top = block
    # 글머리표가 하나도 안 잡혔으면 문단으로 (안전장치)
    if not blocks:
        return [_paragraph(p) for p in _split_paragraphs(text)]
    return blocks


def _callout(text, emoji="💡"):
    """눈에 띄는 강조 박스. 섹션의 '한 줄 요약'을 여기에 넣어요(굵게 + 형광펜)."""
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": _rich_runs(text, bold=True),
                        "icon": {"emoji": emoji}}}


def build_notion_blocks(data, date_label):
    """Claude가 준 JSON을 노션 '블록'(문단/제목/목록)으로 바꿉니다."""
    blocks = []

    # 1. 핵심 요약 (3줄) — 일간은 "오늘의 핵심", 주간은 "이번 주 핵심"
    blocks.append(_heading(data.get("headline_label", "📌 오늘의 핵심")))
    for line in data.get("headline", []):
        blocks.append(_bullet(line))

    # 2~7. 나머지 섹션들
    for sec in data.get("sections", []):
        blocks.append(_heading(sec.get("title", "")))
        # 섹션 한 줄 요약을 강조 박스로 먼저 보여줍니다(핵심 → 상세 순서).
        summary = sec.get("summary", "")
        if summary:
            blocks.append(_callout(summary))
        # 그다음 상세 본문을 개조식 글머리표로 넣습니다.
        body = sec.get("body", "")
        for blk in _bullets_from_body(body):
            blocks.append(blk)
        # 출처 링크
        links = sec.get("links", [])
        if links:
            blocks.append(_paragraph("🔗 출처"))
            for lk in links:
                title = lk.get("title", "기사")
                url = lk.get("url", "")
                if url:
                    blocks.append(_bullet(title, url=url))

    return blocks


def _split_paragraphs(text, limit=1800):
    """긴 본문을 문단(빈 줄) 단위로 나누고, 너무 길면 잘라 줍니다."""
    if not text:
        return []
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    result = []
    for p in parts:
        while len(p) > limit:
            result.append(p[:limit])
            p = p[limit:]
        result.append(p)
    return result


def create_notion_page(data, title):
    """노션 부모 페이지 아래에 'title' 제목의 새 하위 페이지를 만들고 내용을 채웁니다."""
    print("📝 3단계: 노션에 브리핑 페이지를 만드는 중입니다...")
    blocks = build_notion_blocks(data, title)

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    payload = {
        "parent": {"page_id": NOTION_PARENT_PAGE_ID},
        "properties": {
            "title": {"title": [{"type": "text", "text": {"content": title}}]}
        },
        # 노션은 한 번에 100블록까지 받으므로 처음 100개만 먼저 넣습니다.
        "children": blocks[:100],
    }

    resp = requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        print("❌ 노션 페이지 생성 실패:", resp.status_code)
        print(resp.text)
        sys.exit(1)

    page = resp.json()
    page_id = page["id"]

    # 블록이 100개를 넘으면 나머지를 이어서 추가합니다.
    rest = blocks[100:]
    while rest:
        chunk, rest = rest[:100], rest[100:]
        r2 = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=headers, json={"children": chunk}, timeout=30,
        )
        if r2.status_code != 200:
            print("⚠️ 추가 블록 넣기 실패:", r2.text)
            break
        time.sleep(0.3)

    print(f"✅ 완료! 노션에 '{title}' 페이지가 만들어졌어요.")
    print("   페이지 주소:", page.get("url", "(노션에서 확인)"))


# ══════════════════════════════════════════════════════════
# 전체 실행
# ══════════════════════════════════════════════════════════
def main():
    check_secrets()
    date_label = datetime.now(KST).strftime("%Y-%m-%d")

    items = collect_news()
    if not items:
        print("오늘은 모인 뉴스가 없어요. (주말·휴일이거나 출처가 일시적으로 막혔을 수 있어요)")
        return

    quotes = fetch_market_quotes()
    data = summarize_with_claude(items, date_label, quotes)
    create_notion_page(data, f"증시 브리핑 - {date_label} 아침")


if __name__ == "__main__":
    main()
