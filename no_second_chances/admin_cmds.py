import asyncio
import io
import re
import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait
from database.db import (
    add_to_blacklist,
    remove_from_blacklist,
    get_blacklist_page,
    get_all_seen_users,
    get_banned_count,
)
from no_second_chances.cache import rate_limiter, blacklist_cache, stats_cache
from no_second_chances.ai_client import generate_ban_joke
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


def _admin_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats", callback_data=f"adm_stats_{chat_id}"),
            InlineKeyboardButton("📋 Blacklist", callback_data=f"adm_bl_{chat_id}_0"),
        ],
        [
            InlineKeyboardButton("✅ Unban User", callback_data=f"adm_unban_prompt_{chat_id}"),
            InlineKeyboardButton("🚫 Ban User", callback_data=f"adm_ban_prompt_{chat_id}"),
        ],
        [
            InlineKeyboardButton("📤 Export Users", callback_data=f"adm_export_{chat_id}"),
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

        await message.reply_text(
            f"🛡️ **No Second Chances — Admin Panel**\n"
            f"🏘️ Chat: `{chat_id}`\n\n"
            "Select an action below:",
            reply_markup=_admin_keyboard(chat_id),
        )

    @app.on_callback_query(filters.regex(r"^adm_stats_(-?\d+)$"))
    async def cb_stats(client: Client, query: CallbackQuery):
        chat_id = int(query.data[len("adm_stats_"):])
        try:
            cached = stats_cache.get(f"stats_{chat_id}")
            if not cached:
                count = await get_banned_count(chat_id)
                cached = {"banned": count}
                stats_cache.set(f"stats_{chat_id}", cached, ttl=120)

            await query.edit_message_text(
                f"📊 **Group Statistics**\n\n"
                f"🏘️ Chat ID: `{chat_id}`\n"
                f"🚫 Blacklisted users: `{cached['banned']}`\n\n"
                "_Cache refreshes every 2 minutes._",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data=f"adm_back_{chat_id}")]
                ]),
            )
        except Exception as e:
            logger.error(f"adm_stats callback error: {e}")
            await query.answer("Error fetching stats.", show_alert=True)

    @app.on_callback_query(filters.regex(r"^adm_bl_(-?\d+)_(\d+)$"))
    async def cb_blacklist(client: Client, query: CallbackQuery):
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
                    ts = entry.get("exit_time", "?")
                    if hasattr(ts, "strftime"):
                        ts = ts.strftime("%Y-%m-%d %H:%M")
                    lines.append(f"`{i + page * _PAGE_SIZE}.` `{uid}` — {ts}")
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
            keyboard.append(
                [InlineKeyboardButton("🔙 Back", callback_data=f"adm_back_{chat_id}")]
            )

            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"adm_bl callback error: {e}")
            await query.answer("Error fetching blacklist.", show_alert=True)

    @app.on_callback_query(filters.regex(r"^adm_export_(-?\d+)$"))
    async def cb_export(client: Client, query: CallbackQuery):
        chat_id = int(query.data[len("adm_export_"):])
        await query.answer("📤 Generating export...", show_alert=False)
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

    @app.on_callback_query(filters.regex(r"^adm_back_(-?\d+)$"))
    async def cb_back(client: Client, query: CallbackQuery):
        chat_id = int(query.data[len("adm_back_"):])
        await query.edit_message_text(
            f"🛡️ **No Second Chances — Admin Panel**\n"
            f"🏘️ Chat: `{chat_id}`\n\n"
            "Select an action below:",
            reply_markup=_admin_keyboard(chat_id),
        )

    @app.on_callback_query(filters.regex(r"^adm_close$"))
    async def cb_close(client: Client, query: CallbackQuery):
        await query.message.delete()

    @app.on_callback_query(filters.regex(r"^adm_(un)?ban_prompt_(-?\d+)$"))
    async def cb_ban_unban_prompt(client: Client, query: CallbackQuery):
        m = re.match(r"^adm_(un)?ban_prompt_(-?\d+)$", query.data)
        action = "unban" if m.group(1) else "ban"
        chat_id = m.group(2)
        emoji = "✅" if action == "unban" else "🚫"
        await query.answer(
            f"{emoji} Use /{action} <user_id> in the group chat to {action} a user.",
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

        parts = message.text.split()
        if len(parts) < 2:
            await message.reply_text("🚫 **Usage:** `/ban <user_id>`")
            return

        try:
            target_id = int(parts[1])
        except ValueError:
            await message.reply_text("⚠️ Invalid user ID. Must be a number.")
            return

        try:
            await client.ban_chat_member(chat_id, target_id)
            await add_to_blacklist(target_id, chat_id, 0)
            blacklist_cache.set(f"{target_id}:{chat_id}", True, ttl=300)

            joke = await generate_ban_joke(target_id)
            await message.reply_text(
                f"🚫 **User `{target_id}` has been banned.**\n\n_{joke}_"
            )
        except Exception as e:
            logger.error(f"/ban error: {e}")
            await message.reply_text(f"⚠️ Failed to ban user: `{e}`")

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

        parts = message.text.split()
        if len(parts) < 2:
            await message.reply_text("✅ **Usage:** `/unban <user_id>`")
            return

        try:
            target_id = int(parts[1])
        except ValueError:
            await message.reply_text("⚠️ Invalid user ID. Must be a number.")
            return

        try:
            await client.unban_chat_member(chat_id, target_id)
            removed = await remove_from_blacklist(target_id, chat_id)
            blacklist_cache.delete(f"{target_id}:{chat_id}")

            if removed:
                await message.reply_text(
                    f"✅ **User `{target_id}` has been unbanned and removed from blacklist.**"
                )
            else:
                await message.reply_text(
                    f"✅ **User `{target_id}` unbanned.** _(Was not in blacklist)_"
                )
        except Exception as e:
            logger.error(f"/unban error: {e}")
            await message.reply_text(f"⚠️ Failed to unban user: `{e}`")

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
                ts = entry.get("exit_time", "?")
                if hasattr(ts, "strftime"):
                    ts = ts.strftime("%Y-%m-%d %H:%M")
                lines.append(f"`{i}.` `{uid}` — {ts}")

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
                f"Generated at: {datetime.datetime.utcnow().isoformat()}",
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
