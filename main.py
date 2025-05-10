import asyncio
import uvicorn
import logging
import os
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Create media directory
media_dir = Path(__file__).resolve().parent / "media"
media_dir.mkdir(parents=True, exist_ok=True)

async def start_api():
    """Start the FastAPI server."""
    config = uvicorn.Config(
        "app.api.main:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
    )
    server = uvicorn.Server(config)
    await server.serve()

async def start_bot():
    """Start the Telegram bot."""
    from app.bot.main import main as bot_main
    await bot_main()

async def main():
    """Start all components."""
    # Start API and bot concurrently
    await asyncio.gather(
        start_api(),
        start_bot(),
    )

if __name__ == "__main__":
    asyncio.run(main())
