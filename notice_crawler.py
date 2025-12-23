import os
import json
import time
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from datetime import datetime
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

# 환경 변수 로드
load_dotenv()

# 설정 값
INHA_NOTICE_URL = "https://www.inha.ac.kr/kr/950/subview.do" 
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
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
    except Exception as e:
        print(f"Gemini 설정 실패: {e}")
        model = None
else:
    print("Gemini API 키가 없거나 라이브러리가 로드되지 않아 AI 요약 기능을 사용할 수 없습니다.")

def get_driver():
    """Selenium WebDriver 설정을 하고 드라이버 객체를 반환합니다."""
    chrome_options = Options()
    # 헤드리스 모드 (화면 없이 실행) - 서버 환경에서 필수
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # 봇 탐지 방지 옵션들
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--window-size=1920,1080")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# 0. 보낸 공지사항 목록 관리
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

# 1. 크롤링 함수 (Selenium 사용)
def get_inha_notices(driver):
    print("공지사항 페이지 접속 중...")
    driver.get(INHA_NOTICE_URL)
    
    # 페이지 로딩 대기 (테이블이 뜰 때까지 최대 10초 대기)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.board-table tbody tr"))
        )
    except Exception as e:
        print(f"페이지 로딩 대기 시간 초과 또는 요소 찾기 실패: {e}")

    # 오늘과 어제 날짜 계산
    now = datetime.now()
    today = now.date()
    yesterday_date = today.replace(day=today.day - 1) # simple logic, might fail on 1st of month. using timedelta is better
    from datetime import timedelta
    yesterday = today - timedelta(days=1)
    
    print(f"기준 날짜: {today} ~ {yesterday}")

    filtered_notices = []
    
    # 공지사항 행들 찾기
    rows = driver.find_elements(By.CSS_SELECTOR, ".artclTable tbody tr")
    print(f"총 행 개수: {len(rows)}")
    
    for row in rows:
        try:
            # 날짜 찾기
            date_text = row.find_element(By.CSS_SELECTOR, "._artclTdRdate").text.strip()
            # 포맷: YYYY.MM.DD. 또는 YYYY.MM.DD
            clean_date = date_text.rstrip('.')
            notice_date = datetime.strptime(clean_date, "%Y.%m.%d").date()
            
            # 날짜 필터링 (오늘 또는 어제)
            if notice_date < yesterday:
                continue

            # 제목 및 링크 요소 찾기
            title_tag = row.find_element(By.CSS_SELECTOR, "._artclTdTitle a")
            title = title_tag.text.strip()
            link = title_tag.get_attribute('href')
            
            notice_data = {"title": title, "link": link, "date": date_text}
            filtered_notices.append(notice_data)

        except Exception as e:
            # 날짜 파싱 실패 등 무시
            continue
    
    print(f"날짜 필터링 후 공지: {len(filtered_notices)}개")
    return filtered_notices

# 2. 본문 내용 가져오기 (Selenium 사용)
def get_notice_content(driver, url):
    try:
        driver.get(url)
        # 본문 로딩 대기
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".view-con"))
        )
        content_element = driver.find_element(By.CSS_SELECTOR, ".view-con")
        return content_element.text.strip()
    except Exception as e:
        return f"본문 로딩 실패: {e}"

# 3. AI 요약 함수 (GPT) - Retry Logic 추가
def summarize_text(text):
    if model is None:
        return "AI 요약 기능이 비활성화되어 있습니다 (API 키 없음 또는 라이브러리 로드 실패)."

    if len(text) > 5000:
        text = text[:5000]
        
    prompt = f"다음은 대학교 공지사항이야. 내용을 읽기 쉽게 3줄로 핵심만 요약해줘. 말투는 '~함'체로 간결하게 해줘:\n\n{text}"
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg:
                print(f"API 할당량 초과 (429). 60초 대기 후 재시도... ({attempt+1}/{max_retries})")
                time.sleep(60)
            else:
                return f"요약 실패: {e}"
    
    return "요약 실패: API 할당량 초과로 인해 3회 재시도했으나 실패했습니다."

# 4. 이메일 전송 함수 - 기존 유지
def send_email(subject, body):
    msg = MIMEMultipart()
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = TO_EMAIL
    msg['Subject'] = subject
    
    msg.attach(MIMEText(body, 'plain'))
    
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.send_message(msg)

# 메인 실행 로직
def main():
    # GitHub Actions 여부 확인
    event_name = os.getenv("GITHUB_EVENT_NAME")
    is_manual_run = (event_name == "workflow_dispatch")
    
    print(f"실행 모드: {'수동(테스트)' if is_manual_run else '자동(일반)'}")

    if not is_manual_run:
        sent_links = load_sent_notices()
    else:
        sent_links = [] 
    
    # 드라이버 시작
    driver = get_driver()
    
    try:
        all_notices = get_inha_notices(driver)
    except Exception as e:
        print(f"공지사항 목록 가져오기 실패: {e}")
        driver.quit()
        return

    new_notices = []
    
    for notice in all_notices:
        if notice['link'] not in sent_links:
            new_notices.append(notice)
    
    if len(new_notices) > 10:
        new_notices = new_notices[:10]

    if not new_notices:
        print("새로운 공지사항이 없습니다.")
        driver.quit()
        return

    print(f"보낼 공지사항: {len(new_notices)}개")
    report_content = ""
    
    for notice in new_notices:
        print(f"처리 중: {notice['title']}")
        try:
            full_content = get_notice_content(driver, notice['link'])
            # API 할당량 제한(RPM) 방지를 위해 넉넉히 대기
            print("API 호출 전 20초 대기...")
            time.sleep(20) 
            
            summary = summarize_text(full_content)
            
            report_content += f"[{notice['title']}]\n"
            report_content += f"날짜: {notice['date']}\n"
            report_content += f"링크: {notice['link']}\n"
            report_content += f"요약:\n{summary}\n"
            report_content += "-" * 30 + "\n\n"
            
            if not is_manual_run:
                sent_links.append(notice['link'])
            
        except Exception as e:
            print(f"처리 중 오류 ({notice['title']}): {e}")
    
    # 드라이버 종료
    driver.quit()
    
    if report_content:
        subject = f"[인하대] {'(테스트) ' if is_manual_run else ''}새로운 공지사항 ({len(new_notices)}건)"
        try:
            send_email(subject, report_content)
            print("이메일 발송 완료!")
            
            if not is_manual_run:
                save_sent_notices(sent_links)
                print("보낸 목록 업데이트 완료.")
            else:
                print("테스트 모드: 보낸 목록 업데이트 안함.")
            
        except Exception as e:
            print(f"이메일 발송 실패: {e}")

if __name__ == "__main__":
    main()