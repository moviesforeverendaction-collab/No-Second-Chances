import os
from dotenv import load_dotenv
from logger import logger

# Safe loading of .env file
if os.path.exists(".env"):
    load_dotenv()
else:
    logger.warning(".env file not found. Using environment variables.")

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = os.getenv("ADMIN_ID", "")  # Can be a single ID or comma-separated list

# Basic validation
if not all([API_ID, API_HASH, BOT_TOKEN, MONGO_URL]):
    logger.critical("Missing required environment variables (API_ID, API_HASH, BOT_TOKEN, MONGO_URL)!")
    raise EnvironmentError("Incomplete configuration.")

try:
    API_ID = int(API_ID)
except ValueError:
    logger.critical("API_ID must be an integer!")
    raise

# Parse ADMIN_ID into a list of integers
ADMIN_IDS = []
if ADMIN_ID:
    try:
        ADMIN_IDS = [int(i.strip()) for i in ADMIN_ID.split(",") if i.strip()]
    except ValueError:
        logger.warning("ADMIN_ID contains invalid integers. Falling back to empty list.")
