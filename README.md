# 📈 증시 브리핑 자동 작성기

매일 아침 7시(한국시간)에 증시 뉴스를 모아 Claude로 요약하고,
노션 "증시 브리핑" 페이지 아래에 새 하위 페이지로 정리해 줍니다.

## 브리핑 구조
1. 오늘의 핵심 (3줄 요약)
2. 거시경제 — 큰 그림
3. 해외 증시 — 미국 & 암호화폐
4. 국내 증시 — 코스피 & 코스닥
5. 섹터 — 오늘 움직인 업종
6. 개별 주식 — 눈에 띈 종목
7. 관전 포인트

## 내 컴퓨터에서 테스트하기
```bash
# 1) 필요한 부품 설치 (처음 한 번만)
pip install -r requirements.txt

# 2) .env 파일을 만들고 비밀값 3개를 채우기
#    (.env.example 을 복사해서 .env 로 이름 바꾸기)

# 3) 실행
python main.py
```

## 필요한 비밀값 (환경변수 3개)
| 이름 | 설명 |
|------|------|
| `ANTHROPIC_API_KEY` | Claude API 키 (sk-ant-...) |
| `NOTION_TOKEN` | 노션 연동(Integration) 토큰 |
| `NOTION_PARENT_PAGE_ID` | "증시 브리핑" 페이지 ID (32자리) |

- 로컬: `.env` 파일에 저장 (`.gitignore`로 깃 제외됨)
- 자동실행: GitHub → Settings → Secrets and variables → Actions 에 같은 이름으로 등록

## 자동실행
- **일간**: `.github/workflows/briefing.yml` → 매일 UTC 22:00 (= 한국 07:00) 실행 (`main.py`)
- **주간**: `.github/workflows/weekly.yml` → 매주 일요일 UTC 09:00 (= 한국 18:00) 실행 (`weekly.py`)
  - 주제: "핫 섹터의 변화 흐름". 섹터 ETF·지수의 주간 등락을 받아와 뜬/식은 섹터와 자금 로테이션을 정리합니다.

깃허브 저장소의 Actions 탭에서 직접 실행(workflow_dispatch)으로 테스트도 가능합니다.

## 뉴스 출처 바꾸기
`feeds.py` 의 `FEEDS` 목록만 고치면 됩니다.
