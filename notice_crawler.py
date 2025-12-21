import os
import json
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import google.generativeai as genai

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
genai.configure(api_key=GEMINI_API_KEY)
# 무료로 빠르고 성능 좋은 Flash 모델 사용
model = genai.GenerativeModel('gemini-1.5-flash')

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

# 1. 크롤링 함수
def get_inha_notices():
    response = requests.get(INHA_NOTICE_URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    notices = []
    
    # 보통 tr 태그 안에 공지사항들이 리스트로 있음
    rows = soup.select("table.board-table tbody tr") 
    
    for row in rows:
        try:
            # 공지사항들은 보통 번호가 있거나 '공지'라고 되어있음.
            # 여기서는 모든 게시물을 일단 긁어옵니다.
            title_tag = row.select_one(".td-subject a")
            title = title_tag.text.strip()
            link = "https://www.inha.ac.kr" + title_tag['href']
            
            # 날짜도 가져오긴 하지만 필터링에는 쓰지 않음 (디버깅용)
            date_text = row.select_one(".td-date").text.strip()
            
            notices.append({"title": title, "link": link, "date": date_text})
        except Exception as e:
            continue
            
    return notices

# 2. 본문 내용 가져오기 (링크 접속)
def get_notice_content(url):
    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        # 본문 영역 클래스 찾기 (예: .view-con)
        content_element = soup.select_one(".view-con")
        if content_element:
            return content_element.text.strip()
        else:
            return "본문을 찾을 수 없습니다."
    except:
        return "본문 로딩 실패"

# 3. AI 요약 함수 (GPT)
def summarize_text(text):
    if len(text) > 5000: # 너무 길면 자르기
        text = text[:5000]
        
    prompt = f"다음은 대학교 공지사항이야. 내용을 읽기 쉽게 3줄로 핵심만 요약해줘. 말투는 '~함'체로 간결하게 해줘:\n\n{text}"
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"요약 실패: {e}"

# 4. 이메일 전송 함수
def send_email(subject, body):
    msg = MIMEMultipart()
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = TO_EMAIL
    msg['Subject'] = subject
    
    msg.attach(MIMEText(body, 'plain')) # HTML로 보내려면 'html'로 변경
    
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.send_message(msg)