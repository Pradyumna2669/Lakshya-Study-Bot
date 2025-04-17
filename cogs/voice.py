import discord
from discord import app_commands
from discord.ext import commands
from core.bot import StoicBot
import logging

logger = logging.getLogger(__name__)

class VoiceSystem(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot
        self.active_tasks = {}

    @commands.hybrid_command()
    @app_commands.describe(action="add/remove/list", vc="Voice channel")
    async def monitor_vc(self, ctx: commands.Context, action: str, vc: discord.VoiceChannel = None):
        """Manage monitored voice channels"""
        valid_actions = ["add", "remove", "list"]
        action = action.lower()
        
        if action not in valid_actions:
            return await ctx.send("❌ Invalid action! Use add/remove/list", ephemeral=True)

        try:
            monitored_vcs = await self.bot.db_handler.get_monitored_vcs(ctx.guild.id)
            events_cog = self.bot.get_cog("Events")

            if action == "add":
                if not vc:
                    return await ctx.send("❌ Please specify a voice channel to add", ephemeral=True)
                    
                if vc.id not in monitored_vcs:
                    await self.bot.db_handler.add_monitored_vc(ctx.guild.id, vc.id)
                    await ctx.send(f"✅ Added {vc.mention} to monitored channels", ephemeral=True)
                    # Start monitoring existing members
                    if events_cog:
                        async with events_cog.lock:
                            for member in vc.members:
                                if not member.bot and member.id not in events_cog.monitored_members:
                                    events_cog.monitored_members[member.id] = self.bot.loop.create_task(
                                        events_cog.member_monitor_loop(member)
                                    )
                else:
                    await ctx.send(f"⚠️ {vc.mention} is already being monitored", ephemeral=True)

            elif action == "remove":
                if not vc:
                    return await ctx.send("❌ Please specify a voice channel to remove", ephemeral=True)
                    
                if vc.id in monitored_vcs:
                    await self.bot.db_handler.remove_monitored_vc(ctx.guild.id, vc.id)
                    # Stop monitoring members in this VC
                    if events_cog:
                        async with events_cog.lock:
                            for member in vc.members:
                                if member.id in events_cog.monitored_members:
                                    events_cog.monitored_members[member.id].cancel()
                                    del events_cog.monitored_members[member.id]
                    await ctx.send(f"✅ Removed {vc.mention} from monitored channels", ephemeral=True)
                else:
                    await ctx.send(f"⚠️ {vc.mention} is not currently monitored", ephemeral=True)

            elif action == "list":
                embed = discord.Embed(title="Monitored Voice Channels", color=0x7289da)
                if monitored_vcs:
                    embed.description = "\n".join([f"<#{vc_id}>" for vc_id in monitored_vcs])
                    embed.set_footer(text=f"Total channels: {len(monitored_vcs)}")
                else:
                    embed.description = "No voice channels are currently being monitored"
                await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Monitor VC command failed: {e}", exc_info=True)
            await ctx.send("❌ An error occurred while processing your request", ephemeral=True)
            await self.bot.log_to_support(f"❌ An error occurred while processing your request: {e}")
        
async def setup(bot: StoicBot):
    await bot.add_cog(VoiceSystem(bot))