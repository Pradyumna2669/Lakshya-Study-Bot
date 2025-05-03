import discord
from datetime import datetime
from discord.ext import commands
from discord import app_commands, ui, ButtonStyle, SelectOption, Permissions
from discord.ui import View, Button, Select, RoleSelect, ChannelSelect
from core.bot import StoicBot
from typing import Optional, List

# --------------------------- Setup Components ---------------------------
class RoleCreationChoice(View):
    def __init__(self):
        super().__init__(timeout=300)
        self.choice = None

    @ui.button(label="Create All Recommended", style=ButtonStyle.green)
    async def create_all(self, interaction: discord.Interaction, button: ui.Button):
        self.choice = "all"
        await interaction.response.defer()
        self.stop()

    @ui.button(label="Create None", style=ButtonStyle.red)
    async def create_none(self, interaction: discord.Interaction, button: ui.Button):
        self.choice = "none"
        await interaction.response.defer()
        self.stop()

class RoleSelectCustom(RoleSelect):
    def __init__(self, field, description, **kwargs):
        super().__init__(**kwargs)
        self.field = field
        self.description = description

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        self.view.data[self.field] = role.id
        
        if self.field == "mute_role_id":
            await self.view.configure_mute_role(role)
        
        await self.view.update_embed()
        await interaction.response.defer()

class ChannelSelectCustom(ChannelSelect):
    def __init__(self, field, description, **kwargs):
        super().__init__(**kwargs)
        self.field = field
        self.description = description

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        self.view.data[self.field] = channel.id
        await self.view.update_embed()
        await interaction.response.defer()

class ButtonCustom(Button):
    async def callback(self, interaction: discord.Interaction):
        if self.label == "Back":
            self.view.current_field -= 1
        elif self.label == "Skip":
            self.view.current_field += 1
        elif self.label == "Confirm":
            if len(self.view.data) < len(self.view.required_fields):
                await interaction.response.send_message(
                    "❗ Please complete all required fields!",
                    ephemeral=True
                )
                return
            
            await self.finish_setup(interaction)
            return

        self.view.add_components()
        await self.view.update_embed()
        await interaction.response.defer()

    async def finish_setup(self, interaction: discord.Interaction):
        success = await self.view.cog.db.set_server_config(
            self.view.ctx.guild.id,
            **self.view.data
        )
        
        if not success:
            await interaction.response.send_message("❌ Failed to save configuration!", ephemeral=True)
            return

        auto_created = await self.view.handle_missing_roles(interaction)
        
        embed = discord.Embed(
            title="✅ Setup Complete",
            description="Server configuration finished!\n\n" + 
                        ("Some roles were automatically created\n" if auto_created else "") +
                        "You can modify settings anytime with `/config`",
            color=0x00ff00
        )

        for field, value in self.view.data.items():
            pretty_name = next((f[1] for f in self.view.required_fields if f[0] == field), field)
            if 'role' in field:
                embed.add_field(name=pretty_name, value=f"<@&{value}>", inline=True)
            else:
                embed.add_field(name=pretty_name, value=f"<#{value}>", inline=True)

        if 'mute_role_id' not in self.view.data:
            embed.add_field(
                name="⚠️ Important Note",
                value="Mute functionality will not work until a mute role is configured!",
                inline=False
            )

        await interaction.response.edit_message(embed=embed, view=None)
        self.view.stop()

class SetupView(View):
    def __init__(self, cog, required_fields):
        super().__init__(timeout=600)
        self.cog = cog
        self.required_fields = required_fields
        self.data = {}
        self.current_field = 0
        self.ctx = None
        self.message = None
        self.auto_create = False
        self.add_components()

    def add_components(self):
        self.clear_items()
        field_name, description = self.required_fields[self.current_field]
        
        if "role" in field_name:
            self.add_item(RoleSelectCustom(
                placeholder=f"Select {description}",
                field=field_name,
                description=description
            ))
        else:
            self.add_item(ChannelSelectCustom(
                placeholder=f"Select {description}",
                field=field_name,
                description=description
            ))
            
        if self.current_field > 0:
            self.add_item(ButtonCustom(style=ButtonStyle.grey, label="Back"))
        if self.current_field < len(self.required_fields)-1:
            self.add_item(ButtonCustom(style=ButtonStyle.grey, label="Skip"))
        self.add_item(ButtonCustom(style=ButtonStyle.green, label="Confirm"))

    async def update_embed(self):
        current_field = self.required_fields[self.current_field]
        embed = discord.Embed(
            title=f"Server Setup ({self.current_field + 1}/{len(self.required_fields)})",
            description=f"**{current_field[1]}**\n{self.get_field_description(current_field[0])}",
            color=0x5865F2
        )
        
        if self.data:
            embed.add_field(
                name="Current Selections",
                value="\n".join([f"• {self.required_fields[i][1]}: {self.format_value(k, v)}" 
                               for i, (k, v) in enumerate(self.data.items())]),
                inline=False
            )
            
        await self.message.edit(embed=embed, view=self)

    def format_value(self, field, value):
        if "role" in field:
            return f"<@&{value}>"
        return f"<#{value}>"

    def get_field_description(self, field_name):
        descriptions = {
            "mod_role_id": "The role for basic moderators (can warn and mute)",
            "admin_role_id": "The role for server administrators",
            "elder_role_id": "The role for experienced community members",
            "mute_role_id": "The role used for muted members\n(I'll create one if needed)",
            "compliance_role_id": "The role for content reviewers",
            "afk_channel_id": "Channel for inactive members",
            "log_channel_id": "Where moderation actions will be logged"
        }
        return descriptions.get(field_name, "Required for server configuration")

    async def handle_missing_roles(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛠 Auto-Role Setup",
            description="I can create missing roles with recommended settings:",
            color=0x5865F2
        )
        
        role_definitions = {
            'mute_role_id': {
                'name': 'Muted',
                'color': 0x808080,
                'permissions': Permissions.none(),
                'description': "Will automatically configure channel permissions"
            },
            'mod_role_id': {
                'name': 'Moderator',
                'color': 0x00ff00,
                'permissions': Permissions(
                    manage_messages=True, 
                    kick_members=True, 
                    ban_members=False
                )
            }
        }

        needed_roles = []
        for field, _ in self.required_fields:
            if field in role_definitions and field not in self.data:
                needed_roles.append((field, role_definitions[field]))

        if not needed_roles:
            return True

        embed.add_field(
            name="Recommended Roles",
            value="\n".join([f"• **{rd['name']}** - {rd.get('description', '')}" 
                   for _, rd in needed_roles]),
            inline=False
        )

        choice_view = RoleCreationChoice()
        await interaction.followup.send(embed=embed, view=choice_view, ephemeral=True)
        await choice_view.wait()

        if choice_view.choice == "all":
            created_roles = {}
            for field, rd in needed_roles:
                try:
                    role = discord.utils.get(self.ctx.guild.roles, name=rd['name'])
                    if not role:
                        role = await self.ctx.guild.create_role(
                            name=rd['name'],
                            color=rd['color'],
                            permissions=rd['permissions'],
                            reason="Auto-created during setup"
                        )
                    
                    if field == 'mute_role_id':
                        await self.configure_mute_role(role)
                    
                    created_roles[field] = role.id
                except discord.HTTPException as e:
                    print(f"Error creating role: {e}")
                    continue
            
            self.data.update(created_roles)
            await self.cog.db.set_server_config(self.ctx.guild.id, **self.data)
            
            confirm_embed = discord.Embed(
                title="✅ Auto-Created Roles",
                description="Successfully created recommended roles:",
                color=0x00ff00
            )
            for field, rid in created_roles.items():
                role = self.ctx.guild.get_role(rid)
                if role:
                    confirm_embed.add_field(
                        name=role_definitions[field]['name'],
                        value=role.mention,
                        inline=True
                    )
            await interaction.followup.send(embed=confirm_embed, ephemeral=True)
            return True
        
        return False

    async def configure_mute_role(self, role: discord.Role):
        try:
            positions = [r.position for r in self.ctx.guild.roles]
            new_position = min(max(positions) - 2, 1)
            await role.edit(position=new_position)
            
            for channel in self.ctx.guild.channels:
                await channel.set_permissions(
                    role,
                    send_messages=False,
                    add_reactions=False,
                    create_public_threads=False,
                    create_private_threads=False,
                    send_messages_in_threads=False
                )
        except discord.HTTPException as e:
            print(f"Error configuring mute role: {e}")

# --------------------------- Moderation Cog ---------------------------

class Moderation(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot
        self.db = bot.db_handler
        self.active_setups = {}
        self._tracked_commands = ['warn', 'mute', 'unmute', 'del_warn']

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

    async def is_configured(self, guild_id: int):
        config = await self.db.get_server_config(guild_id)
        required_fields = [
            'mod_role_id', 'admin_role_id', 'elder_role_id',
            'mute_role_id', 'compliance_role_id',
            'afk_channel_id', 'log_channel_id'
        ]
        return config and all(field in config and config[field] for field in required_fields)

    @commands.hybrid_command(
        name="setup",
        description="Interactive server configuration wizard"
    )
    async def setup(self, ctx: commands.Context):
        if ctx.guild.id in self.active_setups:
            return await ctx.send("A setup session is already in progress!", ephemeral=True)
            
        required_fields = [
            ("mod_role_id", "Moderator Role"),
            ("admin_role_id", "Admin Role"),
            ("elder_role_id", "Elder Role"),
            ("mute_role_id", "Mute Role"),
            ("compliance_role_id", "Compliance Role"),
            ("afk_channel_id", "AFK Channel"),
            ("log_channel_id", "Log Channel")
        ]
        
        view = SetupView(self, required_fields)
        view.ctx = ctx
        
        embed = discord.Embed(
            title="Server Setup Wizard",
            description="Let's configure your server! Use the dropdowns below to select roles and channels.",
            color=0x5865F2
        )
        
        view.message = await ctx.send(embed=embed, view=view)
        self.active_setups[ctx.guild.id] = view

    @commands.hybrid_command(
        name="showconfig",
        description="View current server configuration"
    )
    async def show_config(self, ctx: commands.Context):
        config = await self.db.get_server_config(ctx.guild.id)
        if not config:
            return await ctx.send("❌ Server not configured!", ephemeral=True)

        embed = discord.Embed(
            title=f"{ctx.guild.name} Configuration",
            color=0x5865F2
        )
        
        role_fields = {
            "Moderator": config.get('mod_role_id'),
            "Admin": config.get('admin_role_id'),
            "Elder": config.get('elder_role_id'),
            "Mute": config.get('mute_role_id'),
            "Compliance": config.get('compliance_role_id')
        }
        
        channel_fields = {
            "AFK Channel": config.get('afk_channel_id'),
            "Log Channel": config.get('log_channel_id')
        }

        embed.add_field(
            name="Roles",
            value="\n".join([f"• {name}: {self.format_field(ctx.guild, value, 'role')}" 
                           for name, value in role_fields.items()]),
            inline=False
        )
        
        embed.add_field(
            name="Channels",
            value="\n".join([f"• {name}: {self.format_field(ctx.guild, value, 'channel')}" 
                           for name, value in channel_fields.items()]),
            inline=False
        )
        
        await ctx.send(embed=embed, ephemeral=True)

    def format_field(self, guild: discord.Guild, value: int, type_: str) -> str:
        if not value:
            return "Not set"
            
        if type_ == "role":
            role = guild.get_role(value)
            return f"{role.mention} ({role.name})" if role else "Deleted Role"
        else:
            channel = guild.get_channel(value)
            return f"{channel.mention} (#{channel.name})" if channel else "Deleted Channel"

    @commands.hybrid_command(name="config", description="Manage server configuration")
    async def config_manage(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Configuration Manager",
            description="Choose what you want to configure:",
            color=0x5865F2
        )
        
        view = View()
        view.add_item(Button(
            style=ButtonStyle.primary,
            label="Add Missing Roles",
            custom_id="create_roles"
        ))
        view.add_item(Button(
            style=ButtonStyle.secondary,
            label="Edit Existing Settings",
            custom_id="edit_settings"
        ))
        
        msg = await ctx.send(embed=embed, view=view)
        
        try:
            interaction = await self.bot.wait_for(
                "button_click",
                check=lambda i: i.user == ctx.author and i.message.id == msg.id,
                timeout=300
            )
            
            if interaction.component.custom_id == "create_roles":
                await self.handle_role_creation(interaction)
            elif interaction.component.custom_id == "edit_settings":
                await self.start_edit_mode(interaction)
                
        except TimeoutError:
            await msg.edit(view=None)

    async def handle_role_creation(self, interaction: discord.Interaction):
        config = await self.db.get_server_config(interaction.guild.id)
        view = SetupView(self, [])
        view.ctx = await self.bot.get_context(interaction.channel.send())
        view.ctx.author = interaction.user
        view.ctx.guild = interaction.guild
        
        role_definitions = {
            'mute_role_id': {
                'name': 'Muted',
                'color': 0x808080,
                'permissions': Permissions.none(),
                'description': "Will automatically configure channel permissions"
            },
            'mod_role_id': {
                'name': 'Moderator',
                'color': 0x00ff00,
                'permissions': Permissions(
                    manage_messages=True, 
                    kick_members=True, 
                    ban_members=False
                )
            }
        }

        needed_roles = []
        for field in ['mute_role_id', 'mod_role_id']:
            if not config.get(field):
                needed_roles.append((field, role_definitions[field]))

        if not needed_roles:
            await interaction.response.send_message("All recommended roles already exist!", ephemeral=True)
            return

        embed = discord.Embed(
            title="🛠 Auto-Role Setup",
            description="I can create missing roles with recommended settings:",
            color=0x5865F2
        )
        embed.add_field(
            name="Recommended Roles",
            value="\n".join([f"• **{rd['name']}** - {rd.get('description', '')}" 
                   for _, rd in needed_roles]),
            inline=False
        )

        choice_view = RoleCreationChoice()
        await interaction.response.send_message(embed=embed, view=choice_view, ephemeral=True)
        await choice_view.wait()

        if choice_view.choice == "all":
            created_roles = {}
            for field, rd in needed_roles:
                try:
                    role = discord.utils.get(interaction.guild.roles, name=rd['name'])
                    if not role:
                        role = await interaction.guild.create_role(
                            name=rd['name'],
                            color=rd['color'],
                            permissions=rd['permissions'],
                            reason="Auto-created via /config"
                        )
                    
                    if field == 'mute_role_id':
                        await view.configure_mute_role(role)
                    
                    created_roles[field] = role.id
                except discord.HTTPException as e:
                    print(f"Error creating role: {e}")
                    continue
            
            config.update(created_roles)
            await self.db.set_server_config(interaction.guild.id, **config)
            
            confirm_embed = discord.Embed(
                title="✅ Auto-Created Roles",
                description="Successfully created recommended roles:",
                color=0x00ff00
            )
            for field, rid in created_roles.items():
                role = interaction.guild.get_role(rid)
                if role:
                    confirm_embed.add_field(
                        name=role_definitions[field]['name'],
                        value=role.mention,
                        inline=True
                    )
            await interaction.followup.send(embed=confirm_embed, ephemeral=True)

    async def start_edit_mode(self, interaction: discord.Interaction):
        config = await self.db.get_server_config(interaction.guild.id)
        required_fields = [
            ("mod_role_id", "Moderator Role"),
            ("admin_role_id", "Admin Role"),
            ("elder_role_id", "Elder Role"),
            ("mute_role_id", "Mute Role"),
            ("compliance_role_id", "Compliance Role"),
            ("afk_channel_id", "AFK Channel"),
            ("log_channel_id", "Log Channel")
        ]
        
        view = SetupView(self, required_fields)
        view.ctx = await self.bot.get_context(interaction.channel.send())
        view.ctx.author = interaction.user
        view.ctx.guild = interaction.guild
        view.data = config
        
        embed = discord.Embed(
            title="Edit Server Configuration",
            description="Select the setting you want to modify:",
            color=0x5865F2
        )
        
        for field, value in config.items():
            pretty_name = next((f[1] for f in required_fields if f[0] == field), field)
            embed.add_field(
                name=pretty_name,
                value=view.format_value(field, value),
                inline=True
            )
        
        view.message = await interaction.response.send_message(embed=embed, view=view)

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

    @commands.hybrid_command()
    @app_commands.describe(member="Member to mute", reason="Reason for mute")
    async def mute(self, ctx: commands.Context, member: discord.Member, *, 
                 reason: str = "No reason provided"):
        """Mute a member"""
        config = await self.db.get_server_config(ctx.guild.id)
        if not config:
                return await ctx.send("❌ Server not configured!", ephemeral=True)
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

            if member.voice and member.voice.channel:
                await member.move_to(None, reason="Muted by moderator")

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
        await ctx.send(f"🧹 Deleted {len(deleted)} messages.", delete_after=5)
        await ctx.message.delete(delay=1)

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

    @commands.hybrid_command()
    @app_commands.describe(member="Member to unmute")
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        """Unmute a member"""
        config = await self.db.get_server_config(ctx.guild.id)
        if not config:
                return await ctx.send("❌ Server not configured!", ephemeral=True)
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

    @commands.hybrid_command()
    @app_commands.describe(member="Member to warn", reason="Reason for warning")
    async def warn(self, ctx: commands.Context, member: discord.Member, *, 
                 reason: str = "No reason provided"):
        """Warn a member"""
        config = await self.db.get_server_config(ctx.guild.id)
        if not config:
                return await ctx.send("❌ Server not configured!", ephemeral=True)
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
            
            await ctx.interaction.user.send(embed=embed)
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

    @commands.hybrid_command()
    @app_commands.describe(member="Member to check warnings for")
    async def warnings(self, ctx: commands.Context, member: discord.Member):
        """View member's warnings"""
        try:
            config = self.db.get_server_config(ctx.guild.id)
            if not config:
                return await ctx.send("❌ Server not configured!", ephemeral=True)
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

    @commands.hybrid_command()
    @app_commands.describe(
        member="Member to remove warnings from",
        count="Number of warnings to remove"
    )
    async def del_warn(self, ctx: commands.Context, member: discord.Member, count: int = 1):
        """Remove warnings from a member"""
        config = await self.db.get_server_config(ctx.guild.id)
        if not config:
                return await ctx.send("❌ Server not configured!", ephemeral=True)
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
                "3. Configure all roles\n"
                "4. Countdown System Active ✅\n"
                "5. Session System Active ✅"
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