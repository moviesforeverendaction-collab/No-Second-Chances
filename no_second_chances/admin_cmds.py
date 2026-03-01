import asyncio
import io
import re
import time
from datetime import datetime, UTC
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatMemberStatus, ChatAction
from pyrogram.errors import FloodWait, UserIsBlocked
from database.db import (
    add_to_blacklist,
    remove_from_blacklist,
    get_blacklist_page,
    get_all_seen_users,
    get_banned_count,
    get_chat_settings,
    set_chat_setting,
    get_join_leave_trend,
)
from no_second_chances.cache import (
    rate_limiter,
    blacklist_cache,
    stats_cache,
    member_count_cache,
    settings_cache,
)
from no_second_chances.ai_client import generate_ban_joke, generate_ban_dm
from secret import ADMIN_IDS
from logger import logger

_broadcast_sem = asyncio.Semaphore(5)
_PAGE_SIZE = 10


def _is_authorized(user_id: int, member) -> bool:
    if user_id in ADMIN_IDS:
        return True
    if member and member.status in (
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
    ):
        return True
    return False


def _admin_keyboard(chat_id: int, ban_count: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats", callback_data=f"adm_stats_{chat_id}"),
            InlineKeyboardButton(f"📋 Blacklist ({ban_count})", callback_data=f"adm_bl_{chat_id}_0"),
        ],
        [
            InlineKeyboardButton("⚡ Quick Ban", callback_data=f"adm_ban_prompt_{chat_id}"),
            InlineKeyboardButton("✅ Quick Unban", callback_data=f"adm_unban_prompt_{chat_id}"),
        ],
        [
            InlineKeyboardButton("📤 Export Users", callback_data=f"adm_export_{chat_id}"),
            InlineKeyboardButton("📈 Analytics", callback_data=f"adm_analytics_{chat_id}"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data=f"adm_settings_{chat_id}"),
            InlineKeyboardButton("🔔 Notifications", callback_data=f"adm_notifications_{chat_id}"),
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="adm_close"),
        ],
    ])


def register_admin_cmds(app: Client):

    @app.on_message(filters.command("admin") & filters.group)
    async def admin_panel(client: Client, message: Message):
        user_id = message.from_user.id
        chat_id = message.chat.id
        try:
            member = await client.get_chat_member(chat_id, user_id)
            if not _is_authorized(user_id, member):
                return
        except Exception:
            return

        await client.send_chat_action(chat_id, ChatAction.TYPING)

        try:
            chat = await client.get_chat(chat_id)
            chat_name = chat.title or "This Chat"
        except Exception:
            chat_name = f"Chat {chat_id}"

        count_key = f"member_count:{chat_id}"
        member_count = member_count_cache.get(count_key)
        if member_count is None:
            member_count = await client.get_chat_members_count(chat_id)
            member_count_cache.set(count_key, member_count, ttl=60)

        ban_count = await get_banned_count(chat_id)

        header = (
            f"🛡️ **No Second Chances — Admin Panel**\n"
            f"──────────────────────────────\n"
            f"🏘️ **{chat_name}**\n"
            f"👥 Members: `{member_count}`  |  🟢 Bot Active\n"
            f"🚫 Bans enforced: `{ban_count}`\n"
            f"──────────────────────────────\n"
            f"Select an action below:"
        )

        await message.reply_text(header, reply_markup=_admin_keyboard(chat_id, ban_count))

    @app.on_message(filters.command("admin") & filters.private)
    async def admin_panel_private(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            return

        await client.send_chat_action(message.chat.id, ChatAction.TYPING)

        stats = stats_cache.get("global_stats")
        if stats is None:
            from database.db import get_global_stats
            stats = await get_global_stats()
            stats_cache.set("global_stats", stats, ttl=120)

        header = (
            f"🛡️ **Global Admin Dashboard**\n"
            f"──────────────────────────────\n"
            f"👥 Users tracked: `{stats.get('total_users', 0)}`\n"
            f"🚫 Total bans: `{stats.get('total_blacklisted', 0)}`\n"
            f"🏘️ Active chats: `{stats.get('total_chats', 0)}`\n"
            f"──────────────────────────────\n"
            f"Select an action below:"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 Global Stats", callback_data="gadm_stats"),
                InlineKeyboardButton("👥 All Users", callback_data="gadm_users"),
            ],
            [
                InlineKeyboardButton("📢 Broadcast", callback_data="gadm_broadcast"),
                InlineKeyboardButton("🖥️ Status", callback_data="gadm_status"),
            ],
            [InlineKeyboardButton("❌ Close", callback_data="adm_close")],
        ])

        await message.reply_text(header, reply_markup=keyboard)

    @app.on_callback_query(filters.regex(r"^gadm_stats$"))
    async def cb_gadm_stats(client: Client, query: CallbackQuery):
        await query.answer("")
        stats = stats_cache.get("global_stats")
        if stats is None:
            from database.db import get_global_stats
            stats = await get_global_stats()
            stats_cache.set("global_stats", stats, ttl=120)
        text = (
            "📊 **Global Bot Statistics**\n"
            "──────────────────────────────\n"
            f"👥 Users tracked: `{stats.get('total_users', 0)}`\n"
            f"🚫 Total bans: `{stats.get('total_blacklisted', 0)}`\n"
            f"🏘️ Active chats: `{stats.get('total_chats', 0)}`\n"
            "──────────────────────────────\n"
            "_Stats cached for 2 minutes._"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="gadm_back")]])
        )

    @app.on_callback_query(filters.regex(r"^gadm_users$"))
    async def cb_gadm_users(client: Client, query: CallbackQuery):
        await query.answer("📤 Use /users command to export all users as a text file.", show_alert=True)

    @app.on_callback_query(filters.regex(r"^gadm_broadcast$"))
    async def cb_gadm_broadcast(client: Client, query: CallbackQuery):
        await query.answer("📢 Use /broadcast command — reply to any message with /broadcast to send it to all chats.", show_alert=True)

    @app.on_callback_query(filters.regex(r"^gadm_status$"))
    async def cb_gadm_status(client: Client, query: CallbackQuery):
        await query.answer("🖥️ Use /status command in private chat to view system health.", show_alert=True)

    @app.on_callback_query(filters.regex(r"^gadm_back$"))
    async def cb_gadm_back(client: Client, query: CallbackQuery):
        await query.answer("")
        stats = stats_cache.get("global_stats")
        if stats is None:
            from database.db import get_global_stats
            stats = await get_global_stats()
            stats_cache.set("global_stats", stats, ttl=120)
        header = (
            f"🛡️ **Global Admin Dashboard**\n"
            f"──────────────────────────────\n"
            f"👥 Users tracked: `{stats.get('total_users', 0)}`\n"
            f"🚫 Total bans: `{stats.get('total_blacklisted', 0)}`\n"
            f"🏘️ Active chats: `{stats.get('total_chats', 0)}`\n"
            f"──────────────────────────────\n"
            f"Select an action below:"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 Global Stats", callback_data="gadm_stats"),
                InlineKeyboardButton("👥 All Users", callback_data="gadm_users"),
            ],
            [
                InlineKeyboardButton("📢 Broadcast", callback_data="gadm_broadcast"),
                InlineKeyboardButton("🖥️ Status", callback_data="gadm_status"),
            ],
            [InlineKeyboardButton("❌ Close", callback_data="adm_close")],
        ])
        await query.edit_message_text(header, reply_markup=keyboard)

    @app.on_callback_query(filters.regex(r"^adm_stats_(-?\d+)$"))
    async def cb_stats(client: Client, query: CallbackQuery):
        await query.answer("")
        chat_id = int(query.data[len("adm_stats_"):])
        try:
            cached = stats_cache.get(f"stats_{chat_id}")
            if not cached:
                count = await get_banned_count(chat_id)
                count_key = f"member_count:{chat_id}"
                member_count = member_count_cache.get(count_key)
                if member_count is None:
                    member_count = await client.get_chat_members_count(chat_id)
                    member_count_cache.set(count_key, member_count, ttl=60)
                cached = {"banned": count, "members": member_count}
                stats_cache.set(f"stats_{chat_id}", cached, ttl=120)

            await query.edit_message_text(
                f"📊 **Group Statistics**\n\n"
                f"🏘️ Chat ID: `{chat_id}`\n"
                f"👥 Members: `{cached.get('members', 0)}`\n"
                f"🚫 Blacklisted users: `{cached['banned']}`\n\n"
                f"_Cache refreshes every 2 minutes._",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data=f"adm_back_{chat_id}")
                ]]),
            )
        except Exception as e:
            logger.error(f"adm_stats callback error: {e}")

    @app.on_callback_query(filters.regex(r"^adm_bl_(-?\d+)_(\d+)$"))
    async def cb_blacklist(client: Client, query: CallbackQuery):
        await query.answer("")
        m = re.match(r"^adm_bl_(-?\d+)_(\d+)$", query.data)
        chat_id = int(m.group(1))
        page = int(m.group(2))
        try:
            entries, total = await get_blacklist_page(chat_id, page, _PAGE_SIZE)
            total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

            if not entries:
                text = f"📋 **Blacklist** (Page {page + 1}/{total_pages})\n\nNo entries found."
            else:
                lines = [f"📋 **Blacklist** — Page {page + 1}/{total_pages} ({total} total)\n"]
                for i, entry in enumerate(entries, 1):
                    uid = entry["user_id"]
                    username = entry.get("username") or entry.get("first_name") or "Unknown"
                    ts = entry.get("exit_time", "?")
                    ban_count = entry.get("ban_count", 1)
                    if hasattr(ts, "strftime"):
                        ts = ts.strftime("%Y-%m-%d %H:%M")
                    lines.append(f"`{i + page * _PAGE_SIZE}.` {username} (`{uid}`) — {ts} [×{ban_count}]")
                text = "\n".join(lines)

            nav_buttons = []
            if page > 0:
                nav_buttons.append(
                    InlineKeyboardButton("⬅️ Prev", callback_data=f"adm_bl_{chat_id}_{page - 1}")
                )
            if page < total_pages - 1:
                nav_buttons.append(
                    InlineKeyboardButton("Next ➡️", callback_data=f"adm_bl_{chat_id}_{page + 1}")
                )

            keyboard = []
            if nav_buttons:
                keyboard.append(nav_buttons)
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"adm_back_{chat_id}")])

            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"adm_bl callback error: {e}")

    @app.on_callback_query(filters.regex(r"^adm_export_(-?\d+)$"))
    async def cb_export(client: Client, query: CallbackQuery):
        await query.answer("📤 Generating export...", show_alert=False)
        chat_id = int(query.data[len("adm_export_"):])
        try:
            users = await get_all_seen_users(chat_id=chat_id)
            if not users:
                await query.answer("No users found to export.", show_alert=True)
                return

            lines = [
                f"No Second Chances — User Export for chat {chat_id}",
                f"Total: {len(users)} users",
                "=" * 40,
                "",
            ]
            for u in users:
                uid = u.get("user_id", "?")
                first = u.get("first_seen", "?")
                last = u.get("last_seen", "?")
                if hasattr(first, "strftime"):
                    first = first.strftime("%Y-%m-%d %H:%M")
                if hasattr(last, "strftime"):
                    last = last.strftime("%Y-%m-%d %H:%M")
                lines.append(f"User ID: {uid} | First: {first} | Last: {last}")

            content = "\n".join(lines)
            bio = io.BytesIO(content.encode("utf-8"))
            bio.name = f"users_{chat_id}.txt"

            await client.send_document(
                query.message.chat.id,
                document=bio,
                caption=f"📤 **User Export** — {len(users)} users from `{chat_id}`",
                file_name=f"users_{chat_id}.txt",
            )
        except Exception as e:
            logger.error(f"adm_export error: {e}")
            await query.answer("Export failed.", show_alert=True)

    @app.on_callback_query(filters.regex(r"^adm_analytics_(-?\d+)$"))
    async def cb_analytics(client: Client, query: CallbackQuery):
        await query.answer("")
        chat_id = int(query.data[len("adm_analytics_"):])
        try:
            trend = await get_join_leave_trend(chat_id, 7)

            if not trend:
                text = "📈 **Join/Leave Trend (7 days)**\n\nNo data available for this period."
            else:
                lines = ["📈 **Join/Leave Trend (7 days)**\n"]
                for date, data in sorted(trend.items()):
                    joins = data.get("joins", 0)
                    leaves = data.get("leaves", 0)
                    lines.append(f"`{date}`: +{joins} joined  / -{leaves} left")
                text = "\n".join(lines)

            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data=f"adm_back_{chat_id}")
                ]])
            )
        except Exception as e:
            logger.error(f"adm_analytics error: {e}")

    @app.on_callback_query(filters.regex(r"^adm_settings_(-?\d+)$"))
    async def cb_settings_link(client: Client, query: CallbackQuery):
        await query.answer("")
        chat_id = int(query.data[len("adm_settings_"):])
        settings = settings_cache.get(str(chat_id))
        if settings is None:
            settings = await get_chat_settings(chat_id)

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

        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"adm_back_{chat_id}")])

        await query.edit_message_text(
            "⚙️ **Chat Settings**\n─────────────────\nConfigure bot behavior for this chat:\n",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    @app.on_callback_query(filters.regex(r"^adm_notifications_(-?\d+)$"))
    async def cb_notifications(client: Client, query: CallbackQuery):
        await query.answer("")
        chat_id = int(query.data[len("adm_notifications_"):])
        await query.edit_message_text(
            "🔔 **Notifications**\n─────────────────\n"
            "Configure how the bot notifies you about events:\n\n"
            "Use **/settings** in the group to configure notification preferences.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=f"adm_back_{chat_id}")
            ]])
        )

    @app.on_callback_query(filters.regex(r"^adm_back_(-?\d+)$"))
    async def cb_back(client: Client, query: CallbackQuery):
        await query.answer("")
        chat_id = int(query.data[len("adm_back_"):])

        try:
            chat = await client.get_chat(chat_id)
            chat_name = chat.title or "This Chat"
        except Exception:
            chat_name = f"Chat {chat_id}"

        count_key = f"member_count:{chat_id}"
        member_count = member_count_cache.get(count_key)
        if member_count is None:
            member_count = await client.get_chat_members_count(chat_id)
            member_count_cache.set(count_key, member_count, ttl=60)

        ban_count = await get_banned_count(chat_id)

        header = (
            f"🛡️ **No Second Chances — Admin Panel**\n"
            f"──────────────────────────────\n"
            f"🏘️ **{chat_name}**\n"
            f"👥 Members: `{member_count}`  |  🟢 Bot Active\n"
            f"🚫 Bans enforced: `{ban_count}`\n"
            f"──────────────────────────────\n"
            f"Select an action below:"
        )

        await query.edit_message_text(header, reply_markup=_admin_keyboard(chat_id, ban_count))

    @app.on_callback_query(filters.regex(r"^adm_close$"))
    async def cb_close(client: Client, query: CallbackQuery):
        await query.message.delete()

    @app.on_callback_query(filters.regex(r"^adm_ban_prompt_(-?\d+)$"))
    async def cb_ban_prompt(client: Client, query: CallbackQuery):
        await query.answer(
            "⚡ Use /ban <user_id> or reply to a user's message with /ban to ban them.",
            show_alert=True,
        )

    @app.on_callback_query(filters.regex(r"^adm_unban_prompt_(-?\d+)$"))
    async def cb_unban_prompt(client: Client, query: CallbackQuery):
        await query.answer(
            "✅ Use /unban <user_id> or reply to a user's message with /unban to unban them.",
            show_alert=True,
        )

    @app.on_message(filters.command("ban") & filters.group)
    async def ban_command(client: Client, message: Message):
        user_id = message.from_user.id
        chat_id = message.chat.id
        try:
            member = await client.get_chat_member(chat_id, user_id)
            if not _is_authorized(user_id, member):
                return
        except Exception:
            return

        await client.send_chat_action(chat_id, ChatAction.TYPING)

        target_id = None
        target_name = None
        target_username = None
        target = None

        if message.reply_to_message and message.reply_to_message.from_user:
            target = message.reply_to_message.from_user
            target_id = target.id
            target_name = target.first_name
            target_username = target.username
        else:
            parts = message.text.split()
            if len(parts) < 2:
                await message.reply_text(
                    "🚫 **Usage:** `/ban <user_id>` or reply to a user's message with `/ban`"
                )
                return
            try:
                target_id = int(parts[1])
                try:
                    target = await client.get_users(target_id)
                    target_name = target.first_name
                    target_username = target.username
                except Exception:
                    target_name = f"User #{target_id}"
            except ValueError:
                await message.reply_text("⚠️ Invalid user ID. Must be a number.")
                return

        if not target_id:
            return

        if target and target.is_bot:
            await message.reply_text("⚠️ Cannot ban bots.")
            return

        display_name = f"@{target_username}" if target_username else target_name

        try:
            chat = await client.get_chat(chat_id)
            chat_name = chat.title or "this group"
        except Exception:
            chat_name = "this group"

        confirm_msg = await message.reply_text(
            f"⚠️ **Confirm Ban**\n\n"
            f"You are about to permanently ban:\n"
            f"👤 **{display_name}** (`{target_id}`)\n\n"
            f"This will also blacklist them from rejoining.\n"
            f"Are you sure?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Confirm Ban", callback_data=f"adm_confirm_ban_{chat_id}_{target_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data="adm_close"),
                ]
            ])
        )

    @app.on_callback_query(filters.regex(r"^adm_confirm_ban_(-?\d+)_(\d+)$"))
    async def cb_confirm_ban(client: Client, query: CallbackQuery):
        await query.answer("")
        m = re.match(r"^adm_confirm_ban_(-?\d+)_(\d+)$", query.data)
        chat_id = int(m.group(1))
        target_id = int(m.group(2))

        await query.edit_message_text("⏳ Processing ban...")

        try:
            try:
                target_user = await client.get_users(target_id)
                display_name = f"@{target_user.username}" if target_user.username else target_user.first_name or f"User #{target_id}"
                first_name = target_user.first_name or ""
                username = target_user.username or ""
            except Exception:
                display_name = f"User #{target_id}"
                first_name = ""
                username = ""

            await client.ban_chat_member(chat_id, target_id)

            await add_to_blacklist(target_id, chat_id, 0, first_name=first_name, username=username)
            blacklist_cache.set(f"{target_id}:{chat_id}", True, ttl=300)

            joke = await generate_ban_joke(target_id)
            timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

            settings = settings_cache.get(str(chat_id))
            if settings is None:
                settings = await get_chat_settings(chat_id)

            if settings.get("dm_banned_user", False) and first_name:
                try:
                    try:
                        chat_obj = await client.get_chat(chat_id)
                        chat_name = chat_obj.title or "this group"
                    except Exception:
                        chat_name = "this group"

                    dm_text = await generate_ban_dm(target_id, first_name, chat_name)
                    await client.send_message(target_id, dm_text)
                except UserIsBlocked:
                    logger.warning(f"Cannot DM banned user {target_id} — user blocked the bot")
                except Exception as e:
                    logger.warning(f"Failed to DM banned user {target_id}: {e}")

            await query.edit_message_text(
                f"🚫 **Banned**\n"
                f"─────────────\n"
                f"👤 {display_name} (`{target_id}`)\n"
                f"📅 Banned: `{timestamp}`\n"
                f"─────────────\n"
                f"_{joke}_"
            )
        except Exception as e:
            logger.error(f"ban confirmation error: {e}")
            await query.edit_message_text(f"⚠️ Failed to ban user: `{e}`")

    @app.on_message(filters.command("unban") & filters.group)
    async def unban_command(client: Client, message: Message):
        user_id = message.from_user.id
        chat_id = message.chat.id
        try:
            member = await client.get_chat_member(chat_id, user_id)
            if not _is_authorized(user_id, member):
                return
        except Exception:
            return

        await client.send_chat_action(chat_id, ChatAction.TYPING)

        target_id = None
        target_name = None
        target_username = None

        if message.reply_to_message and message.reply_to_message.from_user:
            target = message.reply_to_message.from_user
            target_id = target.id
            target_name = target.first_name
            target_username = target.username
        else:
            parts = message.text.split()
            if len(parts) < 2:
                await message.reply_text(
                    "✅ **Usage:** `/unban <user_id>` or reply to a user's message with `/unban`"
                )
                return
            try:
                target_id = int(parts[1])
                try:
                    user_obj = await client.get_users(target_id)
                    target_name = user_obj.first_name
                    target_username = user_obj.username
                except Exception:
                    target_name = f"User #{target_id}"
            except ValueError:
                await message.reply_text("⚠️ Invalid user ID. Must be a number.")
                return

        if not target_id:
            return

        display_name = f"@{target_username}" if target_username else target_name

        from database.db import blacklist_coll
        bl_doc = await blacklist_coll.find_one({"user_id": target_id, "chat_id": chat_id})
        exit_time = bl_doc.get("exit_time") if bl_doc else None
        ts_str = exit_time.strftime("%Y-%m-%d %H:%M") if exit_time and hasattr(exit_time, "strftime") else "unknown"

        confirm_msg = await message.reply_text(
            f"✅ **Confirm Unban**\n\n"
            f"👤 **{display_name}** (`{target_id}`)\n"
            f"🚫 Banned since: `{ts_str}`\n\n"
            f"This will remove them from the blacklist.\n"
            f"Are you sure?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Confirm Unban", callback_data=f"adm_confirm_unban_{chat_id}_{target_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data="adm_close"),
                ]
            ])
        )

    @app.on_callback_query(filters.regex(r"^adm_confirm_unban_(-?\d+)_(\d+)$"))
    async def cb_confirm_unban(client: Client, query: CallbackQuery):
        await query.answer("")
        m = re.match(r"^adm_confirm_unban_(-?\d+)_(\d+)$", query.data)
        chat_id = int(m.group(1))
        target_id = int(m.group(2))

        await query.edit_message_text("⏳ Processing unban...")

        try:
            await client.unban_chat_member(chat_id, target_id)
            removed = await remove_from_blacklist(target_id, chat_id)
            blacklist_cache.delete(f"{target_id}:{chat_id}")

            if removed:
                await query.edit_message_text(
                    f"✅ **User `{target_id}` has been unbanned and removed from blacklist.**"
                )
            else:
                await query.edit_message_text(
                    f"✅ **User `{target_id}` unbanned.** _(Was not in blacklist)_"
                )
        except Exception as e:
            logger.error(f"unban confirmation error: {e}")
            await query.edit_message_text(f"⚠️ Failed to unban user: `{e}`")

    @app.on_message(filters.command("blacklist") & filters.group)
    async def blacklist_command(client: Client, message: Message):
        user_id = message.from_user.id
        chat_id = message.chat.id
        try:
            member = await client.get_chat_member(chat_id, user_id)
            if not _is_authorized(user_id, member):
                return
        except Exception:
            return

        await client.send_chat_action(chat_id, ChatAction.TYPING)

        try:
            entries, total = await get_blacklist_page(chat_id, 0, _PAGE_SIZE)
            total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

            if not entries:
                await message.reply_text("📋 **Blacklist is empty.**")
                return

            lines = [
                f"📋 **Blacklist** — Page 1/{total_pages} ({total} total)\n"
            ]
            for i, entry in enumerate(entries, 1):
                uid = entry["user_id"]
                username = entry.get("username") or entry.get("first_name") or "Unknown"
                ts = entry.get("exit_time", "?")
                ban_count = entry.get("ban_count", 1)
                if hasattr(ts, "strftime"):
                    ts = ts.strftime("%Y-%m-%d %H:%M")
                lines.append(f"`{i}.` {username} (`{uid}`) — {ts} [×{ban_count}]")

            nav = []
            if total_pages > 1:
                nav.append(
                    InlineKeyboardButton("Next ➡️", callback_data=f"adm_bl_{chat_id}_1")
                )

            keyboard = [nav] if nav else []
            keyboard.append([InlineKeyboardButton("❌ Close", callback_data="adm_close")])

            await message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception as e:
            logger.error(f"/blacklist error: {e}")
            await message.reply_text("⚠️ Error fetching blacklist.")

    @app.on_message(filters.command("users") & (filters.private | filters.group))
    async def users_command(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            return

        try:
            users = await get_all_seen_users()
            if not users:
                await message.reply_text("📊 No users found in database.")
                return

            lines = [
                "No Second Chances — Global User Export",
                f"Total users: {len(users)}",
                f"Generated at: {datetime.now(UTC).isoformat()}",
                "=" * 50,
                "",
            ]
            for u in users:
                uid = u.get("user_id", "?")
                cid = u.get("chat_id", "?")
                first = u.get("first_seen", "?")
                last = u.get("last_seen", "?")
                if hasattr(first, "strftime"):
                    first = first.strftime("%Y-%m-%d %H:%M")
                if hasattr(last, "strftime"):
                    last = last.strftime("%Y-%m-%d %H:%M")
                lines.append(f"UID: {uid} | Chat: {cid} | First: {first} | Last: {last}")

            content = "\n".join(lines)
            bio = io.BytesIO(content.encode("utf-8"))
            bio.name = "all_users.txt"

            status_msg = await message.reply_text("📤 Generating user list...")
            await client.send_document(
                message.chat.id,
                document=bio,
                caption=f"📤 **Global User Export** — `{len(users)}` total entries",
                file_name="all_users.txt",
            )
            await status_msg.delete()
        except Exception as e:
            logger.error(f"/users error: {e}")
            await message.reply_text(f"⚠️ Export failed: `{e}`")

    @app.on_message(filters.command("broadcast") & filters.private)
    async def broadcast_command(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            return

        if not message.reply_to_message:
            await message.reply_text(
                "📢 **Usage:** Reply to a message with `/broadcast` to send it to all tracked chats."
            )
            return

        try:
            users = await get_all_seen_users()
            chat_ids = list({u["chat_id"] for u in users if u.get("chat_id")})

            if not chat_ids:
                await message.reply_text("⚠️ No chats to broadcast to.")
                return

            status_msg = await message.reply_text(
                f"📢 Broadcasting to {len(chat_ids)} chats..."
            )

            success = 0
            failed = 0

            async def send_one(cid: int) -> None:
                nonlocal success, failed
                async with _broadcast_sem:
                    try:
                        await message.reply_to_message.copy(cid)
                        success += 1
                        await asyncio.sleep(0.05)
                    except FloodWait as fw:
                        await asyncio.sleep(fw.value + 1)
                        try:
                            await message.reply_to_message.copy(cid)
                            success += 1
                        except Exception:
                            failed += 1
                    except Exception:
                        failed += 1

            batch_size = 50
            for i in range(0, len(chat_ids), batch_size):
                batch = chat_ids[i: i + batch_size]
                await asyncio.gather(*[send_one(cid) for cid in batch])
                if (i + batch_size) % 1000 == 0:
                    try:
                        await status_msg.edit_text(
                            f"📢 Broadcasting... {i + batch_size}/{len(chat_ids)} chats done."
                        )
                    except Exception:
                        pass

            await status_msg.edit_text(
                f"📢 **Broadcast Complete**\n\n"
                f"✅ Success: `{success}`\n"
                f"❌ Failed: `{failed}`\n"
                f"📊 Total: `{len(chat_ids)}`"
            )
        except Exception as e:
            logger.error(f"/broadcast error: {e}")
            await message.reply_text(f"⚠️ Broadcast failed: `{e}`")
