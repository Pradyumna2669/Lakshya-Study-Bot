import discord
from discord import app_commands
from discord.ext import commands
from core.bot import StoicBot

class Management(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot
    
    async def is_owner(ctx: commands.Context):
        return await ctx.bot.is_owner(ctx.author)

    @commands.hybrid_command()
    @commands.is_owner()
    async def reload(self, ctx: commands.Context, cog: str):
        """Reload a cog (Owner only)"""
        try:
            await self.bot.reload_extension(f"cogs.{cog}")
            await ctx.send(f"✅ Reloaded {cog}", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ Error reloading {cog}: {str(e)}", ephemeral=True)

    @commands.hybrid_command()
    @commands.is_owner()
    async def cogs(self, ctx: commands.Context):
        """List loaded cogs (Owner only)"""
        cogs = "\n".join(self.bot.extensions.keys())
        await ctx.send(f"Loaded cogs:\n{cogs}", ephemeral=True)

async def setup(bot: StoicBot):
    await bot.add_cog(Management(bot))