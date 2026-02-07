import os
import time
import logging
import asyncio
import html
import re
import threading
import random
from collections import deque

import pymongo
from flask import Flask
from telethon import TelegramClient
from telethon.sessions import StringSession
from telegram import Bot
from telegram.error import TelegramError

# کتابخانه‌های توییتر و هوش مصنوعی
import tweepy
import google.generativeai as genai

# LOGGING
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# 0. CONFIG & SERVER SETUP
# -------------------------------------------------------------------------
API_ID = int(os.environ.get("TELEGRAM_API_ID"))
API_HASH = os.environ.get("TELEGRAM_API_HASH")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
MONGO_URL = os.environ.get("MONGO_URL")
STRING_SESSION = os.environ.get("STRING_SESSION")

# --- تنظیمات توییتر ---
TWITTER_CONSUMER_KEY = os.environ.get("TWITTER_API_KEY")
TWITTER_CONSUMER_SECRET = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_SECRET")

# --- تنظیمات Gemini ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- کانال‌های خبری ---
NEWS_CHANNELS = [
    "BBCPersian", "RadioFarda", "Tasnimnews", 
    "deutsch_news1", "khabarfuri", "KHABAREROOZ_IR"
]

# --- کانال‌های پروکسی ---
PROXY_CHANNELS = [
    "iProxyem", "Proxymelimon", "famoushaji", 
    "V2rrayVPN", "napsternetv"
]

BLACKLIST = [
    "@deutsch_news1", "deutsch_news1", "آخرین اخبارفوری آلمان",
    "@radiofarda_official", "radiofarda_official", "RadioFarda", "@RadioFarda",
    "@BBCPersian", "BBCPersian",
    "Tasnimnews", "@TasnimNews",
    "@KhabarFuri", "KhabarFuri", "KhabarFuri | اخبار",
    "🔴@KHABAREROOZ_IR", "@KHABAREROOZ_IR", "KHABAREROOZ_IR",
    "https://www.TasnimNews.ir", "www.TasnimNews.ir",
    "سایت تسنیم را در آدرس زیر ببینید:", "▪️سایت تسنیم را در آدرس زیر ببینید:",
    "#درعمق" , "درعمق" , 
    "عضو شوید", "join", "لینک عضویت", "کلیک کنید",
    "📷", "@" , "▪️"
]

NEWS_SIGNATURE = "\n\n📡 <b>رادار هوشمند اخبار جهان</b>\n🆔 @NewsRadar_hub"
PROXY_SIGNATURE = "\n\n🔐 <b>کانفیگ اختصاصی | اتصال امن</b>\n🆔 @NewsRadar_hub"

app = Flask(__name__)

@app.route('/')
def home():
    return "NewsRadar BOT IS ALIVE (TURBO + TWITTER SAFE MODE) 🚀"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# -------------------------------------------------------------------------
# 1. AI & TWITTER AGENT (استراتژی امنیتی)
# -------------------------------------------------------------------------
class SocialAgent:
    def __init__(self):
        self.twitter_api = None
        self.gemini_model = None
        self.last_tweet_time = 0
        
        # ✅ تنظیم حیاتی: هر ۹۰ دقیقه یک توییت (۵۴۰۰ ثانیه)
        # این یعنی روزانه ۱۶ توییت = ماهی ۴۸۰ توییت (زیر سقف ۵۰۰ تایی توییتر)
        self.tweet_interval = 5400  

        if GEMINI_API_KEY:
            try:
                genai.configure(api_key=GEMINI_API_KEY)
                # ✅ تغییر مدل به 1.5-flash برای رفع محدودیت ۲۰ تایی گوگل
                self.gemini_model = genai.GenerativeModel('gemini-2.5-flash')
                logger.info("✅ Gemini AI Connected (Model: 1.5-flash)")
            except Exception as e:
                logger.error(f"❌ Gemini Error: {e}")

        if TWITTER_CONSUMER_KEY and TWITTER_ACCESS_TOKEN:
            try:
                self.twitter_client = tweepy.Client(
                    consumer_key=TWITTER_CONSUMER_KEY,
                    consumer_secret=TWITTER_CONSUMER_SECRET,
                    access_token=TWITTER_ACCESS_TOKEN,
                    access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
                )
                logger.info("✅ Twitter API Connected")
            except Exception as e:
                logger.error(f"❌ Twitter Auth Error: {e}")

    def generate_tweet_content(self, news_text):
        if not self.gemini_model: return None
        try:
            prompt = (
                "به عنوان یک استراتژیست ارشد خبر در توییتر (X)، متن زیر را بازنویسی کن.\n"
                "🎭 **پرسونا:** مثل ایلان ماسک یا ترامپ صریح، جسور و کوبنده باش. اما تحلیل‌هایت عمیق باشد.\n"
                "🗣 **لحن:** جوان ۲۵ ساله، امروزی و پرشور.\n"
                "🕊 **رویکرد:** نیم‌نگاهی به آزادی ایران، اما واقع‌گرا و بی‌طرف.\n"
                "📏 **قوانین:**\n"
                "1. حداکثر ۲۰۰ کاراکتر.\n"
                "2. بدون لینک.\n"
                "3. از ۲-۳ هشتگ ترند استفاده کن.\n"
                "4. فقط متن نهایی فارسی.\n\n"
                f"📝 متن خبر: {news_text[:1500]}"
            )
            response = self.gemini_model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"⚠️ Gemini Generation Failed: {e}")
            return None

    def post_tweet(self, text):
        if not hasattr(self, 'twitter_client'): return
        
        # چک کردن زمان (۹۰ دقیقه)
        if time.time() - self.last_tweet_time < self.tweet_interval:
            return 

        try:
            final_tweet = f"{text}\n\n🔗 جزئیات در کانال تلگرام:\nt.me/NewsRadar_hub"
            self.twitter_client.create_tweet(text=final_tweet)
            self.last_tweet_time = time.time()
            logger.info("🐦 Tweet Sent Successfully!")
        except Exception as e:
            logger.error(f"❌ Tweet Failed: {e}")

# -------------------------------------------------------------------------
# 2. CLOUD MEMORY & CLEANER & CORE
# -------------------------------------------------------------------------
class CloudMemory:
    def __init__(self):
        try:
            self.client = pymongo.MongoClient(MONGO_URL)
            self.db = self.client['nexus_db']
            self.collection = self.db['history']
            logger.info("✅ Connected to MongoDB Atlas")
        except Exception as e:
            logger.error(f"❌ DB Error: {e}")
            self.collection = None

    def is_url_seen(self, url):
        if self.collection is not None:
            return self.collection.find_one({"url": str(url)}) is not None
        return False

    def add_posted_item(self, url, text_snippet):
        if self.collection is not None:
            try:
                self.collection.insert_one({"url": str(url), "date": time.time()})
            except: pass

class ContentCleaner:
    @staticmethod
    def clean_news(text):
        if not text: return ""
        for bad in BLACKLIST:
            text = re.sub(f"(?i){re.escape(bad)}", "", text)
        text = re.sub(r'@\w+', '', text)
        text = re.sub(r'https?://\S+|www\.\S+', '', text)
        text = html.escape(text)
        emoji = "📰"
        keywords = {
            "جنگ": "⚔️", "حمله": "💥", "انفجار": "💣", "کشته": "⚫️",
            "آمریکا": "🇺🇸", "ایران": "🇮🇷", "اسرائیل": "🇮🇱", "فلسطین": "🇵🇸",
            "دلار": "💵", "طلا": "💰", "بورس": "📈", "فوتبال": "⚽️", "فوری": "🔴"
        }
        for k,v in keywords.items():
            if k in text: emoji = v; break
        
        clean = text.strip()
        while "\n\n\n" in clean: clean = clean.replace("\n\n\n", "\n\n")
        lines = clean.split('\n')
        if lines: lines[0] = f"<b>{emoji} {lines[0]}</b>"
        return "\n".join(lines) + NEWS_SIGNATURE

    @staticmethod
    def extract_configs(text):
        if not text: return []
        return re.findall(r'(vless://\S+|vmess://\S+|trojan://\S+|ss://\S+)', text)

def final_text_safe(text):
    if len(text) > 1000:
        clean_text = re.sub(r'<[^>]+>', '', text)
        return clean_text[:1000] + "..."
    return text

class NexusBot:
    def __init__(self):
        self.bot = Bot(token=BOT_TOKEN)
        self.memory = CloudMemory()
        self.social_agent = SocialAgent()

    async def telegram_loop(self):
        logger.info("🟢 NewsRadar Monitor Started (Turbo + Twitter Safe Mode 🌪️)")
        try:
            async with TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH) as client:
                if not client.is_connected(): await client.connect()
                
                while True:
                    current_batch = NEWS_CHANNELS + PROXY_CHANNELS
                    random.shuffle(current_batch)
                    
                    for channel in current_batch:
                        try:
                            async for msg in client.iter_messages(channel, limit=10):
                                unique_id = f"tg_{channel}_{msg.id}"
                                if self.memory.is_url_seen(unique_id): continue 

                                sent = False
                                
                                # 🛑 باند اخبار
                                if channel in NEWS_CHANNELS:
                                    has_text = msg.text and len(msg.text) > 10
                                    has_media = msg.media is not None
                                    if not has_text and not has_media: continue

                                    raw_text = msg.text if msg.text else ""
                                    final_text = ContentCleaner.clean_news(raw_text)
                                    
                                    try:
                                        if has_media:
                                            path = await client.download_media(msg, file="temp_media")
                                            if path:
                                                safe_caption = final_text_safe(final_text)
                                                if path.endswith(('.jpg','.png','.webp')):
                                                    await self.bot.send_photo(chat_id=CHANNEL_ID, photo=open(path,'rb'), caption=safe_caption, parse_mode="HTML")
                                                elif path.endswith(('.mp4','.mov','.avi')):
                                                    await self.bot.send_video(chat_id=CHANNEL_ID, video=open(path,'rb'), caption=safe_caption, parse_mode="HTML")
                                                else:
                                                    await self.bot.send_document(chat_id=CHANNEL_ID, document=open(path,'rb'), caption=safe_caption, parse_mode="HTML")
                                                os.remove(path)
                                                sent = True
                                        else:
                                            await self.bot.send_message(chat_id=CHANNEL_ID, text=final_text, parse_mode="HTML", disable_web_page_preview=True)
                                            sent = True
                                        
                                        # ارسال به توییتر (با شرط ۹۰ دقیقه)
                                        if sent and raw_text:
                                            threading.Thread(target=self.process_twitter, args=(raw_text,)).start()

                                    except Exception as e:
                                        logger.error(f"News Send Error: {e}")
                                        # کد اصلاح شده و مرتب
                                    if os.path.exists("temp_media"): 
                                        try: 
                                            os.remove("temp_media")
                                        except: 
                                            pass

                                # 🛑 باند پروکسی
                                elif channel in PROXY_CHANNELS:
                                    if not msg.text: continue
                                    configs = ContentCleaner.extract_configs(msg.text)
                                    if configs:
                                        for conf in configs:
                                            if "vless" in conf: p_type = "VLESS"
                                            elif "vmess" in conf: p_type = "VMESS"
                                            elif "trojan" in conf: p_type = "TROJAN"
                                            else: p_type = "PROXY"

                                            final_proxy_text = (
                                                f"🚀 <b>سرور جدید {p_type}</b>\n"
                                                f"<code>{conf}</code>"
                                                f"{PROXY_SIGNATURE}"
                                            )
                                            try:
                                                await self.bot.send_message(chat_id=CHANNEL_ID, text=final_proxy_text, parse_mode="HTML")
                                                sent = True
                                                await asyncio.sleep(1)
                                            except Exception as e:
                                                logger.error(f"Proxy Send Error: {e}")

                                if sent:
                                    logger.info(f"🚀 Sent from {channel}: {unique_id}")
                                    self.memory.add_posted_item(unique_id, msg.text)
                                    await asyncio.sleep(5)

                        except Exception as e:
                            if "PersistentTimestampOutdatedError" not in str(e):
                                logger.error(f"Channel Error ({channel}): {e}")
                        
                        logger.info(f"⏳ Waiting 10s before next channel...")
                        await asyncio.sleep(10)

                    logger.info("💤 Cycle finished. Sleeping for 3 minutes...")
                    await asyncio.sleep(180)

        except Exception as e:
            logger.error(f"CRITICAL: Telegram Login Failed! Error: {e}")

    def process_twitter(self, raw_text):
        try:
            ai_tweet = self.social_agent.generate_tweet_content(raw_text)
            if ai_tweet:
                self.social_agent.post_tweet(ai_tweet)
        except Exception as e:
            logger.error(f"Twitter Thread Error: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_web_server).start()
    bot = NexusBot()
    print("NewsRadar CLOUD: ONLINE 📡")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bot.telegram_loop())


