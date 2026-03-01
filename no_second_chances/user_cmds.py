import io
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from no_second_chances.wallpaper import get_anime_wallpaper
from no_second_chances.ai_client import AI_ENABLED, answer_user_question
from no_second_chances.cache import rate_limiter
from database.db import add_plea, get_global_stats
from secret import (
    DEV_USERNAME,
    DOCS_URL,
    COMMUNITY_URL,
    FEEDBACK_URL,
    SUPPORT_CHAT_ID,
    ADMIN_IDS,
)
from logger import logger

_EXCLUDED_COMMANDS = [
    "start", "sorry", "stats", "users", "admin",
    "ban", "unban", "broadcast", "blacklist",
]


def register_user_cmds(app: Client):

    @app.on_message(filters.command("start") & (filters.private | filters.group))
    async def start_command(client: Client, message: Message):
        user = message.from_user
        if not user:
            return

        if rate_limiter.is_rate_limited(user.id):
            return

        buttons = []
        row1 = []
        if DEV_USERNAME:
            row1.append(
                InlineKeyboardButton("👨‍💻 Dev", url=f"https://t.me/{DEV_USERNAME}")
            )
        if DOCS_URL:
            row1.append(InlineKeyboardButton("📖 Docs", url=DOCS_URL))
        if row1:
            buttons.append(row1)

        row2 = []
        if COMMUNITY_URL:
            row2.append(InlineKeyboardButton("👥 Community", url=COMMUNITY_URL))
        if FEEDBACK_URL:
            row2.append(InlineKeyboardButton("💬 Feedback", url=FEEDBACK_URL))
        if row2:
            buttons.append(row2)

        keyboard = InlineKeyboardMarkup(buttons) if buttons else None

        caption = (
            f"👋 **Hey {user.first_name}!**\n\n"
            "🚫 **No Second Chances** is an anti-rejoin enforcement bot.\n\n"
            "**How it works:**\n"
            "• Users who leave a group are permanently banned from rejoining\n"
            "• Admins can manage bans, view stats, and export user lists\n"
            "• Use `/admin` in a group to access the control panel\n\n"
            "_Add me to your group and make me an admin to get started._"
        )

        try:
            wallpaper_url = await get_anime_wallpaper()
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    wallpaper_url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        img_bytes = io.BytesIO(await resp.read())
                        img_bytes.name = "wallpaper.jpg"
                        await message.reply_photo(
                            photo=img_bytes,
                            caption=caption,
                            reply_markup=keyboard,
                        )
                        return
        except Exception as e:
            logger.warning(f"Failed to send wallpaper: {e}")

        await message.reply_text(caption, reply_markup=keyboard)

    @app.on_message(filters.command("sorry") & filters.private)
    async def sorry_command(client: Client, message: Message):
        user = message.from_user
        if not user:
            return

        if rate_limiter.is_rate_limited(user.id):
            await message.reply_text("⏳ Please slow down. Try again in a few seconds.")
            return

        parts = message.text.split(None, 2)
        if len(parts) < 2:
            await message.reply_text(
                "📝 **Usage:** `/sorry <group_id> <your message>`\n\n"
                "Example:\n`/sorry -1001234567890 I promise I won't leave again!`",
            )
            return

        chat_ref = parts[1]
        plea_text = parts[2] if len(parts) > 2 else "No message provided."

        try:
            chat_id_int = int(chat_ref)
        except ValueError:
            chat_id_int = 0

        await add_plea(user.id, chat_id_int, plea_text)

        plea_message = (
            f"🙏 **Unban Request**\n\n"
            f"**User:** [{user.first_name}](tg://user?id={user.id}) (`{user.id}`)\n"
            f"**Username:** @{user.username or 'N/A'}\n"
            f"**Group ID:** `{chat_ref}`\n"
            f"**Plea:** {plea_text}\n\n"
            f"_Use_ `/unban {user.id}` _in the group to grant the request._"
        )

        sent = False
        if SUPPORT_CHAT_ID:
            try:
                await client.send_message(SUPPORT_CHAT_ID, plea_message)
                sent = True
            except Exception as e:
                logger.warning(f"/sorry: Failed to send to SUPPORT_CHAT_ID: {e}")

        if not sent:
            for admin_id in ADMIN_IDS:
                try:
                    await client.send_message(admin_id, plea_message)
                    sent = True
                except Exception as e:
                    logger.warning(f"/sorry: Failed to DM admin {admin_id}: {e}")

        if sent:
            await message.reply_text(
                "✅ Your plea has been sent to the admins.\n"
                "_Please be patient while they review your request._"
            )
        else:
            await message.reply_text(
                "⚠️ Could not reach admins. Please contact them directly."
            )

    @app.on_message(filters.command("stats") & (filters.private | filters.group))
    async def stats_command(client: Client, message: Message):
        try:
            from no_second_chances.cache import stats_cache

            cached = stats_cache.get("global_stats")
            if not cached:
                cached = await get_global_stats()
                stats_cache.set("global_stats", cached, ttl=120)

            text = (
                "📊 **No Second Chances — Bot Stats**\n\n"
                f"👥 Total users tracked: `{cached.get('total_users', 0)}`\n"
                f"🚫 Total blacklisted: `{cached.get('total_blacklisted', 0)}`\n"
                f"🏘️ Active chats: `{cached.get('total_chats', 0)}`\n"
            )
            await message.reply_text(text)
        except Exception as e:
            logger.error(f"/stats error: {e}")
            await message.reply_text("⚠️ Failed to fetch stats. Try again later.")

    @app.on_message(
        filters.private
        & ~filters.command(_EXCLUDED_COMMANDS)
        & filters.text
    )
    async def ai_chat_handler(client: Client, message: Message):
        if not AI_ENABLED:
            return
        user = message.from_user
        if not user or not message.text:
            return
        if rate_limiter.is_rate_limited(user.id):
            return

        answer = await answer_user_question(message.text, username=user.first_name)
        if answer:
            await message.reply_text(f"🤖 {answer}")
