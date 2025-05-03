# -*- coding: utf-8 -*-
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta
from core.bot import StoicBot

class ExamCountdown(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot
        self.db = bot.db_handler
        self.last_checked_date = {}
        self.exam_countdown_task.start()

    def cog_unload(self):
        self.exam_countdown_task.cancel()

    @tasks.loop(minutes=1)
    async def exam_countdown_task(self):
        try:
            now = datetime.utcnow().date()
            data = await self.db.get_all_exam_countdowns()

            for item in data:
                guild_id = item["guild_id"]
                exam_name = item["name"]
                exam_date = datetime.strptime(item["date"], "%Y-%m-%d").date()
                channel_id = item["channel_id"]

                # Only update if day has changed
                if self.last_checked_date.get((guild_id, exam_name)) == now:
                    continue
                self.last_checked_date[(guild_id, exam_name)] = now
                await self.bot.log_to_support(f"Updated Exam Countdown.")
                remaining_days = (exam_date - now).days
                try:
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue

                    channel = guild.get_channel(channel_id)
                    if not channel or not isinstance(channel, discord.VoiceChannel):
                        continue

                    if remaining_days > 0:
                        new_name = f"{exam_name}: {remaining_days}d left"
                    elif remaining_days == 0:
                        new_name = f"{exam_name}: Today 🎯"
                    else:
                        new_name = f"{exam_name}: Done ✅"
                        await self.db.remove_exam_countdown(guild_id, exam_name)

                    if channel.name != new_name:
                        await channel.edit(name=new_name)
                except Exception as e:
                    print(f"[Error] Guild {guild_id}, Channel {channel_id}: {e}")

        except Exception as e:
            print(f"[Exam Task Error] {e}")

    @exam_countdown_task.before_loop
    async def before_countdown_loop(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="set_exam_countdown", description="Set an exam countdown in a voice channel")
    @app_commands.describe(
        exam_name="Name of the exam",
        exam_date="Exam date in YYYY-MM-DD format",
        channel="Voice channel to display the countdown"
    )
    async def set_exam_countdown(
        self,
        interaction: discord.Interaction,
        exam_name: str,
        exam_date: str,
        channel: discord.VoiceChannel
    ):
        try:
            user = interaction.user
            
            if not user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "You need administrator permission to use this command.",
                    ephemeral=True
                )
                return
                
            # Validate date
            date_obj = datetime.strptime(exam_date, "%Y-%m-%d").date()
            await self.db.add_exam_countdown(interaction.guild.id, exam_name, exam_date, channel.id)
            await interaction.response.send_message(
                f"✅ Countdown set for **{exam_name}** on `{exam_date}` in **{channel.name}**", ephemeral=True
            )
            mod_embed = discord.Embed(
                title="🔧 VC Countdown Request Issued",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            mod_embed.add_field(name="👤 Issued By", value=interaction.user.mention, inline=True)
            mod_embed.add_field(name=f"Countdown set for **{exam_name}** on `{exam_date}` in **{channel.name}**", inline=True)
            mod_embed.set_footer(
                text="Logged by Lakshya Bot",
                icon_url=self.bot.user.display_avatar.url
            )
            await self.bot.log_to_mod(interaction, embed=mod_embed)
        except ValueError:
            await interaction.response.send_message("❌ Invalid date format! Use YYYY-MM-DD.", ephemeral=True)
        except Exception as e:
            print(f"[Set Exam Countdown Error] {e}")
            await interaction.response.send_message("❌ Failed to set exam countdown.", ephemeral=True)
            await self.bot.log_to_support(f"❌ Failed to set exam countdown.")

    @app_commands.command(name="remove_exam_countdown", description="Remove exam countdown from a voice channel")
    @app_commands.describe(channel="Voice channel to remove countdown from")
    async def remove_exam_countdown(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel
    ):
        try:
            guild_id = interaction.guild.id
            channel_id = channel.id
            user = interaction.user
            
            if not user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "You need administrator permission to use this command.",
                    ephemeral=True
                )
                return

            # Get all exams for this guild
            data = await self.db.get_all_exam_countdowns()
            channel_exams = [
                item for item in data 
                if item["guild_id"] == guild_id 
                and item["channel_id"] == channel_id
            ]
            
            if not channel_exams:
                await interaction.response.send_message(
                    f"❌ No exam countdown found in {channel.mention}.", 
                    ephemeral=True
                )
                return
                
            # Remove all exams associated with this channel
            for exam in channel_exams:
                await self.db.remove_exam_countdown(guild_id, exam["name"])
            
            await interaction.response.send_message(
                f"✅ Removed {len(channel_exams)} countdown(s) from {channel.mention}.", 
                ephemeral=True
            )
            
            # Logging
            mod_embed = discord.Embed(
                title="🔧 VC Countdown(s) Removed",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            mod_embed.add_field(name="👤 Issued By", value=interaction.user.mention)
            mod_embed.add_field(name="Channel", value=channel.mention)
            mod_embed.add_field(
                name="Removed Countdowns", 
                value="\n".join([f"- {exam['name']}" for exam in channel_exams]) or "None",
                inline=False
            )
            mod_embed.set_footer(text="Logged by Lakshya Bot", icon_url=self.bot.user.display_avatar.url)
            await self.bot.log_to_mod(interaction, embed=mod_embed)
            
        except Exception as e:
            print(f"[Remove Exam Countdown Error] {e}")
            await interaction.response.send_message(
                "❌ Failed to remove exam countdown.", 
                ephemeral=True
            )
            await self.bot.log_to_support(
                f"❌ Failed to remove exam countdown from channel {channel_id} in guild {guild_id}."
            )

    @app_commands.command(name="list_examcountdowns", description="List all active exam countdowns in this server")
    async def list_examcountdowns(self, interaction: discord.Interaction):
        try:
            guild_id = interaction.guild.id
            data = await self.db.get_all_exam_countdowns()
            guild_exams = [item for item in data if item["guild_id"] == guild_id]
            
            if not guild_exams:
                await interaction.response.send_message(
                    "ℹ️ No active exam countdowns in this server.", 
                    ephemeral=True
                )
                return
                
            embed = discord.Embed(
                title="📅 Active Exam Countdowns",
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow()
            )
            
            today = datetime.utcnow().date()
            for exam in sorted(guild_exams, key=lambda x: x["date"]):
                exam_date = datetime.strptime(exam["date"], "%Y-%m-%d").date()
                channel = interaction.guild.get_channel(exam["channel_id"])
                remaining_days = (exam_date - today).days
                
                status = (
                    "✅ Completed" if remaining_days < 0 
                    else "🎯 Today" if remaining_days == 0 
                    else f"{remaining_days} days left"
                )
                
                embed.add_field(
                    name=f"**{exam['name']}**",
                    value=(
                        f"📅 Date: {exam['date']}\n"
                        f"📢 Channel: {channel.mention if channel else 'Deleted Channel'}\n"
                        f"⏳ Status: {status}"
                    ),
                    inline=False
                )
                
            embed.set_footer(text=f"Total active countdowns: {len(guild_exams)}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            print(f"[List Exam Countdowns Error] {e}")
            await interaction.response.send_message(
                "❌ Failed to retrieve exam countdowns.", 
                ephemeral=True
            )

async def setup(bot: StoicBot):
    await bot.add_cog(ExamCountdown(bot))
