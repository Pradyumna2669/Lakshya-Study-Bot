import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
import datetime

# Database setup
conn = sqlite3.connect('bans.db')
c = conn.cursor()

# Create tables
c.execute('''CREATE TABLE IF NOT EXISTS bans
             (user_id INTEGER, guild_id INTEGER,
              mod_id INTEGER, reason TEXT,
              ban_date TIMESTAMP, PRIMARY KEY(user_id, guild_id))''')

c.execute('''CREATE TABLE IF NOT EXISTS appeals
             (appeal_id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER, guild_id INTEGER,
              responses TEXT, status TEXT DEFAULT 'pending',
              mod_id INTEGER, decision_date TIMESTAMP)''')

c.execute('''CREATE TABLE IF NOT EXISTS appeal_forms
             (guild_id INTEGER PRIMARY KEY,
              questions TEXT)''')

conn.commit()

class AppealModal(discord.ui.Modal):
    def __init__(self, questions, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.questions = questions
        self.responses = {}
        
        for i, question in enumerate(questions.split('\n')):
            self.add_item(discord.ui.TextInput(
                label=question[:45],
                style=discord.TextStyle.paragraph,
                custom_id=f"q{i}",
                required=True
            ))

    async def on_submit(self, interaction: discord.Interaction):
        for item in self.children:
            self.responses[item.label] = item.value
        
        # Store appeal in database
        c.execute('''INSERT INTO appeals 
                    (user_id, guild_id, responses)
                    VALUES (?, ?, ?)''',
                 (interaction.user.id, interaction.guild.id, 
                  str(self.responses)))
        conn.commit()
        
        # Send to mod channel
        appeal_id = c.lastrowid
        embed = discord.Embed(
            title=f"New Unban Appeal (ID: {appeal_id})",
            description=f"From: {interaction.user.mention}",
            color=discord.Color.orange()
        )
        
        for q, a in self.responses.items():
            embed.add_field(name=q, value=a, inline=False)
            
        view = AppealDecisionView(appeal_id)
        
        mod_channel = discord.utils.get(interaction.guild.text_channels, name="mod-log")
        if mod_channel:
            await mod_channel.send(embed=embed, view=view)
        
        await interaction.response.send_message(
            "✅ Your appeal has been submitted!", 
            ephemeral=True
        )

class AppealDecisionView(discord.ui.View):
    def __init__(self, appeal_id):
        super().__init__(timeout=None)
        self.appeal_id = appeal_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="approve_appeal")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Update database
        c.execute('''UPDATE appeals SET 
                    status='approved', mod_id=?, decision_date=?
                    WHERE appeal_id=?''',
                 (interaction.user.id, datetime.datetime.now(), self.appeal_id))
        
        # Get user info
        c.execute('SELECT user_id, guild_id FROM appeals WHERE appeal_id=?', (self.appeal_id,))
        user_id, guild_id = c.fetchone()
        
        # Remove ban
        c.execute('DELETE FROM bans WHERE user_id=? AND guild_id=?', (user_id, guild_id))
        conn.commit()
        
        # Unban user
        guild = interaction.guild
        user = await guild.fetch_ban(discord.Object(id=user_id))
        await guild.unban(user.user)
        
        # Update embed
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Decision", value=f"Approved by {interaction.user.mention}")
        await interaction.message.edit(embed=embed, view=None)
        
        await interaction.response.send_message("Appeal approved!", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, custom_id="deny_appeal")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Update database
        c.execute('''UPDATE appeals SET 
                    status='denied', mod_id=?, decision_date=?
                    WHERE appeal_id=?''',
                 (interaction.user.id, datetime.datetime.now(), self.appeal_id))
        conn.commit()
        
        # Update embed
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="Decision", value=f"Denied by {interaction.user.mention}")
        await interaction.message.edit(embed=embed, view=None)
        
        await interaction.response.send_message("Appeal denied!", ephemeral=True)

class BanSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.persistent_views_added = False

    async def cog_load(self):
        if not self.persistent_views_added:
            self.bot.add_view(AppealDecisionView(0))  # Dummy view for persistence
            self.persistent_views_added = True

    @app_commands.command(name="ban", description="Ban a user from the server")
    @app_commands.default_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, user: discord.User, reason: str = "No reason provided"):
        try:
            # Ban the user
            await interaction.guild.ban(user, reason=reason)
            
            # Create ban message with appeal button
            embed = discord.Embed(
                title="You've been banned",
                description=f"**Reason:** {reason}\n\nYou can appeal this ban by clicking the button below.",
                color=discord.Color.red()
            )
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Appeal Ban",
                style=discord.ButtonStyle.blurple,
                custom_id=f"appeal_ban:{interaction.guild.id}"
            ))
            
            # Send DM to banned user
            try:
                await user.send(embed=embed, view=view)
            except discord.Forbidden:
                pass  # User has DMs disabled
            
            # Store ban in database
            c.execute('''INSERT INTO bans 
                        (user_id, guild_id, mod_id, reason, ban_date)
                        VALUES (?, ?, ?, ?, ?)''',
                     (user.id, interaction.guild.id, interaction.user.id, 
                      reason, datetime.datetime.now()))
            conn.commit()
            
            await interaction.response.send_message(
                f"✅ {user.mention} has been banned", 
                ephemeral=True
            )

        except Exception as e:
            await interaction.response.send_message(
                f"Error: {str(e)}", 
                ephemeral=True
            )

    @app_commands.command(name="set_appeal_questions", description="Set questions for the unban appeal form")
    @app_commands.default_permissions(administrator=True)
    async def set_appeal_questions(self, interaction: discord.Interaction):
        modal = discord.ui.Modal(title="Configure Appeal Questions")
        modal.add_item(discord.ui.TextInput(
            label="Questions (one per line)",
            style=discord.TextStyle.paragraph,
            default="Why should you be unbanned?\nWhat will you do differently?",
            required=True,
            custom_id="questions"
        ))
        
        async def on_submit(interaction: discord.Interaction):
            questions = modal.children[0].value
            c.execute('''INSERT OR REPLACE INTO appeal_forms
                        (guild_id, questions) VALUES (?, ?)''',
                     (interaction.guild.id, questions))
            conn.commit()
            await interaction.response.send_message(
                "✅ Appeal questions updated!", 
                ephemeral=True
            )
        
        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type == discord.InteractionType.component:
            if interaction.data['custom_id'].startswith("appeal_ban"):
                guild_id = int(interaction.data['custom_id'].split(":")[1])
                
                # Check if user is banned
                c.execute('SELECT * FROM bans WHERE user_id=? AND guild_id=?',
                         (interaction.user.id, guild_id))
                if not c.fetchone():
                    await interaction.response.send_message(
                        "You are not banned from this server.", 
                        ephemeral=True
                    )
                    return
                
                # Check existing appeals
                c.execute('''SELECT status FROM appeals 
                            WHERE user_id=? AND guild_id=? 
                            ORDER BY appeal_id DESC''',
                         (interaction.user.id, guild_id))
                appeal = c.fetchone()
                if appeal and appeal[0] in ['pending', 'approved']:
                    await interaction.response.send_message(
                        "You already have an active appeal!", 
                        ephemeral=True
                    )
                    return
                
                # Get questions
                c.execute('SELECT questions FROM appeal_forms WHERE guild_id=?', (guild_id,))
                questions = c.fetchone()
                if not questions:
                    await interaction.response.send_message(
                        "This server hasn't configured appeal questions yet.", 
                        ephemeral=True
                    )
                    return
                
                # Show appeal form
                modal = AppealModal(
                    questions=questions[0],
                    title="Unban Appeal Form"
                )
                await interaction.response.send_modal(modal)

async def setup(bot):
    await bot.add_cog(BanSystem(bot))