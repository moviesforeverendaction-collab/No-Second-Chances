from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatMemberStatus
from database.db import get_banned_count
from secret import ADMIN_IDS
from logger import logger

def register_ui(app: Client):

    @app.on_message(filters.command("admin") & filters.group)
    async def admin_panel(_, message: Message):
        """
        Sends the admin panel keyboard to authorized users.
        """
        chat_id = message.chat.id
        user_id = message.from_user.id
        
        try:
            # Check if user is in the global ADMIN_IDS list OR is a chat admin/owner
            is_global_admin = user_id in ADMIN_IDS
            member = await app.get_chat_member(chat_id, user_id)
            is_chat_admin = member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)

            if not (is_global_admin or is_chat_admin):
                return # Ignore unauthorized users
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 View Stats", callback_data=f"stats_{chat_id}")],
                [InlineKeyboardButton("❌ Close Menu", callback_data="close_admin")]
            ])
            
            await message.reply_text(
                "🛡 **No Second Chances: Admin Panel**\n\nManage anti-rejoin settings and view group statistics.",
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Error in /admin command: {e}")

    @app.on_callback_query(filters.regex(r"^stats_"))
    async def view_stats(_, query: CallbackQuery):
        """
        Displays statistics for the chat.
        """
        chat_id = int(query.data.split("_")[1])
        
        try:
            banned_count = await get_banned_count(chat_id)
            
            await query.answer(f"Total Banned: {banned_count}", show_alert=True)
            
            # Update the message with stats
            await query.edit_message_text(
                f"📊 **Group Statistics**\n\n"
                f"• Total users blacklisted: `{banned_count}`\n\n"
                f"Action logged at UTC time.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_admin")]
                ])
            )
        except Exception as e:
            logger.error(f"Error in stats callback: {e}")

    @app.on_callback_query(filters.regex(r"^back_admin$"))
    async def back_to_main(_, query: CallbackQuery):
        """
        Returns to main admin panel.
        """
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 View Stats", callback_data=f"stats_{query.message.chat.id}")],
            [InlineKeyboardButton("❌ Close Menu", callback_data="close_admin")]
        ])
        await query.edit_message_text(
            "🛡 **No Second Chances: Admin Panel**\n\nManage anti-rejoin settings and view group statistics.",
            reply_markup=keyboard
        )

    @app.on_callback_query(filters.regex(r"^close_admin$"))
    async def close_menu(_, query: CallbackQuery):
        """
        Closes the admin menu.
        """
        await query.message.delete()
        await query.answer("Menu closed.")
