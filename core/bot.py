import discord
from discord import Embed
import os
import logging
import traceback
import asyncio
from typing import Union
from discord.ext import commands
from core.database_handler import DatabaseHandler
from core.ticket_db import setup_database
from cogs.utils import log_to_dev_channel

logger = logging.getLogger(__name__)

class StoicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.voice_states = True
        intents.guilds = True
        self.dev_log_channel_id = 1358417132970442823

        super().__init__(
            command_prefix=commands.when_mentioned_or('!'),
            intents=intents,
            help_command=None,
            chunk_guilds_at_startup=False
        )

        setup_database()

        self.db_handler = None

        self.nsfw_keywords = [
            "cock", "deepthroat", "dick", "cumshot",
            "fuck", "sperm", "porn", "nude", "sex"
            # ... Add more if needed
        ]

        self.support_guild_id = int(os.getenv("SUPPORT_SERVER_ID", 0))
        self.log_channel_id = int(os.getenv("LOG_CHANNEL_ID", 0))
        self._log_queue = asyncio.Queue()
        self._log_worker = None  # We'll create this later inside setup_hook()

    async def setup_hook(self):
        try:
            self.db_handler = DatabaseHandler()
            await self.db_handler.initialize()
            await self.db_handler.connect()
        
            await self.load_extensions()
            await self.tree.sync()

            self._log_worker = asyncio.create_task(self._log_worker_loop())  # 👈 moved here

            logger.info("Bot setup completed successfully")
        except Exception as e:
            logger.critical(f"Failed to initialize bot: {str(e)}", exc_info=True)
            raise

    async def load_extensions(self):
        cogs = [
            'cogs.moderation',
            'cogs.tickets',
            'cogs.voice',
            'cogs.events',
            'cogs.utilities',
            'cogs.management',
            'cogs.voicelogs',
            'cogs.examcountdown',
            'cogs.study_session',
            'cogs.sticky',
            'cogs.content_moderation'
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"Successfully loaded cog: {cog}")
            except Exception as e:
                logger.error(f"Failed to load cog {cog}: {str(e)}", exc_info=True)

    async def log_to_support(self, content: Union[str, Embed]):
        await self._log_queue.put(content)

    async def _log_worker_loop(self):
        while True:
            try:
                content = await self._log_queue.get()
                await self._send_log(content)
                await asyncio.sleep(1.1)  # Roughly 1 message per second to be safe
            except Exception as e:
                print(f"[LogWorkerError] {e}")

    async def _send_log(self, content: Union[str, Embed]):
        channel = self.get_channel(self.dev_log_channel_id)
        if not channel:
            print("[LogWorker] Could not find log channel.")
            return

        try:
            if isinstance(content, str):
                if len(content) > 1900:
                    for i in range(0, len(content), 1900):
                        await channel.send(f"```\n{content[i:i+1900]}\n```")
                        await asyncio.sleep(1.1)
                else:
                    await channel.send(f"```\n{content}\n```")
            elif isinstance(content, Embed):
                await channel.send(embed=content)
            else:
                await channel.send("Unsupported log content format.")
        except Exception as e:
            print(f"[LogSendError] Failed to send log: {e}")

    async def log_to_mod(self, interaction: discord.Interaction, *, message: str = None, embed: discord.Embed = None):
        """Handle both text and embed logging"""
        try:
            config = await self.db_handler.get_server_config(interaction.guild.id)
            if not (log_channel := config.get('log_channel_id')):
                return
                
            channel = self.get_channel(log_channel)
            if not channel:
                return
                
            if embed:
                await channel.send(embed=embed)
            elif message:
                await channel.send(message)
                
        except Exception as e:
            logging.error(f"Mod log failed: {e}")

    async def on_error(self, event_method: str, *args, **kwargs):
        logger.error(f"Unhandled exception in {event_method}", exc_info=True)

        embed = discord.Embed(
            title="⚠️ Bot Error",
            color=0xff0000,
            description=f"```python\n{traceback.format_exc()[:2000]}```"
        )
        embed.add_field(name="Event", value=event_method)
        await self.log_to_support(embed)
    
    async def on_ready(self):
        print(f"[INFO] Bot is online as {self.user} (ID: {self.user.id})")
        await log_to_dev_channel(
                self,
                f"Bot is online as {self.user} (ID: {self.user.id})",
                "INFO"
            )
        try:
            await self.log_to_support(
                f"✅ **Lakshya Bot is now online!**\n"
                f"**Username:** {self.user}\n"
                f"**ID:** {self.user.id}\n"
                f"**Connected to:** {len(self.guilds)} servers"
            )
        except Exception as e:
            print(f"[ERROR] Failed to log startup message: {e}")
            self.log_to_support(f"[ERROR] Failed to log startup message: {e}")

    async def close(self):
        await self.db_handler.close()
        await super().close()
        logger.info("Bot shutdown completed")

# Bot instance
bot = StoicBot()
