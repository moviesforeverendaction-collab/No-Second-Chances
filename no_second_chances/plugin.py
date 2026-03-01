from pyrogram import Client, filters
from pyrogram.types import ChatMemberUpdated
from pyrogram.enums import ChatMemberStatus
from database.db import add_to_blacklist, is_user_blacklisted, log_user_join
from no_second_chances.cache import blacklist_cache, member_count_cache
from no_second_chances.ai_client import generate_ban_joke
from logger import logger


def register_plugin(app: Client):

    @app.on_chat_member_updated()
    async def handle_chat_member_update(_, cms: ChatMemberUpdated):
        chat_id = cms.chat.id
        old = cms.old_chat_member
        new = cms.new_chat_member

        user = (new.user if new else old.user) if (new or old) else None
        if not user or user.is_bot:
            return

        user_id = user.id

        is_join = False
        is_leave = False

        if new and new.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            if not old or old.status not in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            ):
                is_join = True

        if old and old.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            if not new or new.status not in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
                ChatMemberStatus.RESTRICTED,
            ):
                # Admin-initiated ban: do not blacklist
                if new and new.status == ChatMemberStatus.BANNED:
                    is_leave = False
                else:
                    is_leave = True

        if is_join:
            try:
                await log_user_join(user_id, chat_id)
                logger.info(f"User {user_id} joined chat {chat_id}. Logged.")

                bl_key = f"{user_id}:{chat_id}"
                cached_bl = blacklist_cache.get(bl_key)
                if cached_bl is None:
                    cached_bl = await is_user_blacklisted(user_id, chat_id)
                    blacklist_cache.set(bl_key, cached_bl, ttl=300)

                if cached_bl:
                    await app.ban_chat_member(chat_id, user_id)
                    joke = await generate_ban_joke(user_id)
                    logger.info(
                        f"🚨 ALERT: Blacklisted user {user_id} attempted to rejoin {chat_id}. BANNED."
                    )
                    try:
                        await app.send_message(chat_id, f"🚫 {joke}")
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Error handling join for {user_id} in {chat_id}: {e}")

        elif is_leave:
            try:
                if old and old.status in (
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER,
                ):
                    logger.info(f"Admin/Owner {user_id} left {chat_id}. No action taken.")
                    return

                count_key = f"member_count:{chat_id}"
                member_count = member_count_cache.get(count_key)
                if member_count is None:
                    member_count = await app.get_chat_members_count(chat_id)
                    member_count_cache.set(count_key, member_count, ttl=60)

                await add_to_blacklist(user_id, chat_id, member_count)
                blacklist_cache.set(f"{user_id}:{chat_id}", True, ttl=300)
                logger.info(
                    f"User {user_id} left {chat_id}. Blacklisted. (Userbase: {member_count})"
                )
            except Exception as e:
                logger.error(f"Error handling leave for {user_id} in {chat_id}: {e}")
