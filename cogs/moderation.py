import discord
from datetime import datetime
from discord.ext import commands
from discord import app_commands
from core.bot import StoicBot
from typing import Optional

class SetupSession:
    def __init__(self, ctx, required_fields):
        self.ctx = ctx
        self.required_fields = required_fields
        self.current_step = 0
        self.data = {}
        self.prompt_messages = []
        self.user_responses = []

    async def start(self):
        await self.send_next_prompt()

    async def send_next_prompt(self):
        if self.current_step >= len(self.required_fields):
            return await self.finish_setup()

        field_name, description = self.required_fields[self.current_step]
        embed = discord.Embed(
            title=f"Server Setup - Step {self.current_step + 1}/{len(self.required_fields)}",
            description=f"Please enter the **{description}** ID:",
            color=0x5865F2
        )
        msg = await self.ctx.send(embed=embed)
        self.prompt_messages.append(msg)
        self.current_step += 1

    async def process_input(self, message: discord.Message):
        """Process user's response message"""
        try:
            self.user_responses.append(message)
            
            field_name = self.required_fields[self.current_step - 1][0]
            
            try:
                int_value = int(message.content)
            except ValueError:
                raise ValueError("Please enter a valid numeric ID")
                
            if "role" in field_name:
                obj = self.ctx.guild.get_role(int_value)
            else:
                obj = self.ctx.guild.get_channel(int_value)
                
            if not obj:
                raise ValueError("This ID doesn't exist in the server")
                
            self.data[field_name] = int_value
            await self._cleanup_messages()
            await self.send_next_prompt()
            
        except ValueError as e:
            error_msg = await self.ctx.send(
                f"❌ {str(e)}! Please try again:",
                delete_after=10
            )
            self.prompt_messages.append(error_msg)

    async def _cleanup_messages(self):
        """Safely delete setup messages"""
        try:
            if self.prompt_messages:
                try:
                    await self.prompt_messages[-1].delete()
                except discord.NotFound:
                    pass
                self.prompt_messages.pop()
            
            if self.user_responses:
                try:
                    await self.user_responses[-1].delete()
                except discord.NotFound:
                    pass
                self.user_responses.pop()
                
        except discord.HTTPException as e:
            print(f"Error cleaning up messages: {e}")

    async def finish_setup(self):
        """Finalize the setup process"""
        try:
            cog = self.ctx.bot.get_cog('Moderation')
            success = await cog.db.set_server_config(
                self.ctx.guild.id,
                **self.data
            )
            
            if not success:
                raise Exception("Failed to save configuration to database")
            
            embed = discord.Embed(
                title="✅ Server Configuration Complete",
                description="You can now use moderation commands!",
                color=0x00ff00
            )
            
            for field_name, value in self.data.items():
                pretty_name = field_name.replace("_", " ").title()
                if "role" in field_name:
                    embed.add_field(name=pretty_name, value=f"<@&{value}>", inline=True)
                else:
                    embed.add_field(name=pretty_name, value=f"<#{value}>", inline=True)
            
            confirm_msg = await self.ctx.send(embed=embed)
            self.prompt_messages.append(confirm_msg)
            
            try:
                await self.ctx.channel.delete_messages(self.prompt_messages)
            except:
                pass
                
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Setup Failed",
                description=f"Error: {str(e)}",
                color=0xff0000
            )
            await self.ctx.send(embed=error_embed)
        finally:
            cog = self.ctx.bot.get_cog('Moderation')
            if cog and self.ctx.guild.id in cog.active_setups:
                del cog.active_setups[self.ctx.guild.id]

class Moderation(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot
        self.db = bot.db_handler
        self.active_setups = {}
        self._tracked_commands = ['warn', 'mute', 'unmute', 'del_warn']

    async def is_configured(self, guild_id: int):
        config = await self.db.get_server_config(guild_id)
        required_fields = [
            'mod_role_id', 'admin_role_id', 'elder_role_id',
            'mute_role_id', 'compliance_role_id',
            'afk_channel_id', 'log_channel_id'
        ]
        return config and all(field in config and config[field] for field in required_fields)

    def create_mod_embed(self, title: str, member: discord.Member, 
                       moderator: discord.Member, reason: str):
        embed = discord.Embed(
            title=title,
            color=0x5865F2
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        fields = [
            ("User", f"{member.mention}\nID: {member.id}", False),
            ("Moderator", moderator.mention, False),
            ("Reason", reason or "No reason provided", False)
        ]
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
        return embed

    @staticmethod
    def check_config():
        async def predicate(ctx: commands.Context):
            cog = ctx.cog
            if not await cog.is_configured(ctx.guild.id):
                embed = discord.Embed(
                    title="❌ Configuration Required",
                    description="This server must be configured first!\nUse `/setup`",
                    color=0xff0000
                )
                if ctx.interaction:
                    await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    await ctx.send(embed=embed)
                return False
            return True
        return commands.check(predicate)

    async def _log_action(self, ctx: commands.Context, action_data: dict):
        """Handle logging for both developer and server channels"""
        log_embed = discord.Embed(
            title=f"Member {action_data['action'].title()}",
            color=self._get_action_color(action_data['action']),
            timestamp=datetime.utcnow()
        )
        log_embed.set_author(
            name=f"{action_data['member']} (ID: {action_data['member'].id})",
            icon_url=action_data['member'].display_avatar.url
        )
        log_embed.add_field(name="Moderator", value=f"{ctx.author.mention}\nID: {ctx.author.id}", inline=False)
        log_embed.add_field(name="Reason", value=action_data.get('reason', 'Not specified'), inline=False)
        
        if 'duration' in action_data:
            log_embed.add_field(name="Duration", value=action_data['duration'], inline=True)
        if 'warnings_count' in action_data:
            log_embed.add_field(name="Total Warnings", value=action_data['warnings_count'], inline=True)
        
        log_embed.add_field(name="Channel", value=ctx.channel.mention, inline=True)
        log_embed.set_footer(text=f"Case ID: {ctx.message.id}")

        # Server mod log
        config = await self.db.get_server_config(ctx.guild.id)
        if config and (log_channel := ctx.guild.get_channel(config['log_channel_id'])):
            try:
                await log_channel.send(embed=log_embed)
            except discord.HTTPException:
                pass

        # Developer log
        await self.bot.log_to_support(log_embed)

    def _get_action_color(self, action: str) -> int:
        colors = {
            'warn': 0xffcc00, 'mute': 0xff9900,
            'unmute': 0x00ff00, 'del_warn': 0x00ccff,
            'kick': 0xff3300, 'ban': 0x990000
        }
        return colors.get(action.lower(), 0x5865F2)

    @commands.hybrid_command(
    name="setup",  # Better to use lowercase with no spaces
    description="Setup Your Server",
    help="Setup all Roles, Channel ID's For Your Server")
    async def setup(self, ctx: commands.Context):
        """Initialize server configuration"""
        if ctx.guild.id in self.active_setups:
            return await ctx.send("❗ Setup session already in progress!", ephemeral=True)

        required_fields = [
            ("mod_role_id", "Moderator Role"),
            ("admin_role_id", "Admin Role"),
            ("elder_role_id", "Elder Role"),
            ("mute_role_id", "Mute Role"),
            ("compliance_role_id", "Compliance Role"),
            ("afk_channel_id", "AFK Channel"),
            ("log_channel_id", "Log Channel")
        ]

        session = SetupSession(ctx, required_fields)
        self.active_setups[ctx.guild.id] = session
        await session.start()

    @check_config()
    @commands.hybrid_command()
    @app_commands.describe(member="Member to mute", reason="Reason for mute")
    async def mute(self, ctx: commands.Context, member: discord.Member, *, 
                 reason: str = "No reason provided"):
        """Mute a member"""
        config = await self.db.get_server_config(ctx.guild.id)
        required_roles = [config['mod_role_id'], config['admin_role_id'], config['elder_role_id']]
        
        if not any(role.id in required_roles for role in ctx.author.roles):
            return await ctx.send("❌ Permission denied!", ephemeral=True)
        
        if not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.send("❌ I need 'Manage Roles' permission!", ephemeral=True)

        config = await self.db.get_server_config(ctx.guild.id)
        mute_role = ctx.guild.get_role(config['mute_role_id'])
        
        if not mute_role:
            return await ctx.send("❌ Mute role not found!", ephemeral=True)
        
        if mute_role in member.roles:
            return await ctx.send("ℹ️ Member already muted!", ephemeral=True)
            
        try:
            await member.add_roles(mute_role, reason=reason)
            embed = self.create_mod_embed("🔇 Member Muted", member, ctx.author, reason)
            await ctx.send(embed=embed)
            await self._log_action(ctx, {
                'action': 'mute',
                'member': member,
                'reason': reason
            })
        except discord.Forbidden:
            await ctx.send("❌ Missing permissions to mute!", ephemeral=True)
    
    @commands.hybrid_command(name="purge", description="Delete a number of messages (Admin only, max 150).")
    @commands.has_permissions(administrator=True)
    async def purge(self, ctx: commands.Context, count: int):
        if count > 150:
            await ctx.send("⚠️ You can only delete up to 150 messages at once.", ephemeral=True)
            return
        if count < 1:
            await ctx.send("⚠️ Please specify a number greater than 0.", ephemeral=True)
            return

        deleted = await ctx.channel.purge(limit=count)
        confirmation = await ctx.send(f"🧹 Deleted {len(deleted)} messages.", delete_after=5)
        await ctx.message.delete(delay=1)  # Clean up the original command call


    @commands.hybrid_command(
    name="showconfig",  # Better to use lowercase with no spaces
    description="Display current server configuration settings",
    help="Shows all configured roles, channels, and moderation settings for this server")
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

    @check_config()
    @commands.hybrid_command()
    @app_commands.describe(member="Member to unmute")
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        """Unmute a member"""
        config = await self.db.get_server_config(ctx.guild.id)
        required_roles = [config['mod_role_id'], config['admin_role_id'], config['elder_role_id']]
        
        if not any(role.id in required_roles for role in ctx.author.roles):
            return await ctx.send("❌ Permission denied!", ephemeral=True)
        
        if not ctx.guild.me.guild_permissions.manage_roles:
            return await ctx.send("❌ I need 'Manage Roles' permission!", ephemeral=True)

        config = await self.db.get_server_config(ctx.guild.id)
        mute_role = ctx.guild.get_role(config['mute_role_id'])
        
        if not mute_role:
            return await ctx.send("❌ Mute role not found!", ephemeral=True)
        
        if mute_role not in member.roles:
            return await ctx.send("ℹ️ Member isn't muted!", ephemeral=True)
            
        try:
            await member.remove_roles(mute_role)
            embed = self.create_mod_embed("🔊 Member Unmuted", member, ctx.author, None)
            await ctx.send(embed=embed)
            await self._log_action(ctx, {
                'action': 'unmute',
                'member': member
            })
        except discord.Forbidden:
            await ctx.send("❌ Missing permissions to unmute!", ephemeral=True)

    @check_config()
    @commands.hybrid_command()
    @app_commands.describe(member="Member to warn", reason="Reason for warning")
    async def warn(self, ctx: commands.Context, member: discord.Member, *, 
                 reason: str = "No reason provided"):
        """Warn a member"""
        config = await self.db.get_server_config(ctx.guild.id)
        required_roles = [config['mod_role_id'], config['admin_role_id'], config['elder_role_id']]
        
        if not any(role.id in required_roles for role in ctx.author.roles):
            return await ctx.send("❌ Permission denied!", ephemeral=True)
        
        try:
            await self.db.add_warning(ctx.guild.id, member.id, ctx.author.id, reason)
            warnings = await self.db.get_warnings(ctx.guild.id, member.id)
            
            embed = self.create_mod_embed("⚠️ Member Warned", member, ctx.author, reason)
            embed.add_field(name="Total Warnings", value=f"{len(warnings)}/5", inline=False)
            await ctx.send(embed=embed)
            
            await self._log_action(ctx, {
                'action': 'warn',
                'member': member,
                'reason': reason,
                'warnings_count': len(warnings)
            })
            
            if len(warnings) >= 5:
                mute_role = ctx.guild.get_role(config['mute_role_id'])
                if mute_role:
                    try:
                        await member.add_roles(mute_role)
                        await ctx.send(embed=self.create_mod_embed(
                            "🔇 Auto-Muted", member, ctx.guild.me, "5 warnings"
                        ))
                        await self._log_action(ctx, {
                            'action': 'mute',
                            'member': member,
                            'reason': "Automatic mute (5 warnings)"
                        })
                    except discord.Forbidden:
                        await ctx.send("❌ Auto-mute failed!", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}", ephemeral=True)

    @check_config()
    @commands.hybrid_command()
    @app_commands.describe(member="Member to check warnings for")
    async def warnings(self, ctx: commands.Context, member: discord.Member):
        """View member's warnings"""
        try:
            warnings = await self.db.get_warnings(ctx.guild.id, member.id)
            embed = discord.Embed(
                title=f"Warnings for {member.display_name}",
                color=0xff9900
            )
            
            if warnings:
                embed.description = f"Total warnings: {len(warnings)}"
                for idx, warn in enumerate(warnings[:3], 1):
                    embed.add_field(
                        name=f"Warning #{idx} ({warn['created_at'][:10]})",
                        value=warn['reason'],
                        inline=False
                    )
                if len(warnings) > 3:
                    embed.set_footer(text=f"Showing 3 of {len(warnings)} warnings")
            else:
                embed.description = "No warnings found"
                
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}", ephemeral=True)

    @check_config()
    @commands.hybrid_command()
    @app_commands.describe(
        member="Member to remove warnings from",
        count="Number of warnings to remove"
    )
    async def del_warn(self, ctx: commands.Context, member: discord.Member, count: int = 1):
        """Remove warnings from a member"""
        config = await self.db.get_server_config(ctx.guild.id)
        required_roles = [config['mod_role_id'], config['admin_role_id'], config['elder_role_id']]
        
        if not any(role.id in required_roles for role in ctx.author.roles):
            return await ctx.send("❌ Permission denied!", ephemeral=True)
        
        try:
            deleted = await self.db.delete_warnings(ctx.guild.id, member.id, count)
            embed = discord.Embed(
                description=f"✅ Removed {deleted} warnings" if deleted else "ℹ️ No warnings to remove",
                color=0x00ff00 if deleted else 0xff9900
            )
            await ctx.send(embed=embed)
            await self._log_action(ctx, {
                'action': 'del_warn',
                'member': member,
                'reason': f"Removed {deleted} warnings"
            })
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}", ephemeral=True)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        embed = discord.Embed(
            title="🔧 Configuration Required",
            description=(
                f"Thanks for adding me to {guild.name}!\n\n"
                "**To set up moderation features:**\n"
                "1. Create required roles\n"
                "2. Use `/setup` command\n"
                "3. Configure all settings"
            ),
            color=0x5865F2
        )
        
        try:
            channel = next(
                c for c in guild.text_channels 
                if c.permissions_for(guild.me).send_messages
            )
            await channel.send(embed=embed)
        except (StopIteration, discord.Forbidden):
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        guild_id = message.guild.id if message.guild else None
        if not guild_id or guild_id not in self.active_setups:
            return

        session = self.active_setups[guild_id]
        if message.channel != session.ctx.channel or message.author != session.ctx.author:
            return

        try:
            await session.process_input(message)
        except Exception as e:
            print(f"Error processing setup input: {e}")

async def setup(bot: StoicBot):
    await bot.add_cog(Moderation(bot))