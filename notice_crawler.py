import os
import re
import json
import difflib
import smtplib
from urllib.parse import urljoin, urlencode
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

KST = timezone(timedelta(hours=9))

TARGET_BOARDS = [
    {
        "name": "인하대 일반공지",
        "url": "https://www.inha.ac.kr/kr/950/subview.do"
    },
    {
        "name": "국제처 공지사항",
        "url": "https://internationalcenter.inha.ac.kr/internationalcenter/9905/subview.do"
    }
]

# 게시판 공지는 최근 며칠치를 보고, 이미 보낸 링크로 중복을 거른다.
# (실행이 늦어지거나 주말이 끼어도 놓치지 않게 넉넉히 잡음)
BOARD_LOOKBACK_DAYS = 7

# 인하대 통합검색: 모든 *.inha.ac.kr 게시판(사업단·센터·학과·국제처 등)을 한 번에 검색
SEARCH_URL = "https://www.inha.ac.kr/search/search.jsp"
# 제목 검색어 ('|' = OR)
OVERSEAS_QUERY = "|".join([
    "해외", "국외", "파견", "교환학생", "교환장학", "어학연수", "지역연구", "글로벌",
    "섬머", "썸머", "summer", "winter",
    "미국", "일본", "중국", "독일", "영국", "프랑스", "캐나다", "호주", "싱가포르",
    "베트남", "태국", "대만", "핀란드", "실리콘밸리",
])
OVERSEAS_LOOKBACK_DAYS = 7
OVERSEAS_FIRST_RUN_LOOKBACK_DAYS = 30  # 처음 실행할 때는 한 달치 모집 공고를 몰아서 보냄
SEARCH_MAX_PAGES = 50  # 페이지당 10건

# --- 해외 파견 공고 판별 규칙 (제목 기준) ---
# 확실한 해외 신호
OVERSEAS_STRONG = re.compile(
    r"해외|국외|파견|교환\s*학생|교환\s*장학|어학\s*연수|지역\s*연구|섬머\s*코스|썸머|"
    r"summer\s*(school|session|program|course)|winter\s*(school|session|program|course)|현지\s*(연수|탐방|실습)",
    re.I,
)
# 약한 신호(국가명/글로벌)는 프로그램성 단어와 같이 나올 때만 인정
OVERSEAS_WEAK = re.compile(
    r"글로벌|미국|일본|중국|독일|영국|프랑스|캐나다|호주|싱가포르|베트남|태국|대만|유럽|핀란드|실리콘\s*밸리"
)
PROGRAM_WORDS = re.compile(
    r"프로그램|연수|탐방|캠프|인턴|현장\s*실습|코스|스쿨|school|파견|교환|봉사단|리더십|네트워크|챌린지|TEFL",
    re.I,
)
# 해외로 보내주는 공고가 아닌 것들
TITLE_EXCLUDE = re.compile(
    r"체험기|대학\s*정보|석학|특강|학술지|논문|바이어|통역|외국인|유학생|국제\s*학생|재외|"
    r"학점\s*인정|포럼|페스티벌|축제|서포터|채용|아르바이트|결과|선정자|합격자|만족도|설문|"
    r"수강\s*상담|번역|아포스티유|박람회|해외\s*자원|영어\s*캠프|비자|보험|등록증|입찰"
)
# 검색 결과의 [사이트-게시판] 라벨 기준 제외
LABEL_EXCLUDE = re.compile(
    r"뉴스|언론|보도|갤러리|포토|자유게시판|장터|주거|아르바이트|입찰|생명윤리|국제입학|"
    r"콜로|세미나|특강|체험기|대학\s*정보|센터소식|자료실|양식|수업자료|위원게시판"
)
# 같은 공고가 여러 게시판에 올라왔을 때 원본으로 우선 보여줄 출처
PREFERRED_LABEL = re.compile(r"사업단|사업본부|국제처|센터|지원단|교육연구단|BK21")

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL")
SENT_NOTICES_FILE = "sent_notices.json"
STATE_FILE = "state.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}

_session = requests.Session()
_session.headers.update(HEADERS)
_driver = None


def get_driver():
    """Selenium WebDriver (requests가 막혔을 때만 사용)"""
    global _driver
    if _driver is None:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager

        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument(f"user-agent={HEADERS['User-Agent']}")
        chrome_options.add_argument("--window-size=1920,1080")
        service = Service(ChromeDriverManager().install())
        _driver = webdriver.Chrome(service=service, options=chrome_options)
    return _driver


def fetch_html(url, looks_ok):
    """requests로 먼저 받아보고, 차단/로그인 리다이렉트로 보이면 Selenium으로 재시도"""
    try:
        r = _session.get(url, timeout=30)
        r.encoding = "utf-8"
        if r.status_code == 200 and "ssologin" not in r.url and looks_ok(r.text):
            return r.text
        print(f"requests 응답 이상(status={r.status_code}, url={r.url}) → Selenium 재시도")
    except requests.RequestException as e:
        print(f"requests 실패({e}) → Selenium 재시도")

    import time
    driver = get_driver()
    driver.get(url)
    time.sleep(3)
    return driver.page_source


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def clean_date_text(text):
    """날짜 텍스트 정제 (YYYY.MM.DD 또는 YYYY-MM-DD)"""
    text = text.strip().rstrip('.')
    for fmt in ["%Y.%m.%d", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def clean_title(text):
    text = " ".join(text.split())
    return re.sub(r"\s*새글$", "", text)


def get_notices_from_url(board_info, since):
    """특정 게시판 URL에서 since 이후 공지사항을 수집"""
    url = board_info['url']
    board_name = board_info['name']

    print(f"[{board_name}] 접속 중... ({url})")
    html = fetch_html(url, lambda t: "_artclTdTitle" in t)
    soup = BeautifulSoup(html, "html.parser")

    rows = soup.select("table tbody tr")
    print(f"[{board_name}] 총 행 개수: {len(rows)}")

    collected_notices = []
    for row in rows:
        date_elem = row.select_one("._artclTdRdate, .td-date, .date")
        notice_date = clean_date_text(date_elem.get_text(strip=True)) if date_elem else None
        if not notice_date or notice_date < since:
            continue

        title_elem = row.select_one("._artclTdTitle a, .td-subject a, .subject a, .title a")
        if not title_elem or not title_elem.get("href"):
            continue

        collected_notices.append({
            "source": board_name,
            "title": clean_title(title_elem.get_text(" ")),
            "link": urljoin(url, title_elem["href"]),
            "date": str(notice_date),
        })

    print(f"[{board_name}] 수집된 공지: {len(collected_notices)}개")
    return collected_notices


def search_inha_boards(query, start_date, end_date):
    """인하대 통합검색(게시판, 제목, 최신순)으로 모든 사이트의 게시글 검색"""
    results = []
    for page in range(SEARCH_MAX_PAGES):
        params = {
            "query": query, "realQuery": query, "collection": "inhabbs",
            "sort": "DATE", "range": "C", "searchField": "TITLE",
            "startDate": start_date.strftime("%Y.%m.%d"),
            "endDate": end_date.strftime("%Y.%m.%d"),
            "startCount": page * 10, "reQuery": "",
        }
        html = fetch_html(f"{SEARCH_URL}?{urlencode(params)}", lambda t: "통합검색" in t)
        soup = BeautifulSoup(html, "html.parser")

        items = soup.select("a.tit")
        for a in items:
            dt = a.find_parent("dt")
            date_elem = dt.select_one(".tit_date") if dt else None
            full = clean_title(a.get_text())
            m = re.match(r"\[([^\]]*)\]\s*(.*)", full)
            label, title = (m.group(1).strip(), m.group(2).strip()) if m else ("", full)
            results.append({
                "label": label,
                "title": title,
                "link": a["href"].replace("?layout=unknown", ""),
                "date": str(clean_date_text(date_elem.get_text(strip=True))) if date_elem else "",
            })

        total = re.search(r"총\s*([\d,]+)\s*건", soup.get_text(" "))
        total = int(total.group(1).replace(",", "")) if total else 0
        if not items or (page + 1) * 10 >= total:
            break
    print(f"[통합검색] {start_date} ~ {end_date}: {len(results)}건")
    return results


def is_overseas_program(item):
    title, label = item["title"], item["label"]
    if LABEL_EXCLUDE.search(label) or TITLE_EXCLUDE.search(title):
        return False
    if OVERSEAS_STRONG.search(title):
        return True
    return bool(OVERSEAS_WEAK.search(title) and PROGRAM_WORDS.search(title))


def title_key(title):
    """재게시 공고 비교용: 앞의 [말머리] 제거 후 공백/기호 제거"""
    t = title
    while True:
        stripped = re.sub(r"^\s*[\[【(<][^\]】)>]*[\]】)>]\s*", "", t)
        if stripped == t:
            break
        t = stripped
    return re.sub(r"[\W_]+", "", t).lower()


def similar(a, b):
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def group_reposts(items):
    """같은 공고가 학과 게시판 등에 재게시된 것을 하나로 묶음"""
    groups = []
    for item in items:
        key = title_key(item["title"])
        for g in groups:
            if similar(key, g["key"]):
                g["items"].append(item)
                break
        else:
            groups.append({"key": key, "items": [item]})

    for g in groups:
        # 사업단/국제처 등 원 출처 > 학교 공지사항 > 가장 먼저 올라온 글
        g["main"] = min(
            g["items"],
            key=lambda x: (not PREFERRED_LABEL.search(x["label"]), x["label"] != "공지사항", x["date"]),
        )
    return groups


def collect_overseas(today, state, sent_keys):
    first_run = "overseas_sent" not in state
    days = OVERSEAS_FIRST_RUN_LOOKBACK_DAYS if first_run else OVERSEAS_LOOKBACK_DAYS
    items = search_inha_boards(OVERSEAS_QUERY, today - timedelta(days=days), today)
    items = [i for i in items if is_overseas_program(i)]
    groups = group_reposts(items)
    new_groups = [g for g in groups if not any(similar(g["key"], k) for k in sent_keys)]
    new_groups.sort(key=lambda g: g["main"]["date"], reverse=True)
    print(f"[해외 파견] 후보 {len(items)}건 → 공고 {len(groups)}개 → 새 공고 {len(new_groups)}개")
    return new_groups


def send_email(subject, body):
    if os.getenv("DRY_RUN"):
        print(f"\n===== [DRY_RUN] {subject} =====\n{body}")
        return True

    msg = MIMEMultipart()
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = TO_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
        print("이메일 발송 성공")
        return True
    except Exception as e:
        print(f"이메일 발송 실패: {e}")
        return False


def main():
    event_name = os.getenv("GITHUB_EVENT_NAME")
    # 수동 실행(Actions 화면의 Run workflow)은 테스트: 중복 체크 없이 보내고 기록도 안 남김.
    # 외부 크론에서 run_mode=normal 로 호출하면 정식 실행으로 취급.
    is_manual_run = event_name == "workflow_dispatch" and os.getenv("RUN_MODE") != "normal"
    dry_run = bool(os.getenv("DRY_RUN"))
    persist = not is_manual_run and not dry_run

    now = datetime.now(KST)
    today = now.date()
    state = load_json(STATE_FILE, {})

    # GitHub 자체 스케줄은 지연이 심해 백업용. 오늘 이미 확인을 마쳤으면 건너뜀.
    if event_name == "schedule" and state.get("last_check_date") == str(today):
        print(f"오늘({today}) 이미 확인함 → 백업 스케줄 건너뜀")
        return

    sent_links = [] if is_manual_run else load_json(SENT_NOTICES_FILE, [])
    sent_keys = [] if is_manual_run else state.get("overseas_sent", [])

    try:
        # 1) 기존: 학교 공지 + 국제처 게시판
        since = today - timedelta(days=BOARD_LOOKBACK_DAYS)
        board_notices = []
        for board in TARGET_BOARDS:
            try:
                for notice in get_notices_from_url(board, since):
                    if notice['link'] not in sent_links and notice['link'] not in [n['link'] for n in board_notices]:
                        board_notices.append(notice)
            except Exception as e:
                print(f"{board['name']} 처리 중 오류: {e}")

        # 2) 신규: 인하대 전체 사이트(사업단 등)에서 해외 파견 공고
        overseas_error = None
        try:
            overseas = collect_overseas(today, state, sent_keys)
        except Exception as e:
            print(f"해외 파견 공고 검색 중 오류: {e}")
            overseas, overseas_error = [], type(e).__name__
    finally:
        if _driver is not None:
            _driver.quit()

    if board_notices:
        print(f"총 보낼 공지: {len(board_notices)}개")
        body = ""
        for notice in board_notices:
            body += f"[{notice['source']}] {notice['title']}\n"
            body += f"📅 {notice['date']} | 🔗 링크: {notice['link']}\n"
            body += "=" * 40 + "\n\n"
        if send_email(f"[인하대 알림] 새로운 공지사항 ({len(board_notices)}건)", body) and persist:
            sent_links = (sent_links + [n['link'] for n in board_notices])[-500:]
            save_json(SENT_NOTICES_FILE, sent_links)
    else:
        print("새로운 공지사항이 없습니다.")

    # 해외 파견 공고: 확인은 하루 한 번 꼭, 메일은 새 공고가 있을 때만.
    # 검색이나 발송이 실패하면 '오늘 확인함'으로 남기지 않아서 다음 백업 스케줄이 다시 시도함.
    if overseas_error:
        print(f"해외 파견 공고 확인 실패({overseas_error}) → 다음 실행에서 재시도")
        return

    if overseas:
        body = "인하대 전체 사이트(사업단·센터·국제처·학과 게시판)에서 찾은 해외 파견/연수 공고입니다.\n\n"
        for g in overseas:
            main_item = g["main"]
            others = len(g["items"]) - 1
            body += f"[{main_item['label'] or '인하대'}] {main_item['title']}\n"
            body += f"📅 {main_item['date']} | 🔗 링크: {main_item['link']}\n"
            if others:
                body += f"   (다른 게시판 {others}곳에도 게시됨)\n"
            body += "=" * 40 + "\n\n"
        if not send_email(f"[인하대 해외파견] 새 공고 ({len(overseas)}건)", body):
            return
        state["overseas_sent"] = (sent_keys + [g["key"] for g in overseas])[-1000:]
    else:
        print("새로운 해외 파견 공고가 없습니다. (메일 안 보냄)")

    if persist:
        state.setdefault("overseas_sent", [])
        state["last_check_date"] = str(today)
        save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
