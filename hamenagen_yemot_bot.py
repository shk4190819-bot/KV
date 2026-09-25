import os
import re
import time
import threading
import subprocess
import requests
from bs4 import BeautifulSoup
from flask import Flask

# ================= הגדרות =================
# חשוב: אל תשאירו טוקן אמיתי בקוד! שימו אותו כמשתנה סביבה בפלטפורמה שבה
# אתם מריצים את הבוט (Render/Railway/Heroku וכו') ולא בקובץ עצמו.
YEMOT_TOKEN = os.environ.get("YEMOT_TOKEN", "")
EXTENSION_PATH = os.environ.get("EXTENSION_PATH", "ivr2:/5")

# בסיס ה-API של וורדפרס באתר החדש
WP_API_BASE = "https://hamenagen.net/wp-json/wp/v2"
# הקטגוריה שממנה רוצים לשלוף שירים חדשים (לפי התפריט באתר: "שירים חדשים")
CATEGORY_SLUG = "music-news"

CHECK_INTERVAL = 60  # בודק כל 5 דקות
LAST_ID_FILE = "last_id_hamenagen.txt"

app = Flask(__name__)


@app.route('/')
def home():
    return "Hamenagen -> Yemot Bot is running!"


# --- 1. העלאת קובץ שמע (MP3) לימות המשיח ---
def upload_audio_to_yemot(file_path, file_name):
    url = "https://www.call2all.co.il/ym/api/UploadFile"
    full_path = f"{EXTENSION_PATH}/{file_name}"
    params = {"token": YEMOT_TOKEN, "path": full_path}
    try:
        with open(file_path, 'rb') as f:
            response = requests.post(url, data=params, files={'file': f})
        print(f"[+] תשובת שרת ימות (העלאת שמע): {response.text}")
    except Exception as e:
        print(f"[-] שגיאה בהעלאת שמע: {e}")


# --- 2. העלאת טקסט (TTS) לימות המשיח - לצורך הקראת פרטי השיר ---
def upload_text_to_yemot(text_content, file_name):
    url = "https://www.call2all.co.il/ym/api/UploadTextFile"
    full_path = f"{EXTENSION_PATH}/{file_name}"
    params = {
        "token": YEMOT_TOKEN,
        "what": full_path,
        "contents": text_content
    }
    try:
        response = requests.post(url, data=params)
        print(f"[+] תשובת שרת ימות (העלאת טקסט): {response.text}")
    except Exception as e:
        print(f"[-] שגיאה בהעלאת טקסט: {e}")


# --- 2.5 מציאת מזהה קטגוריה לפי slug (פעם אחת, בהפעלה) ---
def get_category_id(slug):
    try:
        r = requests.get(f"{WP_API_BASE}/categories", params={"slug": slug}, timeout=15)
        data = r.json()
        if data:
            return data[0]["id"]
    except Exception as e:
        print(f"[-] שגיאה במציאת קטגוריה: {e}")
    return None


# --- 3. חילוץ קישור YouTube מתוך תוכן הפוסט ---
YOUTUBE_PATTERNS = [
    r'youtube\.com/embed/([\w-]{11})',
    r'youtube\.com/watch\?v=([\w-]{11})',
    r'youtu\.be/([\w-]{11})',
]


def extract_youtube_id(html_content):
    for pattern in YOUTUBE_PATTERNS:
        m = re.search(pattern, html_content)
        if m:
            return m.group(1)
    return None


# --- 4. הורדת שמע בלבד מ-YouTube בעזרת yt-dlp ---
def download_audio_from_youtube(video_id, out_path_no_ext):
    url = f"https://www.youtube.com/watch?v={video_id}"
    # דורש התקנה: pip install yt-dlp   וגם ffmpeg מותקן במערכת
    cmd = [
        "yt-dlp",
        "-x", "--audio-format", "mp3",
        "-o", f"{out_path_no_ext}.%(ext)s",
        url,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        mp3_path = f"{out_path_no_ext}.mp3"
        if os.path.exists(mp3_path):
            return mp3_path
    except subprocess.CalledProcessError as e:
        print(f"[-] שגיאה בהורדת שמע מיוטיוב: {e.stderr}")
    return None


def build_song_description(title, excerpt_html):
    # ניקוי התיאור (excerpt) מ-HTML לטקסט פשוט
    clean_excerpt = ""
    if excerpt_html:
        soup = BeautifulSoup(excerpt_html, 'html.parser')
        clean_excerpt = soup.get_text(separator=' ').strip()

    # כותרת הפוסט כוללת בד"כ גם את שם/שמות האמן (לפי הפורמט באתר: "שם השיר - אמן")
    parts = [title]
    if clean_excerpt and clean_excerpt not in title:
        parts.append(clean_excerpt)
    return " | ".join(parts)


def process_and_upload(post_id, title, html_content, excerpt_html=""):
    print(f"[*] מעבד פוסט חדש: {title} ({post_id})")
    video_id = extract_youtube_id(html_content)

    if not video_id:
        print("[-] לא נמצא קישור YouTube בפוסט, מדלג.")
        return

    # 1. הורדה והעלאת קובץ השמע
    temp_base = f"temp_{post_id}"
    mp3_path = download_audio_from_youtube(video_id, temp_base)

    if mp3_path:
        upload_audio_to_yemot(mp3_path, f"{post_id}.mp3")
        os.remove(mp3_path)
    else:
        print(f"[-] נכשל בהורדת השמע עבור פוסט {post_id}")
        return  # אין טעם להעלות פרטים בלי שיר בפועל

    # 2. העלאת פרטי השיר (שם + תיאור) כטקסט להקראה (TTS)
    description = build_song_description(title, excerpt_html)
    upload_text_to_yemot(description, f"{post_id}_details.tts")


# --- לולאת הבוט ---
def run_bot():
    print("--- הבוט הופעל ברקע (מול hamenagen.net) ---")

    category_id = get_category_id(CATEGORY_SLUG)
    if category_id:
        print(f"[+] נמצאה קטגוריה '{CATEGORY_SLUG}' עם מזהה {category_id}")
    else:
        print(f"[!] לא נמצאה קטגוריה '{CATEGORY_SLUG}', ימשוך פוסטים מכל הקטגוריות.")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*'
    }

    while True:
        try:
            print("מושך נתונים מה-API של hamenagen...")
            params = {"per_page": 5, "orderby": "date", "order": "desc"}
            if category_id:
                params["categories"] = category_id

            response = requests.get(f"{WP_API_BASE}/posts", headers=headers, params=params, timeout=15)

            if response.status_code == 200:
                posts = response.json()

                if posts:
                    latest_post = posts[0]
                    post_id = str(latest_post['id'])
                    title = latest_post['title']['rendered']
                    raw_html = latest_post['content']['rendered']
                    excerpt_html = latest_post.get('excerpt', {}).get('rendered', '')

                    saved_id = ""
                    if os.path.exists(LAST_ID_FILE):
                        with open(LAST_ID_FILE, "r") as f:
                            saved_id = f.read().strip()

                    if post_id != saved_id:
                        print(f"!!! פוסט חדש זוהה: {post_id} - {title} !!!")
                        process_and_upload(post_id, title, raw_html, excerpt_html)
                        with open(LAST_ID_FILE, "w") as f:
                            f.write(post_id)
                    else:
                        print(f"אין פוסטים חדשים (האחרון שנסרק: {saved_id}).")
                else:
                    print("ה-API לא החזיר פוסטים.")
            else:
                print(f"שגיאה בגישה ל-API. קוד תגובה: {response.status_code}")

        except Exception as e:
            print(f"שגיאה בסריקת ה-API: {e}")

        time.sleep(CHECK_INTERVAL)


# --- הפעלת האפליקציה ---
if __name__ == '__main__':
    if not YEMOT_TOKEN:
        print("[!] אזהרה: YEMOT_TOKEN לא הוגדר כמשתנה סביבה!")
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
