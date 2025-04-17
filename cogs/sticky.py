import discord
import asyncio
import logging
from discord import ui, app_commands, Embed
from discord.ext import commands
from typing import Optional, Dict, Tuple
from core.bot import StoicBot
from core.database_handler import DatabaseHandler

class StickyModal(ui.Modal, title='Create Sticky Message'):
    def __init__(self, bot: StoicBot, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.db = DatabaseHandler()
        self.bot.loop.create_task(self.initialize_db())

    async def initialize_db(self):
        await self.db.initialize()

    title_input = ui.TextInput(
        label='Sticky Title',
        placeholder='Enter your sticky title here...',
        max_length=256
    )

    description_input = ui.TextInput(
        label='Sticky Content',
        placeholder='Enter your message content here...',
        style=discord.TextStyle.paragraph,
        max_length=4000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            guild_id = interaction.guild_id
            channel_id = interaction.channel.id
            existing = await self.db.get_sticky(guild_id, channel_id)

            # Create embed with proper formatting
            embed = discord.Embed(
                title=str(self.title_input),
                description=str(self.description_input),
                color=discord.Color.blurple()
            )
            embed.set_footer(
                text="Powered by Lakshya - Stay Motivated",
                icon_url=self.bot.user.display_avatar.url
            )

            if existing:
                try:
                    message = await interaction.channel.fetch_message(existing["message_id"])
                    await message.edit(embed=embed)
                    await self.db.update_sticky_message_id(guild_id, channel_id, message.id)
                    response = "✅ Updated existing sticky message!"
                except discord.NotFound:
                    new_message = await interaction.channel.send(embed=embed)
                    await self.db.set_sticky(
                        guild_id, channel_id, channel_id,
                        str(self.title_input), str(self.description_input),
                        new_message.id
                    )
                    response = "✅ Recreated missing sticky message!"
            else:
                new_message = await interaction.channel.send(embed=embed)
                await self.db.set_sticky(
                    guild_id, channel_id, channel_id,
                    str(self.title_input), str(self.description_input),
                    new_message.id
                )
                response = "✅ New sticky message created!"

            await interaction.followup.send(response, ephemeral=True)

        except Exception as e:
            logging.error(f"Sticky modal error: {e}", exc_info=True)
            await interaction.followup.send("❌ Error creating sticky message!", ephemeral=True)

class Sticky(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot
        self.db = DatabaseHandler()
        self.pending_refreshes: Dict[int, Tuple[asyncio.Task, int]] = {}
        self.bot.loop.create_task(self.initialize_db())

    def cog_unload(self):
        """Cancel all pending refresh tasks on cog unload"""
        for channel_id, (task, _) in self.pending_refreshes.items():
            task.cancel()
        self.pending_refreshes.clear()
        if self.db:
            self.bot.loop.create_task(self.db.close())

    async def initialize_db(self):
        await self.db.initialize()

    @commands.Cog.listener()
    async def on_ready(self):
        logging.info("Sticky cog ready!")
        self.bot.log_to_support("Sticky Cog is ready!")

    async def _handle_sticky_refresh(self, channel: discord.TextChannel, sticky: dict):
        """Centralized method to handle sticky refresh logic"""
        channel_id = channel.id
        try:
            if channel_id in self.pending_refreshes:
                existing_task, count = self.pending_refreshes[channel_id]
                new_count = count + 1
                
                if new_count >= 4:  # Threshold reached
                    existing_task.cancel()
                    del self.pending_refreshes[channel_id]
                    await self.refresh_sticky(channel, sticky)
                else:  # Just update count
                    self.pending_refreshes[channel_id] = (existing_task, new_count)
            else:  # First message in new cycle
                task = self.bot.loop.create_task(
                    self._delayed_refresh(channel, sticky, delay=15)
                )
                self.pending_refreshes[channel_id] = (task, 1)
        except Exception as e:
            logging.error(f"Refresh handler error: {e}", exc_info=True)

    async def _delayed_refresh(self, channel: discord.TextChannel, sticky: dict, delay: int):
        """Handle delayed sticky refresh with cleanup"""
        try:
            await asyncio.sleep(delay)
            await self.refresh_sticky(channel, sticky)
        except asyncio.CancelledError:
            pass  # Expected cancellation when threshold reached
        except Exception as e:
            logging.error(f"Delayed refresh error: {e}", exc_info=True)
            self.bot.log_to_support(f"Delayed refresh error: {e}", exc_info=True)
        finally:
            self.pending_refreshes.pop(channel.id, None)

    @app_commands.command(name="sticky", description="Create or edit a sticky message")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def create_sticky(self, interaction: discord.Interaction):
        """Open the sticky message creation modal"""
        await interaction.response.send_modal(StickyModal(self.bot))

    @app_commands.command(name="unsticky", description="Remove sticky from this channel")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def remove_sticky(self, interaction: discord.Interaction):
        """Remove existing sticky message"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            guild_id = interaction.guild_id
            channel_id = interaction.channel.id
            
            if existing := await self.db.get_sticky(guild_id, channel_id):
                try:
                    message = await interaction.channel.fetch_message(existing["message_id"])
                    await message.delete()
                except discord.NotFound:
                    pass
                
                await self.db.remove_sticky(guild_id, channel_id)
                self.pending_refreshes.pop(channel_id, None)  # Cancel any pending refresh

            await interaction.followup.send("✅ Sticky message removed!", ephemeral=True)
        except Exception as e:
            logging.error(f"Unsticky error: {e}", exc_info=True)
            await interaction.followup.send("❌ Error removing sticky!", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle message events for sticky positioning"""
        if message.author.bot or not message.guild:
            return

        try:
            if (sticky := await self.db.get_sticky(message.guild.id, message.channel.id)) \
               and sticky["target_channel_id"] == message.channel.id:
                await self._handle_sticky_refresh(message.channel, sticky)
        except Exception as e:
            logging.error(f"Sticky maintenance error: {e}", exc_info=True)

    async def refresh_sticky(self, channel: discord.TextChannel, sticky: dict):
        """Refresh sticky message position"""
        try:
            # Delete old message if exists
            try:
                old_message = await channel.fetch_message(sticky["message_id"])
                await old_message.delete()
            except (discord.NotFound, discord.HTTPException):
                pass

            # Create new embed
            embed = Embed(
                title=sticky["title"],
                description=sticky["description"],
                color=discord.Color.blurple()
            )
            embed.set_footer(
                text="Powered by Lakshya - Stay Motivated",
                icon_url=self.bot.user.display_avatar.url
            )

            # Send new message and update database
            new_message = await channel.send(embed=embed)
            await self.db.update_sticky_message_id(
                sticky["guild_id"],
                sticky["source_channel_id"],
                new_message.id
            )
        except discord.Forbidden:
            logging.warning(f"Missing permissions in {channel.name}")
        except Exception as e:
            logging.error(f"Sticky refresh failed: {e}", exc_info=True)

    async def close(self):
        """Properly close database connection"""
        if self.conn and not self.conn.is_closed:
            await self.conn.close()
            self.conn = None
            logging.info("Database connection closed")

async def setup(bot: StoicBot):
    await bot.add_cog(Sticky(bot))
    logging.info("Sticky cog loaded!")