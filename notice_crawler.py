import os
import json
import time
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

# Selenium 관련 임포트
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchElementException, TimeoutException

# 환경 변수 로드
load_dotenv()

# ==========================================
# [설정] 수집할 게시판 목록 (여기에 링크를 추가하세요)
# 중요: 이 리스트의 순서대로 이메일에 내용이 작성됩니다.
# ==========================================
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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL")
SENT_NOTICES_FILE = "sent_notices.json"

# Gemini 설정
model = None
if HAS_GENAI and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.0-flash') # 모델명 최신화
    except Exception as e:
        print(f"Gemini 설정 실패: {e}")
        model = None
else:
    print("Gemini API 키가 없거나 라이브러리가 로드되지 않아 AI 요약 기능을 사용할 수 없습니다.")

def get_driver():
    """Selenium WebDriver 설정"""
    chrome_options = Options()
    # GitHub Actions 환경 등에서는 headless 필수
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    chrome_options.add_argument("--window-size=1920,1080")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def load_sent_notices():
    if not os.path.exists(SENT_NOTICES_FILE):
        return []
    try:
        with open(SENT_NOTICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_sent_notices(sent_list):
    with open(SENT_NOTICES_FILE, "w", encoding="utf-8") as f:
        json.dump(sent_list, f, ensure_ascii=False, indent=4)

def clean_date_text(text):
    """날짜 텍스트 정제 (YYYY.MM.DD 또는 YYYY-MM-DD)"""
    text = text.strip().rstrip('.')
    for fmt in ["%Y.%m.%d", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None

def get_notices_from_url(driver, board_info):
    """특정 게시판 URL에서 공지사항을 수집 (다양한 선택자 대응)"""
    url = board_info['url']
    board_name = board_info['name']
    
    print(f"[{board_name}] 접속 중... ({url})")
    driver.get(url)
    
    # 테이블 로딩 대기
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "tbody"))
        )
    except TimeoutException:
        print(f"[{board_name}] 페이지 로딩 시간 초과 또는 게시판 없음")
        return []

    # 날짜 계산
    now = datetime.now()
    today = now.date()
    weekday = today.weekday()
    
    # 월요일(0)이면 당일 공지만, 평일이면 어제+오늘
    if weekday == 0: 
        target_date = today
        print(f"오늘은 월요일. 당일({today}) 공지만 수집")
    else: 
        target_date = today - timedelta(days=1)
        print(f"평일. 어제({target_date})부터 오늘({today})까지 수집")

    collected_notices = []
    
    # 모든 행(tr) 가져오기
    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    print(f"[{board_name}] 총 행 개수: {len(rows)}")
    
    for row in rows:
        try:
            # 1. 날짜 추출 시도 (여러 클래스 이름 시도)
            date_text = ""
            date_selectors = ["._artclTdRdate", ".td-date", ".date", "td:nth-child(4)", "td:nth-child(3)"] # 메인, 학과사이트, 일반 테이블 순
            
            for selector in date_selectors:
                try:
                    elem = row.find_element(By.CSS_SELECTOR, selector)
                    date_text = elem.text.strip()
                    if clean_date_text(date_text): # 날짜 형식이 맞으면 중단
                        break
                except NoSuchElementException:
                    continue
            
            notice_date = clean_date_text(date_text)
            # 날짜를 찾지 못했거나, 기준 날짜보다 오래된 경우 스킵
            if not notice_date or notice_date < target_date:
                continue

            # 2. 제목 및 링크 추출 시도
            title_elem = None
            title_selectors = ["._artclTdTitle a", ".td-subject a", ".subject a", ".title a", "td.title a", "a"]
            
            for selector in title_selectors:
                try:
                    elem = row.find_element(By.CSS_SELECTOR, selector)
                    # 링크가 있고 제목 길이가 적당하면 채택
                    if elem.get_attribute('href') and len(elem.text.strip()) > 1:
                        title_elem = elem
                        break
                except NoSuchElementException:
                    continue
            
            if not title_elem:
                continue

            title = title_elem.text.strip()
            link = title_elem.get_attribute('href')
            
            # 공지사항 데이터 저장
            notice_data = {
                "source": board_name,
                "title": title,
                "link": link,
                "date": str(notice_date)
            }
            collected_notices.append(notice_data)

        except Exception as e:
            # print(f"행 파싱 에러: {e}") # 디버깅용
            continue
    
    print(f"[{board_name}] 수집된 공지: {len(collected_notices)}개")
    return collected_notices

def get_notice_content(driver, url):
    """본문 내용 가져오기 (여러 선택자 대응)"""
    try:
        driver.get(url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        # 본문 영역 선택자 후보들 (인하대 메인, K2Web, 일반 게시판 등)
        content_selectors = [".artclView", ".bbs-view", ".view-con", ".con-area", "#divViewCn"]
        
        text = ""
        content_element = None
        
        for selector in content_selectors:
            try:
                content_element = driver.find_element(By.CSS_SELECTOR, selector)
                text = content_element.text.strip()
                if len(text) > 0:
                    break
            except NoSuchElementException:
                continue
        
        # 텍스트가 없거나 이미지만 있는 경우
        if len(text) < 50:
            if content_element:
                imgs = content_element.find_elements(By.TAG_NAME, "img")
                if imgs:
                    return "[이미지 공지] 상세 내용은 링크를 확인해주세요."
            return "본문 내용을 텍스트로 추출하지 못했습니다. 링크를 확인하세요."
        
        return text
    except Exception as e:
        return f"본문 로딩 실패: {e}"

def summarize_text(text):
    if model is None:
        return "AI 요약 불가 (API 키 없음)"

    if text.startswith("[이미지 공지]") or text.startswith("본문 내용"):
        return text

    if len(text) > 5000:
        text = text[:5000]
        
    prompt = f"다음 대학교 공지사항을 핵심만 3줄 이내로 요약해줘. 말투는 '~함'체:\n\n{text}"
    
    # 429 에러 등을 대비해 간단한 2회 재시도
    for attempt in range(2):
        try:
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            time.sleep(5)
            continue
    return "요약 실패 (API 오류)"

def send_email(subject, body):
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
    except Exception as e:
        print(f"이메일 발송 실패: {e}")

def main():
    # GitHub Actions 수동 실행 여부
    event_name = os.getenv("GITHUB_EVENT_NAME")
    is_manual_run = (event_name == "workflow_dispatch")
    
    sent_links = load_sent_notices() if not is_manual_run else []
    
    driver = get_driver()
    
    all_new_notices = []
    
    # 설정된 모든 게시판 순회 (순서: 일반공지 -> 국제처)
    for board in TARGET_BOARDS:
        try:
            notices = get_notices_from_url(driver, board)
            
            # 이미 보낸 공지 필터링
            for notice in notices:
                if notice['link'] not in sent_links:
                    all_new_notices.append(notice)
                    
        except Exception as e:
            print(f"{board['name']} 처리 중 오류: {e}")

    # 새 공지가 없으면 종료
    if not all_new_notices:
        print("새로운 공지사항이 없습니다.")
        driver.quit()
        return

    print(f"총 보낼 공지: {len(all_new_notices)}개")
    
    email_body = ""
    processed_links = []
    
    # all_new_notices 리스트에는 이미 게시판 순서대로 공지가 들어있음
    for notice in all_new_notices:
        print(f"처리 중: {notice['title']}")
        content = get_notice_content(driver, notice['link'])
        
        # API 호출 속도 조절
        time.sleep(5) 
        summary = summarize_text(content)
        
        # 이메일 본문 작성
        email_body += f"[{notice['source']}] {notice['title']}\n"
        email_body += f"📅 {notice['date']} | 🔗 링크: {notice['link']}\n"
        email_body += f"📝 요약: {summary}\n"
        email_body += "=" * 40 + "\n\n"
        
        processed_links.append(notice['link'])

    driver.quit()
    
    if email_body:
        title = f"[인하대 알림] 새로운 공지사항 ({len(all_new_notices)}건)"
        send_email(title, email_body)
        
        if not is_manual_run:
            # 기존 목록 + 새 목록 합쳐서 저장
            final_list = sent_links + processed_links
            # 파일 크기 무한 증가 방지를 위해 최근 500개만 유지
            if len(final_list) > 500:
                final_list = final_list[-500:]
            save_sent_notices(final_list)

if __name__ == "__main__":
    main()
