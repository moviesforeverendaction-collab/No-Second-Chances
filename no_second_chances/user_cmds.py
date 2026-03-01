import io
import aiohttp
import time
import re
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatAction
from no_second_chances.wallpaper import get_anime_wallpaper
from no_second_chances.ai_client import AI_ENABLED, answer_user_question, generate_plea_response
from no_second_chances.cache import rate_limiter, stats_cache
from no_second_chances.uptime import BOT_START_TIME
from database.db import (
    add_plea,
    get_global_stats,
    upsert_bot_user,
    get_plea,
    update_plea_status,
)
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
    "help", "ping", "profile", "settings", "status",
]

_sorry_states: dict[int, dict] = {}


def _format_uptime() -> str:
    from datetime import datetime, UTC
    delta = datetime.now(UTC) - BOT_START_TIME
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    return f"{hours}h {minutes}m"


def _make_progress_bar(value: int, total: int) -> str:
    if total == 0:
        return "░" * 10
    ratio = min(1.0, value / total)
    filled = int(ratio * 10)
    return "█" * filled + "░" * (10 - filled)


def register_user_cmds(app: Client):

    @app.on_message(filters.command("start") & (filters.private | filters.group))
    async def start_command(client: Client, message: Message):
        user = message.from_user
        if not user:
            return

        if rate_limiter.is_rate_limited(user.id):
            return

        await client.send_chat_action(message.chat.id, ChatAction.TYPING)

        is_new = await upsert_bot_user(user.id, user.first_name, user.username)

        stats = stats_cache.get("global_stats")
        if stats is None:
            stats = await get_global_stats()
            stats_cache.set("global_stats", stats, ttl=120)

        total_users = stats.get("total_users", 0)
        total_blacklisted = stats.get("total_blacklisted", 0)
        total_chats = stats.get("total_chats", 0)

        greeting = (
            f"👋 Welcome back, **{user.first_name}**!"
            if not is_new
            else f"👋 Hey **{user.first_name}**!"
        )

        ai_badge = "🤖 **AI Powered**" if AI_ENABLED else "⚡ **Fast Mode**"
        uptime = _format_uptime()
        ban_rate = _make_progress_bar(total_blacklisted, total_users)

        caption = (
            f"{greeting}\n\n"
            f"🚫 **No Second Chances** — Anti-Rejoin Enforcement\n"
            f"{'─' * 28}\n"
            f"{ai_badge}  |  ⏱️ Up `{uptime}`\n\n"
            f"📊 **Live Stats**\n"
            f"👥 Users tracked: `{total_users}`\n"
            f"🚫 Bans enforced: `{total_blacklisted}`\n"
            f"🏘️ Active chats: `{total_chats}`\n"
            f"{'─' * 28}\n"
            f"Add me to your group and make me an admin to get started."
        )

        buttons = []
        row1 = []
        if DEV_USERNAME:
            row1.append(InlineKeyboardButton("👨‍💻 Dev", url=f"https://t.me/{DEV_USERNAME}"))
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

        buttons.append([
            InlineKeyboardButton("ℹ️ About", callback_data="start_about"),
            InlineKeyboardButton("📊 Stats", callback_data="start_stats"),
        ])
        buttons.append([InlineKeyboardButton("🔍 What can I do?", callback_data="adm_features")])

        keyboard = InlineKeyboardMarkup(buttons) if buttons else None

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

    @app.on_callback_query(filters.regex(r"^start_about$"))
    async def cb_start_about(client: Client, query: CallbackQuery):
        await query.answer("")
        await query.edit_message_text(
            "ℹ️ **About No Second Chances**\n\n"
            "A professional Telegram bot that enforces strict anti-rejoin policies for groups and channels.\n\n"
            "**Features:**\n"
            "• Automatically bans users who leave and attempt to rejoin\n"
            "• Protects admins/owners from being blacklisted\n"
            "• Rich admin panel with statistics\n"
            "• Unban plea system for users\n"
            "• AI-powered ban jokes (when configured)\n"
            "• Per-chat settings and customization\n\n"
            "**Made with ❤️ for group admins.**",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="start_back")
            ]])
        )

    @app.on_callback_query(filters.regex(r"^start_stats$"))
    async def cb_start_stats(client: Client, query: CallbackQuery):
        await query.answer("")
        stats = stats_cache.get("global_stats") or await get_global_stats()
        stats_cache.set("global_stats", stats, ttl=120)

        text = (
            "📊 **Global Statistics**\n\n"
            f"👥 Total users tracked: `{stats.get('total_users', 0)}`\n"
            f"🚫 Total blacklisted: `{stats.get('total_blacklisted', 0)}`\n"
            f"🏘️ Active chats: `{stats.get('total_chats', 0)}`\n\n"
            f"Ban rate: `{_make_progress_bar(stats.get('total_blacklisted', 0), stats.get('total_users', 1))}`"
        )

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="start_back")
            ]])
        )

    @app.on_callback_query(filters.regex(r"^start_back$"))
    async def cb_start_back(client: Client, query: CallbackQuery):
        await query.answer("")
        user = query.from_user
        if not user:
            return

        stats = stats_cache.get("global_stats") or await get_global_stats()
        stats_cache.set("global_stats", stats, ttl=120)

        total_users = stats.get("total_users", 0)
        total_blacklisted = stats.get("total_blacklisted", 0)
        total_chats = stats.get("total_chats", 0)

        greeting = "👋 **Welcome back!**"
        ai_badge = "🤖 **AI Powered**" if AI_ENABLED else "⚡ **Fast Mode**"
        uptime = _format_uptime()

        caption = (
            f"{greeting}\n\n"
            f"🚫 **No Second Chances** — Anti-Rejoin Enforcement\n"
            f"{'─' * 28}\n"
            f"{ai_badge}  |  ⏱️ Up `{uptime}`\n\n"
            f"📊 **Live Stats**\n"
            f"👥 Users tracked: `{total_users}`\n"
            f"🚫 Bans enforced: `{total_blacklisted}`\n"
            f"🏘️ Active chats: `{total_chats}`\n"
            f"{'─' * 28}\n"
            f"Add me to your group and make me an admin to get started."
        )

        buttons = []
        row1 = []
        if DEV_USERNAME:
            row1.append(InlineKeyboardButton("👨‍💻 Dev", url=f"https://t.me/{DEV_USERNAME}"))
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

        buttons.append([
            InlineKeyboardButton("ℹ️ About", callback_data="start_about"),
            InlineKeyboardButton("📊 Stats", callback_data="start_stats"),
        ])
        buttons.append([InlineKeyboardButton("🔍 What can I do?", callback_data="adm_features")])

        await query.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(buttons))

    @app.on_callback_query(filters.regex(r"^adm_features$"))
    async def cb_features(client: Client, query: CallbackQuery):
        await query.answer("")
        await query.edit_message_text(
            "🔍 **What can I do?**\n\n"
            "**👤 For Users:**\n"
            "`/start` — Welcome screen & live stats\n"
            "`/stats` — Global bot statistics\n"
            "`/sorry` — Submit an unban plea\n"
            "`/profile` — Your personal profile\n"
            "`/ping` — Check bot latency\n"
            "`/help` — Full command list\n\n"
            "**🛡️ For Group Admins:**\n"
            "`/admin` — Admin control panel\n"
            "`/ban` — Ban a user (or reply to message)\n"
            "`/unban` — Unban a user\n"
            "`/blacklist` — View blacklisted users\n"
            "`/settings` — Configure per-chat settings\n"
            "`/status` — Bot health dashboard\n\n"
            "**🤖 Autonomous:**\n"
            "• Auto-bans blacklisted users on rejoin\n"
            "• Tracks all member exits\n"
            "• Plea approval workflow with notifications",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="start_back")
            ]])
        )

    @app.on_message(filters.command("sorry") & filters.private)
    async def sorry_command(client: Client, message: Message):
        user = message.from_user
        if not user:
            return

        if rate_limiter.is_rate_limited(user.id):
            await message.reply_text("⏳ Please slow down. Try again in a few seconds.")
            return

        await client.send_chat_action(message.chat.id, ChatAction.TYPING)

        _sorry_states[user.id] = {
            "step": 1,
            "started_at": time.monotonic(),
            "group_id": None,
            "plea_text": None,
        }

        await message.reply_text(
            "🙏 **Unban Request — Step 1/3**\n\n"
            "Please send the **Group ID** of the chat you were banned from.\n"
            "_Tip: Group IDs start with `-100`_",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="sorry_cancel")
            ]])
        )

    @app.on_message(
        filters.private & filters.text & ~filters.command(_EXCLUDED_COMMANDS)
    )
    async def sorry_state_handler(client: Client, message: Message):
        user = message.from_user
        if not user:
            return

        state = _sorry_states.get(user.id)
        if not state:
            if AI_ENABLED and rate_limiter.is_rate_limited(user.id):
                return
            if AI_ENABLED:
                answer = await answer_user_question(message.text, username=user.first_name)
                if answer:
                    await message.reply_text(f"🤖 {answer}")
            return

        if time.monotonic() - state.get("started_at", 0) > 300:
            del _sorry_states[user.id]
            await message.reply_text("⏱️ Your session has expired. Please start over with /sorry")
            return

        await client.send_chat_action(message.chat.id, ChatAction.TYPING)

        if state["step"] == 1:
            try:
                chat_id = int(message.text.strip())
                if not str(chat_id).startswith("-100"):
                    await message.reply_text(
                        "⚠️ Invalid Group ID format. Group IDs should start with `-100`. Try again:"
                    )
                    return
                state["group_id"] = chat_id
                state["step"] = 2
                await message.reply_text(
                    "🙏 **Unban Request — Step 2/3**\n\n"
                    "Please write your plea message explaining why you should be unbanned.\n"
                    "_Be sincere and concise._",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Cancel", callback_data="sorry_cancel")
                    ]])
                )
            except ValueError:
                await message.reply_text(
                    "⚠️ Invalid Group ID. Please send a valid number (e.g., `-1001234567890`):"
                )

        elif state["step"] == 2:
            plea_text = message.text.strip()
            if len(plea_text) < 10:
                await message.reply_text(
                    "⚠️ Your plea is too short. Please write a more detailed message:"
                )
                return
            if len(plea_text) > 500:
                await message.reply_text(
                    "⚠️ Your plea is too long. Please keep it under 500 characters:"
                )
                return

            state["plea_text"] = plea_text
            state["step"] = 3

            await message.reply_text(
                f"🙏 **Unban Request — Step 3/3**\n\n"
                f"**Group ID:** `{state['group_id']}`\n"
                f"**Your plea:**\n_{plea_text}_\n\n"
                f"Please review and confirm:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Confirm & Send", callback_data="sorry_confirm"),
                        InlineKeyboardButton("🔙 Back", callback_data="sorry_back"),
                    ],
                    [InlineKeyboardButton("❌ Cancel", callback_data="sorry_cancel")]
                ])
            )

    @app.on_callback_query(filters.regex(r"^sorry_confirm$"))
    async def cb_sorry_confirm(client: Client, query: CallbackQuery):
        await query.answer("")
        user = query.from_user
        if not user:
            return

        state = _sorry_states.get(user.id)
        if not state or state["step"] != 3:
            await query.edit_message_text("⚠️ Session expired. Please start over with /sorry")
            return

        plea_id = await add_plea(user.id, state["group_id"], state["plea_text"])

        if not plea_id:
            await query.edit_message_text("⚠️ Failed to submit your plea. Please try again later.")
            del _sorry_states[user.id]
            return

        plea_message = (
            f"🙏 **New Unban Request**\n"
            f"──────────────────────\n"
            f"👤 User: [{user.first_name}](tg://user?id={user.id}) (`{user.id}`)\n"
            f"📛 Username: @{user.username or 'N/A'}\n"
            f"🏘️ Group ID: `{state['group_id']}`\n"
            f"💬 Plea: {state['plea_text']}\n"
            f"📅 Submitted: `{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}`\n"
            f"──────────────────────"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"plea_approve_{plea_id}"),
                InlineKeyboardButton("❌ Deny", callback_data=f"plea_deny_{plea_id}"),
            ]
        ])

        sent = False
        if SUPPORT_CHAT_ID:
            try:
                await client.send_message(SUPPORT_CHAT_ID, plea_message, reply_markup=keyboard)
                sent = True
            except Exception as e:
                logger.warning(f"/sorry: Failed to send to SUPPORT_CHAT_ID: {e}")

        if not sent:
            for admin_id in ADMIN_IDS:
                try:
                    await client.send_message(admin_id, plea_message, reply_markup=keyboard)
                    sent = True
                except Exception as e:
                    logger.warning(f"/sorry: Failed to DM admin {admin_id}: {e}")

        del _sorry_states[user.id]

        if sent:
            await query.edit_message_text(
                "✅ **Your plea has been sent!**\n\n"
                "_Please be patient while the admins review your request._"
            )
        else:
            await query.edit_message_text(
                "⚠️ Could not reach admins. Please contact them directly."
            )

    @app.on_callback_query(filters.regex(r"^sorry_cancel$"))
    async def cb_sorry_cancel(client: Client, query: CallbackQuery):
        await query.answer("")
        user = query.from_user
        if user:
            _sorry_states.pop(user.id, None)
        await query.edit_message_text("❌ Request cancelled.")

    @app.on_callback_query(filters.regex(r"^sorry_back$"))
    async def cb_sorry_back(client: Client, query: CallbackQuery):
        await query.answer("")
        user = query.from_user
        if not user:
            return

        state = _sorry_states.get(user.id)
        if state:
            state["step"] = 1
            state["group_id"] = None

        await query.edit_message_text(
            "🙏 **Unban Request — Step 1/3**\n\n"
            "Please send the **Group ID** of the chat you were banned from.\n"
            "_Tip: Group IDs start with `-100`_",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="sorry_cancel")
            ]])
        )

    @app.on_callback_query(filters.regex(r"^plea_(approve|deny)_([0-9a-f]{24})$"))
    async def cb_plea_decision(client: Client, query: CallbackQuery):
        await query.answer("")
        m = re.match(r"^plea_(approve|deny)_([0-9a-f]{24})$", query.data)
        action = m.group(1)
        plea_id = m.group(2)

        plea = await get_plea(plea_id)
        if not plea:
            await query.edit_message_text("⚠️ Plea not found.")
            return

        await update_plea_status(plea_id, action)

        user_id = plea.get("user_id")
        first_name = plea.get("first_name") or "User"

        dm_text = await generate_plea_response(action == "approve", first_name)

        try:
            await client.send_message(user_id, dm_text)
        except Exception as e:
            logger.warning(f"Failed to DM user {user_id} about plea decision: {e}")

        admin_name = query.from_user.first_name or "Admin"
        emoji = "✅" if action == "approve" else "❌"
        await query.edit_message_text(
            f"{emoji} **Plea {action}ed by {admin_name}**\n\n"
            f"User has been notified."
        )

    @app.on_message(filters.command("stats") & (filters.private | filters.group))
    async def stats_command(client: Client, message: Message):
        try:
            await client.send_chat_action(message.chat.id, ChatAction.TYPING)

            cached = stats_cache.get("global_stats")
            if not cached:
                cached = await get_global_stats()
                stats_cache.set("global_stats", cached, ttl=120)

            total_users = cached.get("total_users", 0)
            total_blacklisted = cached.get("total_blacklisted", 0)
            total_chats = cached.get("total_chats", 0)

            text = (
                "📊 **No Second Chances — Bot Stats**\n\n"
                f"👥 Total users tracked: `{total_users}`\n"
                f"🚫 Total blacklisted: `{total_blacklisted}`\n"
                f"🏘️ Active chats: `{total_chats}`\n\n"
                f"Ban rate: `{_make_progress_bar(total_blacklisted, total_users)}`"
            )
            await message.reply_text(text)
        except Exception as e:
            logger.error(f"/stats error: {e}")
            await message.reply_text("⚠️ Failed to fetch stats. Try again later.")
