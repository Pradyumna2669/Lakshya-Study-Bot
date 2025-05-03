import discord
from discord.ext import commands
from core.bot import StoicBot

OWNER_ID = 746984807644266508  # Your user ID

# Owner check
def is_owner():
    async def predicate(ctx):
        return ctx.author.id == OWNER_ID
    return commands.check(predicate)

class Management(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot

    @commands.command(name="load")
    @is_owner()
    async def load(self, ctx: commands.Context, extension: str):
        try:
            await self.bot.load_extension(f'cogs.{extension}')
            await self.bot.tree.sync()  # Sync slash commands if any
            await ctx.send(f"✅ Loaded `{extension}` successfully.")
        except Exception as e:
            await ctx.send(f"❌ Error loading `{extension}`:\n```{e}```")

    @commands.command(name="unload")
    @is_owner()
    async def unload(self, ctx: commands.Context, extension: str):
        try:
            await self.bot.unload_extension(f'cogs.{extension}')
            await ctx.send(f"❌ Unloaded `{extension}` successfully.")
        except Exception as e:
            await ctx.send(f"❌ Error unloading `{extension}`:\n```{e}```")

    @commands.command(name="reload")
    @is_owner()
    async def reload(self, ctx: commands.Context, extension: str):
        try:
            await self.bot.unload_extension(f'cogs.{extension}')
            await self.bot.load_extension(f'cogs.{extension}')
            await self.bot.tree.sync()  # Sync slash commands if any
            await ctx.send(f"🔁 Reloaded `{extension}` successfully.")
        except Exception as e:
            await ctx.send(f"❌ Error reloading `{extension}`:\n```{e}```")

async def setup(bot: StoicBot):
    await bot.add_cog(Management(bot))
