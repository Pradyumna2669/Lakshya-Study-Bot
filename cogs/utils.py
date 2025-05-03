import datetime
import discord
import pytz
import logging
import asyncio
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Constants
IST = pytz.timezone('Asia/Kolkata')
COOLDOWN_MINUTES = 5
GRACE_PERIOD_MINUTES = 5
RATE_LIMIT_DELAY = 1.1  # Discord rate limit (in seconds)

# Global rate limit tracking
last_message_time = 0.0  # Initialize with 0 timestamp

# Developer logging
DEV_SERVER_ID = 1358374550315733133
DEV_CHANNEL_ID = 1358417132970442823

def get_current_time():
    """Get the current time in UTC"""
    return datetime.now(timezone.utc)

def get_formatted_time(time_obj):
    """Format a time object to a human-readable string"""
    return time_obj.strftime('%Y-%m-%d %H:%M:%S')

def get_current_date_ist():
    """Get the current date in Indian Standard Time"""
    return datetime.now(IST).strftime('%Y-%m-%d')

def get_previous_date_ist():
    """Get the previous date in Indian Standard Time"""
    previous_date = datetime.now(IST) - timedelta(days=1)
    return previous_date.strftime('%Y-%m-%d')

def get_date_from_string(date_str, format='%d-%m-%Y'):
    """Convert a date string to a date object, returns None if invalid"""
    try:
        date_obj = datetime.strptime(date_str, format).replace(tzinfo=IST)
        return date_obj.strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        return None

def format_duration(minutes):
    """Format duration in minutes to a human-readable string"""
    hours, remainder = divmod(minutes, 60)
    if hours > 0:
        return f"{hours} hour{'s' if hours > 1 else ''} {remainder} minute{'s' if remainder != 1 else ''}"
    else:
        return f"{remainder} minute{'s' if remainder != 1 else ''}"

def format_remaining_time(end_time):
    """Format the remaining time until end_time"""
    now = get_current_time()
    if now >= end_time:
        return "Completed"
    
    remaining = end_time - now
    total_seconds = remaining.total_seconds()
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours > 0:
        return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
    elif minutes > 0:
        return f"{int(minutes)}m {int(seconds)}s"
    else:
        return f"{int(seconds)}s"

def calculate_time_studied(start_time, end_time, paused_duration=0):
    """Calculate the total time studied between start_time and end_time, accounting for pauses"""
    # If end_time is in the future, use current time instead
    now = get_current_time()
    actual_end = min(end_time, now)
    
    # Calculate the duration in minutes
    duration = (actual_end - start_time).total_seconds() / 60
    
    # Subtract the time spent in pauses
    duration -= paused_duration / 60
    
    return max(0, round(duration))

async def create_embed(title, description, color=discord.Color.blue(), fields=None, footer=None):
    """Create a Discord embed with the given parameters"""
    embed = discord.Embed(title=title, description=description, color=color)
    
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    
    if footer:
        embed.set_footer(text=footer)
    
    return embed

async def send_message_safely(ctx, content=None, embed=None, ephemeral=False, mention_user=False):
    """Safely send a message, handling potential errors"""
    try:
        # Apply rate limiting
        await respect_rate_limit()
        
        # Add mention to content if requested
        if mention_user and hasattr(ctx, 'user') and content is not None:
            content = f"{ctx.user.mention} {content}"
        
        if hasattr(ctx, 'response') and hasattr(ctx.response, 'send_message'):
            await ctx.response.send_message(content=content, embed=embed, ephemeral=ephemeral)
        else:
            await ctx.send(content=content, embed=embed)
        return True
    except discord.Forbidden:
        logger.error(f"Missing permissions to send message in channel {getattr(ctx.channel, 'id', 'unknown')}")
        return False
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False

async def send_dm_safely(user, content=None, embed=None):
    """Safely send a DM to a user, handling potential errors"""
    try:
        # Apply rate limiting
        await respect_rate_limit()
        
        # Create a DM channel if one doesn't exist
        if user.dm_channel is None:
            await user.create_dm()
        
        await user.dm_channel.send(content=content, embed=embed)
        return True
    except discord.Forbidden:
        logger.error(f"User {get_user_display(user)} has DMs disabled")
        return False
    except Exception as e:
        logger.error(f"Error sending DM to user {get_user_display(user)}: {e}")
        return False

async def send_channel_message(channel, content=None, embed=None, mention_user=None):
    """Send a message to a channel, with optional user mention"""
    try:
        # Apply rate limiting
        await respect_rate_limit()
        
        # Add user mention if provided
        if mention_user:
            content = f"{mention_user.mention} {content}" if content else mention_user.mention
        
        await channel.send(content=content, embed=embed)
        return True
    except Exception as e:
        logger.error(f"Error sending message to channel {channel.id}: {e}")
        return False

def get_percentage_of_goal(minutes, goal_minutes):
    """Calculate the percentage of a goal completed"""
    if goal_minutes <= 0:
        return 0
    percentage = (minutes / goal_minutes) * 100
    return min(100, round(percentage, 1))  # Cap at 100%

def is_midnight_ist():
    """Check if it's currently midnight in Indian Standard Time"""
    now = datetime.now(IST)
    return now.hour == 0 and now.minute == 0

async def respect_rate_limit():
    """Respect Discord's rate limit by waiting if necessary"""
    global last_message_time
    current_time = time.time()
    time_since_last = current_time - last_message_time
    
    # If we've sent a message too recently, wait
    if time_since_last < RATE_LIMIT_DELAY:
        await asyncio.sleep(RATE_LIMIT_DELAY - time_since_last)
    
    # Update the last message time
    last_message_time = time.time()

async def log_to_dev_channel(bot, message, level="INFO"):
    """Log a message to the developer channel"""
    try:
        # Get the development server and channel
        guild = bot.get_guild(DEV_SERVER_ID)
        if not guild:
            logger.warning(f"Could not find development server with ID {DEV_SERVER_ID}")
            return
            
        channel = guild.get_channel(DEV_CHANNEL_ID)
        if not channel:
            logger.warning(f"Could not find development channel with ID {DEV_CHANNEL_ID}")
            return
        
        # Format the log message
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"[{timestamp}] [{level}] {message}"
        
        # Respect rate limiting
        await respect_rate_limit()
        
        # Send the log message
        await channel.send(formatted_message)
    except Exception as e:
        logger.error(f"Error logging to dev channel: {e}")

def get_user_display(user):
    """Get a user's display string including ID and name if available"""
    if hasattr(user, 'name') and user.name:
        return f"{user.name} (ID: {user.id})"
    return f"User ID: {user.id}"
