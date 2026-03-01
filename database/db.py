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
bot_users_coll = db["bot_users"]
chat_settings_coll = db["chat_settings"]

DEFAULT_SETTINGS = {
    "notify_leave": False,
    "post_ban_joke": True,
    "dm_banned_user": False,
    "auto_welcome": False,
}


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
        await pleas_coll.create_index(
            [("created_at", -1)],
            name="plea_created_at"
        )
        await bot_users_coll.create_index(
            [("user_id", 1)],
            unique=True,
            name="unique_bot_user"
        )
        await bot_users_coll.create_index(
            [("last_seen", -1)],
            name="bot_user_last_seen"
        )
        await chat_settings_coll.create_index(
            [("chat_id", 1)],
            unique=True,
            name="unique_chat_settings"
        )
        logger.info("Database indexes setup successfully.")
    except Exception as e:
        logger.error(f"Error setting up database indexes: {e}")


async def add_to_blacklist(user_id: int, chat_id: int, userbase_count: int, first_name: str = "", username: str = "") -> bool:
    try:
        existing = await blacklist_coll.find_one({"user_id": user_id, "chat_id": chat_id})
        if existing:
            await blacklist_coll.update_one(
                {"user_id": user_id, "chat_id": chat_id},
                {
                    "$set": {
                        "first_name": first_name,
                        "username": username,
                        "userbase_count": userbase_count,
                        "last_attempt": datetime.now(UTC),
                    },
                    "$inc": {"ban_count": 1},
                }
            )
        else:
            await blacklist_coll.insert_one({
                "user_id": user_id,
                "chat_id": chat_id,
                "first_name": first_name,
                "username": username,
                "exit_time": datetime.now(UTC),
                "userbase_count": userbase_count,
                "last_attempt": datetime.now(UTC),
                "ban_count": 1,
            })
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


async def add_plea(user_id: int, chat_id: int, message: str) -> str | None:
    try:
        result = await pleas_coll.insert_one({
            "user_id": user_id,
            "chat_id": chat_id,
            "message": message,
            "status": "pending",
            "created_at": datetime.now(UTC),
        })
        return str(result.inserted_id)
    except Exception as e:
        logger.error(f"Error adding plea for user {user_id}: {e}")
        return None


async def update_plea_status(plea_id: str, status: str) -> bool:
    try:
        from bson import ObjectId
        await pleas_coll.update_one(
            {"_id": ObjectId(plea_id)},
            {
                "$set": {
                    "status": status,
                    "resolved_at": datetime.now(UTC),
                }
            }
        )
        return True
    except Exception as e:
        logger.error(f"Error updating plea status: {e}")
        return False


async def get_plea(plea_id: str) -> dict | None:
    try:
        from bson import ObjectId
        doc = await pleas_coll.find_one({"_id": ObjectId(plea_id)})
        return doc
    except Exception as e:
        logger.error(f"Error fetching plea: {e}")
        return None


async def upsert_bot_user(user_id: int, first_name: str, username: str | None = None) -> bool:
    try:
        existing = await bot_users_coll.find_one({"user_id": user_id})
        if existing:
            await bot_users_coll.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "first_name": first_name,
                        "username": username,
                        "last_seen": datetime.now(UTC),
                    },
                    "$inc": {"interaction_count": 1},
                }
            )
            return False
        else:
            await bot_users_coll.insert_one({
                "user_id": user_id,
                "first_name": first_name,
                "username": username,
                "first_seen": datetime.now(UTC),
                "last_seen": datetime.now(UTC),
                "interaction_count": 1,
            })
            return True
    except Exception as e:
        logger.error(f"Error upserting bot user: {e}")
        return False


async def get_bot_user(user_id: int) -> dict | None:
    try:
        return await bot_users_coll.find_one({"user_id": user_id})
    except Exception as e:
        logger.error(f"Error fetching bot user: {e}")
        return None


async def get_chat_settings(chat_id: int) -> dict:
    try:
        doc = await chat_settings_coll.find_one({"chat_id": chat_id})
        if doc:
            return doc.get("settings", {})
        return {}
    except Exception as e:
        logger.error(f"Error fetching chat settings: {e}")
        return {}


async def set_chat_setting(chat_id: int, key: str, value: bool) -> None:
    try:
        await chat_settings_coll.update_one(
            {"chat_id": chat_id},
            {
                "$set": {f"settings.{key}": value},
            },
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error setting chat setting: {e}")


async def increment_ban_count(user_id: int, chat_id: int) -> None:
    try:
        await blacklist_coll.update_one(
            {"user_id": user_id, "chat_id": chat_id},
            {
                "$inc": {"ban_count": 1},
                "$set": {"last_attempt": datetime.now(UTC)},
            }
        )
    except Exception as e:
        logger.error(f"Error incrementing ban count: {e}")


async def get_user_blacklist_entries(user_id: int) -> list:
    try:
        cursor = blacklist_coll.find({"user_id": user_id}, {"_id": 0}).sort("exit_time", -1)
        return await cursor.to_list(length=None)
    except Exception as e:
        logger.error(f"Error fetching user blacklist entries: {e}")
        return []


async def get_join_leave_trend(chat_id: int, days: int = 7) -> dict:
    try:
        from datetime import timedelta
        start_date = datetime.now(UTC) - timedelta(days=days)

        pipeline = [
            {"$match": {"chat_id": chat_id, "first_seen": {"$gte": start_date}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$first_seen"}},
                "joins": {"$sum": 1}
            }},
            {"$sort": {"_id": 1}}
        ]
        join_results = await seen_coll.aggregate(pipeline).to_list(length=None)

        pipeline = [
            {"$match": {"chat_id": chat_id, "exit_time": {"$gte": start_date}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$exit_time"}},
                "leaves": {"$sum": 1}
            }},
            {"$sort": {"_id": 1}}
        ]
        leave_results = await blacklist_coll.aggregate(pipeline).to_list(length=None)

        trend = {}
        for jr in join_results:
            trend[jr["_id"]] = {"joins": jr["joins"], "leaves": 0}
        for lr in leave_results:
            if lr["_id"] in trend:
                trend[lr["_id"]]["leaves"] = lr["leaves"]
            else:
                trend[lr["_id"]] = {"joins": 0, "leaves": lr["leaves"]}

        return trend
    except Exception as e:
        logger.error(f"Error fetching join/leave trend: {e}")
        return {}
