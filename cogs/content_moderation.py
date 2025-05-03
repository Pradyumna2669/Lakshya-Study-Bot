"""
Content Moderation Cog for Discord Bot
Handles automatic detection and moderation of NSFW and promotional content in messages
"""
import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import os
import logging
import datetime
import re
from cogs.utils import log_to_dev_channel # Added import for logging function

class ContentModeration(commands.Cog):
    """
    A cog for moderating content in Discord servers.
    Monitors messages for NSFW and promotional content and takes appropriate action.
    """

    def __init__(self, bot):
        self.bot = bot
        self.db_path = 'data/moderation.db'
        self.logger = logging.getLogger('content_moderation')
        self.logger.setLevel(logging.INFO)

        # Default promotional keywords - these will be checked in all servers
        self.promotional_keywords = [
            "discord.gg/", "discord.com/invite/", "discordapp.com/invite/",
            "buy now", "join my server", "click here", "check out my",
            "follow me", "subscribe to", "check my profile", "dm me for", 
            "selling", "promotion", "advertise", "advertisement", 
            "special offer", "limited time", "discount code"
        ]

        # Ensure database directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # Setup task to initialize database
        self.bot.loop.create_task(self.setup_database())

    async def setup_database(self):
        """Initialize the database with necessary tables if they don't exist"""
        async with aiosqlite.connect(self.db_path) as db:
            # Table for storing NSFW keywords per guild
            await db.execute('''
                CREATE TABLE IF NOT EXISTS nsfw_keywords (
                    guild_id INTEGER,
                    keyword TEXT,
                    added_by INTEGER,
                    added_at TIMESTAMP,
                    PRIMARY KEY (guild_id, keyword)
                )
            ''')

            # Table for storing content moderation settings per guild
            await db.execute('''
                CREATE TABLE IF NOT EXISTS moderation_settings (
                    guild_id INTEGER PRIMARY KEY,
                    nsfw_detection_enabled BOOLEAN DEFAULT 1,
                    promotion_detection_enabled BOOLEAN DEFAULT 1,
                    last_updated TIMESTAMP,
                    updated_by INTEGER
                )
            ''')
            await db.commit()

    async def get_server_config(self, guild_id):
        """Fetch server configuration from the database"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # First check if table exists
            cursor = await db.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='server_config'
            """)
            if not await cursor.fetchone():
                return None

            cursor = await db.execute(
                "SELECT * FROM server_config WHERE guild_id = ?",
                (guild_id,)
            )
            config = await cursor.fetchone()

            if not config:
                self.logger.warning(f"Server {guild_id} not configured. Setup required.")
                return None

            return config

    async def is_moderation_enabled(self, guild_id):
        """Check if content moderation is enabled for a guild"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT nsfw_detection_enabled, promotion_detection_enabled FROM moderation_settings WHERE guild_id = ?",
                (guild_id,)
            )
            settings = await cursor.fetchone()

            if not settings:
                # Default to enabled if no settings found
                return True, True

            return settings['nsfw_detection_enabled'], settings['promotion_detection_enabled']

    async def get_nsfw_keywords(self, guild_id):
        """Get the list of NSFW keywords for a guild"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT keyword FROM nsfw_keywords WHERE guild_id = ?",
                (guild_id,)
            )
            keywords = await cursor.fetchall()
            return [row[0] for row in keywords]

    async def is_authorized(self, member, guild_id):
        """Check if a member has the required roles to use moderation commands"""
        if member.guild_permissions.administrator:
            return True

        try:
            config = await self.get_server_config(guild_id)
            required_roles = [config['mod_role_id'], config['admin_role_id'], config['elder_role_id']]

            # Filter out None values
            required_roles = [role_id for role_id in required_roles if role_id]

            if not required_roles:  # If no roles are configured
                return member.guild_permissions.manage_messages

            return any(role.id in required_roles for role in member.roles if role)
        except Exception as e:
            self.logger.error(f"Error checking authorization: {e}")
            # Default to checking manage_messages permission if there's an error
            return member.guild_permissions.manage_messages

    @commands.Cog.listener()
    async def on_message(self, message):
        """Monitor messages for NSFW or promotional content"""
        # Ignore DMs, bot messages, and empty messages
        if not message.guild or message.author.bot or not message.content:
            return

        # Get moderation settings
        nsfw_enabled, promo_enabled = await self.is_moderation_enabled(message.guild.id)

        if not nsfw_enabled and not promo_enabled:
            return  # Moderation is completely disabled

        content = message.content.lower()
        violation = None

        # Check for NSFW content
        if nsfw_enabled:
            nsfw_keywords = await self.get_nsfw_keywords(message.guild.id)
            # Split message into words and check each word
            message_words = set(word.lower() for word in re.findall(r'\b\w+\b', content))
            
            # Check if any NSFW keyword matches exactly with any word
            if any(keyword.lower() in message_words for keyword in nsfw_keywords):
                violation = "NSFW content"

        # Check for promotional content
        if not violation and promo_enabled:
            if any(keyword.lower() in content for keyword in self.promotional_keywords):
                violation = "Promotional content"

        # If violation found, take action
        if violation:
            await self.handle_violation(message, violation)

    async def handle_violation(self, message, violation_type):
        """Handle content violations by muting the member and logging the action"""
        try:
            # Get server configuration
            config = await self.get_server_config(message.guild.id)
            if not config:
                await message.channel.send("⚠️ Bot not configured! Please ask an administrator to run the `/setup` command.")
                return
            mute_role_id = config['mute_role_id'] if config else None

            if not mute_role_id:
                self.logger.warning(f"No mute role configured for guild {message.guild.id}")
                return

            mute_role = message.guild.get_role(mute_role_id)
            if not mute_role:
                self.logger.warning(f"Configured mute role {mute_role_id} not found in guild {message.guild.id}")
                return

            # Apply mute role to the member
            await message.author.add_roles(mute_role, reason=f"Auto-moderation: {violation_type}")

            # Create embed for logging
            embed = discord.Embed(
                title=f"Auto-Moderation Action: {violation_type}",
                description=f"User muted for posting {violation_type.lower()}",
                color=discord.Color.red(),
                timestamp=datetime.datetime.utcnow()
            )

            embed.add_field(name="User", value=f"{message.author.mention} ({message.author.id})", inline=False)
            embed.add_field(name="Channel", value=message.channel.mention, inline=False)
            embed.add_field(name="Message", value=f"```{message.content[:1000]}```", inline=False)

            # Get server config for log channel
            config = await self.get_server_config(message.guild.id)
            if config and config['log_channel_id']:
                log_channel = message.guild.get_channel(config['log_channel_id'])
                if log_channel:
                    await log_channel.send(embed=embed)

            # Delete the message
            await message.delete()

            # Notify user (via DM if possible)
            try:
                dm_embed = discord.Embed(
                    title="🚫 Moderation Action",
                    description=f"You have been muted in **{message.guild.name}**",
                    color=discord.Color.red(),
                    timestamp=datetime.datetime.utcnow()
                )
                dm_embed.add_field(name="Reason", value=f"Posting {violation_type.lower()}", inline=False)
                dm_embed.add_field(
                    name="What to do next", 
                    value="1. Review the server rules\n2. Contact a moderator if you believe this was a mistake", 
                    inline=False
                )
                dm_embed.set_footer(text=f"Server: {message.guild.name}")
                
                await message.author.send(embed=dm_embed)
            except discord.Forbidden:
                # Can't DM the user, send in channel instead
                temp_msg = await message.channel.send(
                    f"{message.author.mention} You have been muted for posting {violation_type.lower()}."
                )
                # Delete the notification after a few seconds
                await temp_msg.delete(delay=10)

        except Exception as e:
            error_msg = f"Error handling violation: {e}"
            self.logger.error(error_msg)
            await log_to_dev_channel(self.bot, error_msg, "ERROR")

    @app_commands.command(name="add_nsfw_word", description="Add a new NSFW keyword to the filter")
    @app_commands.describe(
        keyword="The keyword or phrase to add to the NSFW filter"
    )
    async def add_nsfw_word(self, interaction: discord.Interaction, keyword: str):
        """Add a new NSFW keyword to the filter for this server"""
        # Check permissions
        if not await self.is_authorized(interaction.user, interaction.guild_id):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        # Validate keyword
        if not keyword or len(keyword) < 2:
            await interaction.response.send_message("Keyword must be at least 2 characters long.", ephemeral=True)
            return

        # Add to database
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO nsfw_keywords (guild_id, keyword, added_by, added_at) VALUES (?, ?, ?, ?)",
                    (interaction.guild_id, keyword.lower(), interaction.user.id, datetime.datetime.utcnow())
                )
                await db.commit()

            await interaction.response.send_message(f"Added `{keyword}` to the NSFW filter.", ephemeral=True)

            # Log action
            embed = discord.Embed(
                title="NSFW Filter Updated",
                description=f"Keyword added to the NSFW filter",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.utcnow()
            )
            embed.add_field(name="Keyword", value=f"`{keyword}`", inline=False)
            embed.add_field(name="Added by", value=f"{interaction.user.mention} ({interaction.user.id})", inline=False)

            # Get server config for log channel
            config = await self.get_server_config(interaction.guild_id)
            if config and config['log_channel_id']:
                log_channel = interaction.guild.get_channel(config['log_channel_id'])
                if log_channel:
                    await log_channel.send(embed=embed)

        except Exception as e:
            error_msg = f"Error adding NSFW keyword: {e}"
            self.logger.error(error_msg)
            await log_to_dev_channel(self.bot, error_msg, "ERROR")
            await interaction.response.send_message("An error occurred while adding the keyword.", ephemeral=True)

    @app_commands.command(name="remove_nsfw_word", description="Remove a keyword from the NSFW filter")
    @app_commands.describe(
        keyword="The keyword or phrase to remove from the NSFW filter"
    )
    async def remove_nsfw_word(self, interaction: discord.Interaction, keyword: str):
        """Remove a keyword from the NSFW filter for this server"""
        # Check permissions
        if not await self.is_authorized(interaction.user, interaction.guild_id):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        # Remove from database
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "DELETE FROM nsfw_keywords WHERE guild_id = ? AND LOWER(keyword) = LOWER(?)",
                    (interaction.guild_id, keyword.lower())
                )

                if cursor.rowcount == 0:
                    await interaction.response.send_message(
                        f"Keyword `{keyword}` was not found in the NSFW filter.", 
                        ephemeral=True
                    )
                    return

                await db.commit()

            await interaction.response.send_message(f"Removed `{keyword}` from the NSFW filter.", ephemeral=True)

            # Log action
            embed = discord.Embed(
                title="NSFW Filter Updated",
                description=f"Keyword removed from the NSFW filter",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.utcnow()
            )
            embed.add_field(name="Keyword", value=f"`{keyword}`", inline=False)
            embed.add_field(name="Removed by", value=f"{interaction.user.mention} ({interaction.user.id})", inline=False)

            # Get server config for log channel
            config = await self.bot.db.get_server_config(interaction.guild_id)
            if config and config['log_channel_id']:
                log_channel = interaction.guild.get_channel(config['log_channel_id'])
                if log_channel:
                    await log_channel.send(embed=embed)

        except Exception as e:
            error_msg = f"Error handling violation: {e}"
            self.logger.error(error_msg)
            await log_to_dev_channel(self.bot, error_msg, "ERROR")
            await interaction.response.send_message("An error occurred while removing the keyword.", ephemeral=True)

    @app_commands.command(name="list_nsfw_words", description="List all NSFW keywords for this server")
    async def list_nsfw_words(self, interaction: discord.Interaction):
        """List all NSFW keywords configured for this server"""
        # Check permissions
        if not await self.is_authorized(interaction.user, interaction.guild_id):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        try:
            keywords = await self.get_nsfw_keywords(interaction.guild_id)

            if not keywords:
                await interaction.response.send_message("No NSFW keywords have been configured for this server.", ephemeral=True)
                return

            # Format the list of keywords
            formatted_keywords = "\n".join(f"• {keyword}" for keyword in keywords)

            # Split into chunks if the list is too long
            if len(formatted_keywords) <= 4000:
                embed = discord.Embed(
                    title="NSFW Filter Keywords",
                    description=formatted_keywords,
                    color=discord.Color.blue()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                # Handle very long lists by sending multiple embeds
                chunks = [formatted_keywords[i:i+4000] for i in range(0, len(formatted_keywords), 4000)]

                await interaction.response.send_message("Sending NSFW keyword list (multiple messages)...", ephemeral=True)

                for i, chunk in enumerate(chunks):
                    embed = discord.Embed(
                        title=f"NSFW Filter Keywords (Part {i+1}/{len(chunks)})",
                        description=chunk,
                        color=discord.Color.blue()
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            self.logger.error(f"Error listing NSFW keywords: {e}")
            await interaction.response.send_message("An error occurred while fetching the keywords.", ephemeral=True)

    @app_commands.command(name="toggle_content_moderation", description="Toggle content moderation features")
    @app_commands.describe(
        nsfw_detection="Enable/disable NSFW content detection",
        promotion_detection="Enable/disable promotional content detection"
    )
    async def toggle_content_moderation(
        self, 
        interaction: discord.Interaction, 
        nsfw_detection: bool = None, 
        promotion_detection: bool = None
    ):
        """Toggle content moderation features on or off"""
        # Check permissions
        if not await self.is_authorized(interaction.user, interaction.guild_id):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        if nsfw_detection is None and promotion_detection is None:
            await interaction.response.send_message(
                "You need to specify at least one moderation feature to toggle.", 
                ephemeral=True
            )
            return

        try:
            # Get current settings
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT nsfw_detection_enabled, promotion_detection_enabled FROM moderation_settings WHERE guild_id = ?",
                    (interaction.guild_id,)
                )
                current_settings = await cursor.fetchone()

                if not current_settings:
                    # Default settings if none exist
                    current_nsfw = True
                    current_promo = True
                else:
                    current_nsfw = bool(current_settings['nsfw_detection_enabled'])
                    current_promo = bool(current_settings['promotion_detection_enabled'])

                # Update with new values if provided
                new_nsfw = nsfw_detection if nsfw_detection is not None else current_nsfw
                new_promo = promotion_detection if promotion_detection is not None else current_promo

                # Update database
                await db.execute(
                    """
                    INSERT INTO moderation_settings 
                    (guild_id, nsfw_detection_enabled, promotion_detection_enabled, last_updated, updated_by)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET
                    nsfw_detection_enabled = ?,
                    promotion_detection_enabled = ?,
                    last_updated = ?,
                    updated_by = ?
                    """,
                    (
                        interaction.guild_id, new_nsfw, new_promo, datetime.datetime.utcnow(), interaction.user.id,
                        new_nsfw, new_promo, datetime.datetime.utcnow(), interaction.user.id
                    )
                )
                await db.commit()

            # Prepare response message
            changes = []
            if nsfw_detection is not None:
                changes.append(f"NSFW detection: {'Enabled' if new_nsfw else 'Disabled'}")
            if promotion_detection is not None:
                changes.append(f"Promotion detection: {'Enabled' if new_promo else 'Disabled'}")

            status_message = "\n".join(changes)

            await interaction.response.send_message(
                f"Content moderation settings updated:\n{status_message}", 
                ephemeral=True
            )

            # Log action
            embed = discord.Embed(
                title="Content Moderation Settings Updated",
                description=status_message,
                color=discord.Color.blue(),
                timestamp=datetime.datetime.utcnow()
            )
            embed.add_field(name="Updated by", value=f"{interaction.user.mention} ({interaction.user.id})", inline=False)

            # Get server config for log channel
            config = await self.bot.db.get_server_config(interaction.guild_id)
            if config and config['log_channel_id']:
                log_channel = interaction.guild.get_channel(config['log_channel_id'])
                if log_channel:
                    await log_channel.send(embed=embed)

        except Exception as e:
            error_msg = f"Error toggling content moderation: {e}"
            self.logger.error(error_msg)
            await log_to_dev_channel(self.bot, error_msg, "ERROR")
            await interaction.response.send_message("An error occurred while updating the settings.", ephemeral=True)

    @app_commands.command(name="moderation_status", description="Check current content moderation settings")
    async def moderation_status(self, interaction: discord.Interaction):
        """Display current content moderation settings for this server"""
        # Check permissions
        if not await self.is_authorized(interaction.user, interaction.guild_id):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        try:
            # Get current settings
            nsfw_enabled, promo_enabled = await self.is_moderation_enabled(interaction.guild_id)
            keywords_count = len(await self.get_nsfw_keywords(interaction.guild_id))

            embed = discord.Embed(
                title="Content Moderation Status",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.utcnow()
            )

            embed.add_field(
                name="NSFW Detection", 
                value=f"{'🟢 Enabled' if nsfw_enabled else '🔴 Disabled'}", 
                inline=False
            )
            embed.add_field(
                name="Promotion Detection", 
                value=f"{'🟢 Enabled' if promo_enabled else '🔴 Disabled'}", 
                inline=False
            )
            embed.add_field(
                name="NSFW Keywords Count", 
                value=f"{keywords_count} words configured", 
                inline=False
            )
            embed.add_field(
                name="Promotion Keywords Count", 
                value=f"{len(self.promotional_keywords)} default keywords", 
                inline=False
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            self.logger.error(f"Error fetching moderation status: {e}")
            await interaction.response.send_message("An error occurred while fetching the status.", ephemeral=True)

async def setup(bot):
    """Setup function to add the cog to the bot"""
    await bot.add_cog(ContentModeration(bot))