import os
import sys
import asyncio
import aiohttp
from aiohttp import web
from pyrogram import Client
from secret import API_ID, API_HASH, BOT_TOKEN, MONGO_URL
from database.db import setup_database
from no_second_chances.plugin import register_plugin
from no_second_chances.admin_cmds import register_admin_cmds
from no_second_chances.user_cmds import register_user_cmds
from no_second_chances.settings_cmds import register_settings_cmds
from no_second_chances.ai_client import initialize_ai
from logger import logger

if sys.platform != "win32":
    try:
        import uvloop
        uvloop.install()
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
            bot_token=BOT_TOKEN,
            sleep_threshold=60,
        )
        self.web_app = web.Application()
        self.web_app.add_routes([
            web.get("/", self.handle_home),
            web.get("/health", self.handle_health),
        ])
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
            await asyncio.sleep(14 * 60)

    async def _cache_eviction_loop(self):
        from no_second_chances.cache import (
            blacklist_cache, member_count_cache, stats_cache, wallpaper_cache,
            settings_cache, bot_users_cache
        )
        while True:
            await asyncio.sleep(300)
            blacklist_cache.evict_expired()
            member_count_cache.evict_expired()
            stats_cache.evict_expired()
            wallpaper_cache.evict_expired()
            settings_cache.evict_expired()
            bot_users_cache.evict_expired()

    async def start(self):
        try:
            logger.info("Starting No Second Chances Bot...")

            await setup_database()
            await initialize_ai()

            register_plugin(self.app)
            register_admin_cmds(self.app)
            register_user_cmds(self.app)
            register_settings_cmds(self.app)

            await self.app.start()
            logger.info("Bot is now online and enforcing rules.")

            await self.start_web_server()

            asyncio.create_task(self.self_ping())
            asyncio.create_task(self._cache_eviction_loop())

            await asyncio.Event().wait()
        except Exception as e:
            logger.error(f"Error during bot startup: {e}")
            raise
        finally:
            await self.stop()

    async def stop(self):
        logger.info("Stopping Bot...")
        try:
            await self.app.stop()
        except Exception:
            pass
        if self.runner:
            await self.runner.cleanup()
        logger.info("Bot stopped successfully.")


async def main():
    bot = NoSecondChancesBot()
    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        logger.critical(f"FATAL ERROR: {e}")
