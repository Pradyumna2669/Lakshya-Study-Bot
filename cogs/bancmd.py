import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import logging
from datetime import datetime
from typing import Optional
from cogs.utils import log_to_dev_channel

logger = logging.getLogger('discord.cog')

class Database:
    def __init__(self):
        self.pool = None

    async def initialize(self):
        self.pool = await aiosqlite.connect('data/modlog.db')
        await self._create_tables()

    async def _create_tables(self):
        async with self.pool.execute('''CREATE TABLE IF NOT EXISTS guild_modlog 
                                      (guild_id INTEGER PRIMARY KEY, 
                                       channel_id INTEGER,
                                       ping_role_id INTEGER)'''):
            pass
        
        async with self.pool.execute('''CREATE TABLE IF NOT EXISTS blacklist 
                                      (user_id INTEGER PRIMARY KEY)'''):
            pass
        
        async with self.pool.execute('''CREATE TABLE IF NOT EXISTS pending_appeals 
                                      (user_id INTEGER,
                                       guild_id INTEGER,
                                       PRIMARY KEY (user_id, guild_id))'''):
            pass
        
        async with self.pool.execute('''CREATE TABLE IF NOT EXISTS unban_questions 
                                      (guild_id INTEGER,
                                       question TEXT,
                                       position INTEGER,
                                       PRIMARY KEY (guild_id, position))'''):
            pass
        
        await self.pool.commit()

    async def execute(self, query, params=()):
        async with self.pool.execute(query, params) as cursor:
            await self.pool.commit()
            return cursor

    async def fetch(self, query, params=()):
        async with self.pool.execute(query, params) as cursor:
            return await cursor.fetchall()

    async def close(self):
        await self.pool.close()

class UnbanRequestView(discord.ui.View):
    def __init__(self, guild_id: int = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(
        label="Request Unban",
        style=discord.ButtonStyle.red,
        custom_id="unban_request:button"
    )
    async def request_unban(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.guild_id:
            return await interaction.response.send_message(
                "❌ Server information not found. Please contact staff directly.",
                ephemeral=True
            )
        
        user = interaction.user
        db = interaction.client.db

        # Check blacklist
        if await db.fetch('SELECT 1 FROM blacklist WHERE user_id = ?', (user.id,)):
            return await interaction.response.send_message(
                "❌ You are not allowed to submit another appeal.",
                ephemeral=True
            )
        
        # Check pending appeals
        if await db.fetch('SELECT 1 FROM pending_appeals WHERE user_id = ? AND guild_id = ?',
                         (user.id, self.guild_id)):
            return await interaction.response.send_message(
                "❌ You already have a pending appeal. Please wait for a response.",
                ephemeral=True
            )
        
        # Get questions
        questions = await db.fetch(
            'SELECT question FROM unban_questions WHERE guild_id = ? ORDER BY position',
            (self.guild_id,)
        )
        questions = [q[0] for q in questions]

        if not questions:
            return await interaction.response.send_message(
                "❌ This server hasn't configured unban questions yet.",
                ephemeral=True
            )

        await interaction.response.send_modal(UnbanRequestModal(self.guild_id, questions))

class UnbanRequestModal(discord.ui.Modal):
    def __init__(self, guild_id: int, questions: list):
        super().__init__(title="Unban Request Form", timeout=600)
        self.guild_id = guild_id
        self.questions = questions

        for idx, question in enumerate(questions):
            self.add_item(discord.ui.TextInput(
                label=question,
                style=discord.TextStyle.paragraph,
                required=idx < 2
            ))

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.client.get_guild(self.guild_id)
        user = interaction.user
        db = interaction.client.db

        try:
            # Add to pending appeals
            await db.execute(
                'INSERT OR IGNORE INTO pending_appeals (user_id, guild_id) VALUES (?, ?)',
                (user.id, self.guild_id)
            )

            # Get modlog config
            modlog = await db.fetch(
                'SELECT channel_id, ping_role_id FROM guild_modlog WHERE guild_id = ?',
                (self.guild_id,)
            )
            
            if not modlog or not modlog[0][0]:
                return await interaction.response.send_message(
                    "❌ Modlog channel not configured. Please contact server staff.",
                    ephemeral=True
                )
            
            channel_id, ping_role_id = modlog[0]
            channel = guild.get_channel(channel_id)
            
            if not channel:
                return await interaction.response.send_message(
                    "❌ Modlog channel not found. Please contact server staff.",
                    ephemeral=True
                )

            # Create mention string
            mention_str = ""
            if ping_role_id:
                role = guild.get_role(ping_role_id)
                if role:
                    mention_str = role.mention
            else:
                admins = [m.mention for m in guild.members if m.guild_permissions.administrator]
                mention_str = " ".join(admins) if admins else "@here"

            # Create embed
            embed = discord.Embed(
                title=f"Unban Request from {user} ({user.id})",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            embed.set_thumbnail(url=user.display_avatar.url)
            
            for q, a in zip(self.questions, [item.value for item in self.children]):
                embed.add_field(name=q, value=a or "N/A", inline=False)

            # Create approval view
            view = discord.ui.View()
            
            async def approve_callback(btn_interaction: discord.Interaction):
                if not btn_interaction.user.guild_permissions.administrator:
                    return await btn_interaction.response.send_message(
                        "❌ You need administrator permissions to do this.",
                        ephemeral=True
                    )
                
                try:
                    async with db.pool.execute(
                        'DELETE FROM pending_appeals WHERE user_id = ? AND guild_id = ?',
                        (user.id, guild.id)
                    ):
                        pass
                    
                    async with db.pool.execute(
                        'DELETE FROM blacklist WHERE user_id = ?',
                        (user.id,)
                    ):
                        pass
                    
                    await guild.unban(user)
                    invite = await channel.create_invite(max_uses=1, reason=f"Unban approval for {user}")
                    
                    try:
                        await user.send(f"🎉 Your unban request was approved!\nJoin back using: {invite.url}")
                    except discord.Forbidden:
                        pass
                    
                    await btn_interaction.message.edit(view=None)
                    await btn_interaction.response.send_message(f"✅ Successfully unbanned {user}", ephemeral=True)
                
                except Exception as e:
                    logger.error(f"Unban error: {str(e)}")
                    await log_to_dev_channel(
                        self,
                        f"Unban error: {str(e)}",
                        "ERROR"
                    )
                    await btn_interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

            async def reject_callback(btn_interaction: discord.Interaction):
                if not btn_interaction.user.guild_permissions.administrator:
                    return await btn_interaction.response.send_message(
                        "❌ You need administrator permissions to do this.",
                        ephemeral=True
                    )
                
                try:
                    async with db.pool.execute(
                        'DELETE FROM pending_appeals WHERE user_id = ? AND guild_id = ?',
                        (user.id, guild.id)
                    ):
                        pass
                    
                    async with db.pool.execute(
                        'INSERT OR REPLACE INTO blacklist VALUES (?)',
                        (user.id,)
                    ):
                        pass
                    
                    try:
                        await user.send("❌ Your unban request has been denied.")
                    except discord.Forbidden:
                        pass
                    
                    await btn_interaction.message.edit(view=None)
                    await btn_interaction.response.send_message(
                        f"✅ Successfully rejected {user}'s request", 
                        ephemeral=True
                    )
                
                except Exception as e:
                    logger.error(f"Rejection error: {str(e)}")
                    await log_to_dev_channel(
                        self,
                        f"Rejection error: {str(e)}",
                        "ERROR"
                    )
                    await btn_interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

            # Add buttons
            approve_btn = discord.ui.Button(style=discord.ButtonStyle.green, label="Approve")
            reject_btn = discord.ui.Button(style=discord.ButtonStyle.red, label="Reject")
            approve_btn.callback = approve_callback
            reject_btn.callback = reject_callback
            view.add_item(approve_btn)
            view.add_item(reject_btn)

            await channel.send(content=f"{mention_str}\nNew unban request:", embed=embed, view=view)
            await interaction.response.send_message(
                "✅ Your appeal has been submitted to the moderators!",
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Unban request error: {str(e)}")
            await log_to_dev_channel(
                        self,
                        f"Unban request error: {str(e)}",
                        "ERROR"
                    )
            await db.execute(
                'DELETE FROM pending_appeals WHERE user_id = ? AND guild_id = ?',
                (user.id, self.guild_id)
            )
            await interaction.response.send_message(
                "❌ An error occurred. Please try again later.",
                ephemeral=True
            )

class BanAppealCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
        self.bot.add_view(UnbanRequestView())

    async def cog_load(self):
        await self.db.initialize()

    async def cog_unload(self):
        await self.db.close()

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f'Logged in as {self.bot.user} (ID: {self.bot.user.id})')
        logger.info(f'Serving {len(self.bot.guilds)} guilds')

    @app_commands.command(name="setup_modlog", description="Configure modlog channel")
    @app_commands.default_permissions(administrator=True)
    async def setup_modlog(self, interaction: discord.Interaction,
                         channel: discord.TextChannel,
                         ping_role: Optional[discord.Role] = None):
        try:
            await self.db.execute(
                '''INSERT INTO guild_modlog (guild_id, channel_id, ping_role_id)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    ping_role_id = excluded.ping_role_id''',
                (interaction.guild.id, channel.id, ping_role.id if ping_role else None)
            )
            await interaction.response.send_message(
                f"✅ Modlog configured in {channel.mention}" + 
                (f" with ping role {ping_role.mention}" if ping_role else ""),
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Modlog setup error: {str(e)}")
            await log_to_dev_channel(
                        self,
                        f"Modlog setup error: {str(e)}",
                        "ERROR"
                    )
            await interaction.response.send_message("❌ Failed to configure modlog.", ephemeral=True)

    @app_commands.command(name="ban", description="Ban a user from the server")
    @app_commands.default_permissions(ban_members=True)
    async def ban_user(self, interaction: discord.Interaction,
                     user: discord.User,
                     reason: str = "No reason provided"):
        try:
            try:
                await interaction.guild.fetch_ban(user)
                return await interaction.response.send_message("ℹ️ User already banned.", ephemeral=True)
            except discord.NotFound:
                pass

            await interaction.guild.ban(user, reason=f"{interaction.user}: {reason}")
            
            if not await self.db.fetch('SELECT 1 FROM blacklist WHERE user_id = ?', (user.id,)):
                try:
                    embed = discord.Embed(
                        title=f"Banned from {interaction.guild.name}",
                        description="Appeal your ban using the button below:",
                        color=discord.Color.red()
                    )
                    embed.add_field(name="Reason", value=reason)
                    await user.send(embed=embed, view=UnbanRequestView(interaction.guild.id))
                except discord.Forbidden:
                    pass

            await interaction.response.send_message(f"✅ Banned {user.mention}", ephemeral=True)
        
        except Exception as e:
            logger.error(f"Ban error: {str(e)}")
            await log_to_dev_channel(
                        self.bot,
                        f"Ban error: {str(e)}",
                        "ERROR"
                    )
            await interaction.response.send_message(f"❌ Failed to ban: {str(e)}", ephemeral=True)

    @app_commands.command(name="add_unban_question", description="Add a question to the unban form")
    @app_commands.describe(question="The question text", position="Position (1-based, optional)")
    @app_commands.default_permissions(administrator=True)
    async def add_unban_question(self, interaction: discord.Interaction,
                               question: str,
                               position: Optional[int] = None):
        try:
            count = await self.db.fetch(
                'SELECT COUNT(*) FROM unban_questions WHERE guild_id = ?',
                (interaction.guild.id,)
            )
            if count[0][0] >= 5:
                return await interaction.response.send_message("❌ Max 5 questions allowed.", ephemeral=True)

            if not position:
                max_pos = await self.db.fetch(
                    'SELECT MAX(position) FROM unban_questions WHERE guild_id = ?',
                    (interaction.guild.id,)
                )
                position = (max_pos[0][0] or 0) + 1
            else:
                await self.db.execute(
                    'UPDATE unban_questions SET position = position + 1 WHERE guild_id = ? AND position >= ?',
                    (interaction.guild.id, position)
                )

            await self.db.execute(
                'INSERT INTO unban_questions (guild_id, question, position) VALUES (?, ?, ?)',
                (interaction.guild.id, question, position)
            )
            await interaction.response.send_message(f"✅ Added question at position {position}", ephemeral=True)
        
        except Exception as e:
            logger.error(f"Add question error: {str(e)}")
            await log_to_dev_channel(
                        self.bot,
                        f"Add question error: {str(e)}",
                        "ERROR"
                    )
            await interaction.response.send_message("❌ Failed to add question.", ephemeral=True)

    @app_commands.command(name="remove_unban_question", description="Remove a question by position")
    @app_commands.describe(position="The position to remove")
    @app_commands.default_permissions(administrator=True)
    async def remove_unban_question(self, interaction: discord.Interaction, position: int):
        try:
            await self.db.execute(
                'DELETE FROM unban_questions WHERE guild_id = ? AND position = ?',
                (interaction.guild.id, position)
            )
            await self.db.execute(
                'UPDATE unban_questions SET position = position - 1 WHERE guild_id = ? AND position > ?',
                (interaction.guild.id, position)
            )
            await interaction.response.send_message(f"✅ Removed question at position {position}", ephemeral=True)
        except Exception as e:
            logger.error(f"Remove question error: {str(e)}")
            await log_to_dev_channel(
                        self.bot,
                        f"Remove question error: {str(e)}",
                        "ERROR"
                    )
            await interaction.response.send_message("❌ Failed to remove question.", ephemeral=True)

    @app_commands.command(name="list_unban_questions", description="Show current questions")
    @app_commands.default_permissions(administrator=True)
    async def list_unban_questions(self, interaction: discord.Interaction):
        try:
            questions = await self.db.fetch(
                'SELECT question, position FROM unban_questions WHERE guild_id = ? ORDER BY position',
                (interaction.guild.id,)
            )
            
            embed = discord.Embed(
                title="Unban Questions",
                color=discord.Color.blue()
            )
            
            for q, pos in questions:
                embed.add_field(name=f"Position {pos}", value=q, inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"List questions error: {str(e)}")
            await log_to_dev_channel(
                        self.bot,
                        f"List question error: {str(e)}",
                        "ERROR"
                    )
            await interaction.response.send_message("❌ Failed to retrieve questions.", ephemeral=True)

    # @app_commands.command(name="help", description="Show help information")
    # async def help_command(self, interaction: discord.Interaction):
    #     embed = discord.Embed(
    #         title="Ban Appeal Bot Help",
    #         description="Moderation bot with ban appeal system",
    #         color=discord.Color.blue()
    #     )
    #     embed.add_field(
    #         name="Commands",
    #         value=(
    #             "• `/setup_modlog` - Configure modlog channel\n"
    #             "• `/ban` - Ban a user\n"
    #             "• `/add_unban_question` - Add appeal question\n"
    #             "• `/remove_unban_question` - Remove question\n"
    #             "• `/list_unban_questions` - Show questions\n"
    #             "• `/help` - This menu"
    #         )
    #     )
    #     await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        try:
            await self.db.execute(
                'DELETE FROM blacklist WHERE user_id = ?',
                (user.id,)
            )
            await self.db.execute(
                'DELETE FROM pending_appeals WHERE user_id = ? AND guild_id = ?',
                (user.id, guild.id)
            )
            logger.info(f"Cleaned up unban data for {user.id}")
        except Exception as e:
            logger.error(f"Unban cleanup error: {str(e)}")

async def setup(bot):
    await bot.add_cog(BanAppealCog(bot))