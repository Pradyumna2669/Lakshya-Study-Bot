import discord
from discord import Embed, app_commands, User
from discord.ext import commands
from core.bot import StoicBot
import platform
import psutil
import time
import logging

logger = logging.getLogger(__name__)

class Utilities(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot

    @commands.hybrid_command()
    async def help(self, ctx):
        embed = discord.Embed(
            title="📚 Lakshya Study Bot Commands",
            description="Here's everything I can do to help manage the server:",
            color=0x7289da
        )
        embed.set_thumbnail(url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        
        # New Study Tracking Section
        study_commands = (
            "⏱️ **Study Tracking**\n"
            "> `/start [duration]` - Start study session (default 60 mins)\n"
            "> `/status` - Check session status & controls\n"
            "> `/leaderboard` - Daily study rankings\n"
            "> `/summary [days]` - Past study stats\n"
            "> `/stats` - Personal study analytics\n"
        )
        embed.add_field(name="📚 Study Sessions", value=study_commands, inline=False)

        # Moderation + New Content Moderation
        mod_commands = (
            "🔧 **Moderation**\n"
            "> `/mute @user [reason]` - Silence rule breakers\n"
            "> `/unmute @user` - Restore speaking privileges\n"
            "> `/warn @user [reason]` - Issue formal warning\n"
            "> `/warnings @user` - Check warning history\n"
            "> `/del_warn @user [count]` - Remove warnings\n"
            "> `/purge [count]` - Delete recent messages\n"
            "> `/showconfig` - Show current moderation config\n"
            "\n"
            "🛡️ **Content Moderation**\n"
            "> `/add_nsfw_word [keyword]` - Add an NSFW keyword (Mod)\n"
            "> `/remove_nsfw_word [keyword]` - Remove an NSFW keyword (Mod)\n"
            "> `/list_nsfw_words` - List all NSFW keywords (Mod)\n"
            "> `/toggle_content_moderation [nsfw] [promo]` - Toggle NSFW/Promotion detection (Mod)\n"
            "> `/moderation_status` - View moderation settings (Mod)"
        )
        embed.add_field(name="🛠️ Staff Tools", value=mod_commands, inline=False)

        # Voice Logging
        voicelog_commands = (
            "🎙️ **Voice Logging**\n"
            "> `!voicelog enable [channel]` - Enable VC logging (uses current channel if not specified)\n"
            "> `!voicelog disable` - Disable VC logging\n"
            "> `!voicelog status` - Show current logging status\n"
        )
        embed.add_field(name="🎧 Voice Channel Tools", value=voicelog_commands, inline=False)

        # Ticket System
        ticket_commands = (
            "🎟️ **Ticket System**\n"
            "> Use the dropdown in <#1308361779126210621> to:\n"
            "> - Get **Help** (🆘)\n"
            "> - **Apply for Staff** (📋)\n"
            "> - Appeal a **Ban** (🔒)\n"
        )
        embed.add_field(name="💬 Support System", value=ticket_commands, inline=False)

        # Study Tools
        study_tools = (
            "📖 **Study Features**\n"
            "> `!rule` - View study room guidelines\n"
            "> `!pingvc` - Notify VC members\n"
            "> `/monitor_vc` - Manage cam monitoring\n"
            "> `/set_exam_countdown` - Set exam countdown\n"
            "> `/remove_exam_countdown` - Remove countdown\n"
            "> `/send_dm` - Send embedded DM to user\n"
        )
        embed.add_field(name="🧠 Study Tools", value=study_tools, inline=False)

        # Sticky Messages
        sticky_commands = (
            "📌 **Sticky Messages**\n"
            "> `/set_sticky` - Set a sticky embed using modal\n"
            "> `/remove_sticky` - Remove sticky from a channel\n"
        )
        embed.add_field(name="📍 Sticky Feature", value=sticky_commands, inline=False)

        embed.set_footer(
            text=f"Requested by {ctx.author.display_name}",
            icon_url=ctx.author.avatar.url if ctx.author.avatar else None
        )

        await ctx.send(embed=embed)

    @commands.hybrid_command()
    async def ping(self, ctx):
        """Check bot latency"""
        latency = round(self.bot.latency * 1000)
        await ctx.send(f"🏓 Pong! Latency: {latency}ms")

    @commands.hybrid_command()
    async def health(self, ctx):
        """Show system health"""
        bot_latency = round(self.bot.latency * 1000)  # in ms
        cpu_usage = psutil.cpu_percent()
        ram_usage = psutil.virtual_memory().percent
        uptime_seconds = time.time() - psutil.boot_time()
        uptime_hours = round(uptime_seconds / 3600, 2)

        embed = discord.Embed(title="🩺 System Health", color=discord.Color.green())
        embed.add_field(name="Latency", value=f"{bot_latency} ms", inline=False)
        embed.add_field(name="CPU Usage", value=f"{cpu_usage}%", inline=True)
        embed.add_field(name="RAM Usage", value=f"{ram_usage}%", inline=True)
        embed.add_field(name="Uptime", value=f"{uptime_hours} hrs", inline=False)
        embed.set_footer(text=platform.system() + " - " + platform.release())

        await ctx.send(embed=embed)

    # Error listener for prefix command permission errors
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use this command.")

    # Hybrid command with dual permission checks
    @commands.hybrid_command(name="send_dm", description="📨 Send a custom embedded direct message to a user")
    @app_commands.describe(
        user="The user to message",
        message="The content of your message"
    )
    @commands.has_permissions(manage_messages=True)
    async def send_dm(self, ctx: commands.Context, user: discord.User, message: str):
        """Send an elegant embedded DM to a user with server branding"""
        try:
            # Create main embed for recipient
            embed = discord.Embed(
                title="📬 Message from Server Staff",
                description=message,
                color=discord.Color.gold(),
                timestamp=ctx.message.created_at
            )
            embed.set_author(
                name=ctx.guild.name,
                icon_url=ctx.guild.icon.url if ctx.guild.icon else None
            )
            embed.set_footer(text="Happy Learning", icon_url=self.bot.user.display_avatar.url)

            # Send DM
            await user.send(embed=embed)

            # Create log embed
            log_embed = discord.Embed(
                title="📨 DM Sent Successfully",
                color=discord.Color.green(),
                timestamp=ctx.message.created_at
            )
            log_embed.add_field(name="Recipient", value=f"{user.mention}\n`{user.id}`", inline=True)
            log_embed.add_field(name="Content", value=message, inline=False)
            log_embed.add_field(name="Sent By", value=f"{ctx.author.mention}\n`{ctx.author.id}`", inline=True)
            log_embed.set_thumbnail(url=user.display_avatar.url)

            # Send confirmation and log
            confirmation = f"✅ Successfully sent DM to {user.mention}"
            await ctx.send(confirmation, ephemeral=bool(ctx.interaction))
            await self.bot.log_to_mod(ctx.interaction, embed=log_embed)

        except discord.Forbidden:
            error_embed = discord.Embed(
                title="❌ DM Failed",
                description="This user has DMs disabled or blocked the bot",
                color=discord.Color.red()
            )
            error_embed.add_field(name="User", value=user.mention)
            await ctx.send(embed=error_embed, ephemeral=bool(ctx.interaction))
            await self.bot.log_to_support(error_embed)

        except Exception as e:
            logger.error(f"DM Error: {str(e)}", exc_info=True)
            error_embed = discord.Embed(
                title="⚠️ Unexpected Error",
                description="Failed to send DM",
                color=discord.Color.orange()
            )
            error_embed.add_field(name="Error Details", value=f"```{str(e)[:1000]}```")
            await ctx.send(embed=error_embed, ephemeral=bool(ctx.interaction))
            
    @commands.command(name="sync")
    @commands.is_owner()
    async def sync_commands(self, ctx):
        await self.bot.tree.sync()
        await ctx.send("✅ Slash commands synced globally.")
        
async def setup(bot: StoicBot):
    await bot.add_cog(Utilities(bot))