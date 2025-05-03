import logging
import datetime
import os
import jinja2
from typing import List, Dict, Any, Optional, Tuple
import discord
from core.github_api import GitHubAPI

logger = logging.getLogger(__name__)

# Set up the Jinja2 environment
template_loader = jinja2.FileSystemLoader(searchpath="./templates")
template_env = jinja2.Environment(loader=template_loader)

class TranscriptGenerator:
    def __init__(self):
        self.template = template_env.get_template("transcript_template.html")
    
    def format_timestamp(self, timestamp: datetime.datetime) -> str:
        """Format a timestamp for display in the transcript"""
        return timestamp.strftime("%Y-%m-%d %H:%M:%S")
    
    def escape_html(self, text: str) -> str:
        """Escape HTML special characters"""
        return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )
    
    async def collect_channel_messages(self, channel: discord.TextChannel) -> List[Dict[str, Any]]:
        """Collect all messages from a channel for the transcript"""
        formatted_messages = []
        
        async for message in channel.history(limit=None, oldest_first=True):
            # Format attachments
            attachments = []
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith('image/'):
                    # For images, we'll include them directly (if they're still available)
                    attachments.append({
                        'name': attachment.filename,
                        'url': attachment.url,
                        'is_image': True
                    })
                else:
                    attachments.append({
                        'name': attachment.filename,
                        'url': attachment.url,
                        'is_image': False
                    })
            
            # Format embeds
            embeds = []
            for embed in message.embeds:
                embed_dict = {
                    'title': embed.title,
                    'description': embed.description,
                    'color': embed.color.value if embed.color else None,
                    'fields': [{
                        'name': field.name,
                        'value': field.value,
                        'inline': field.inline
                    } for field in embed.fields],
                    'footer': embed.footer.text if embed.footer else None,
                    'timestamp': embed.timestamp.strftime("%Y-%m-%d %H:%M:%S") if embed.timestamp else None
                }
                embeds.append(embed_dict)
            
            formatted_messages.append({
                'author': {
                    'name': message.author.display_name,
                    'id': message.author.id,
                    'avatar_url': message.author.display_avatar.url
                },
                'content': message.content,
                'clean_content': message.clean_content,
                'timestamp': message.created_at,
                'formatted_timestamp': self.format_timestamp(message.created_at),
                'attachments': attachments,
                'embeds': embeds,
                'reactions': [{'emoji': str(reaction.emoji), 'count': reaction.count} for reaction in message.reactions]
            })
        
        return formatted_messages
    
    async def generate_transcript(
        self, 
        channel: discord.TextChannel, 
        ticket_data: Dict[str, Any]
    ) -> str:
        """Generate HTML transcript from channel messages"""
        messages = await self.collect_channel_messages(channel)
        
        # Get the guild (server) for the member info
        guild = channel.guild
        
        # Get the creator's current display name
        creator = guild.get_member(ticket_data['creator_id'])
        creator_name = creator.display_name if creator else f"Unknown User ({ticket_data['creator_id']})"
        
        # Get the claimed by user's display name if applicable
        claimed_by_name = "Not claimed"
        if ticket_data.get('claimed_by'):
            claimed_by_user = guild.get_member(ticket_data['claimed_by'])
            claimed_by_name = claimed_by_user.display_name if claimed_by_user else f"Unknown User ({ticket_data['claimed_by']})"
        
        # Prepare context for template
        context = {
            'server_name': guild.name,
            'server_id': guild.id,
            'ticket_number': ticket_data['ticket_number'],
            'ticket_type': ticket_data['type_name'],
            'creator_name': creator_name,
            'creator_id': ticket_data['creator_id'],
            'claimed_by': claimed_by_name,
            'claimed_by_id': ticket_data.get('claimed_by'),
            'created_at': ticket_data['created_at'],
            'messages': messages,
            'generated_at': self.format_timestamp(datetime.datetime.now()),
            'channel_name': channel.name
        }
        
        # Render the template
        return self.template.render(**context)
    
    async def upload_transcript(
        self,
        transcript: str,
        server_id: int,
        ticket_number: int,
        ticket_data: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """
        Upload transcript to GitHub repository and return the URL
        Returns (success, url_or_error_message)
        """
        # Create GitHub API instance with token from environment variable
        github_api = GitHubAPI()
        
        # Get current timestamp for unique filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Define file path in the repository following the required structure
        file_path = f"tickets_transcripts/{server_id}/ticket_{ticket_number}_{timestamp}.html"
        
        # Create detailed commit message
        creator_id = ticket_data.get('creator_id', 'unknown')
        ticket_type = ticket_data.get('type_name', 'general')
        commit_message = f"Ticket #{ticket_number} ({ticket_type}) transcript from server {server_id} by user {creator_id}"
        
        # Upload the file
        return await github_api.create_file(file_path, transcript, commit_message)
