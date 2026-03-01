from pyrogram import Client, filters
from pyrogram.types import ChatMemberUpdated
from pyrogram.enums import ChatMemberStatus
from database.db import add_to_blacklist, is_user_blacklisted, log_user_join
from logger import logger

def register_plugin(app: Client):
    
    @app.on_chat_member_updated()
    async def handle_chat_member_update(_, cms: ChatMemberUpdated):
        """
        Detects leaves and joins to enforce anti-rejoin rules for Groups and Channels.
        """
        chat_id = cms.chat.id
        old = cms.old_chat_member
        new = cms.new_chat_member
        
        user = (new.user if new else old.user) if (new or old) else None
        if not user or user.is_bot:
            return

        user_id = user.id
        
        # Determine the action
        is_join = False
        is_leave = False
        
        # Standard Member Logic
        if new and new.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            if not old or old.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                is_join = True
        
        if old and old.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            if not new or new.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                is_leave = True

        # Case: USER JOINED
        if is_join:
            try:
                # 1. Log the join in MongoDB (History)
                await log_user_join(user_id, chat_id)
                logger.info(f"User {user_id} joined chat {chat_id}. Logged.")

                # 2. Check if blacklisted
                if await is_user_blacklisted(user_id, chat_id):
                    # Ban immediately
                    await app.ban_chat_member(chat_id, user_id)
                    logger.info(f"🚨 ALERT: Blacklisted user {user_id} attempted to rejoin {chat_id}. BANNED.")
                    
                    # Notify (optional, but requested 'on the spot')
                    # await app.send_message(chat_id, f"🚫 **Enforcement Action**: User {user_id} banned for anti-rejoin violation.")
            except Exception as e:
                logger.error(f"Error handling join for {user_id} in {chat_id}: {e}")

        # Case: USER LEFT
        elif is_leave:
            try:
                # Don't blacklist admins/owners
                if old and old.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                    logger.info(f"Admin/Owner {user_id} left {chat_id}. No action taken.")
                    return

                # Log exit to blacklist
                member_count = await app.get_chat_members_count(chat_id)
                await add_to_blacklist(user_id, chat_id, member_count)
                logger.info(f"User {user_id} left {chat_id}. Blacklisted. (Userbase: {member_count})")
            except Exception as e:
                logger.error(f"Error handling leave for {user_id} in {chat_id}: {e}")
