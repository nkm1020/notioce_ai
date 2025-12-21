import os
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

# Gemini 설정
genai.configure(api_key=GEMINI_API_KEY)
# 무료로 빠르고 성능 좋은 Flash 모델 사용
model = genai.GenerativeModel('gemini-1.5-flash')

# 1. 크롤링 함수
def get_inha_notices():
    response = requests.get(INHA_NOTICE_URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    notices = []
    today = datetime.now().strftime("%Y.%m.%d") # 오늘 날짜 (예: 2024.05.21)

    # 주의: 아래 선택자(selector)는 실제 사이트 구조에 맞춰 수정해야 함
    # 보통 tr 태그 안에 공지사항들이 리스트로 있음
    rows = soup.select("table.board-table tbody tr") 
    
    for row in rows:
        try:
            # 날짜 확인 (보통 td 중 하나에 날짜가 있음)
            date_text = row.select_one(".td-date").text.strip()
            
            # 오늘 올라온 글만 타겟팅 (필요 시 로직 변경 가능)
            if date_text == today:
                title_tag = row.select_one(".td-subject a")
                title = title_tag.text.strip()
                link = "https://www.inha.ac.kr" + title_tag['href']
                
                notices.append({"title": title, "link": link, "date": date_text})
        except Exception as e:
            continue
            
    return notices

# 2. 본문 내용 가져오기 (링크 접속)
def get_notice_content(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    # 본문 영역 클래스 찾기 (예: .view-con)
    content = soup.select_one(".view-con").text.strip()
    return content

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

# 메인 실행 로직
def main():
    print("공지사항 확인 중...")
    try:
        new_notices = get_inha_notices()
    except Exception as e:
        print(f"공지사항을 가져오는 중 오류 발생: {e}")
        return
    
    if not new_notices:
        print("오늘 새로운 공지사항이 없습니다.")
        return

    report_content = ""
    
    for notice in new_notices:
        print(f"처리 중: {notice['title']}")
        # 상세 내용 가져오기
        try:
            full_content = get_notice_content(notice['link'])
            # 요약하기
            summary = summarize_text(full_content)
            
            # 보고서에 추가
            report_content += f"[{notice['title']}]\n"
            report_content += f"링크: {notice['link']}\n"
            report_content += f"요약:\n{summary}\n"
            report_content += "-" * 30 + "\n\n"
        except Exception as e:
            print(f"공지사항 처리 중 오류 발생 ({notice['title']}): {e}")
    
    if report_content:
        # 메일 발송
        try:
            send_email(f"[인하대 공지봇] {datetime.now().strftime('%Y-%m-%d')} 요약 리포트", report_content)
            print("이메일 발송 완료!")
        except Exception as e:
            print(f"이메일 발송 실패: {e}")

if __name__ == "__main__":
    main()