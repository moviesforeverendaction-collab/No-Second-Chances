import os
import sys
import asyncio
import aiohttp
from aiohttp import web
from pyrogram import Client
from secret import API_ID, API_HASH, BOT_TOKEN, MONGO_URL
from database.db import setup_database
from no_second_chances.plugin import register_plugin
from no_second_chances.ui import register_ui
from logger import logger

# Performance: uvloop is not available on Windows. Only set if supported.
if sys.platform != "win32":
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        logger.info("Using uvloop for better performance.")
    except ImportError:
        logger.warning("uvloop not found. Using default event loop.")
else:
    logger.info("Running on Windows. Using default event loop policy.")


class NoSecondChancesBot:
    def __init__(self):
        self.app = Client(
            name="no_second_chances_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN
        )
        self.web_app = web.Application()
        self.web_app.add_routes([web.get('/', self.handle_home), web.get('/health', self.handle_health)])
        self.runner = None

    async def handle_home(self, request):
        return web.Response(text="No Second Chances Bot is running!")

    async def handle_health(self, request):
        return web.Response(text="OK", status=200)

    async def start_web_server(self):
        port = int(os.getenv("PORT", "10000"))
        self.runner = web.AppRunner(self.web_app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", port)
        await site.start()
        logger.info(f"Web server started on port {port}")

    async def self_ping(self):
        """
        Periodically pings the bot to keep it awake on Render.
        """
        url = os.getenv("SELF_PING_URL")
        if not url:
            logger.info("SELF_PING_URL not set. Skipping self-ping task.")
            return

        logger.info(f"Starting self-ping task for URL: {url}")
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        logger.info(f"Self-ping status: {response.status}")
            except Exception as e:
                logger.error(f"Self-ping failed: {e}")
            
            # Wait 14 minutes (Render free tier sleeps after 15m of inactivity)
            await asyncio.sleep(14 * 60)

    async def start(self):
        try:
            logger.info("Starting No Second Chances Bot...")
            
            # Setup database
            await setup_database()
            
            # Register handlers
            register_plugin(self.app)
            register_ui(self.app)
            
            # Start the app
            await self.app.start()
            logger.info("Bot is now online and enforcing rules.")
            
            # Start web server
            await self.start_web_server()
            
            # Start self-ping in background
            asyncio.create_task(self.self_ping())
            
            # Keep the bot running
            await asyncio.Event().wait()
        except Exception as e:
            logger.error(f"Error during bot startup: {e}")
            raise


    async def stop(self):
        logger.info("Stopping Bot...")
        await self.app.stop()
        logger.info("Bot stopped successfully.")

if __name__ == "__main__":
    bot = NoSecondChancesBot()
    loop = asyncio.get_event_loop()
    
    try:
        loop.run_until_complete(bot.start())
    except (KeyboardInterrupt, SystemExit):
        loop.run_until_complete(bot.stop())
    except Exception as e:
        logger.critical(f"FATAL ERROR: {e}")
        loop.run_until_complete(bot.stop())
