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
MAX_ATTEMPTS = 3            # כמה פעמים לנסות פוסט שנכשל לפני שמוותרים
DESCRIPTION_MAX_CHARS = 1500  # אורך מקסימלי של טקסט ההקראה

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/html, */*'
}

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
    r'youtube\.com/shorts/([\w-]{11})',
    r'youtu\.be/([\w-]{11})',
]
DIRECT_MEDIA_PATTERN = r'https?://[^\s"\'<>\\]+?\.(?:mp3|m4a|wav|aac|ogg|mp4|webm)(?:\?[^\s"\'<>\\]*)?'


def find_media_url(html_content):
    """מחפש בתוכן הפוסט קישור למדיה: יוטיוב, או קובץ שמע/וידאו ישיר.
    מטפל גם ב-JSON של אלמנטור, שבו הלוכסנים מסומנים כ- \\/ """
    text = html_content.replace('\\/', '/').replace('&amp;', '&')

    for pattern in YOUTUBE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"

    m = re.search(DIRECT_MEDIA_PATTERN, text, re.IGNORECASE)
    if m:
        return m.group(0)

    # דיבוג: מדפיס את כל הקישורים שנמצאו בפוסט כדי להבין איפה המדיה
    urls = sorted(set(re.findall(r'https?://[^\s"\'<>\\]+', text)))
    print(f"[debug] לא נמצאה מדיה. קישורים בפוסט ({len(urls)}):")
    for u in urls[:25]:
        print(f"[debug]   {u}")
    tags = sorted(set(re.findall(r'<(iframe|video|audio|source|embed)\b', text, re.I)))
    print(f"[debug] תגיות מדיה בפוסט: {tags}")
    return None


# --- 4. הורדת שמע בלבד בעזרת yt-dlp (יוטיוב או קישור ישיר) ---
def download_audio(media_url, out_path_no_ext):
    cmd = [
        "yt-dlp",
        "-x", "--audio-format", "mp3",
        "-o", f"{out_path_no_ext}.%(ext)s",
    ]
    # ב-Render אין ffmpeg מותקן - imageio-ffmpeg מספק קובץ הרצה משלו
    try:
        import imageio_ffmpeg
        cmd += ["--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe()]
    except Exception:
        pass
    cmd.append(media_url)

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        mp3_path = f"{out_path_no_ext}.mp3"
        if os.path.exists(mp3_path):
            return mp3_path
    except subprocess.CalledProcessError as e:
        print(f"[-] שגיאה בהורדת שמע: {e.stderr}")
    return None


# באתר, שם קובץ התמונה הראשית של כל שיר מתחיל במזהה היוטיוב שלו,
# למשל: NCsmsBDRkg4-maxresdefault.jpg  ->  https://www.youtube.com/watch?v=NCsmsBDRkg4
IMAGE_YT_PATTERN = r'/([\w-]{11})-(?:maxresdefault|sddefault|hqdefault|mqdefault|default)'


def youtube_url_from_image(image_url):
    if not image_url:
        return None
    m = re.search(IMAGE_YT_PATTERN, image_url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return None


def get_featured_image_url(post):
    """כתובת התמונה הראשית של הפוסט: קודם מה-API (_embed), ואז מדף הפוסט (og:image)."""
    try:
        media = post.get('_embedded', {}).get('wp:featuredmedia', [])
        if media and media[0].get('source_url'):
            return media[0]['source_url']
    except Exception:
        pass

    link = post.get('link')
    if link:
        try:
            r = requests.get(link, headers=HEADERS, timeout=20)
            soup = BeautifulSoup(r.text, 'html.parser')
            tag = soup.find('meta', property='og:image')
            if tag and tag.get('content'):
                return tag['content']
        except Exception as e:
            print(f"[-] שגיאה בטעינת דף הפוסט: {e}")
    return None


def find_media_for_post(post):
    # 1. קישור מדיה בתוך תוכן הפוסט (אם יש)
    media_url = find_media_url(post['content']['rendered'])
    if media_url:
        return media_url

    # 2. מזהה יוטיוב לפי שם התמונה הראשית של הפוסט
    image_url = get_featured_image_url(post)
    print(f"[debug] תמונה ראשית: {image_url}")
    return youtube_url_from_image(image_url)


def clean_html_text(html):
    if not html:
        return ""
    text = BeautifulSoup(html, 'html.parser').get_text(separator='\n')
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def build_song_description(title, content_html, excerpt_html=""):
    # תוכן הפוסט באתר הוא טקסט התיאור/קרדיטים/מילות השיר
    body = clean_html_text(content_html) or clean_html_text(excerpt_html)
    text = title if not body else f"{title}\n{body}"
    return text[:DESCRIPTION_MAX_CHARS]


def process_and_upload(post):
    """מחזיר True אם השיר הועלה בהצלחה."""
    post_id = str(post['id'])
    title = clean_html_text(post['title']['rendered'])
    print(f"[*] מעבד פוסט חדש: {title} ({post_id})")

    media_url = find_media_for_post(post)
    if not media_url:
        print("[-] לא נמצא קישור למדיה בפוסט.")
        return False
    print(f"[+] נמצאה מדיה: {media_url}")

    # 1. הורדה והעלאת קובץ השמע
    mp3_path = download_audio(media_url, f"temp_{post_id}")
    if not mp3_path:
        print(f"[-] נכשל בהורדת השמע עבור פוסט {post_id}")
        return False  # אין טעם להעלות פרטים בלי שיר בפועל

    upload_audio_to_yemot(mp3_path, f"{post_id}.mp3")
    os.remove(mp3_path)

    # 2. העלאת פרטי השיר (שם + תיאור/מילים) כטקסט להקראה (TTS)
    description = build_song_description(
        title, post['content']['rendered'], post.get('excerpt', {}).get('rendered', ''))
    upload_text_to_yemot(description, f"{post_id}_details.tts")
    return True


# --- לולאת הבוט ---
def run_bot():
    print("--- הבוט הופעל ברקע (מול hamenagen.net) ---")

    category_id = get_category_id(CATEGORY_SLUG)
    if category_id:
        print(f"[+] נמצאה קטגוריה '{CATEGORY_SLUG}' עם מזהה {category_id}")
    else:
        print(f"[!] לא נמצאה קטגוריה '{CATEGORY_SLUG}', ימשוך פוסטים מכל הקטגוריות.")

    attempts = {}

    while True:
        try:
            print("מושך נתונים מה-API של hamenagen...")
            params = {"per_page": 5, "orderby": "date", "order": "desc",
                      "_embed": "wp:featuredmedia"}
            if category_id:
                params["categories"] = category_id

            response = requests.get(f"{WP_API_BASE}/posts", headers=HEADERS, params=params, timeout=20)

            if response.status_code == 200:
                posts = response.json()

                if posts:
                    latest_post = posts[0]
                    post_id = str(latest_post['id'])

                    saved_id = ""
                    if os.path.exists(LAST_ID_FILE):
                        with open(LAST_ID_FILE, "r") as f:
                            saved_id = f.read().strip()

                    if post_id == saved_id:
                        print(f"אין פוסטים חדשים (האחרון שנסרק: {saved_id}).")
                    elif attempts.get(post_id, 0) >= MAX_ATTEMPTS:
                        print(f"פוסט {post_id} נכשל {MAX_ATTEMPTS} פעמים, ממתין לפוסט הבא.")
                    else:
                        print(f"!!! פוסט חדש זוהה: {post_id} !!!")
                        attempts[post_id] = attempts.get(post_id, 0) + 1
                        if process_and_upload(latest_post):
                            with open(LAST_ID_FILE, "w") as f:
                                f.write(post_id)
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
