import discord
from discord.ext import commands, tasks
from discord import app_commands
from core.bot import StoicBot
import logging
from datetime import datetime, timezone
import asyncio

logger = logging.getLogger(__name__)

class Events(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot
        self.monitored_members = {}
        self.monitored_vcs = set()  # (guild_id, vc_id)
        self.lock = asyncio.Lock()
        self.refresh_vc_cache.start()

    def cog_unload(self):
        self.refresh_vc_cache.cancel()
        asyncio.create_task(self.cleanup_tasks())


    @app_commands.command(name="fake_cam", description="Warn user to use proper desk or face cam.")
    async def fake_cam(self, interaction: discord.Interaction, user: discord.Member):
        try:
            embed = discord.Embed(
                title=("🚨 Fake Cam Warning !!"),
                description=(" Hey! Please use a proper **desk cam** or **face cam** instead of a fake cam while you're in the voice channel."),
                color=discord.Color.blurple()
            )
            embed.set_footer(
                text="Powered by Lakshya - Stay Motivated",
                icon_url=self.bot.user.display_avatar.url
            )
            await user.send(embed=embed)
            mod_embed = discord.Embed(
                title="🔧 Fake Cam Request Issued",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            mod_embed.add_field(name="👤 Issued By", value=interaction.user.mention, inline=True)
            mod_embed.add_field(name="🎯 Target User", value=user.mention, inline=True)
            mod_embed.set_footer(
                text="Logged by Lakshya Bot",
                icon_url=self.bot.user.display_avatar.url
            )
            await self.bot.log_to_mod(interaction, embed=mod_embed)
            await interaction.response.send_message(f"✅ Message sent to {user.mention} about fake cam usage.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ Couldn't send DM to {user.mention}. They might have DMs turned off.", ephemeral=True)

    @app_commands.command(name="full_screen_share", description="Ask user to share their full screen.")
    async def full_screen_share(self, interaction: discord.Interaction, user: discord.Member):
        try:
            embed = discord.Embed(
                title=("🚨 Screen Share !!"),
                description=("🖥️ Please **share your full screen** (not just a window or browser tab) while you're in the voice channel."),
                color=discord.Color.blurple()
            )
            embed.set_footer(
                text="Powered by Lakshya - Stay Motivated",
                icon_url=self.bot.user.display_avatar.url
            )
            await user.send(embed=embed)
            mod_embed = discord.Embed(
                title="🔧 Screen Share Request Issued",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            mod_embed.add_field(name="👤 Issued By", value=interaction.user.mention, inline=True)
            mod_embed.add_field(name="🎯 Target User", value=user.mention, inline=True)
            mod_embed.set_footer(
                text="Logged by Lakshya Bot",
                icon_url=self.bot.user.display_avatar.url
            )
            await self.bot.log_to_mod(interaction, embed=mod_embed)
            await interaction.response.send_message(f"✅ Message sent to {user.mention} about full screen sharing.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ Couldn't send DM to {user.mention}. They might have DMs turned off.", ephemeral=True)

    @tasks.loop(minutes=5)
    async def refresh_vc_cache(self):
        try:
            new_data = await self.bot.db_handler.get_all_monitored_vcs()
            self.monitored_vcs = {(entry['guild_id'], entry['vc_id']) for entry in new_data}
            logger.info(f"Refreshed VC cache: {len(self.monitored_vcs)} channels")
        except Exception as e:
            logger.error(f"VC cache refresh failed: {e}")

    async def cleanup_tasks(self):
        async with self.lock:
            for task in self.monitored_members.values():
                task.cancel()
            self.monitored_members.clear()

    async def start_monitoring(self, member: discord.Member):
        """Start monitoring a member in voice channel"""
        async with self.lock:
            if member.id not in self.monitored_members:
                self.monitored_members[member.id] = self.bot.loop.create_task(
                    self.member_monitor_loop(member)
                )  # Properly indented closing parenthesis
                logger.info(f"Started monitoring {member}")
                await self.bot.log_to_support(f"Started monitoring {member}")

    async def stop_monitoring(self, member: discord.Member):
        async with self.lock:
            if member.id in self.monitored_members:
                self.monitored_members[member.id].cancel()
                del self.monitored_members[member.id]
                logger.info(f"Stopped monitoring {member}")
                await self.bot.log_to_support(f"Stopped monitoring {member}")

    async def send_dm(self, member: discord.Member, message: str):
        try:
            await member.send(message)
            logger.debug(f"Sent DM to {member}")
        except discord.Forbidden:
            logger.warning(f"Can't DM {member} (disabled DMs)")
        except Exception as e:
            logger.error(f"DM failed: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        guild_id = member.guild.id
        
        try:
            # Only handle actual channel changes
            if before.channel == after.channel:
                logger.debug(f"Ignoring non-channel change for {member}")
                return

            # Handle joins to monitored channels
            if after.channel and (guild_id, after.channel.id) in self.monitored_vcs:
                logger.info(f"{member} joined monitored VC")
                await self.start_monitoring(member)
            
            # Handle leaves from monitored channels
            if before.channel and (guild_id, before.channel.id) in self.monitored_vcs:
                logger.info(f"{member} left monitored VC")
                await self.stop_monitoring(member)

        except Exception as e:
            logger.error(f"Voice state error: {e}", exc_info=True)

    async def member_monitor_loop(self, member: discord.Member):
        """Continuous monitoring loop that persists through media state changes"""
        try:
            guild = member.guild
            logger.info(f"Starting continuous monitoring for {member}")
            await self.bot.log_to_support(f"Starting continuous monitoring for {member}")

            config = await self.bot.db_handler.get_server_config(guild.id)
            afk_channel_id = config.get('afk_channel_id')
            compliance_role_id = config.get('compliance_role_id')
            
            if not all([afk_channel_id, compliance_role_id]):
                logger.error("Missing required monitoring config")
                await self.bot.log_to_support(f"Missing required monitoring config")
                return

            afk_channel = guild.get_channel(afk_channel_id)
            compliance_role = guild.get_role(compliance_role_id)
            
            warnings = 0
            last_warn = None
            consecutive_errors = 0

            while True:
                try:
                    # Refresh member state with retries
                    try:
                        member = await asyncio.wait_for(
                            guild.fetch_member(member.id),
                            timeout=10
                        )
                    except (discord.NotFound, asyncio.TimeoutError):
                        logger.info(f"{member} left guild or unreachable")
                        break

                    # Validate voice status
                    if not member.voice or not member.voice.channel:
                        logger.info(f"{member} left voice channel")
                        break

                    if member.voice.channel.id == afk_channel_id:
                        logger.info(f"{member} in AFK channel")
                        break

                    # Media check with error handling
                    try:
                        has_media = member.voice.self_video or member.voice.self_stream
                    except AttributeError:
                        logger.warning(f"Voice state error for {member}")
                        await asyncio.sleep(5)
                        continue

                    # Main monitoring logic
                    now = datetime.now(timezone.utc)
                    if not has_media:
                        if last_warn is None:
                            # First warning immediately
                            await self.send_dm(member, "⚠️ Please enable camera/screenshare within 60 seconds")
                            warnings = 1
                            last_warn = now
                        else:
                            elapsed = (now - last_warn).total_seconds()
                            if elapsed > 60:  # 1 minute cooldown
                                warnings += 1
                                last_warn = now
                                
                                if warnings == 2:
                                    await self.send_dm(member, "⏳ Final warning! Enable cam/screen share or be moved to AFK!")
                                elif warnings >= 3:
                                    await member.move_to(afk_channel)
                                    await self.send_dm(member, "🚫 You were moved to AFK for non-compliance")
                                    logger.info(f"Moved {member} to AFK")
                                    break
                    else:
                        # Reset warnings when media is enabled
                        if warnings > 0:
                            logger.debug(f"{member} enabled media, resetting warnings")
                            warnings = 0
                            last_warn = None

                    # Manage compliance role
                    try:
                        if has_media:
                            if compliance_role in member.roles:
                                await member.remove_roles(compliance_role)
                        else:
                            if compliance_role not in member.roles:
                                await member.add_roles(compliance_role)
                    except discord.Forbidden:
                        logger.error(f"Missing role permissions in {guild.name}")
                        break

                    consecutive_errors = 0
                    await asyncio.sleep(15)  # Check every 15 seconds

                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Monitoring error ({consecutive_errors}/3): {e}")
                    if consecutive_errors >= 3:
                        break
                    await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Critical monitoring failure: {e}", exc_info=True)
        finally:
            logger.info(f"Ending monitoring for {member}")
            await self.bot.log_to_support(f"Ending monitoring for {member}")
            await self.stop_monitoring(member)

async def setup(bot: StoicBot):
    await bot.add_cog(Events(bot))