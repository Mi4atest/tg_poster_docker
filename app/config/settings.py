import os
from typing import List
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from .env file
load_dotenv()

# Base directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Telegram Bot settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [int(user_id) for user_id in os.getenv("ALLOWED_USER_IDS", "").split(",") if user_id]

# VK API settings
VK_APP_ID = os.getenv("VK_APP_ID")
VK_APP_SECRET = os.getenv("VK_APP_SECRET")
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN")
VK_GROUP_ID = os.getenv("VK_GROUP_ID")

# Telegram Channel settings
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Database settings
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

# Security settings
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# API settings
API_HOST = os.getenv("API_HOST", "localhost")
API_PORT = int(os.getenv("API_PORT", "8002"))

# Media storage settings
MEDIA_DIR = BASE_DIR / "media"
MEDIA_STRUCTURE = "{year}/{month}/{day}/{post_name}"

# Ensure media directory exists
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
