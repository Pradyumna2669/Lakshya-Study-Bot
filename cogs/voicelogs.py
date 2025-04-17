import discord
from discord.ext import commands
from datetime import datetime
from core.bot import StoicBot

class VoiceLogs(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot
        self.db = bot.db_handler
    
    def format_footer(self, user_id: int) -> str:
        """Format footer with current time in a more readable format"""
        now = datetime.now()
        return f"User ID - {user_id} • Today at {now.strftime('%H:%M')}"

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        try:
            if not await self.db.is_voice_logging_enabled(member.guild.id):
                return

            log_channel_id = await self.db.get_voice_log_channel(member.guild.id)
            if not log_channel_id:
                return

            log_channel = self.bot.get_channel(log_channel_id)
            if not log_channel:
                return

            # User joined a voice channel
            if not before.channel and after.channel:
                embed = discord.Embed(
                    color=discord.Color.green(),
                    description=(
                        f"Member joined voice channel\n\n"
                        f"{member.mention} joined {after.channel.mention}"
                    )
                )
            
            # User left a voice channel
            elif before.channel and not after.channel:
                embed = discord.Embed(
                    color=discord.Color.red(),
                    description=(
                        f"Member left voice channel\n\n"
                        f"{member.mention} left {before.channel.mention}"
                    )
                )
            
            # User switched voice channels
            elif before.channel and after.channel and before.channel != after.channel:
                embed = discord.Embed(
                    color=discord.Color.blue(),
                    description=(
                        f"Member switched voice channels\n\n"
                        f"{member.mention} moved from {before.channel.mention} to {after.channel.mention}"
                    )
                )
            else:
                return

            embed.set_author(name=f"{member.name}", icon_url=member.display_avatar.url)
            embed.set_footer(text=self.format_footer(member.id))
            await log_channel.send(embed=embed)

        except Exception as e:
            print(f"Error in voice state update: {e}")

    @commands.group(name="voicelog", aliases=["vclog"])
    @commands.has_permissions(manage_guild=True)
    async def voice_log(self, ctx):
        """Voice channel activity logging commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @voice_log.command(name="enable")
    async def enable_logging(self, ctx, channel: discord.TextChannel = None):
        """Enable voice logging in the specified channel"""
        channel = channel or ctx.channel
        await self.db.enable_voice_logging(ctx.guild.id, channel.id)
        embed = discord.Embed(
            color=discord.Color.green(),
            description=f"✅ Voice logging enabled in {channel.mention}"
        )
        await ctx.send(embed=embed)

    @voice_log.command(name="disable")
    async def disable_logging(self, ctx):
        """Disable voice logging"""
        await self.db.disable_voice_logging(ctx.guild.id)
        embed = discord.Embed(
            color=discord.Color.red(),
            description="❌ Voice logging disabled"
        )
        await ctx.send(embed=embed)

    @voice_log.command(name="status")
    async def logging_status(self, ctx):
        """Check voice logging status"""
        config = await self.db.get_voice_logging_config(ctx.guild.id)
        
        if config and config.get('enabled'):
            channel = ctx.guild.get_channel(config['log_channel_id'])
            status = f"✅ Enabled in {channel.mention if channel else 'deleted channel'}"
            color = discord.Color.green()
        else:
            status = "❌ Disabled"
            color = discord.Color.red()

        embed = discord.Embed(
            color=color,
            title="Voice Logging Status",
            description=status
        )
        await ctx.send(embed=embed)

async def setup(bot: StoicBot):
    await bot.add_cog(VoiceLogs(bot))