from dotenv import load_dotenv
load_dotenv()

import os
import logging
import traceback
from core.bot import bot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

@bot.event
async def on_ready():
    """Improved ready handler"""
    logging.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logging.info(f"Guild Count: {len(bot.guilds)}")
    logging.info(f"Latency: {round(bot.latency * 1000)}ms")

if __name__ == "__main__":
    try:
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ValueError("Missing DISCORD_TOKEN in environment")
            
        bot.run(token)
    except Exception as e:
        logging.critical(f"Failed to start bot: {str(e)}", exc_info=True)
        raise