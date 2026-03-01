import time
import re
import io
from datetime import datetime, UTC, timedelta
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatAction
from database.db import (
    get_chat_settings,
    set_chat_setting,
    get_bot_user,
    get_user_blacklist_entries,
    get_global_stats,
    get_join_leave_trend,
    DEFAULT_SETTINGS,
)
from no_second_chances.cache import (
    settings_cache,
    bot_users_cache,
    stats_cache,
    rate_limiter,
    blacklist_cache,
    member_count_cache,
)
from no_second_chances.uptime import BOT_START_TIME
from no_second_chances.ai_client import AI_ENABLED, _provider
from secret import ADMIN_IDS
from logger import logger


def _format_uptime() -> str:
    delta = datetime.now(UTC) - BOT_START_TIME
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    seconds = int(delta.total_seconds() % 60)
    return f"{hours}h {minutes}m {seconds}s"


def register_settings_cmds(app: Client):

    @app.on_message(filters.command("ping") & (filters.private | filters.group))
    async def ping_command(client: Client, message: Message):
        try:
            await client.send_chat_action(message.chat.id, ChatAction.TYPING)
            t1 = time.monotonic()
            msg = await message.reply_text("⏳ Pinging...")
            latency = (time.monotonic() - t1) * 1000
            await msg.edit_text(f"🏓 **Pong!** `{latency:.0f}ms`")
        except Exception as e:
            logger.error(f"/ping error: {e}")

    @app.on_message(filters.command("help") & (filters.private | filters.group))
    async def help_command(client: Client, message: Message):
        try:
            await client.send_chat_action(message.chat.id, ChatAction.TYPING)
            text = (
                "📖 **Help — Command Reference**\n\n"
                "👤 **User Commands:**\n"
                "`/start` — Welcome screen & live stats\n"
                "`/stats` — Global bot statistics\n"
                "`/sorry` — Submit an unban plea\n"
                "`/profile` — Your personal profile\n"
                "`/ping` — Check bot latency\n"
                "`/help` — This command list\n\n"
                "🛡️ **Admin Commands:**\n"
                "`/admin` — Admin control panel\n"
                "`/ban` — Ban a user (or reply to message)\n"
                "`/unban` — Unban a user\n"
                "`/blacklist` — View blacklisted users\n"
                "`/settings` — Configure per-chat settings\n"
                "`/status` — Bot health dashboard\n"
            )
            await message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Close", callback_data="adm_close")
                ]])
            )
        except Exception as e:
            logger.error(f"/help error: {e}")

    @app.on_message(filters.command("profile") & filters.private)
    async def profile_command(client: Client, message: Message):
        user = message.from_user
        if not user:
            return

        if rate_limiter.is_rate_limited(user.id):
            return

        try:
            await client.send_chat_action(message.chat.id, ChatAction.TYPING)

            target_id = user.id
            parts = message.text.split()
            if len(parts) > 1 and user.id in ADMIN_IDS:
                try:
                    target_id = int(parts[1])
                except ValueError:
                    await message.reply_text("⚠️ Invalid user ID.")
                    return

            bot_user = bot_users_cache.get(f"user:{target_id}")
            if bot_user is None:
                bot_user = await get_bot_user(target_id)
                if bot_user:
                    bot_users_cache.set(f"user:{target_id}", bot_user, ttl=120)

            blacklist_entries = await get_user_blacklist_entries(target_id)

            if not bot_user:
                await message.reply_text("👤 User not found in database.")
                return

            first_seen = bot_user.get("first_seen", datetime.now(UTC))
            last_seen = bot_user.get("last_seen", datetime.now(UTC))
            interaction_count = bot_user.get("interaction_count", 0)
            username = bot_user.get("username")

            text = (
                f"👤 **Profile** — {bot_user['first_name']}\n"
                "─────────────────\n"
                f"🆔 ID: `{target_id}`\n"
                f"📛 Username: @{username or 'N/A'}\n"
                f"📅 First seen: `{first_seen.strftime('%Y-%m-%d %H:%M') if hasattr(first_seen, 'strftime') else 'N/A'}`\n"
                f"🔁 Last seen: `{last_seen.strftime('%Y-%m-%d %H:%M') if hasattr(last_seen, 'strftime') else 'N/A'}`\n"
                f"💬 Interactions: `{interaction_count}`\n"
                "─────────────────\n"
            )

            if blacklist_entries:
                text += f"🚫 **Blacklisted in {len(blacklist_entries)} chat(s)**\n"
                for entry in blacklist_entries[:5]:
                    cid = entry.get("chat_id", "?")
                    ts = entry.get("exit_time", "?")
                    if hasattr(ts, "strftime"):
                        ts = ts.strftime("%Y-%m-%d")
                    text += f"  • Chat `{cid}` — {ts}\n"
                if len(blacklist_entries) > 5:
                    text += f"  ... and {len(blacklist_entries) - 5} more\n"
            else:
                text += "✅ **Not blacklisted anywhere**\n"

            await message.reply_text(text)
        except Exception as e:
            logger.error(f"/profile error: {e}")
            await message.reply_text("⚠️ Failed to fetch profile.")

    async def blacklist_coll_find_one():
        from database.db import blacklist_coll
        await blacklist_coll.find_one({"user_id": -1})

    @app.on_message(filters.command("status") & filters.private)
    async def status_command(client: Client, message: Message):
        user = message.from_user
        if not user or user.id not in ADMIN_IDS:
            return

        try:
            await client.send_chat_action(message.chat.id, ChatAction.TYPING)

            t1 = time.monotonic()
            await blacklist_coll_find_one()
            mongo_ping = (time.monotonic() - t1) * 1000

            uptime = _format_uptime()
            ai_status = f"🟢 {_provider}" if AI_ENABLED else "🔴 Disabled"

            stats = stats_cache.get("global_stats") or await get_global_stats()
            stats_cache.set("global_stats", stats, ttl=120)

            from no_second_chances.cache import (
                blacklist_cache, member_count_cache, stats_cache,
                wallpaper_cache, settings_cache, bot_users_cache
            )

            text = (
                "🖥️ **System Status**\n"
                "─────────────────\n"
                f"🟢 Bot: Online\n"
                f"⏱️ Uptime: `{uptime}`\n"
                f"🗄️ MongoDB: `{mongo_ping:.0f}ms`\n"
                f"🤖 AI: {ai_status}\n"
                "─────────────────\n"
                f"📊 Users tracked: `{stats.get('total_users', 0)}`\n"
                f"🚫 Total bans: `{stats.get('total_blacklisted', 0)}`\n"
                f"🏘️ Active chats: `{stats.get('total_chats', 0)}`\n"
                "─────────────────\n"
                f"💾 Cache keys: BL={blacklist_cache.size()} | Stats={stats_cache.size()} | Settings={settings_cache.size()}"
            )

            await message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Refresh", callback_data="adm_status_refresh")
                ]])
            )
        except Exception as e:
            logger.error(f"/status error: {e}")
            await message.reply_text("⚠️ Failed to fetch status.")

    @app.on_callback_query(filters.regex(r"^adm_status_refresh$"))
    async def cb_status_refresh(client: Client, query: CallbackQuery):
        await query.answer("")
        try:
            t1 = time.monotonic()
            await blacklist_coll_find_one()
            mongo_ping = (time.monotonic() - t1) * 1000

            uptime = _format_uptime()
            ai_status = f"🟢 {_provider}" if AI_ENABLED else "🔴 Disabled"

            stats = stats_cache.get("global_stats") or await get_global_stats()
            stats_cache.set("global_stats", stats, ttl=120)

            from no_second_chances.cache import (
                blacklist_cache, member_count_cache, stats_cache,
                wallpaper_cache, settings_cache, bot_users_cache
            )

            text = (
                "🖥️ **System Status**\n"
                "─────────────────\n"
                f"🟢 Bot: Online\n"
                f"⏱️ Uptime: `{uptime}`\n"
                f"🗄️ MongoDB: `{mongo_ping:.0f}ms`\n"
                f"🤖 AI: {ai_status}\n"
                "─────────────────\n"
                f"📊 Users tracked: `{stats.get('total_users', 0)}`\n"
                f"🚫 Total bans: `{stats.get('total_blacklisted', 0)}`\n"
                f"🏘️ Active chats: `{stats.get('total_chats', 0)}`\n"
                "─────────────────\n"
                f"💾 Cache keys: BL={blacklist_cache.size()} | Stats={stats_cache.size()} | Settings={settings_cache.size()}"
            )

            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Refresh", callback_data="adm_status_refresh")
                ]])
            )
        except Exception as e:
            logger.error(f"adm_status_refresh error: {e}")

    @app.on_message(filters.command("settings") & filters.group)
    async def settings_command(client: Client, message: Message):
        from pyrogram.enums import ChatMemberStatus

        user_id = message.from_user.id
        chat_id = message.chat.id
        try:
            member = await client.get_chat_member(chat_id, user_id)
            if user_id not in ADMIN_IDS and member.status not in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            ):
                return
        except Exception:
            return

        try:
            await client.send_chat_action(chat_id, ChatAction.TYPING)

            settings = settings_cache.get(str(chat_id))
            if settings is None:
                settings = await get_chat_settings(chat_id)
                if not settings:
                    settings = DEFAULT_SETTINGS.copy()
                settings_cache.set(str(chat_id), settings, ttl=300)

            keyboard = []
            for key, value in settings.items():
                label_map = {
                    "notify_leave": "🔔 Notify on Leave",
                    "post_ban_joke": "😄 Post Ban Joke",
                    "dm_banned_user": "📨 DM Banned User",
                    "auto_welcome": "👋 Auto-Welcome",
                }
                emoji = "✅" if value else "❌"
                label = label_map.get(key, key)
                keyboard.append([InlineKeyboardButton(
                    f"{label}: {emoji}",
                    callback_data=f"cfg_toggle_{chat_id}_{key}"
                )])

            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_close")])

            text = (
                "⚙️ **Chat Settings**\n"
                "─────────────────\n"
                "Configure bot behavior for this chat:\n"
            )

            await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"/settings error: {e}")

    @app.on_callback_query(filters.regex(r"^cfg_toggle_(-?\d+)_(\w+)$"))
    async def cfg_toggle(client: Client, query: CallbackQuery):
        await query.answer("")
        m = re.match(r"^cfg_toggle_(-?\d+)_(\w+)$", query.data)
        chat_id = int(m.group(1))
        key = m.group(2)

        try:
            settings = settings_cache.get(str(chat_id))
            if settings is None:
                settings = await get_chat_settings(chat_id)
                if not settings:
                    settings = DEFAULT_SETTINGS.copy()

            if key not in settings:
                settings[key] = DEFAULT_SETTINGS.get(key, False)

            new_value = not settings[key]
            await set_chat_setting(chat_id, key, new_value)
            settings_cache.delete(str(chat_id))
            settings[key] = new_value

            keyboard = []
            for k, v in settings.items():
                label_map = {
                    "notify_leave": "🔔 Notify on Leave",
                    "post_ban_joke": "😄 Post Ban Joke",
                    "dm_banned_user": "📨 DM Banned User",
                    "auto_welcome": "👋 Auto-Welcome",
                }
                emoji = "✅" if v else "❌"
                label = label_map.get(k, k)
                keyboard.append([InlineKeyboardButton(
                    f"{label}: {emoji}",
                    callback_data=f"cfg_toggle_{chat_id}_{k}"
                )])

            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"adm_back_{chat_id}")])

            text = (
                "⚙️ **Chat Settings**\n"
                "─────────────────\n"
                "Configure bot behavior for this chat:\n"
            )

            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"cfg_toggle error: {e}")
