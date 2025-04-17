import discord
from discord.ext import commands
from discord import app_commands
from core.bot import StoicBot
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class Tickets(commands.Cog):
    def __init__(self, bot: StoicBot):
        self.bot = bot
        self.persistent_views_added = False

    class TicketCreationView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            self.cooldown = commands.CooldownMapping.from_cooldown(
                1, 60, 
                lambda interaction: (interaction.user.id,)
            )

        @discord.ui.button(
            label="Create Ticket",
            style=discord.ButtonStyle.green,
            custom_id="persistent_ticket_create"
        )
        async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.handle_ticket_creation(interaction)

        async def handle_ticket_creation(self, interaction: discord.Interaction):
            """Handle ticket creation flow with cooldown"""
            bucket = self.cooldown.get_bucket(interaction)
            retry_after = bucket.update_rate_limit()
            
            if retry_after:
                return await interaction.response.send_message(
                    f"Please wait {retry_after:.1f}s before creating another ticket!",
                    ephemeral=True
                )

            try:
                guild = interaction.guild
                user = interaction.user
                bot = interaction.client  # Get bot from interaction
                
                # Get ticket configuration
                config = await bot.db_handler.get_ticket_config(guild.id)
                if not config:
                    return await interaction.response.send_message(
                        "Ticket system not configured!",
                        ephemeral=True
                    )

                # Check existing open tickets
                open_tickets = await bot.db_handler.get_user_tickets(
                    guild_id=guild.id,
                    user_id=user.id,
                    status="open"
                )
                
                if len(open_tickets) >= config['max_tickets']:
                    return await interaction.response.send_message(
                        f"You already have {config['max_tickets']} open tickets!",
                        ephemeral=True
                    )

                # Create channels
                category = guild.get_channel(config['category_id'])
                ticket_number = len(await bot.db_handler.get_guild_tickets(guild.id)) + 1
                
                # Create voice channel
                vc = await guild.create_voice_channel(
                    name=f"ticket-{user.display_name}-{ticket_number}",
                    category=category,
                    reason=f"Ticket system: {user.name}"
                )
                
                # Create text channel
                text_channel = await guild.create_text_channel(
                    name=f"ticket-{user.display_name}-{ticket_number}",
                    category=category,
                    overwrites={
                        guild.default_role: discord.PermissionOverwrite(read_messages=False),
                        user: discord.PermissionOverwrite(
                            read_messages=True,
                            send_messages=True,
                            attach_files=True
                        ),
                        guild.get_role(config['support_role_id']): discord.PermissionOverwrite(
                            read_messages=True,
                            manage_messages=True,
                            manage_channels=True
                        )
                    },
                    reason=f"Ticket system: {user.name}"
                )

                # Create database record
                ticket_id = await bot.db_handler.create_ticket(
                    guild_id=guild.id,
                    user_id=user.id,
                    vc_id=vc.id,
                    channel_id=text_channel.id
                )

                # Send welcome message
                embed = discord.Embed(
                    title=f"Ticket #{ticket_id}",
                    description=f"Hello {user.mention}! Support will be with you shortly.\n"
                              "Please describe your issue in detail below.",
                    color=0x00ff00
                )
                await text_channel.send(
                    embed=embed,
                    view=Tickets.TicketControlsView()
                )

                await interaction.response.send_message(
                    f"Ticket created: {text_channel.mention}",
                    ephemeral=True
                )

                # Log creation
                if config['log_channel_id']:
                    log_channel = guild.get_channel(config['log_channel_id'])
                    if log_channel:
                        log_embed = discord.Embed(
                            title="New Ticket Created",
                            color=0x00ff00,
                            timestamp=datetime.now()
                        )
                        log_embed.add_field(name="User", value=user.mention)
                        log_embed.add_field(name="Voice Channel", value=vc.mention)
                        log_embed.add_field(name="Text Channel", value=text_channel.mention)
                        await log_channel.send(embed=log_embed)

            except Exception as e:
                logger.error(f"Ticket creation error: {e}", exc_info=True)
                await interaction.response.send_message(
                    "Failed to create ticket. Please contact staff.",
                    ephemeral=True
                )

    class TicketControlsView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(
            label="Close Ticket",
            style=discord.ButtonStyle.red,
            custom_id="persistent_ticket_close"
        )
        async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
                bot = interaction.client  # Get bot from interaction
                
                # Get ticket info
                ticket = await bot.db_handler.get_ticket_by_channel(interaction.channel.id)
                if not ticket:
                    return await interaction.response.send_message(
                        "This channel is not a ticket!",
                        ephemeral=True
                    )

                # Update database
                await bot.db_handler.close_ticket(
                    channel_id=interaction.channel.id,
                    reason="Closed by user"
                )

                # Delete channels
                vc = interaction.guild.get_channel(ticket['vc_id'])
                if vc:
                    await vc.delete(reason="Ticket closed")
                await interaction.channel.delete(reason="Ticket closed")

                await interaction.response.send_message(
                    "Ticket closed successfully!",
                    ephemeral=True
                )

                # Log closure
                config = await bot.db_handler.get_ticket_config(interaction.guild.id)
                if config and config['log_channel_id']:
                    log_channel = interaction.guild.get_channel(config['log_channel_id'])
                    if log_channel:
                        log_embed = discord.Embed(
                            title="Ticket Closed",
                            color=0xff0000,
                            timestamp=datetime.now()
                        )
                        log_embed.add_field(name="Closed by", value=interaction.user.mention)
                        log_embed.add_field(name="Ticket ID", value=ticket['ticket_id'])
                        await log_channel.send(embed=log_embed)

            except Exception as e:
                logger.error(f"Ticket closure error: {e}", exc_info=True)
                await interaction.response.send_message(
                    "Failed to close ticket. Please contact staff.",
                    ephemeral=True
                )

    async def cog_load(self):
        """Register persistent views on cog load"""
        if not self.persistent_views_added:
            self.bot.add_view(self.TicketCreationView())
            self.bot.add_view(self.TicketControlsView())
            self.persistent_views_added = True
            logger.info("Persistent ticket views registered")

    @app_commands.command(name="setup_tickets")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        channel="Channel to send ticket panel",
        category="Category for ticket channels",
        support_role="Support team role",
        log_channel="Ticket log channel",
        max_tickets="Max open tickets per user (default 3)"
    )
    async def setup_tickets(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        category: discord.CategoryChannel,
        support_role: discord.Role,
        log_channel: discord.TextChannel,
        max_tickets: int = 3
    ):
        """Initialize the ticket system in this server"""
        try:
            # Save configuration
            await self.bot.db_handler.set_ticket_config(
                guild_id=interaction.guild.id,
                category_id=category.id,
                support_role_id=support_role.id,
                log_channel_id=log_channel.id,
                max_tickets=max_tickets
            )

            # Create panel embed
            embed = discord.Embed(
                title="Support Tickets",
                description="Click the button below to create a new support ticket!",
                color=0x00ff00
            )
            
            # Send panel with persistent button
            await channel.send(embed=embed, view=self.TicketCreationView())
            
            await interaction.response.send_message(
                "Ticket system setup complete!",
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Ticket setup error: {e}", exc_info=True)
            await interaction.response.send_message(
                "Failed to configure ticket system. Check bot permissions.",
                ephemeral=True
            )

    @app_commands.command(name="add_to_ticket")
    @app_commands.describe(user="User to add to this ticket")
    async def add_to_ticket(self, interaction: discord.Interaction, user: discord.Member):
        """Add a user to the current ticket"""
        try:
            # Verify this is a ticket channel
            ticket = await self.bot.db_handler.get_ticket_by_channel(interaction.channel.id)
            if not ticket:
                return await interaction.response.send_message(
                    "This is not a ticket channel!",
                    ephemeral=True
                )

            # Update permissions
            await interaction.channel.set_permissions(
                user,
                read_messages=True,
                send_messages=True,
                reason=f"Added by {interaction.user}"
            )

            # Get associated voice channel
            vc = interaction.guild.get_channel(ticket['vc_id'])
            if vc:
                await vc.set_permissions(
                    user,
                    connect=True,
                    speak=True,
                    reason=f"Added by {interaction.user}"
                )

            await interaction.response.send_message(
                f"Added {user.mention} to the ticket",
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Add user error: {e}", exc_info=True)
            await interaction.response.send_message(
                "Failed to add user to ticket",
                ephemeral=True
            )

async def setup(bot: StoicBot):
    await bot.add_cog(Tickets(bot))