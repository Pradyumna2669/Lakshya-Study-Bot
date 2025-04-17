import discord
from discord.ext import commands
from discord import app_commands
from core.bot import StoicBot
from core.database_handler import DatabaseHandler

class Configuration(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot
        self.db = bot.db_handler

    @commands.hybrid_command()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        "See Currect Configuration Settings"
    )
    async def show_config(self, ctx: commands.Context):
        """Display current server configuration"""
        try:
            config = await self.db.get_server_config(ctx.guild.id)
            if not config:
                return await ctx.send("❌ Server not configured!", ephemeral=True)

            embed = discord.Embed(
                title="Server Configuration",
                color=0x5865F2
            )
            
            roles = {
                "Mute Role": config.get('mute_role_id'),
                "Compliance Role": config.get('compliance_role_id')
            }
            
            channels = {
                "Ticket Category": config.get('ticket_category_id'),
                "AFK Channel": config.get('afk_channel_id')
            }

            for name, role_id in roles.items():
                embed.add_field(
                    name=name,
                    value=f"<@&{role_id}>" if role_id else "Not set",
                    inline=True
                )

            for name, channel_id in channels.items():
                embed.add_field(
                    name=name,
                    value=f"<#{channel_id}>" if channel_id else "Not set",
                    inline=True
                )

            await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:
            await ctx.send(f"❌ Error retrieving config: {str(e)}", ephemeral=True)

async def setup(bot: StoicBot):
    await bot.add_cog(Configuration(bot))