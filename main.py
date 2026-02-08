"""
NewsRadar v7.3 - Proxy Hunter Edition
Features: MTProto Support, .npvt File Support, Zero-Copy Media, Smart Queue
"""

import os
import asyncio
import logging
import re
import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import motor.motor_asyncio
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaWebPage, MessageMediaDocument

# وب‌سرور برای زنده نگه داشتن در Render
try:
    from web_server import keep_alive
except ImportError:
    def keep_alive(): pass

# ============================================================================
# 1. CONFIGURATION
# ============================================================================
@dataclass(frozen=True)
class Config:
    API_ID: int
    API_HASH: str
    STRING_SESSION: str
    TARGET_CHANNEL: str
    MONGO_URI: str
    
    # تنظیمات هوشمند
    MAX_QUEUE_SIZE: int = 200        
    DUPLICATE_TTL: int = 86400 * 3   
    
    NEWS_CHANNELS: tuple = (
        "BBCPersian", "Tasnimnews", 
        "deutsch_news1", "khabarfuri", "KHABAREROOZ_IR"
    )
    
    PROXY_CHANNELS: tuple = (
        "iProxyem", "Proxymelimon", "famoushaji", 
        "V2rrayVPN", "napsternetv", "v2rayng_vpn"
    )
    
    # فرمت‌های فایل قابل قبول برای پروکسی
    PROXY_FILE_EXTENSIONS: tuple = ('.npvt', '.pv', '.conf', '.ovpn')

    BLACKLIST: tuple = (
        "@deutsch_news1", "deutsch_news1", "Deutsch_News1",
        "@radiofarda_official", "radiofarda_official", "RadioFarda",
        "@BBCPersian", "BBCPersian", "bbcpersian", "BBC",
        "Tasnimnews", "@TasnimNews", "خبرگزاری تسنیم",
        "@KhabarFuri", "KhabarFuri", "khabarfuri", "خبر فوری",
        "KHABAREROOZ_IR", "@KHABAREROOZ_IR", "khabarerooz_ir",
        "عضو شوید", "لینک عضویت", "join", "Join",
        "تبلیغ", "vpn", "VPN", "proxy", "فیلترشکن",
        "اینستاگرام", "youtube", "twitter", "http", "www.",
        "@", "🆔", "👇", "👉", "pv", "PV",
        "tasnimnews.ir", "سایت تسنیم را در آدرس زیر ببینید :"
    )
    
    SIG_NEWS = "\n\n📡 <b>رادار اخبار</b>\n🆔 @NewsRadar_hub"
    SIG_PROXY = "\n\n🔐 <b>کانفیگ اختصاصی</b>\n🆔 @NewsRadar_hub"

    @classmethod
    def from_env(cls):
        return cls(
            API_ID=int(os.getenv("TELEGRAM_API_ID", "0")),
            API_HASH=os.getenv("TELEGRAM_API_HASH", ""),
            STRING_SESSION=os.getenv("STRING_SESSION", ""),
            TARGET_CHANNEL=os.getenv("TARGET_CHANNEL", ""),
            MONGO_URI=os.getenv("MONGO_URI", "mongodb://localhost:27017"),
        )

# ============================================================================
# 2. LOGGING
# ============================================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("NewsRadar-v7.3")

# ============================================================================
# 3. CONTENT ENGINE
# ============================================================================
class ContentEngine:
    # 1. پترن پروتکل‌های جدید (Vless, Vmess, etc)
    PROTOCOL_PATTERN = re.compile(r'(vmess|vless|trojan|ss|tuic|hysteria2?)://[a-zA-Z0-9\-_@:/?=&%.#]+')
    
    # 2. پترن اختصاصی MTProto (تلگرام) - شکار لینک‌های t.me/proxy
    MTPROTO_PATTERN = re.compile(r'https://t\.me/proxy\?[a-zA-Z0-9\-_@:/?=&%.#]+')
    
    MENTION_CLEANER = re.compile(r'@[a-zA-Z0-9_]+')

    @staticmethod
    def get_content_hash(text: str) -> str:
        if not text: return "empty"
        normalized = re.sub(r'\s+', '', text.lower().strip())
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

    @classmethod
    def extract_proxies(cls, text: str) -> list:
        if not text: return []
        results = []
        
        # استخراج پروتکل‌ها
        protocols = cls.PROTOCOL_PATTERN.findall(text)
        results.extend([p.strip() for p in protocols if len(p) > 15])
        
        # استخراج MTProto
        mtprotos = cls.MTPROTO_PATTERN.findall(text)
        results.extend([m.strip() for m in mtprotos if len(m) > 15])
        
        return list(set(results))

    @classmethod
    def clean_news(cls, text: str, blacklist: tuple) -> str:
        if not text: return None
        for bad in blacklist:
            if bad in text:
                text = text.replace(bad, "")
        text = cls.MENTION_CLEANER.sub('', text)
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        if len(text) < 25: return None
        return text

    @staticmethod
    def get_emoji(text: str) -> str:
        t = text.lower()
        if any(x in t for x in ['فوری', 'urgent']): return '🔴'
        if any(x in t for x in ['اقتصاد', 'دلار', 'طلا']): return '💰'
        if any(x in t for x in ['جنگ', 'حمله', 'war']): return '⚔️'
        if any(x in t for x in ['ورزش', 'فوتبال']): return '⚽️'
        return '📰'

# ============================================================================
# 4. DATABASE
# ============================================================================
class Database:
    def __init__(self, uri: str):
        self.client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self.client.newsradar_v7
        self.history = self.db.history

    async def initialize(self):
        await self.history.create_index("created_at", expireAfterSeconds=Config.DUPLICATE_TTL)
        await self.history.create_index("content_hash", unique=True)

    async def is_duplicate(self, content_hash: str) -> bool:
        return await self.history.find_one({"content_hash": content_hash}) is not None

    async def save(self, content_hash: str, source: str):
        try:
            await self.history.insert_one({
                "content_hash": content_hash,
                "source": source,
                "created_at": datetime.now(timezone.utc)
            })
        except: pass

# ============================================================================
# 5. QUEUE WORKER
# ============================================================================
class QueueWorker:
    def __init__(self, client: TelegramClient, config: Config):
        self.client = client
        self.config = config
        self.queue = asyncio.Queue(maxsize=config.MAX_QUEUE_SIZE)

    async def add_news(self, msg_obj, clean_text, source):
        await self.queue.put({
            'type': 'news', 'msg_obj': msg_obj,
            'text': clean_text, 'source': source
        })

    # متد جدید: پشتیبانی از فایل و متن برای پروکسی
    async def add_proxy(self, content, source, is_file=False, msg_obj=None):
        await self.queue.put({
            'type': 'proxy',
            'content': content,   # متن کانفیگ یا نام فایل
            'source': source,
            'is_file': is_file,
            'msg_obj': msg_obj    # برای فوروارد فایل
        })

    async def start(self):
        logger.info("👷 Worker Started & Ready...")
        while True:
            item = await self.queue.get()
            try:
                if item['type'] == 'news':
                    await self._publish_news(item)
                elif item['type'] == 'proxy':
                    await self._publish_proxy(item)
                
                await asyncio.sleep(random.uniform(2, 5))
            except Exception as e:
                logger.error(f"Publish Error: {e}")
            finally:
                self.queue.task_done()

    async def _publish_news(self, item):
        text = item['text']
        source = item['source']
        msg_obj = item['msg_obj'] 
        
        emoji = ContentEngine.get_emoji(text)
        header = text.split('\n')[0]
        body = '\n'.join(text.split('\n')[1:])
        caption = f"<b>{emoji} {header}</b>\n\n{body}{self.config.SIG_NEWS}"

        valid_media = msg_obj.media and not isinstance(msg_obj.media, MessageMediaWebPage)

        if valid_media:
            await self.client.send_message(
                self.config.TARGET_CHANNEL, message=caption,
                file=msg_obj.media, parse_mode='html'
            )
        else:
            await self.client.send_message(
                self.config.TARGET_CHANNEL, caption,
                parse_mode='html', link_preview=False
            )
        logger.info(f"✅ News Sent (Src: {source})")

    async def _publish_proxy(self, item):
        source = item['source']
        
        if item['is_file']:
            # حالت فایل: ارسال فایل به روش Zero-Copy
            msg_obj = item['msg_obj']
            caption = f"📁 <b>Config File</b>\nSource: {source}{self.config.SIG_PROXY}"
            await self.client.send_message(
                self.config.TARGET_CHANNEL,
                message=caption,
                file=msg_obj.media,
                parse_mode='html'
            )
            logger.info(f"✅ Proxy File Sent (Src: {source})")
        else:
            # حالت متن: ارسال کانفیگ متنی
            conf = item['content']
            txt = f"🔑 <b>Connect to Freedom</b>\n\n<code>{conf}</code>{self.config.SIG_PROXY}"
            await self.client.send_message(
                self.config.TARGET_CHANNEL,
                txt,
                parse_mode='html',
                link_preview=False
            )
            logger.info(f"✅ Proxy Text Sent (Src: {source})")

# ============================================================================
# 6. MAIN LOGIC
# ============================================================================
async def process_message(message, source, db: Database, worker: QueueWorker, config: Config):
    text = message.text or ""
    
    # --------------------------
    # منطق جدید پردازش پروکسی
    # --------------------------
    if source in config.PROXY_CHANNELS:
        # A. جستجوی کانفیگ‌های متنی (شامل MTProto و Protocols)
        proxies = ContentEngine.extract_proxies(text)
        for conf in proxies:
            h = ContentEngine.get_content_hash(conf)
            if not await db.is_duplicate(h):
                await db.save(h, source)
                await worker.add_proxy(content=conf, source=source, is_file=False)

        # B. جستجوی فایل‌های کانفیگ (مثل .npvt)
        if message.file and message.file.name:
            file_name = message.file.name.lower()
            # بررسی پسوند فایل
            if any(file_name.endswith(ext) for ext in config.PROXY_FILE_EXTENSIONS):
                # برای فایل‌ها از ترکیب نام فایل + کپشن هش می‌سازیم
                unique_id = f"{file_name}_{len(text)}" 
                h = ContentEngine.get_content_hash(unique_id)
                
                if not await db.is_duplicate(h):
                    await db.save(h, source)
                    # ارسال به ورکر با فلگ فایل
                    await worker.add_proxy(content=file_name, source=source, is_file=True, msg_obj=message)

    # --------------------------
    # منطق خبر (بدون تغییر)
    # --------------------------
    elif source in config.NEWS_CHANNELS:
        clean_text = ContentEngine.clean_news(text, config.BLACKLIST)
        if clean_text:
            h = ContentEngine.get_content_hash(clean_text)
            if not await db.is_duplicate(h):
                await db.save(h, source)
                await worker.add_news(message, clean_text, source)

async def main():
    config = Config.from_env()
    db = Database(config.MONGO_URI)
    await db.initialize()
    
    client = TelegramClient(StringSession(config.STRING_SESSION), config.API_ID, config.API_HASH)
    worker = QueueWorker(client, config)
    
    await client.start()
    asyncio.create_task(worker.start())

    logger.info("⏳ Starting Backfill...")
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    all_channels = config.NEWS_CHANNELS + config.PROXY_CHANNELS
    
    for channel in all_channels:
        try:
            async for msg in client.iter_messages(channel, offset_date=one_hour_ago, reverse=True):
                await process_message(msg, channel, db, worker, config)
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Backfill error on {channel}: {e}")
            
    logger.info("✅ Backfill Done. Listening for new messages...")

    @client.on(events.NewMessage(chats=all_channels))
    async def handler(event):
        try:
            chat = await event.get_chat()
            source = chat.username or chat.title
            await process_message(event.message, source, db, worker, config)
        except Exception as e:
            logger.error(f"Handler Error: {e}")

    await client.run_until_disconnected()

if __name__ == "__main__":
    keep_alive()
    try:
        asyncio.run(main())
    except KeyboardInterrupt: pass
    except Exception as e: logger.critical(f"Fatal: {e}")
