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
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")

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


# ══════════════════════════════════════════════════════════
# 2단계: Claude로 요약
# ══════════════════════════════════════════════════════════
def summarize_with_claude(items, date_label):
    """
    모은 뉴스를 Claude에게 주고, 정해진 7개 구조의 브리핑을
    JSON 형태로 받아옵니다. (JSON이라야 노션에 깔끔히 옮길 수 있어요)
    """
    print("🤖 2단계: Claude가 뉴스를 요약하는 중입니다...")
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

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
    prompt = f"""당신은 친근한 증시 브리핑 작가입니다. 아래 오늘({date_label})의 뉴스 목록을 바탕으로
한국어 증시 브리핑을 작성하세요.

[작성 규칙]
- 친근한 설명체("~예요", "~인데요")로, 너무 짧지 않게 "왜 그런지"까지 풀어서 설명하세요.
- 아래 뉴스에 근거해서 쓰고, 근거가 부족한 항목은 솔직히 "관련 뉴스가 적었어요"라고 쓰세요.
- 절대 사실을 지어내지 마세요.
- 각 섹션 본문에서 참고한 뉴스의 링크를 links 목록에 넣으세요.

[뉴스 목록]
{news_block}

[출력 형식]
반드시 아래 JSON 형식 "그대로" 출력하세요. 다른 말, 설명, 코드블록 표시(```)는 절대 넣지 마세요.
{{
  "headline": ["핵심 1줄", "핵심 1줄", "핵심 1줄"],
  "sections": [
    {{"title": "거시경제 — 큰 그림", "body": "여러 문단 가능한 본문", "links": [{{"title": "기사 제목", "url": "링크"}}]}},
    {{"title": "해외 증시 — 미국 & 암호화폐", "body": "...", "links": []}},
    {{"title": "국내 증시 — 코스피 & 코스닥", "body": "...", "links": []}},
    {{"title": "섹터 — 오늘 움직인 업종", "body": "...", "links": []}},
    {{"title": "개별 주식 — 눈에 띈 종목", "body": "...", "links": []}},
    {{"title": "관전 포인트 — 오늘·이번 주 챙길 것", "body": "...", "links": []}}
  ]
}}
"""

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8000,  # 6개 섹션을 길게 쓰므로 넉넉히 (4000은 중간에 잘릴 수 있음)
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    # 혹시 코드블록(```)으로 감싸서 왔으면 벗겨냅니다.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # 앞뒤에 설명 문장이 붙어 와도, 가장 바깥 중괄호 { } 부분만 잘라냅니다.
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]

    try:
        # strict=False: 본문 안에 줄바꿈(엔터)이 들어 있어도 너그럽게 읽습니다.
        data = json.loads(raw, strict=False)
    except json.JSONDecodeError:
        print("   ⚠️ Claude 응답을 JSON으로 읽지 못했어요. 원문을 그대로 사용합니다.")
        data = {"headline": ["오늘의 브리핑"], "sections": [{"title": "브리핑", "body": raw, "links": []}]}

    print("🤖 요약 완료!\n")
    return data


# ══════════════════════════════════════════════════════════
# 3단계: 노션에 작성
# ══════════════════════════════════════════════════════════
NOTION_VERSION = "2022-06-28"


def _heading(text):
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}


def _paragraph(text):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}


def _bullet(text, url=None):
    rich = {"type": "text", "text": {"content": text[:2000]}}
    if url:
        rich["text"]["link"] = {"url": url}
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [rich]}}


def build_notion_blocks(data, date_label):
    """Claude가 준 JSON을 노션 '블록'(문단/제목/목록)으로 바꿉니다."""
    blocks = []

    # 1. 오늘의 핵심 (3줄 요약)
    blocks.append(_heading("📌 오늘의 핵심"))
    for line in data.get("headline", []):
        blocks.append(_bullet(line))

    # 2~7. 나머지 섹션들
    for sec in data.get("sections", []):
        blocks.append(_heading(sec.get("title", "")))
        # 본문은 문단별로 나눠서 넣습니다(노션 한 블록당 글자 제한 대비).
        body = sec.get("body", "")
        for para in _split_paragraphs(body):
            blocks.append(_paragraph(para))
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


def create_notion_page(data, date_label):
    """노션 부모 페이지 아래에 새 하위 페이지를 만들고 내용을 채웁니다."""
    print("📝 3단계: 노션에 브리핑 페이지를 만드는 중입니다...")
    title = f"증시 브리핑 - {date_label} 아침"
    blocks = build_notion_blocks(data, date_label)

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

    data = summarize_with_claude(items, date_label)
    create_notion_page(data, date_label)


if __name__ == "__main__":
    main()
