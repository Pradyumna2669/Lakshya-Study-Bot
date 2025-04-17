import discord
from discord import Embed, app_commands, User
from discord.ext import commands
from core.bot import StoicBot
import platform
import psutil
import time


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
        
        mod_commands = (
            "🔧 **Moderation**\n"
            "> `/mute @user [reason]` - Silence rule breakers\n"
            "> `/unmute @user` - Restore speaking privileges\n"
            "> `/warn @user [reason]` - Issue formal warning\n"
            "> `/warnings @user` - Check warning history\n"
            "> `/del_warn @user [count]` - Remove warnings\n"
            "> `/purge [count]` - Delete recent messages\n"
            "> `/showconfig` - Show current moderation config\n"
        )
        embed.add_field(name="🛠️ Staff Tools", value=mod_commands, inline=False)

        voicelog_commands = (
            "🎙️ **Voice Logging**\n"
            "> `!voicelog enable [channel]` - Enable VC logging (uses current channel if not specified)\n"
            "> `!voicelog disable` - Disable VC logging\n"
            "> `!voicelog status` - Show current logging status\n"
        )
        embed.add_field(name="🎧 Voice Channel Tools", value=voicelog_commands, inline=False)

        ticket_commands = (
            "🎟️ **Ticket System**\n"
            "> Use the dropdown in <#1308361779126210621> to:\n"
            "> - Get **Help** (🆘)\n"
            "> - **Apply for Staff** (📋)\n"
            "> - Appeal a **Ban** (🔒)\n"
        )
        embed.add_field(name="💬 Support System", value=ticket_commands, inline=False)

        study_commands = (
            "📖 **Study Features**\n"
            "> `!rule` - View study room guidelines\n"
            "> `!pingvc` - Notify VC members\n"
            "> `/monitor_vc` - Manage cam monitoring\n"
            "> `/set_exam_countdown` - Set a countdown in a VC for an upcoming exam\n"
            "> `/remove_exam_countdown` - Remove an active exam countdown\n"
            "> `/send_dm` - Send an embedded DM to a user\n"
        )
        embed.add_field(name="🧠 Study Tools", value=study_commands, inline=False)

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
    @commands.hybrid_command(name="send_dm", description="Send a custom embedded DM to a user.")
    @app_commands.describe(user="The user to DM", message="The message to send (as embed)")
    @commands.has_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def send_dm(self, ctx: commands.Context, user: User, message: str):
        """Send an embedded DM to a user (Hybrid command)"""
        try:
            # Create embed
            embed = Embed(
                title="📬 Message from Server Staff",
                description=message,
                color=discord.Color.orange()
            )
            embed.set_footer(
                text=f"Sent by {ctx.author}",
                icon_url=ctx.author.display_avatar.url
            )

            # Send DM and confirm
            await user.send(embed=embed)
            await self.bot.log_to_mod(ctx, embed=embed)
            
            # Context-aware response
            response = f"✅ Successfully sent DM to {user.mention}"
            await ctx.send(response, ephemeral=bool(ctx.interaction))

        except discord.Forbidden:
            response = "❌ Cannot send DM (user might have DMs disabled)"
            await ctx.send(response, ephemeral=bool(ctx.interaction))
        except Exception as e:
            print(f"[Send DM Error] {e}")
            response = "⚠️ Failed to send DM due to an error"
            await ctx.send(response, ephemeral=bool(ctx.interaction))
            
async def setup(bot: StoicBot):
    await bot.add_cog(Utilities(bot))