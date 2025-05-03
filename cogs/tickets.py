import os
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks
import logging
import asyncio
import datetime
import random
from typing import List, Dict, Optional, Any, Tuple
import traceback

from core.ticket_db import (
    get_server_config, save_server_config, get_ticket_types, get_ticket_type,
    save_ticket_type, delete_ticket_type, create_ticket,
    get_ticket_by_channel, claim_ticket, close_ticket,
    check_user_cooldown, delete_ticket_record, set_modal_questions,
    set_ticket_category, get_ticket_category
)
from core.transcript import TranscriptGenerator
from core.github_api import GitHubAPI

logger = logging.getLogger(__name__)

# Default ticket cooldown in seconds (5 minutes)
DEFAULT_COOLDOWN = 300

class TicketSetupModal(discord.ui.Modal):
    """Modal for configuring a ticket type"""
    
    def __init__(self, ticket_type_id: Optional[int] = None, existing_data: Optional[Dict[str, Any]] = None):
        super().__init__(title="Ticket Type Configuration")
        self.ticket_type_id = ticket_type_id
        
        # Create the input fields
        self.name = discord.ui.TextInput(
            label="Ticket Type Name",
            placeholder="e.g. Help Desk, Ban Appeal",
            required=True,
            max_length=100,
            default=existing_data.get("name", "") if existing_data else ""
        )
        self.add_item(self.name)
        
        self.description = discord.ui.TextInput(
            label="Description",
            placeholder="Describe what this ticket type is for",
            required=True,
            max_length=1000,
            style=discord.TextStyle.paragraph,
            default=existing_data.get("description", "") if existing_data else ""
        )
        self.add_item(self.description)
        
        # We'll handle roles separately in the UI

    async def on_submit(self, interaction: discord.Interaction):
        # This will be handled by the calling code
        await interaction.response.defer()


class TicketView(discord.ui.View):
    """View for ticket creation dropdown"""
    
    def __init__(self, ticket_types: List[Dict[str, Any]]):
        super().__init__(timeout=None)  # Persistent view
        
        # Add the ticket creation dropdown
        self.add_item(TicketTypeSelect(ticket_types))


class TicketTypeSelect(discord.ui.Select):
    """Dropdown for selecting ticket types"""
    
    def __init__(self, ticket_types: List[Dict[str, Any]]):
        options = []
        
        for ticket_type in ticket_types:
            options.append(
                discord.SelectOption(
                    label=ticket_type["name"],
                    description=ticket_type["description"][:100],  # Truncate if too long
                    value=str(ticket_type["id"])
                )
            )
        
        super().__init__(
            placeholder="Select a ticket type...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        ticket_type_id = int(self.values[0])
        
        # Check if user is on cooldown
        if not check_user_cooldown(interaction.user.id, interaction.guild_id, DEFAULT_COOLDOWN):
            remaining_time = await self.get_remaining_cooldown(interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(
                f"You're creating tickets too quickly! Please wait {remaining_time} seconds before creating another ticket.",
                ephemeral=True
            )
            return
        
        # Get the ticket type info
        selected_type = get_ticket_type(interaction.guild_id, ticket_type_id)
        
        if not selected_type:
            await interaction.response.send_message(
                "Error: Ticket type not found. Please try again or contact an administrator.",
                ephemeral=True
            )
            return
        
        # Check if modal is enabled for this ticket type
        modal_responses = None
        if selected_type.get('modal_enabled', False) and selected_type.get('modal_questions'):
            # Show modal to collect information
            modal = TicketModal(
                ticket_type_name=selected_type['name'],
                questions=selected_type['modal_questions']
            )
            
            # Send modal
            await interaction.response.send_modal(modal)
            
            # Wait for modal submission
            timed_out = await modal.wait()
            
            if timed_out:
                # User didn't submit the modal
                return
            
            # Get responses from the modal
            modal_responses = modal.get_responses()
        else:
            # No modal, defer response
            await interaction.response.defer(ephemeral=True, thinking=True)
        
        try:
            # Create a new ticket channel
            channel = await self.create_ticket_channel(interaction, selected_type, modal_responses)
            
            if channel:
                await interaction.followup.send(
                    f"Ticket created! Please go to {channel.mention} to discuss your issue.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "Failed to create ticket channel. Please try again later or contact an administrator.",
                    ephemeral=True
                )
        
        except Exception as e:
            logger.error(f"Error creating ticket: {e}", exc_info=True)
            await interaction.followup.send(
                "An error occurred while creating your ticket. Please try again later.",
                ephemeral=True
            )
    
    async def get_remaining_cooldown(self, user_id: int, server_id: int) -> int:
        """Get remaining cooldown time in seconds"""
        # This is a rough estimate - the actual check is done in the database
        return DEFAULT_COOLDOWN
    
    async def create_ticket_channel(
        self, 
        interaction: discord.Interaction, 
        ticket_type: Dict[str, Any],
        modal_responses: Optional[Dict[str, str]] = None
    ) -> Optional[discord.TextChannel]:
        """Create a new ticket channel for the user"""
        guild = interaction.guild
        if not guild:
            return None
        
        # Get server config
        server_config = get_server_config(guild.id)
        if not server_config:
            return None
        
        # Create channel name: ticket-username-number
        next_number = server_config.get("ticket_counter", 0) + 1
        channel_name = f"ticket-{interaction.user.name}-{next_number}"
        
        # Set up permissions
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(
                read_messages=True, 
                send_messages=True,
                attach_files=True,
                embed_links=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True
            )
        }
        
        # Add permissions for assigned roles
        assigned_role_ids = ticket_type.get("assigned_roles", [])
        for role_id in assigned_role_ids:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    attach_files=True,
                    embed_links=True
                )
        
        # Create the channel, using the configured category if available
        try:
            # Check if there's a configured ticket category
            category_id = get_ticket_category(guild.id)
            category = None
            
            if category_id:
                category = guild.get_channel(category_id)
            
            channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                category=category,
                topic=f"Ticket for {interaction.user.display_name} | Type: {ticket_type['name']}"
            )
            
            # Store ticket in database
            ticket_id, ticket_number = create_ticket(
                guild.id,
                channel.id,
                interaction.user.id,
                ticket_type["id"]
            )
            
            if ticket_id == -1:
                # Failed to create ticket in database
                await channel.delete()
                return None
            
            # Send initial message with ticket information
            description = f"Thank you for creating a ticket, {interaction.user.mention}!\n\n" \
                          f"**Type:** {ticket_type['name']}\n" \
                          f"**Description:** {ticket_type['description']}\n\n"
            
            # Add modal responses if provided
            if modal_responses:
                description += "**Additional Information:**\n"
                for question, answer in modal_responses.items():
                    # Add each question and answer to the description
                    description += f"**{question}**\n{answer}\n\n"
            
            description += "A staff member will assist you shortly. Please describe your issue in detail."
            
            embed = discord.Embed(
                title=f"Ticket #{ticket_number}: {ticket_type['name']}",
                description=description,
                color=discord.Color.blue(),
                timestamp=datetime.datetime.now()
            )
            
            embed.set_footer(text=f"Ticket ID: {ticket_id}")
            
            # Create buttons for ticket management
            ticket_controls = TicketControls()
            
            # Send initial message and ping assigned roles
            message = await channel.send(
                content=self._format_role_pings(guild, assigned_role_ids),
                embed=embed,
                view=ticket_controls
            )
            
            # Pin the message
            await message.pin()
            
            return channel
        
        except Exception as e:
            logger.error(f"Failed to create ticket channel: {e}", exc_info=True)
            return None
    
    def _format_role_pings(self, guild: discord.Guild, role_ids: List[int]) -> str:
        """Format pings for assigned roles"""
        pings = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role and not role.is_default():
                pings.append(role.mention)
        
        if pings:
            return f"Support team: {' '.join(pings)}"
        return "No support team has been assigned to this ticket type."


class TicketModal(discord.ui.Modal):
    """Modal for ticket creation with custom questions"""
    
    def __init__(self, ticket_type_name: str, questions: List[str]):
        super().__init__(title=f"{ticket_type_name} - Additional Information")
        self.questions = questions
        self.responses = {}
        
        # Add text inputs for each question (up to 5 max that Discord allows)
        for i, question in enumerate(questions[:5]):
            text_input = discord.ui.TextInput(
                label=question[:45],  # Limit label length to stay within Discord's limits
                placeholder="Your answer here...",
                required=True,
                style=discord.TextStyle.paragraph
            )
            self.add_item(text_input)
            # Store the input with its question for later retrieval
            setattr(self, f"question_{i}", text_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        """When user submits the modal"""
        await interaction.response.defer(ephemeral=True)
        
        # Store responses
        for i, question in enumerate(self.questions[:5]):
            text_input = getattr(self, f"question_{i}")
            self.responses[question] = text_input.value
    
    def get_responses(self) -> Dict[str, str]:
        """Get user responses as dictionary"""
        return self.responses


class TicketControls(discord.ui.View):
    """Buttons for ticket management"""
    
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view
    
    @discord.ui.button(
        label="Claim Ticket", 
        style=discord.ButtonStyle.primary,
        custom_id="claim_ticket"
    )
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Claim a ticket to indicate you're handling it"""
        channel = interaction.channel
        if not channel or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "This can only be used in ticket channels.",
                ephemeral=True
            )
            return
        
        # Check if this is a ticket channel
        ticket_data = get_ticket_by_channel(channel.id)
        if not ticket_data:
            await interaction.response.send_message(
                "This channel is not a ticket channel.",
                ephemeral=True
            )
            return
        
        # Check if ticket is already claimed
        if ticket_data.get("claimed_by"):
            claimed_by = interaction.guild.get_member(ticket_data["claimed_by"])
            claimed_name = claimed_by.display_name if claimed_by else f"Unknown ({ticket_data['claimed_by']})"
            
            await interaction.response.send_message(
                f"This ticket has already been claimed by {claimed_name}.",
                ephemeral=True
            )
            return
        
        # Claim the ticket
        success = claim_ticket(channel.id, interaction.user.id)
        
        if success:
            embed = discord.Embed(
                title="Ticket Claimed",
                description=f"{interaction.user.mention} has claimed this ticket and will be assisting you.",
                color=discord.Color.green(),
                timestamp=datetime.datetime.now()
            )
            
            await interaction.response.send_message(embed=embed)
            
            # Update channel topic
            await channel.edit(
                topic=f"{channel.topic} | Claimed by {interaction.user.display_name}"
            )
        else:
            await interaction.response.send_message(
                "Failed to claim ticket. Please try again.",
                ephemeral=True
            )
    
    @discord.ui.button(
        label="Close Ticket", 
        style=discord.ButtonStyle.danger,
        custom_id="close_ticket"
    )
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Close the ticket and generate a transcript"""
        channel = interaction.channel
        if not channel or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "This can only be used in ticket channels.",
                ephemeral=True
            )
            return
        
        # Check if this is a ticket channel
        ticket_data = get_ticket_by_channel(channel.id)
        if not ticket_data:
            await interaction.response.send_message(
                "This channel is not a ticket channel.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(thinking=True)
        
        try:
            # Close the ticket
            updated_ticket_data = close_ticket(channel.id)
            
            if not updated_ticket_data:
                await interaction.followup.send(
                    "Failed to close ticket. Please try again.",
                    ephemeral=True
                )
                return
            
            # Send closing message
            closing_embed = discord.Embed(
                title="Ticket Closing",
                description="This ticket is now being closed. Generating transcript and deleting channel in 10 seconds...",
                color=discord.Color.orange(),
                timestamp=datetime.datetime.now()
            )
            
            await interaction.followup.send(embed=closing_embed)
            
            # Generate transcript
            transcript_generator = TranscriptGenerator()
            transcript_html = await transcript_generator.generate_transcript(channel, updated_ticket_data)
            
            # Get creation timestamp from ticket data
            created_at = updated_ticket_data.get('created_at', datetime.datetime.now().isoformat())
            if isinstance(created_at, str):
                try:
                    # Try to parse the timestamp
                    created_at = datetime.datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                except ValueError:
                    created_at = datetime.datetime.now()
            
            # Upload transcript to GitHub - no need to pass repo name as it's hardcoded
            success, url = await transcript_generator.upload_transcript(
                transcript_html,
                updated_ticket_data["server_id"],
                updated_ticket_data["ticket_number"],
                updated_ticket_data  # Pass the entire ticket data instead of just the repo
            )
            
            if success and url:
                # Get the transcript channel
                transcript_channel_id = updated_ticket_data.get("transcript_channel_id")
                if transcript_channel_id:
                    transcript_channel = interaction.guild.get_channel(transcript_channel_id)
                    
                    if transcript_channel and isinstance(transcript_channel, discord.TextChannel):
                        # Create detailed embed for transcript log
                        log_embed = discord.Embed(
                            title=f"Ticket #{updated_ticket_data['ticket_number']} Closed",
                            description=(
                                f"**Ticket:** {updated_ticket_data['type_name']}\n"
                                f"**Created by:** <@{updated_ticket_data['creator_id']}> (ID: {updated_ticket_data['creator_id']})\n"
                                f"**Closed by:** {interaction.user.mention} (ID: {interaction.user.id})\n"
                                f"**Created:** <t:{int(created_at.timestamp())}:F>\n"
                                f"**Closed:** <t:{int(datetime.datetime.now().timestamp())}:F>"
                            ),
                            color=discord.Color.blue(),
                            timestamp=datetime.datetime.now()
                        )
                        
                        log_embed.add_field(
                            name="Ticket Type",
                            value=updated_ticket_data['type_name'],
                            inline=True
                        )
                        
                        if updated_ticket_data.get('claimed_by'):
                            log_embed.add_field(
                                name="Claimed By",
                                value=f"<@{updated_ticket_data['claimed_by']}>",
                                inline=True
                            )
                            
                        # Always add transcript link if available
                        log_embed.add_field(
                            name="Transcript",
                            value=f"[View Transcript]({url})",
                            inline=False
                        )
                        
                        # Add footer with timestamp info
                        log_embed.set_footer(text=f"Ticket ID: {updated_ticket_data['id']} • Server ID: {updated_ticket_data['server_id']}")
                        
                        await transcript_channel.send(embed=log_embed)
            
            # Wait before deleting
            await asyncio.sleep(5)
            
            # Delete the channel
            await channel.delete()
            
            # Remove from active tickets database
            delete_ticket_record(channel.id)
        
        except Exception as e:
            logger.error(f"Error closing ticket: {e}", exc_info=True)
            await interaction.followup.send(
                "An error occurred while closing the ticket. Please try again.",
                ephemeral=True
            )


class Tickets(commands.Cog):
    """Ticket system cog for Discord bot"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register persistent views when cog is loaded
        self.register_persistent_views.start()
    
    def cog_unload(self):
        self.register_persistent_views.cancel()
    
    @tasks.loop(count=1)
    async def register_persistent_views(self):
        """Register persistent views after the bot is ready"""
        await self.bot.wait_until_ready()
        
        # Register ticket controls
        self.bot.add_view(TicketControls())
        
        # We can't register TicketView here because it needs dynamic ticket types
        # These will be registered per-server when loading ticket panels
    
    @app_commands.command(name="ticketsetup", description="Configure the ticket system for this server")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_setup(self, interaction: discord.Interaction):
        """Setup command for the ticket system"""
        try:
            # Check if server config exists first
            server_config = get_server_config(interaction.guild_id)
            
            # Create embed for setup instructions
            embed = discord.Embed(
                title="Ticket System Setup",
                description="This command will guide you through setting up the ticket system for your server.",
                color=discord.Color.blue()
            )
            
            # Create setup menu
            view = TicketSetupMenu(server_config)
            
            # Send response immediately without deferring
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in ticket_setup: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while setting up the ticket system. Please try again.",
                    ephemeral=True
                )
    
    @app_commands.command(name="createticketpanel", description="Create a ticket panel in the current channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def create_ticket_panel(self, interaction: discord.Interaction):
        """Create a ticket panel in the current channel"""
        await interaction.response.defer(ephemeral=True)
        
        # Check if server is configured
        server_config = get_server_config(interaction.guild_id)
        if not server_config:
            await interaction.followup.send(
                "You need to set up the ticket system first using `/ticketsetup`.",
                ephemeral=True
            )
            return
        
        # Get ticket types
        ticket_types = get_ticket_types(interaction.guild_id)
        if not ticket_types:
            await interaction.followup.send(
                "You need to create at least one ticket type first using `/ticketsetup`.",
                ephemeral=True
            )
            return
        
        # Create the ticket panel embed
        embed = discord.Embed(
            title="🎫 Create a Ticket",
            description="Select a ticket type from the dropdown menu below to create a new support ticket.",
            color=discord.Color.blue()
        )
        
        # Add info about each ticket type
        for ticket_type in ticket_types:
            embed.add_field(
                name=ticket_type["name"],
                value=ticket_type["description"],
                inline=False
            )
        
        # Create the ticket view with the dropdown
        view = TicketView(ticket_types)
        
        # Send the panel
        await interaction.channel.send(embed=embed, view=view)
        
        await interaction.followup.send("Ticket panel created successfully!", ephemeral=True)


class TicketSetupMenu(discord.ui.View):
    """Simplified menu for ticket system setup"""
    
    def __init__(self, server_config: Optional[Dict[str, Any]]):
        super().__init__(timeout=600)  # 10 minute timeout
        self.server_config = server_config
    
    @discord.ui.button(label="Configure Server Settings", style=discord.ButtonStyle.primary)
    async def configure_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Configure server settings for the ticket system"""
        await interaction.response.defer(ephemeral=True)
        
        # Create embed for server configuration
        embed = discord.Embed(
            title="Server Configuration",
            description="Configure the basic settings for the ticket system.",
            color=discord.Color.blue()
        )
        
        # Create server config view
        view = ServerConfigView(self.server_config)
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="Manage Ticket Types", style=discord.ButtonStyle.primary)
    async def manage_ticket_types(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Manage ticket types"""
        await interaction.response.defer(ephemeral=True)
        
        # Check if server is configured first
        if not self.server_config:
            await interaction.followup.send(
                "You need to configure server settings first!",
                ephemeral=True
            )
            return
        
        # Get ticket types
        ticket_types = get_ticket_types(interaction.guild_id)
        
        # Create embed for ticket type management
        embed = discord.Embed(
            title="Ticket Type Management",
            description="Create, edit, or delete ticket types for your server.",
            color=discord.Color.blue()
        )
        
        if ticket_types:
            for ticket_type in ticket_types:
                embed.add_field(
                    name=ticket_type["name"],
                    value=ticket_type["description"][:100] + "..." if len(ticket_type["description"]) > 100 else ticket_type["description"],
                    inline=False
                )
        else:
            embed.add_field(
                name="No Ticket Types",
                value="You haven't created any ticket types yet. Click 'Create Ticket Type' to get started.",
                inline=False
            )
        
        # Create ticket type management view
        view = TicketTypeManagementView(interaction.guild_id, ticket_types)
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="Test GitHub Connection", style=discord.ButtonStyle.secondary)
    async def test_github(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Test GitHub API connection"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # Check if server is configured
        if not self.server_config or not self.server_config.get("github_repo"):
            await interaction.followup.send(
                "You need to configure server settings with a GitHub repository first!",
                ephemeral=True
            )
            return
        
        # Test GitHub connection
        github_api = GitHubAPI(repo=self.server_config["github_repo"])
        
        # Check if repo exists
        repo_exists = await github_api.check_repo_exists()
        if not repo_exists:
            await interaction.followup.send(
                f"❌ GitHub repository not found: {self.server_config['github_repo']}\n"
                "Please check the repository name and your GitHub token.",
                ephemeral=True
            )
            return
        
        # Check if GitHub Pages is enabled
        pages_enabled = await github_api.check_pages_enabled()
        
        embed = discord.Embed(
            title="GitHub Connection Test",
            color=discord.Color.green() if repo_exists else discord.Color.red()
        )
        
        embed.add_field(
            name="Repository",
            value=f"{self.server_config['github_repo']} {'✅' if repo_exists else '❌'}",
            inline=False
        )
        
        embed.add_field(
            name="GitHub Pages",
            value=f"{'✅ Enabled' if pages_enabled else '⚠️ Not enabled or not detected'}",
            inline=False
        )
        
        if repo_exists:
            embed.add_field(
                name="Transcript URL Format",
                value=f"https://{github_api.username}.github.io/{self.server_config['github_repo'].split('/')[-1]}/tickets/server_id/ticket_id.html",
                inline=False
            )
        
        embed.add_field(
            name="Note",
            value="If GitHub Pages is not enabled, please enable it in your repository settings.\n"
                 "Set the source to the main branch and the folder to / (root).",
            inline=False
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)


class ServerConfigView(discord.ui.View):
    """View for server configuration"""
    
    def __init__(self, server_config: Optional[Dict[str, Any]]):
        super().__init__(timeout=300)  # 5 minute timeout
        self.server_config = server_config
    
    @discord.ui.button(label="Set Admin Roles", style=discord.ButtonStyle.primary)
    async def set_admin_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Set admin roles for the ticket system"""
        await interaction.response.defer(ephemeral=True)
        
        # Create role selection view
        view = RoleSelectionView(
            title="Select Admin Roles",
            description="Select roles that can manage tickets and the ticket system",
            current_roles=self.server_config.get("admin_roles", []) if self.server_config else []
        )
        
        # Send role selection view
        message = await interaction.followup.send(
            "Select roles that should have admin access to the ticket system:",
            view=view,
            ephemeral=True
        )
        
        # Wait for the selection to be done
        timed_out = await view.wait()
        
        if timed_out:
            await message.edit(content="Role selection timed out.", view=None)
            return
        
        # Get the selected roles
        selected_roles = view.selected_roles
        
        # Update server config
        server_config = get_server_config(interaction.guild_id)
        
        if server_config:
            save_server_config(
                interaction.guild_id,
                selected_roles,
                server_config.get("transcript_channel_id", 0),
                server_config.get("github_repo", "")
            )
        else:
            save_server_config(
                interaction.guild_id,
                selected_roles,
                0,
                ""
            )
        
        # Update stored config
        self.server_config = get_server_config(interaction.guild_id)
        
        await message.edit(
            content=f"Admin roles updated! Selected {len(selected_roles)} roles.",
            view=None
        )
    
    @discord.ui.button(label="Set Transcript Channel", style=discord.ButtonStyle.primary)
    async def set_transcript_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Set the channel for ticket transcripts"""
        await interaction.response.defer(ephemeral=True)
        
        # Create channel selection view
        view = ChannelSelectionView(
            title="Select Transcript Channel",
            description="Select a channel where ticket transcripts will be sent"
        )
        
        # Send channel selection view
        message = await interaction.followup.send(
            "Select a channel where ticket transcripts will be sent:",
            view=view,
            ephemeral=True
        )
        
        # Wait for the selection to be done
        timed_out = await view.wait()
        
        if timed_out:
            await message.edit(content="Channel selection timed out.", view=None)
            return
        
        # Get the selected channel
        selected_channel = view.selected_channel
        
        if not selected_channel:
            await message.edit(content="No channel selected.", view=None)
            return
        
        # Update server config
        server_config = get_server_config(interaction.guild_id)
        
        if server_config:
            save_server_config(
                interaction.guild_id,
                server_config.get("admin_roles", []),
                selected_channel.id,
                server_config.get("github_repo", "")
            )
        else:
            save_server_config(
                interaction.guild_id,
                [],
                selected_channel.id,
                ""
            )
        
        # Update stored config
        self.server_config = get_server_config(interaction.guild_id)
        
        await message.edit(
            content=f"Transcript channel set to {selected_channel.mention}!",
            view=None
        )
    
    @discord.ui.button(label="Set Ticket Category", style=discord.ButtonStyle.primary)
    async def set_ticket_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Set the category where tickets will be created"""
        await interaction.response.defer(ephemeral=True)
        
        # Create category selection view (using channel select with CategoryChannel type)
        view = CategorySelectionView(
            title="Select Ticket Category",
            description="Select a category where all tickets will be created"
        )
        
        # Send category selection view
        message = await interaction.followup.send(
            "Select a category where all new tickets will be created:",
            view=view,
            ephemeral=True
        )
        
        # Wait for the selection to be done
        timed_out = await view.wait()
        
        if timed_out:
            await message.edit(content="Category selection timed out.", view=None)
            return
        
        # Get the selected category
        selected_category = view.selected_category
        
        if not selected_category:
            await message.edit(content="No category selected.", view=None)
            return
        
        # Update server config
        server_config = get_server_config(interaction.guild_id)
        
        if server_config:
            save_server_config(
                interaction.guild_id,
                server_config.get("admin_roles", []),
                server_config.get("transcript_channel_id", 0),
                server_config.get("github_repo", ""),
                selected_category.id
            )
        else:
            save_server_config(
                interaction.guild_id,
                [],
                0,
                "",
                selected_category.id
            )
        
        # Update stored config
        self.server_config = get_server_config(interaction.guild_id)
        
        await message.edit(
            content=f"Ticket category set to {selected_category.name}!",
            view=None
        )


class GitHubRepoModal(discord.ui.Modal):
    """Modal for entering GitHub repository information"""
    
    def __init__(self, default_repo: str = ""):
        super().__init__(title="GitHub Repository Configuration")
        
        self.repo_input = discord.ui.TextInput(
            label="GitHub Repository",
            placeholder="username/repository",
            default=default_repo,
            required=True,
            max_length=100
        )
        self.add_item(self.repo_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission"""
        await interaction.response.defer(ephemeral=True)
        
        # Get repository name
        repo_name = self.repo_input.value.strip()
        
        # Validate repository format
        if "/" not in repo_name:
            await interaction.followup.send(
                "Invalid repository format. Please use the format `username/repository`.",
                ephemeral=True
            )
            return
        
        # Update server config
        server_config = get_server_config(interaction.guild_id)
        
        if server_config:
            save_server_config(
                interaction.guild_id,
                server_config.get("admin_roles", []),
                server_config.get("transcript_channel_id", 0),
                repo_name
            )
        else:
            save_server_config(
                interaction.guild_id,
                [],
                0,
                repo_name
            )
        
        await interaction.followup.send(
            f"GitHub repository set to `{repo_name}`!\n\n"
            "Please make sure:\n"
            "1. The repository exists\n"
            "2. The bot's GitHub token has access to it\n"
            "3. GitHub Pages is enabled for the repository\n\n"
            "Use the 'Test GitHub Connection' button to verify.",
            ephemeral=True
        )


class RoleSelectionView(discord.ui.View):
    """View for selecting multiple roles"""
    
    def __init__(self, title: str, description: str, current_roles: List[int] = None):
        super().__init__(timeout=120)  # 2 minute timeout
        self.title = title
        self.description = description
        self.selected_roles = current_roles or []
        self.add_item(RoleSelect(self.selected_roles))
    
    @discord.ui.button(label="Done", style=discord.ButtonStyle.success)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Finish role selection"""
        await interaction.response.defer(ephemeral=True)
        self.stop()


class RoleSelect(discord.ui.RoleSelect):
    """Role select menu for ticket configuration"""
    
    def __init__(self, selected_roles: List[int]):
        super().__init__(
            placeholder="Select roles...",
            min_values=0,
            max_values=25  # Discord limit
        )
        self.selected_role_ids = selected_roles
    
    async def callback(self, interaction: discord.Interaction):
        """Handle role selection"""
        await interaction.response.defer(ephemeral=True)
        
        # Update the selected roles
        self.view.selected_roles = [role.id for role in self.values]
        
        # Show the currently selected roles
        role_mentions = [f"<@&{role_id}>" for role_id in self.view.selected_roles]
        role_text = ", ".join(role_mentions) if role_mentions else "None"
        
        await interaction.followup.send(
            f"Selected roles: {role_text}\n\nClick 'Done' when finished.",
            ephemeral=True
        )


class ChannelSelectionView(discord.ui.View):
    """View for selecting a channel"""
    
    def __init__(self, title: str, description: str):
        super().__init__(timeout=120)  # 2 minute timeout
        self.title = title
        self.description = description
        self.selected_channel = None
        self.add_item(ChannelSelect())
    
    @discord.ui.button(label="Done", style=discord.ButtonStyle.success)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Finish channel selection"""
        await interaction.response.defer(ephemeral=True)
        if not self.selected_channel:
            await interaction.followup.send(
                "You need to select a channel first!",
                ephemeral=True
            )
            return
        
        self.stop()


class ChannelSelect(discord.ui.ChannelSelect):
    """Channel select menu for ticket configuration"""
    
    def __init__(self):
        super().__init__(
            placeholder="Select a channel...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle channel selection"""
        await interaction.response.defer(ephemeral=True)
        
        # Update the selected channel
        self.view.selected_channel = self.values[0]
        
        await interaction.followup.send(
            f"Selected channel: {self.view.selected_channel.mention}\n\nClick 'Done' to confirm.",
            ephemeral=True
        )


class CategorySelectionView(discord.ui.View):
    """View for selecting a category"""
    
    def __init__(self, title: str, description: str):
        super().__init__(timeout=120)  # 2 minute timeout
        self.title = title
        self.description = description
        self.selected_category = None
        self.add_item(CategorySelect())
    
    @discord.ui.button(label="Done", style=discord.ButtonStyle.success)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Finish category selection"""
        await interaction.response.defer(ephemeral=True)
        if not self.selected_category:
            await interaction.followup.send(
                "You need to select a category first!",
                ephemeral=True
            )
            return
        
        self.stop()


class CategorySelect(discord.ui.ChannelSelect):
    """Category select menu for ticket configuration"""
    
    def __init__(self):
        super().__init__(
            placeholder="Select a category...",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle category selection"""
        await interaction.response.defer(ephemeral=True)
        
        # Update the selected category
        self.view.selected_category = self.values[0]
        
        await interaction.followup.send(
            f"Selected category: {self.view.selected_category.name}\n\nClick 'Done' to confirm.",
            ephemeral=True
        )


class TicketTypeManagementView(discord.ui.View):
    """View for managing ticket types"""
    
    def __init__(self, server_id: int, ticket_types: List[Dict[str, Any]]):
        super().__init__(timeout=300)  # 5 minute timeout
        self.server_id = server_id
        self.ticket_types = ticket_types
        
        # Add ticket type selector if there are ticket types
        if ticket_types:
            self.add_item(TicketTypeSelector(ticket_types))
    
    @discord.ui.button(label="Create Ticket Type", style=discord.ButtonStyle.green)
    async def create_ticket_type(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Create a new ticket type"""
        # Create and show modal for ticket type info
        modal = TicketSetupModal()
        await interaction.response.send_modal(modal)
        
        # Wait for modal submission
        try:
            timed_out = await modal.wait()
        except Exception as e:
            logger.error(f"Error in modal: {e}")
            return
        
        if timed_out:
            await interaction.followup.send("Operation timed out.", ephemeral=True)
            return
        
        # Get values from modal
        name = modal.name.value
        description = modal.description.value
        
        # Create role selection view
        view = RoleSelectionView(
            title="Select Assigned Roles",
            description=f"Select roles that should be assigned to {name} tickets"
        )
        
        # Send role selection view
        message = await interaction.followup.send(
            f"Select roles that should be assigned to '{name}' tickets:",
            view=view,
            ephemeral=True
        )
        
        # Wait for the selection to be done
        timed_out = await view.wait()
        
        if timed_out:
            await message.edit(content="Role selection timed out.", view=None)
            return
        
        # Get the selected roles
        assigned_roles = view.selected_roles
        
        # Generate a random color
        color = int("0x%06x" % random.randint(0, 0xFFFFFF), 16)
        
        # Save the ticket type
        type_id = save_ticket_type(
            self.server_id,
            name,
            description,
            assigned_roles,
            color
        )
        
        if type_id > 0:
            await message.edit(
                content=f"Ticket type '{name}' created successfully! Assigned {len(assigned_roles)} roles.",
                view=None
            )
            
            # Update ticket types list
            self.ticket_types = get_ticket_types(self.server_id)
        else:
            await message.edit(
                content="Failed to create ticket type. Please try again.",
                view=None
            )
    
    @discord.ui.button(label="Edit Selected Type", style=discord.ButtonStyle.primary)
    async def edit_ticket_type(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Edit the selected ticket type"""
        await interaction.response.defer(ephemeral=True)
        
        # Check if a type is selected
        if not hasattr(self, 'selected_type_id') or not self.selected_type_id:
            await interaction.followup.send(
                "Please select a ticket type first!",
                ephemeral=True
            )
            return
        
        # Get the selected ticket type
        selected_type = next((t for t in self.ticket_types if t["id"] == self.selected_type_id), None)
        
        if not selected_type:
            await interaction.followup.send(
                "Selected ticket type not found. Please try again.",
                ephemeral=True
            )
            return
        
        # Create and show modal for ticket type info
        modal = TicketSetupModal(self.selected_type_id, selected_type)
        await interaction.followup.send("Edit the ticket type details:", ephemeral=True)
        await interaction.followup.send_modal(modal)
        
        # Wait for modal submission
        timed_out = await modal.wait()
        
        if timed_out:
            await interaction.followup.send("Operation timed out.", ephemeral=True)
            return
        
        # Get values from modal
        name = modal.name.value
        description = modal.description.value
        
        # Create role selection view
        view = RoleSelectionView(
            title="Select Assigned Roles",
            description=f"Select roles that should be assigned to {name} tickets",
            current_roles=selected_type.get("assigned_roles", [])
        )
        
        # Send role selection view
        message = await interaction.followup.send(
            f"Select roles that should be assigned to '{name}' tickets:",
            view=view,
            ephemeral=True
        )
        
        # Wait for the selection to be done
        timed_out = await view.wait()
        
        if timed_out:
            await message.edit(content="Role selection timed out.", view=None)
            return
        
        # Get the selected roles
        assigned_roles = view.selected_roles
        
        # Update the ticket type
        success = save_ticket_type(
            self.server_id,
            name,
            description,
            assigned_roles,
            selected_type.get("color", 0),
            self.selected_type_id
        )
        
        if success > 0:
            await message.edit(
                content=f"Ticket type '{name}' updated successfully! Assigned {len(assigned_roles)} roles.",
                view=None
            )
            
            # Update ticket types list
            self.ticket_types = get_ticket_types(self.server_id)
        else:
            await message.edit(
                content="Failed to update ticket type. Please try again.",
                view=None
            )
    
    @discord.ui.button(label="Configure Modal", style=discord.ButtonStyle.secondary)
    async def configure_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Configure the modal questions for the selected ticket type"""
        await interaction.response.defer(ephemeral=True)
        
        # Check if a type is selected
        if not hasattr(self, 'selected_type_id') or not self.selected_type_id:
            await interaction.followup.send(
                "Please select a ticket type first!",
                ephemeral=True
            )
            return
        
        # Get the selected ticket type
        selected_type = next((t for t in self.ticket_types if t["id"] == self.selected_type_id), None)
        
        if not selected_type:
            await interaction.followup.send(
                "Selected ticket type not found. Please try again.",
                ephemeral=True
            )
            return
        
        # Create embed with instructions
        embed = discord.Embed(
            title=f"Modal Configuration for {selected_type['name']}",
            description=(
                "Modals allow you to collect additional information from users when they create a ticket.\n\n"
                "You can add up to 5 questions that users will need to answer before creating a ticket.\n\n"
                "Current status: " + 
                ("**Enabled**" if selected_type.get("modal_enabled", False) else "**Disabled**")
            ),
            color=discord.Color.blue()
        )
        
        # Get current questions
        current_questions = selected_type.get("modal_questions", [])
        if current_questions:
            question_list = "\n".join([f"{i+1}. {q}" for i, q in enumerate(current_questions)])
            embed.add_field(
                name="Current Questions",
                value=question_list,
                inline=False
            )
        
        # Create view for modal configuration
        view = ModalConfigView(self.server_id, self.selected_type_id, selected_type)
        
        await interaction.followup.send(
            embed=embed,
            view=view,
            ephemeral=True
        )
    
    @discord.ui.button(label="Delete Selected Type", style=discord.ButtonStyle.danger)
    async def delete_ticket_type(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Delete the selected ticket type"""
        await interaction.response.defer(ephemeral=True)
        
        # Check if a type is selected
        if not hasattr(self, 'selected_type_id') or not self.selected_type_id:
            await interaction.followup.send(
                "Please select a ticket type first!",
                ephemeral=True
            )
            return
        
        # Get the selected ticket type
        selected_type = next((t for t in self.ticket_types if t["id"] == self.selected_type_id), None)
        
        if not selected_type:
            await interaction.followup.send(
                "Selected ticket type not found. Please try again.",
                ephemeral=True
            )
            return
        
        # Confirm deletion
        view = ConfirmView()
        
        message = await interaction.followup.send(
            f"Are you sure you want to delete the ticket type '{selected_type['name']}'?",
            view=view,
            ephemeral=True
        )
        
        # Wait for confirmation
        timed_out = await view.wait()
        
        if timed_out or not view.confirmed:
            await message.edit(
                content="Ticket type deletion cancelled.",
                view=None
            )
            return
        
        # Delete the ticket type
        success = delete_ticket_type(self.server_id, self.selected_type_id)
        
        if success:
            await message.edit(
                content=f"Ticket type '{selected_type['name']}' deleted successfully!",
                view=None
            )
            
            # Update ticket types list
            self.ticket_types = get_ticket_types(self.server_id)
            
            # Remove the type selector if there are no more types
            if not self.ticket_types:
                for child in self.children[:]:
                    if isinstance(child, TicketTypeSelector):
                        self.remove_item(child)
                        break
        else:
            await message.edit(
                content="Failed to delete ticket type. Please try again.",
                view=None
            )


class TicketTypeSelector(discord.ui.Select):
    """Dropdown for selecting a ticket type to edit/delete"""
    
    def __init__(self, ticket_types: List[Dict[str, Any]]):
        options = []
        
        for ticket_type in ticket_types:
            options.append(
                discord.SelectOption(
                    label=ticket_type["name"],
                    description=ticket_type["description"][:100] if len(ticket_type["description"]) > 100 else ticket_type["description"],
                    value=str(ticket_type["id"])
                )
            )
        
        super().__init__(
            placeholder="Select a ticket type to edit/delete...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle ticket type selection"""
        await interaction.response.defer(ephemeral=True)
        
        # Set the selected ticket type ID
        self.view.selected_type_id = int(self.values[0])
        
        # Find the selected ticket type
        selected_type = next((t for t in self.view.ticket_types if t["id"] == self.view.selected_type_id), None)
        
        if selected_type:
            # Get role mentions
            guild = interaction.guild
            role_mentions = []
            
            for role_id in selected_type.get("assigned_roles", []):
                role = guild.get_role(role_id)
                if role:
                    role_mentions.append(role.mention)
            
            role_text = ", ".join(role_mentions) if role_mentions else "None"
            
            # Show the selected ticket type details
            await interaction.followup.send(
                f"Selected ticket type: **{selected_type['name']}**\n\n"
                f"Description: {selected_type['description']}\n\n"
                f"Assigned Roles: {role_text}\n\n"
                "Use the 'Edit Selected Type' or 'Delete Selected Type' buttons to modify this ticket type.",
                ephemeral=True
            )


class ModalConfigView(discord.ui.View):
    """View for configuring ticket modals"""
    
    def __init__(self, server_id: int, ticket_type_id: int, ticket_type: Dict[str, Any]):
        super().__init__(timeout=300)  # 5 minute timeout
        self.server_id = server_id
        self.ticket_type_id = ticket_type_id
        self.ticket_type = ticket_type
        self.modal_enabled = ticket_type.get("modal_enabled", False)
        self.questions = ticket_type.get("modal_questions", [])
    
    @discord.ui.button(label="Toggle Modal", style=discord.ButtonStyle.primary)
    async def toggle_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Toggle the modal on/off"""
        await interaction.response.defer(ephemeral=True)
        
        # Toggle modal state
        self.modal_enabled = not self.modal_enabled
        
        # Update the database
        success = set_modal_questions(
            self.server_id,
            self.ticket_type_id,
            self.modal_enabled,
            self.questions
        )
        
        if success:
            status = "enabled" if self.modal_enabled else "disabled"
            await interaction.followup.send(
                f"Modal has been {status} for this ticket type!",
                ephemeral=True
            )
            
            # Update button label
            button.label = "Disable Modal" if self.modal_enabled else "Enable Modal"
        else:
            await interaction.followup.send(
                "Failed to update modal settings. Please try again.",
                ephemeral=True
            )
    
    @discord.ui.button(label="Add Question", style=discord.ButtonStyle.success)
    async def add_question(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Add a new question to the modal"""
        # Check if we already have 5 questions (Discord's limit)
        if len(self.questions) >= 5:
            await interaction.response.send_message(
                "You cannot add more than 5 questions to a modal (Discord limit).",
                ephemeral=True
            )
            return
            
        # Create a modal for entering the new question
        modal = QuestionModal("Add Question", "")
        await interaction.response.send_modal(modal)
        
        # Wait for the user to submit the modal
        timed_out = await modal.wait()
        if timed_out:
            return
        
        # Add the new question to the list
        self.questions.append(modal.question.value)
        
        # Update the database
        success = set_modal_questions(
            self.server_id,
            self.ticket_type_id,
            self.modal_enabled,
            self.questions
        )
        
        if success:
            await interaction.followup.send(
                f"Question added: '{modal.question.value}'",
                ephemeral=True
            )
        else:
            # Remove the question if it failed to save
            self.questions.pop()
            await interaction.followup.send(
                "Failed to save question. Please try again.",
                ephemeral=True
            )
    
    @discord.ui.button(label="Edit Questions", style=discord.ButtonStyle.secondary)
    async def edit_questions(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Edit existing questions"""
        await interaction.response.defer(ephemeral=True)
        
        if not self.questions:
            await interaction.followup.send(
                "There are no questions to edit. Add questions first.",
                ephemeral=True
            )
            return
        
        # Create a view with a select menu for choosing a question to edit
        view = QuestionSelectView(self.questions)
        message = await interaction.followup.send(
            "Select a question to edit:",
            view=view,
            ephemeral=True
        )
        
        # Wait for selection
        timed_out = await view.wait()
        if timed_out:
            await message.edit(content="Selection timed out.", view=None)
            return
        
        if not hasattr(view, 'selected_index'):
            await message.edit(content="No question selected.", view=None)
            return
        
        # Get the selected question and index
        selected_index = view.selected_index
        selected_question = self.questions[selected_index]
        
        # Create a modal for editing the question
        modal = QuestionModal("Edit Question", selected_question)
        await interaction.followup.send_modal(modal)
        
        # Wait for the user to submit the modal
        timed_out = await modal.wait()
        if timed_out:
            return
        
        # Update the question
        self.questions[selected_index] = modal.question.value
        
        # Update the database
        success = set_modal_questions(
            self.server_id,
            self.ticket_type_id,
            self.modal_enabled,
            self.questions
        )
        
        if success:
            await interaction.followup.send(
                f"Question updated to: '{modal.question.value}'",
                ephemeral=True
            )
        else:
            # Revert the change if it failed to save
            self.questions[selected_index] = selected_question
            await interaction.followup.send(
                "Failed to update question. Please try again.",
                ephemeral=True
            )
    
    @discord.ui.button(label="Remove Question", style=discord.ButtonStyle.danger)
    async def remove_question(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Remove a question from the modal"""
        await interaction.response.defer(ephemeral=True)
        
        if not self.questions:
            await interaction.followup.send(
                "There are no questions to remove.",
                ephemeral=True
            )
            return
        
        # Create a view with a select menu for choosing a question to remove
        view = QuestionSelectView(self.questions)
        message = await interaction.followup.send(
            "Select a question to remove:",
            view=view,
            ephemeral=True
        )
        
        # Wait for selection
        timed_out = await view.wait()
        if timed_out:
            await message.edit(content="Selection timed out.", view=None)
            return
        
        if not hasattr(view, 'selected_index'):
            await message.edit(content="No question selected.", view=None)
            return
        
        # Get the selected question and index
        selected_index = view.selected_index
        removed_question = self.questions.pop(selected_index)
        
        # Update the database
        success = set_modal_questions(
            self.server_id,
            self.ticket_type_id,
            self.modal_enabled,
            self.questions
        )
        
        if success:
            await interaction.followup.send(
                f"Question removed: '{removed_question}'",
                ephemeral=True
            )
        else:
            # Add the question back if it failed to save
            self.questions.insert(selected_index, removed_question)
            await interaction.followup.send(
                "Failed to remove question. Please try again.",
                ephemeral=True
            )


class QuestionModal(discord.ui.Modal):
    """Modal for adding or editing a question"""
    
    def __init__(self, title: str, default_question: str = ""):
        super().__init__(title=title)
        
        self.question = discord.ui.TextInput(
            label="Question",
            placeholder="Enter the question you want to ask the user",
            default=default_question,
            required=True,
            max_length=100,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.question)
    
    async def on_submit(self, interaction: discord.Interaction):
        """When user submits the modal"""
        await interaction.response.defer(ephemeral=True)


class QuestionSelectView(discord.ui.View):
    """View for selecting a question to edit or remove"""
    
    def __init__(self, questions: List[str]):
        super().__init__(timeout=60)  # 1 minute timeout
        self.add_item(QuestionSelect(questions))


class QuestionSelect(discord.ui.Select):
    """Dropdown for selecting a question"""
    
    def __init__(self, questions: List[str]):
        options = []
        
        for i, question in enumerate(questions):
            options.append(
                discord.SelectOption(
                    label=f"Question {i+1}",
                    description=question[:100],  # Truncate if too long
                    value=str(i)
                )
            )
        
        super().__init__(
            placeholder="Select a question...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle question selection"""
        await interaction.response.defer(ephemeral=True)
        
        # Set the selected question index
        self.view.selected_index = int(self.values[0])
        
        # Acknowledge the selection
        await interaction.followup.send(
            f"Selected Question {self.view.selected_index + 1}",
            ephemeral=True
        )
        
        # Stop the view
        self.view.stop()


class ConfirmView(discord.ui.View):
    """View for confirming an action"""
    
    def __init__(self):
        super().__init__(timeout=60)  # 1 minute timeout
        self.confirmed = False
    
    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm the action"""
        await interaction.response.defer(ephemeral=True)
        self.confirmed = True
        self.stop()
    
    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel the action"""
        await interaction.response.defer(ephemeral=True)
        self.confirmed = False
        self.stop()


async def setup(bot: commands.Bot):
    """Setup function to add the cog to the bot"""
    import random
    from discord.ext import tasks
    
    await bot.add_cog(Tickets(bot))
    logger.info("Tickets cog loaded successfully")
