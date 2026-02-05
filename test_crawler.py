
import requests
from bs4 import BeautifulSoup

url = "https://www.inha.ac.kr/kr/950/subview.do"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.inha.ac.kr/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

try:
    session = requests.Session()
    response = session.get(url, headers=headers)
    print(f"Status: {response.status_code}")
    print(f"URL: {response.url}")
    
    if "ssologin.do" in response.url:
        print("Redirected to Login Page.")
    else:
        print("Success!")
        soup = BeautifulSoup(response.text, 'html.parser')
        rows = soup.select("table.board-table tbody tr")
        print(f"Rows found: {len(rows)}")
        # print first title to verify
        if rows:
            print(rows[0].select_one(".td-subject a").text.strip())
except Exception as e:
    print(f"Error: {e}")
