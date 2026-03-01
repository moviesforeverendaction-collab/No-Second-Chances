from datetime import datetime, UTC
from motor.motor_asyncio import AsyncIOMotorClient
from secret import MONGO_URL
from logger import logger

# Initialize AsyncIOMotorClient
client = AsyncIOMotorClient(MONGO_URL)
db = client["no_second_chances"]
blacklist_coll = db["blacklist"]
seen_coll = db["seen_users"] # Tracking all users who ever joined

async def setup_database() -> None:
    """
    Initialize database and create unique compound index.
    """
    try:
        # Create a compound unique index on user_id and chat_id
        await blacklist_coll.create_index(
            [("user_id", 1), ("chat_id", 1)],
            unique=True,
            name="unique_blacklist_user_chat"
        )
        # Create a compound unique index on member history
        await seen_coll.create_index(
            [("user_id", 1), ("chat_id", 1)],
            unique=True,
            name="unique_seen_user_chat"
        )
        logger.info("Database unique indexes setup successfully.")
    except Exception as e:
        logger.error(f"Error setting up database indexes: {e}")

async def add_to_blacklist(user_id: int, chat_id: int, userbase_count: int) -> bool:
    """
    Log user exit data (upsert).
    """
    try:
        data = {
            "user_id": user_id,
            "chat_id": chat_id,
            "exit_time": datetime.now(UTC),
            "userbase_count": userbase_count
        }
        await blacklist_coll.update_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"$set": data},
            upsert=True
        )
        logger.info(f"User {user_id} blacklisted from chat {chat_id}.")
        return True
    except Exception as e:
        logger.error(f"Error blacklisting user {user_id}: {e}")
        return False

async def is_user_blacklisted(user_id: int, chat_id: int) -> bool:
    """
    Check if a user is blacklisted from a specific chat.
    """
    try:
        user = await blacklist_coll.find_one({"user_id": user_id, "chat_id": chat_id})
        return user is not None
    except Exception as e:
        logger.error(f"Error checking blacklist for user {user_id}: {e}")
        return False

async def log_user_join(user_id: int, chat_id: int) -> bool:
    """
    Log when a user joins a chat.
    """
    try:
        data = {
            "user_id": user_id,
            "chat_id": chat_id,
            "first_seen": datetime.now(UTC),
            "last_seen": datetime.now(UTC)
        }
        await seen_coll.update_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"$set": {"last_seen": datetime.now(UTC)}, "$setOnInsert": {"first_seen": datetime.now(UTC)}},
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Error logging join for user {user_id}: {e}")
        return False

async def get_banned_count(chat_id: int) -> int:
    """
    Get the total number of banned/blacklisted users for a chat.
    """
    try:
        return await blacklist_coll.count_documents({"chat_id": chat_id})
    except Exception as e:
        logger.error(f"Error getting banned count for chat {chat_id}: {e}")
        return 0
