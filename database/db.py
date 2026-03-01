from datetime import datetime, UTC
from motor.motor_asyncio import AsyncIOMotorClient
from secret import MONGO_URL
from logger import logger

client = AsyncIOMotorClient(
    MONGO_URL,
    maxPoolSize=50,
    minPoolSize=5,
    maxIdleTimeMS=30000,
    connectTimeoutMS=10000,
    serverSelectionTimeoutMS=5000,
)
db = client["no_second_chances"]
blacklist_coll = db["blacklist"]
seen_coll = db["seen_users"]
pleas_coll = db["pleas"]


async def setup_database() -> None:
    try:
        await blacklist_coll.create_index(
            [("user_id", 1), ("chat_id", 1)],
            unique=True,
            name="unique_blacklist_user_chat"
        )
        await blacklist_coll.create_index(
            [("chat_id", 1), ("exit_time", -1)],
            name="bl_chat_sorted"
        )
        await seen_coll.create_index(
            [("user_id", 1), ("chat_id", 1)],
            unique=True,
            name="unique_seen_user_chat"
        )
        await seen_coll.create_index(
            [("user_id", 1)],
            name="seen_user_id"
        )
        await pleas_coll.create_index(
            [("user_id", 1), ("chat_id", 1)],
            name="plea_lookup"
        )
        logger.info("Database indexes setup successfully.")
    except Exception as e:
        logger.error(f"Error setting up database indexes: {e}")


async def add_to_blacklist(user_id: int, chat_id: int, userbase_count: int) -> bool:
    try:
        await blacklist_coll.update_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"$set": {
                "user_id": user_id,
                "chat_id": chat_id,
                "exit_time": datetime.now(UTC),
                "userbase_count": userbase_count
            }},
            upsert=True
        )
        logger.info(f"User {user_id} blacklisted from chat {chat_id}.")
        return True
    except Exception as e:
        logger.error(f"Error blacklisting user {user_id}: {e}")
        return False


async def remove_from_blacklist(user_id: int, chat_id: int) -> bool:
    try:
        result = await blacklist_coll.delete_one({"user_id": user_id, "chat_id": chat_id})
        removed = result.deleted_count > 0
        if removed:
            logger.info(f"User {user_id} removed from blacklist for chat {chat_id}.")
        return removed
    except Exception as e:
        logger.error(f"Error removing user {user_id} from blacklist: {e}")
        return False


async def is_user_blacklisted(user_id: int, chat_id: int) -> bool:
    try:
        user = await blacklist_coll.find_one({"user_id": user_id, "chat_id": chat_id})
        return user is not None
    except Exception as e:
        logger.error(f"Error checking blacklist for user {user_id}: {e}")
        return False


async def log_user_join(user_id: int, chat_id: int) -> bool:
    try:
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
    try:
        return await blacklist_coll.count_documents({"chat_id": chat_id})
    except Exception as e:
        logger.error(f"Error getting banned count for chat {chat_id}: {e}")
        return 0


async def get_blacklist_page(chat_id: int, page: int, page_size: int = 10) -> tuple:
    try:
        total = await blacklist_coll.count_documents({"chat_id": chat_id})
        cursor = (
            blacklist_coll.find({"chat_id": chat_id}, {"_id": 0})
            .sort("exit_time", -1)
            .skip(page * page_size)
            .limit(page_size)
        )
        entries = await cursor.to_list(length=page_size)
        return entries, total
    except Exception as e:
        logger.error(f"Error fetching blacklist page for chat {chat_id}: {e}")
        return [], 0


async def get_all_seen_users(chat_id: int = None) -> list:
    try:
        query = {"chat_id": chat_id} if chat_id is not None else {}
        cursor = seen_coll.find(query, {"_id": 0}).sort("last_seen", -1)
        return await cursor.to_list(length=None)
    except Exception as e:
        logger.error(f"Error fetching seen users: {e}")
        return []


async def get_global_stats() -> dict:
    try:
        total_users = await seen_coll.count_documents({})
        total_blacklisted = await blacklist_coll.count_documents({})
        total_chats = len(await seen_coll.distinct("chat_id"))
        return {
            "total_users": total_users,
            "total_blacklisted": total_blacklisted,
            "total_chats": total_chats,
        }
    except Exception as e:
        logger.error(f"Error fetching global stats: {e}")
        return {"total_users": 0, "total_blacklisted": 0, "total_chats": 0}


async def add_plea(user_id: int, chat_id: int, message: str) -> bool:
    try:
        await pleas_coll.insert_one({
            "user_id": user_id,
            "chat_id": chat_id,
            "message": message,
            "created_at": datetime.now(UTC),
        })
        return True
    except Exception as e:
        logger.error(f"Error adding plea for user {user_id}: {e}")
        return False
