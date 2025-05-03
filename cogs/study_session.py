import asyncio
import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from datetime import datetime, timedelta, timezone
import pytz
import traceback
from typing import Optional, List
import re
from core.bot import StoicBot
from core.schema import Database
from cogs.utils import (
    get_current_time, get_formatted_time, get_current_date_ist, 
    get_previous_date_ist, get_date_from_string, format_duration, format_remaining_time,
    calculate_time_studied, create_embed, send_message_safely, 
    send_dm_safely, send_channel_message, get_percentage_of_goal, is_midnight_ist,
    respect_rate_limit, log_to_dev_channel, get_user_display,
    COOLDOWN_MINUTES, GRACE_PERIOD_MINUTES, IST, DEV_SERVER_ID, DEV_CHANNEL_ID
)

# UI Components for Interactive Buttons
class SessionControlButtons(discord.ui.View):
    def __init__(self, cog, user_id, guild_id, timeout=180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.user_id = user_id
        self.guild_id = guild_id
        self.session_key = (user_id, guild_id)
        self.update_buttons()

    def update_buttons(self):
        """Update button states based on session status"""
        is_paused = False
        has_session = self.session_key in self.cog.active_sessions

        if has_session:
            is_paused = self.cog.active_sessions[self.session_key].get('is_paused', False)

        # Clear existing buttons and add updated ones
        self.clear_items()

        pause_button = discord.ui.Button(
            style=discord.ButtonStyle.primary,
            label="Pause",
            emoji="⏸️",
            disabled=not has_session or is_paused,
            custom_id="pause"
        )
        pause_button.callback = self.pause_callback

        resume_button = discord.ui.Button(
            style=discord.ButtonStyle.success,
            label="Resume",
            emoji="▶️",
            disabled=not has_session or not is_paused,
            custom_id="resume"
        )
        resume_button.callback = self.resume_callback

        stop_button = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Stop",
            emoji="⏹️",
            disabled=not has_session,
            custom_id="stop"
        )
        stop_button.callback = self.stop_callback

        self.add_item(pause_button)
        self.add_item(resume_button)
        self.add_item(stop_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if the user interacting with buttons is the session owner"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "You can only interact with your own sessions.",
                ephemeral=True
            )
            return False
        return True

    async def pause_callback(self, interaction: discord.Interaction):
        """Pause the session"""
        await self.cog.handle_button_action(interaction, self, "pause")

    async def resume_callback(self, interaction: discord.Interaction):
        """Resume the session"""
        await self.cog.handle_button_action(interaction, self, "resume")

    async def stop_callback(self, interaction: discord.Interaction):
        """Stop the session"""
        await self.cog.handle_button_action(interaction, self, "stop")

    async def on_timeout(self):
        """Handle view timeout by disabling all buttons"""
        for item in self.children:
            item.disabled = True

logger = logging.getLogger(__name__)

class StudySession(commands.Cog):
    """A cog for managing study sessions, leaderboards, and study goals."""

    def __init__(self, bot: StoicBot):
        self.bot = bot
        self.db = Database()
        self.active_sessions = {}  # {(user_id, guild_id): session_data}
        self.session_tasks = {}  # {(user_id, guild_id): asyncio.Task}
        self.cooldown_tasks = {}  # {(user_id, guild_id): asyncio.Task}
        self.grace_period_tasks = {}  # {(user_id, guild_id): asyncio.Task}

        # Start background tasks
        self.update_sessions.start()
        self.check_midnight.start()

        # Load active sessions on startup
        asyncio.create_task(self.load_active_sessions())

    def cog_unload(self):
        """Clean up tasks when the cog is unloaded"""
        self.update_sessions.cancel()
        self.check_midnight.cancel()

        # Cancel all running tasks
        for task in self.session_tasks.values():
            task.cancel()

        for task in self.cooldown_tasks.values():
            task.cancel()

        for task in self.grace_period_tasks.values():
            task.cancel()

    async def load_active_sessions(self):
        """Load active sessions from the database on startup"""
        try:
            active_sessions = await self.db.get_all_active_sessions()
            logger.info(f"Loading {len(active_sessions)} active sessions")

            for session in active_sessions:
                session_id, user_id, guild_id, channel_id, start_time, end_time, \
                duration, is_completed, is_paused, pause_time, paused_duration, voice_leave_time = session

                # Convert string timestamps to datetime objects
                start_time = datetime.fromisoformat(start_time)
                end_time = datetime.fromisoformat(end_time)

                # Create a session key
                session_key = (user_id, guild_id)

                # Store session data
                self.active_sessions[session_key] = {
                    'id': session_id,
                    'user_id': user_id,
                    'guild_id': guild_id,
                    'channel_id': channel_id,
                    'start_time': start_time,
                    'end_time': end_time,
                    'duration': duration,
                    'is_paused': bool(is_paused),
                    'pause_time': datetime.fromisoformat(pause_time) if pause_time else None,
                    'paused_duration': paused_duration or 0,
                    'voice_leave_time': datetime.fromisoformat(voice_leave_time) if voice_leave_time else None
                }

                # Resume the session task
                if not is_completed:
                    # Calculate the remaining time
                    now = get_current_time()
                    if end_time > now:
                        # Create a new session task
                        task = asyncio.create_task(
                            self.handle_session(
                                user_id, 
                                guild_id, 
                                channel_id,
                                end_time,
                                is_resumed=True
                            )
                        )
                        self.session_tasks[session_key] = task
                    else:
                        # Session should have ended but was interrupted, complete it
                        asyncio.create_task(self.complete_session(user_id, guild_id, session_id))

                # Resume grace period if needed
                if voice_leave_time:
                    leave_time = datetime.fromisoformat(voice_leave_time)
                    grace_period_end = leave_time + timedelta(minutes=GRACE_PERIOD_MINUTES)

                    if grace_period_end > now:
                        # Grace period still active
                        task = asyncio.create_task(
                            self.handle_grace_period(user_id, guild_id, grace_period_end)
                        )
                        self.grace_period_tasks[session_key] = task
                    else:
                        # Grace period has expired, cancel the session
                        asyncio.create_task(self.cancel_session(user_id, guild_id, session_id))

            logger.info("Active sessions loaded successfully")
        except Exception as e:
            logger.error(f"Error loading active sessions: {e}")
            traceback.print_exc()

    @tasks.loop(minutes=1)
    async def update_sessions(self):
        """Update session data every minute"""
        # This is mostly a safety mechanism to make sure no session is forgotten
        try:
            now = get_current_time()
            for session_key, session_data in list(self.active_sessions.items()):
                user_id, guild_id = session_key

                # Skip paused sessions
                if session_data.get('is_paused', False):
                    continue

                # If end time has passed but task is still running
                if session_data['end_time'] <= now and session_key in self.session_tasks:
                    logger.info(f"Force completing session for user {user_id} in guild {guild_id}")
                    self.session_tasks[session_key].cancel()
                    await self.complete_session(user_id, guild_id, session_data['id'])

            # Clean up completed sessions older than a day
            # This would be in a separate task in a production environment, run less frequently
        except Exception as e:
            logger.error(f"Error in update_sessions task: {e}")

    @update_sessions.before_loop
    async def before_update_sessions(self):
        """Wait for the bot to be ready before starting the task"""
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def check_midnight(self):
        """Check if it's midnight in IST to reset leaderboards"""
        if is_midnight_ist():
            logger.info("Midnight in IST - daily leaderboard reset")
            # No actual data deletion needed, as we query by date

    @check_midnight.before_loop
    async def before_check_midnight(self):
        """Wait for the bot to be ready before starting the task"""
        await self.bot.wait_until_ready()

        # Align to the start of the next minute
        now = datetime.now()
        seconds_until_next_minute = 60 - now.second
        await asyncio.sleep(seconds_until_next_minute)

    @app_commands.command(name="start", description="Start a study session")
    @app_commands.describe(duration="Duration of the study session in minutes (default: 60)")
    async def start_session(self, interaction: discord.Interaction, duration: int = 60):
        """Start a new study session with the specified duration"""
        try:
            user_id = interaction.user.id
            guild_id = interaction.guild_id
            session_key = (user_id, guild_id)

            # Check if user is already in a session
            if session_key in self.active_sessions:
                await interaction.response.send_message(
                    "You already have an active study session. Use `/status` to check its progress or stop it.",
                    ephemeral=True
                )
                return

            # Check if user is on cooldown
            cooldown = await self.db.get_cooldown(user_id, guild_id)
            if cooldown:
                cooldown_end = datetime.fromisoformat(cooldown[0])
                if cooldown_end > get_current_time():
                    time_left = format_remaining_time(cooldown_end)
                    await interaction.response.send_message(
                        f"You're on cooldown! You can start a new session in {time_left}.",
                        ephemeral=True
                    )
                    return
                else:
                    # Cooldown has expired, remove it
                    await self.db.remove_cooldown(user_id, guild_id)

            # Ensure duration is reasonable
            if duration <= 0:
                await interaction.response.send_message(
                    "Study session duration must be positive!",
                    ephemeral=True
                )
                return

            if duration > 480:  # 8 hours max
                await interaction.response.send_message(
                    "Study session duration cannot exceed 8 hours. Setting to 480 minutes.",
                    ephemeral=True
                )
                duration = 480

            # Check if user is in a voice channel
            voice_state = interaction.user.voice
            channel_id = voice_state.channel.id if voice_state else interaction.channel_id

            # Calculate session times
            start_time = get_current_time()
            end_time = start_time + timedelta(minutes=duration)

            # Create session in database
            session_id = await self.db.create_session(
                user_id, guild_id, channel_id, start_time, end_time, duration
            )

            # Store session data in memory
            self.active_sessions[session_key] = {
                'id': session_id,
                'user_id': user_id,
                'guild_id': guild_id,
                'channel_id': channel_id,
                'start_time': start_time,
                'end_time': end_time,
                'duration': duration,
                'is_paused': False,
                'pause_time': None,
                'paused_duration': 0,
                'voice_leave_time': None
            }

            # Create and start the session task
            task = asyncio.create_task(
                self.handle_session(user_id, guild_id, channel_id, end_time)
            )
            self.session_tasks[session_key] = task

            # Send confirmation message
            formatted_end_time = end_time.astimezone(IST).strftime('%H:%M:%S')
            embed = await create_embed(
                title="Study Session Started",
                description=f"Your {duration}-minute study session has started! It will end at {formatted_end_time} IST.",
                color=discord.Color.green(),
                fields=[
                    ("Duration", format_duration(duration), True),
                    ("End Time", formatted_end_time, True),
                    ("Status", "In Progress", True)
                ],
                footer="Use /status to check your progress"
            )

            await interaction.response.send_message(embed=embed)

            # If in voice channel, start tracking
            if voice_state:
                logger.info(f"User {user_id} started a session in voice channel {channel_id}")

        except Exception as e:
            logger.error(f"Error starting session: {e}")
            await interaction.response.send_message(
                "An error occurred while starting your study session. Please try again.",
                ephemeral=True
            )

    async def handle_session(self, user_id, guild_id, channel_id, end_time, is_resumed=False):
        """Handle a running study session"""
        session_key = (user_id, guild_id)

        try:
            # Calculate sleep time until session ends
            now = get_current_time()
            if end_time <= now:
                await self.complete_session(user_id, guild_id, self.active_sessions[session_key]['id'])
                return

            sleep_time = (end_time - now).total_seconds()

            # Get user info for logging
            guild = self.bot.get_guild(guild_id)
            user_display = f"User ID: {user_id}"
            if guild:
                member = guild.get_member(user_id)
                if member:
                    user_display = get_user_display(member)

            # Format timestamp for logging
            end_time_formatted = end_time.astimezone(IST).strftime('%H:%M:%S IST')

            if is_resumed:
                logger.info(f"Resumed session for user {user_id} in guild {guild_id} with {sleep_time:.2f}s remaining")
                await log_to_dev_channel(
                    self.bot,
                    f"Session resumed for {user_display} in guild {guild_id} - will end at {end_time_formatted}",
                    "INFO"
                )
            else:
                logger.info(f"Started session for user {user_id} in guild {guild_id} for {sleep_time:.2f}s")
                await log_to_dev_channel(
                    self.bot,
                    f"New session started for {user_display} in guild {guild_id} - will end at {end_time_formatted}",
                    "INFO"
                )

            # Sleep until the session end time
            await asyncio.sleep(sleep_time)

            # Complete the session
            await self.complete_session(user_id, guild_id, self.active_sessions[session_key]['id'])

        except asyncio.CancelledError:
            logger.info(f"Session task for user {user_id} in guild {guild_id} was cancelled")
            await log_to_dev_channel(
                self.bot,
                f"Session task cancelled for user ID {user_id} in guild {guild_id}",
                "INFO"
            )
        except Exception as e:
            logger.error(f"Error in session task for user {user_id} in guild {guild_id}: {e}")
            traceback.print_exc()
            await log_to_dev_channel(
                self.bot,
                f"Error in session task for user ID {user_id} in guild {guild_id}: {e}",
                "ERROR"
            )

            # Attempt to complete the session if there's an error
            try:
                if session_key in self.active_sessions:
                    await self.complete_session(user_id, guild_id, self.active_sessions[session_key]['id'])
            except Exception as inner_e:
                logger.error(f"Error completing session after exception: {inner_e}")
                await log_to_dev_channel(
                    self.bot,
                    f"Failed to complete session after error for user ID {user_id} in guild {guild_id}: {inner_e}",
                    "ERROR"
                )

    async def complete_session(self, user_id, guild_id, session_id):
        """Complete a study session and update statistics"""
        session_key = (user_id, guild_id)

        try:
            # Check if the session exists
            if session_key not in self.active_sessions:
                logger.warning(f"Tried to complete non-existent session for user {user_id} in guild {guild_id}")
                return

            session_data = self.active_sessions[session_key]

            # Mark session as completed in the database
            await self.db.complete_session(session_id)

            # Calculate study time
            study_time = calculate_time_studied(
                session_data['start_time'],
                session_data['end_time'],
                session_data['paused_duration']
            )

            # Use session start date for stats instead of current date
            session_start_date = session_data['start_time'].astimezone(IST).strftime('%Y-%m-%d')
            await self.db.update_study_stats(user_id, guild_id, session_start_date, study_time)

            # Set cooldown
            cooldown_end = get_current_time() + timedelta(minutes=COOLDOWN_MINUTES)
            await self.db.set_cooldown(user_id, guild_id, cooldown_end)

            # Create cooldown notification task
            cooldown_task = asyncio.create_task(
                self.handle_cooldown(user_id, guild_id, cooldown_end)
            )
            self.cooldown_tasks[session_key] = cooldown_task

            # Clean up session data
            if session_key in self.session_tasks:
                del self.session_tasks[session_key]

            # Send completion messages
            await self.send_completion_messages(user_id, guild_id, study_time)

            # Clean up session data
            del self.active_sessions[session_key]

            logger.info(f"Completed session for user {user_id} in guild {guild_id} with {study_time} minutes studied")

        except Exception as e:
            logger.error(f"Error completing session: {e}")

    async def cancel_session(self, user_id, guild_id, session_id):
        """Cancel a study session without updating statistics"""
        session_key = (user_id, guild_id)

        try:
            # Check if the session exists
            if session_key not in self.active_sessions:
                logger.warning(f"Tried to cancel non-existent session for user {user_id} in guild {guild_id}")
                await log_to_dev_channel(
                    self.bot,
                    f"Attempted to cancel non-existent session for user ID {user_id} in guild {guild_id}",
                    "WARNING"
                )
                return

            session_data = self.active_sessions[session_key]
            channel_id = session_data.get('channel_id')

            # Mark session as completed in the database but don't update stats
            await self.db.complete_session(session_id)

            # Cancel the session task if it exists
            if session_key in self.session_tasks:
                self.session_tasks[session_key].cancel()
                del self.session_tasks[session_key]

            # Clean up grace period task if it exists
            if session_key in self.grace_period_tasks:
                self.grace_period_tasks[session_key].cancel()
                del self.grace_period_tasks[session_key]

            # Send cancellation message
            try:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    user = guild.get_member(user_id)
                    if user:
                        # Send DM with a direct message (not embedded)
                        message = "⚠️ **Your study session has been cancelled because you left the voice channel for too long.**"
                        embed = await create_embed(
                            title="Study Session Cancelled ❌",
                            description="Your session has been automatically cancelled.",
                            color=discord.Color.red(),
                            fields=[
                                ("Reason", "Left voice channel for too long", True),
                                ("Status", "Cancelled", True)
                            ],
                            footer="Join a voice channel and use /start to begin a new session"
                        )
                        await send_dm_safely(user, content=message, embed=embed)

                        # If there's a channel ID, notify there too
                        if channel_id:
                            channel = guild.get_channel(channel_id)
                            if channel:
                                notification = f"❌ {user.mention}'s study session was cancelled due to absence from voice channels."
                                await send_channel_message(channel, content=notification)

                        # Log to developer channel
                        await log_to_dev_channel(
                            self.bot,
                            f"Cancelled session for {get_user_display(user)} in guild {guild_id} due to voice channel absence",
                            "INFO"
                        )
                    else:
                        logger.warning(f"Could not find user {user_id} in guild {guild_id} for cancellation notification")
                        await log_to_dev_channel(
                            self.bot,
                            f"Could not find user {user_id} in guild {guild_id} for cancellation notification",
                            "WARNING"
                        )
            except Exception as msg_error:
                logger.error(f"Error sending cancellation message: {msg_error}")
                traceback.print_exc()
                await log_to_dev_channel(
                    self.bot,
                    f"Error sending cancellation message to user ID {user_id}: {msg_error}",
                    "ERROR"
                )

            # Clean up session data
            del self.active_sessions[session_key]

            logger.info(f"Cancelled session for user {user_id} in guild {guild_id}")

        except Exception as e:
            logger.error(f"Error cancelling session: {e}")
            traceback.print_exc()
            await log_to_dev_channel(
                self.bot,
                f"Error cancelling session for user ID {user_id} in guild {guild_id}: {e}",
                "ERROR"
            )

    async def send_completion_messages(self, user_id, guild_id, study_time):
        """Send completion messages to the user and channel"""
        try:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                logger.error(f"Could not find guild {guild_id}")
                await log_to_dev_channel(self.bot, f"Could not find guild {guild_id}", "ERROR")
                return

            user = guild.get_member(user_id)
            if not user:
                logger.error(f"Could not find user {user_id} in guild {guild_id}")
                await log_to_dev_channel(self.bot, f"Could not find user {get_user_display(user)} in guild {guild_id}", "ERROR")
                return

            # Create completion embed for DM
            embed = await create_embed(
                title="Study Session Completed ✅",
                description=f"Congratulations! You've completed your study session.",
                color=discord.Color.green(),
                fields=[
                    ("Time Studied", format_duration(study_time), True),
                    ("Status", "Completed", True)
                ]
            )

            # Send DM to user
            dm_sent = await send_dm_safely(user, embed=embed)

            # Fetch channel and send public message with ping
            channel_id = self.active_sessions.get((user_id, guild_id), {}).get('channel_id')
            if channel_id:
                try:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        # Create embed without mention
                        public_embed = await create_embed(
                            title="Study Session Completed ✅",
                            description=f"Session completed for a duration of {format_duration(study_time)}!",
                            color=discord.Color.green()
                        )

                        # Send message with ping in content (not in embed) to ensure notification
                        completion_message = f"🎉 **{user.mention} has completed their study session!** 🎉"
                        await send_channel_message(channel, content=completion_message, embed=public_embed)

                        # Log to developer channel
                        await log_to_dev_channel(
                            self.bot, 
                            f"User {get_user_display(user)} completed {study_time} minutes of study in guild {guild_id}",
                            "INFO"
                        )
                    else:
                        logger.error(f"Could not find channel {channel_id} in guild {guild_id}")
                        await log_to_dev_channel(self.bot, f"Could not find channel {channel_id} in guild {guild_id}", "ERROR")
                except Exception as channel_error:
                    logger.error(f"Error sending completion message to channel: {channel_error}")
                    await log_to_dev_channel(self.bot, f"Error sending completion message: {channel_error}", "ERROR")

            if not dm_sent:
                logger.warning(f"Could not send DM to user {get_user_display(user)}")
                await log_to_dev_channel(self.bot, f"Could not send DM to user {get_user_display(user)}", "WARNING")

        except Exception as e:
            logger.error(f"Error sending completion messages: {e}")
            traceback.print_exc()
            await log_to_dev_channel(self.bot, f"Error sending completion messages: {e}", "ERROR")

    async def handle_cooldown(self, user_id, guild_id, cooldown_end):
        """Handle cooldown timer and notification"""
        session_key = (user_id, guild_id)

        try:
            # Calculate sleep time until cooldown ends
            now = get_current_time()
            if cooldown_end <= now:
                return

            sleep_time = (cooldown_end - now).total_seconds()
            logger.info(f"Cooldown for user {user_id} in guild {guild_id} for {sleep_time:.2f}s")
            await log_to_dev_channel(
                self.bot, 
                f"Cooldown started for user ID {user_id} in guild {guild_id} - duration: {format_remaining_time(cooldown_end)}",
                "INFO"
            )

            # Sleep until the cooldown ends
            await asyncio.sleep(sleep_time)

            # Send notification to user
            guild = self.bot.get_guild(guild_id)
            if guild:
                user = guild.get_member(user_id)
                if user:
                    # Send DM to user
                    embed = await create_embed(
                        title="Cooldown Ended ⏲️",
                        description="Your cooldown has ended! You can now start a new study session.",
                        color=discord.Color.blue()
                    )
                    await send_dm_safely(user, embed=embed)

                    # Try to find user's voice channel to notify there as well
                    voice_state = user.voice
                    if voice_state and voice_state.channel:
                        embed = await create_embed(
                            title="Cooldown Ended ⏲️",
                            description="Cooldown period has completed.",
                            color=discord.Color.blue()
                        )
                        # Send with a ping in the content field
                        await send_channel_message(
                            voice_state.channel, 
                            content=f"📢 {user.mention} Your cooldown has ended! You can start a new study session.",
                            embed=None  # Skip embed, just use text with ping
                        )

                    # Log to developer channel
                    await log_to_dev_channel(
                        self.bot, 
                        f"Cooldown ended for user {get_user_display(user)} in guild {guild_id}",
                        "INFO"
                    )
                else:
                    logger.warning(f"Could not find user {user_id} in guild {guild_id} to notify about cooldown end")
                    await log_to_dev_channel(
                        self.bot, 
                        f"Could not find user {user_id} in guild {guild_id} to notify about cooldown end",
                        "WARNING"
                    )

            # Remove cooldown from database
            await self.db.remove_cooldown(user_id, guild_id)

            # Clean up cooldown task
            if session_key in self.cooldown_tasks:
                del self.cooldown_tasks[session_key]

        except asyncio.CancelledError:
            logger.info(f"Cooldown task for user {user_id} in guild {guild_id} was cancelled")
            await log_to_dev_channel(
                self.bot, 
                f"Cooldown task cancelled for user ID {user_id} in guild {guild_id}",
                "INFO"
            )
        except Exception as e:
            logger.error(f"Error in cooldown task for user {user_id} in guild {guild_id}: {e}")
            traceback.print_exc()
            await log_to_dev_channel(
                self.bot, 
                f"Error in cooldown task for user ID {user_id} in guild {guild_id}: {e}",
                "ERROR"
            )

    async def handle_grace_period(self, user_id, guild_id, grace_period_end):
        """Handle grace period for voice channel leave"""
        session_key = (user_id, guild_id)
        try:
            # Calculate sleep time until grace period ends
            now = get_current_time()
            if grace_period_end > now:
                sleep_time = (grace_period_end - now).total_seconds()
                logger.info(f"Grace period for user {user_id} in guild {guild_id} will end in {sleep_time:.2f}s")
                await asyncio.sleep(sleep_time)
            
            # Re-check current time after sleep to handle any delays
            now = get_current_time()
            if now < grace_period_end:
                logger.warning(f"Grace period for {session_key} woke up too early, remaining: {grace_period_end - now}")
                return

            logger.info(f"Processing grace period expiry for {session_key} at {now}")
            
            # Check if the session still exists
            if session_key not in self.active_sessions:
                logger.info(f"Session {session_key} no longer exists during grace period check")
                return

            session_data = self.active_sessions[session_key]
            voice_leave_time = session_data.get('voice_leave_time')
            
            # If voice_leave_time is not set, no action needed
            if not voice_leave_time:
                logger.info(f"No voice_leave_time set for {session_key} during grace period check")
                return

            # Calculate actual time since leaving the voice channel
            time_since_left = (now - voice_leave_time).total_seconds() / 60  # in minutes
            
            if time_since_left >= GRACE_PERIOD_MINUTES:
                logger.info(f"Canceling session {session_key} after {time_since_left:.1f} minutes (grace period exceeded)")
                session_id = session_data['id']
                await self.cancel_session(user_id, guild_id, session_id)
            else:
                logger.info(f"Session {session_key} not canceled (left for {time_since_left:.1f} minutes)")

        except asyncio.CancelledError:
            logger.info(f"Grace period task for {session_key} was cancelled")
            await log_to_dev_channel(
                self.bot, 
                f"Grace period task cancelled for {session_key}",
                "INFO"
            )
        except Exception as e:
            logger.error(f"Error in grace period task for {session_key}: {e}")
            traceback.print_exc()
            await log_to_dev_channel(
                self.bot, 
                f"Error in grace period task for {session_key}: {e}",
                "ERROR"
            )
        finally:
            # Clean up grace period task
            if session_key in self.grace_period_tasks:
                del self.grace_period_tasks[session_key]

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Track user voice channel presence"""
        user_id = member.id
        guild_id = member.guild.id
        session_key = (user_id, guild_id)

        # Check if the user has an active session
        if session_key not in self.active_sessions:
            return

        # User left a voice channel
        if before.channel and not after.channel:
            logger.info(f"User {user_id} left voice channel in guild {guild_id}")
            await log_to_dev_channel(
                self.bot,
                f"User {get_user_display(member)} left voice channel {before.channel.name} during active study session",
                "INFO"
            )

            # Set voice leave time
            leave_time = get_current_time()
            self.active_sessions[session_key]['voice_leave_time'] = leave_time
            await self.db.update_voice_leave_time(self.active_sessions[session_key]['id'], leave_time)

            # Start grace period task
            grace_period_end = leave_time + timedelta(minutes=GRACE_PERIOD_MINUTES)
            task = asyncio.create_task(
                self.handle_grace_period(user_id, guild_id, grace_period_end)
            )
            self.grace_period_tasks[session_key] = task

            # Notify user about grace period
            try:
                # Create informative embed
                embed = await create_embed(
                    title="⚠️ Voice Channel Left - Timer Started ⚠️",
                    description=f"You've left the voice channel during your study session.",
                    color=discord.Color.gold(),
                    fields=[
                        ("Grace Period", f"{GRACE_PERIOD_MINUTES} minutes", True),
                        ("Action Required", "Join any voice channel", True),
                        ("Otherwise", "Session will be cancelled", True)
                    ],
                    footer=f"Your session is currently paused but still active"
                )

                # Send DM with direct ping in content
                content = f"⚠️ **Please rejoin a voice channel within {GRACE_PERIOD_MINUTES} minutes or your study session will be cancelled!**"
                await send_dm_safely(member, content=content, embed=embed)

                # If the user left from a specific channel, send a message there too
                if before.channel:
                    # Only send to the channel if it's not a private/DM channel
                    if hasattr(before.channel, 'guild'):
                        await send_channel_message(
                            before.channel,
                            content=f"⚠️ {member.mention} has left the voice channel. Their study session will be cancelled in {GRACE_PERIOD_MINUTES} minutes if they don't rejoin.",
                            mention_user=None  # No need for another mention in the message
                        )
            except Exception as e:
                logger.error(f"Error sending grace period notification: {e}")
                traceback.print_exc()
                await log_to_dev_channel(
                    self.bot,
                    f"Error sending grace period notification to {get_user_display(member)}: {e}",
                    "ERROR"
                )

        # User joined a voice channel
        elif not before.channel and after.channel:
            # Verify session still exists and has an active grace period
            if session_key in self.active_sessions:
                session_data = self.active_sessions[session_key]
                voice_leave_time = session_data.get('voice_leave_time')
                
                if voice_leave_time:
                    now = get_current_time()
                    grace_period_end = voice_leave_time + timedelta(minutes=GRACE_PERIOD_MINUTES)
                    
                    # Critical check: Only allow resume if within grace period
                    if now > grace_period_end:
                        logger.info(f"User {user_id} rejoined after grace period expired in guild {guild_id}")
                        await log_to_dev_channel(
                            self.bot,
                            f"Session cancellation: {get_user_display(member)} rejoined after grace period",
                            "WARNING"
                        )
                        await self.cancel_session(user_id, guild_id, session_data['id'])
                        return  # Exit without resuming

                    logger.info(f"User {user_id} rejoined voice channel {after.channel.name} in guild {guild_id}")
                    await log_to_dev_channel(
                        self.bot,
                        f"User {get_user_display(member)} rejoined voice channel {after.channel.name} - resuming study session",
                        "INFO"
                    )

                    # Clear voice leave time
                    self.active_sessions[session_key]['voice_leave_time'] = None
                    await self.db.clear_voice_leave_time(session_data['id'])

                    # Cancel grace period task
                    if session_key in self.grace_period_tasks:
                        self.grace_period_tasks[session_key].cancel()
                        del self.grace_period_tasks[session_key]

                    # Notify user
                    try:
                        embed = await create_embed(
                            title="✅ Study Session Resumed",
                            description="You've rejoined a voice channel. Your study session will continue.",
                            color=discord.Color.green(),
                            fields=[
                                ("Status", "Active", True),
                                ("Channel", after.channel.name, True)
                            ]
                        )

                        # Send message both to user and to the channel they joined
                        await send_dm_safely(member, embed=embed)

                        # Send a welcome back message to the voice channel
                        await send_channel_message(
                            after.channel, 
                            content=f"✅ {member.mention} has rejoined. Their study session has resumed!",
                            embed=None
                        )
                    except Exception as e:
                        logger.error(f"Error sending rejoin notification: {e}")
                        traceback.print_exc()
                        await log_to_dev_channel(
                            self.bot,
                            f"Error sending rejoin notification to {get_user_display(member)}: {e}",
                            "ERROR"
                        )

    async def handle_button_action(self, interaction: discord.Interaction, view, action: str):
        """Handle button actions for session controls"""
        try:
            user_id = interaction.user.id
            guild_id = interaction.guild_id
            session_key = (user_id, guild_id)

            # Validate session exists
            if session_key not in self.active_sessions:
                await interaction.response.send_message(
                    "Your session no longer exists.",
                    ephemeral=True
                )
                return

            session_data = self.active_sessions[session_key]

            if action == "pause" and not session_data['is_paused']:
                # Pause the session
                pause_time = get_current_time()
                session_data['is_paused'] = True
                session_data['pause_time'] = pause_time
                await self.db.pause_session(session_data['id'], pause_time)

                # Update buttons
                view.update_buttons()

                # Create status embed
                embed = await self.create_status_embed(session_key)

                await interaction.response.edit_message(embed=embed, view=view)

            elif action == "resume" and session_data['is_paused']:
                # Resume the session
                resume_time = get_current_time()
                paused_duration = int((resume_time - session_data['pause_time']).total_seconds())

                # Update session data
                session_data['is_paused'] = False
                session_data['paused_duration'] += paused_duration
                session_data['pause_time'] = None

                # Update end time to account for pause
                original_end_time = session_data['end_time']
                adjusted_end_time = original_end_time + timedelta(seconds=paused_duration)
                session_data['end_time'] = adjusted_end_time

                # Update database
                await self.db.resume_session(session_data['id'], resume_time, paused_duration)

                # Update session task
                if session_key in self.session_tasks:
                    self.session_tasks[session_key].cancel()

                task = asyncio.create_task(
                    self.handle_session(user_id, guild_id, session_data['channel_id'], adjusted_end_time)
                )
                self.session_tasks[session_key] = task

                # Update buttons
                view.update_buttons()

                # Create status embed
                embed = await self.create_status_embed(session_key)

                await interaction.response.edit_message(embed=embed, view=view)

            elif action == "stop":
                # Calculate study time before stopping
                now = get_current_time()
                paused_duration = session_data['paused_duration']

                # If currently paused, account for current pause time
                if session_data['is_paused'] and session_data['pause_time']:
                    paused_duration += int((now - session_data['pause_time']).total_seconds())

                study_time = calculate_time_studied(
                    session_data['start_time'],
                    now,
                    paused_duration
                )

                # Cancel the session task
                if session_key in self.session_tasks:
                    self.session_tasks[session_key].cancel()

                # Update study stats if time studied is reasonable (> 1 minute)
                if study_time >= 1:
                    current_date = get_current_date_ist()
                    await self.db.update_study_stats(user_id, guild_id, current_date, study_time)

                # Mark session as completed
                await self.db.complete_session(session_data['id'])

                # Set cooldown
                cooldown_end = get_current_time() + timedelta(minutes=COOLDOWN_MINUTES)
                await self.db.set_cooldown(user_id, guild_id, cooldown_end)

                # Create cooldown task
                cooldown_task = asyncio.create_task(
                    self.handle_cooldown(user_id, guild_id, cooldown_end)
                )
                self.cooldown_tasks[session_key] = cooldown_task

                # Clean up session data
                del self.active_sessions[session_key]

                # Disable all buttons
                for child in view.children:
                    child.disabled = True

                embed = await create_embed(
                    title="Study Session Completed ✅",
                    description=f"Your study session has been stopped. You studied for {format_duration(study_time)}.",
                    color=discord.Color.red(),
                    fields=[
                        ("Time studied", format_duration(study_time), True),
                        ("Cooldown ends in", format_remaining_time(cooldown_end), True)
                    ]
                )

                await interaction.response.edit_message(embed=embed, view=view)

            else:
                if action == "pause" and session_data['is_paused']:
                    await interaction.response.send_message(
                        "Your session is already paused.",
                        ephemeral=True
                    )

                if action == "resume" and not session_data['is_paused']:
                    await interaction.response.send_message(
                        "Your session is not paused.",
                        ephemeral=True
                    )

        except Exception as e:
            logger.error(f"Error handling button action: {e}")
            traceback.print_exc()
            await interaction.response.send_message(
                "An error occurred while processing your request.",
                ephemeral=True
            )

    async def create_status_embed(self, session_key):
        """Create a session status embed with current information"""
        if session_key not in self.active_sessions:
            return await create_embed(
                title="No Active Session",
                description="You don't have an active study session.",
                color=discord.Color.light_grey()
            )

        session_data = self.active_sessions[session_key]
        now = get_current_time()
        remaining_time = format_remaining_time(session_data['end_time'])

        # Determine status and color
        if session_data['is_paused']:
            status = "Paused ⏸️"
            status_description = "Your study session is currently paused."
            color = discord.Color.gold()
        else:
            status = "Active ✅"
            status_description = "Your study session is in progress."
            color = discord.Color.green()

        # Calculate elapsed time
        elapsed = now - session_data['start_time']
        elapsed_seconds = int(elapsed.total_seconds())

        # Subtract paused duration
        paused_seconds = session_data['paused_duration']
        if session_data['is_paused'] and session_data['pause_time']:
            paused_seconds += int((now - session_data['pause_time']).total_seconds())

        studied_seconds = max(0, elapsed_seconds - paused_seconds)
        studied_minutes = studied_seconds // 60

        # Format start and end times in IST
        start_time_str = session_data['start_time'].astimezone(IST).strftime('%H:%M:%S')
        end_time_str = session_data['end_time'].astimezone(IST).strftime('%H:%M:%S')

        # Calculate progress percentage
        total_duration = session_data['duration'] * 60  # in seconds
        progress = min(100, int((studied_seconds / total_duration) * 100))

        # Create progress bar
        progress_bar = "⏳ "
        bar_length = 10
        filled = int(progress / 100 * bar_length)
        progress_bar += "█" * filled + "░" * (bar_length - filled)
        progress_bar += f" {progress}%"

        return await create_embed(
            title=f"Study Session Status: {status}",
            description=status_description,
            color=color,
            fields=[
                ("Duration", format_duration(session_data['duration']), True),
                ("Time Studied", format_duration(studied_minutes), True),
                ("Time Remaining", remaining_time, True),
                ("Start Time", f"{start_time_str} IST", True),
                ("End Time", f"{end_time_str} IST", True),
                ("Progress", progress_bar, False),
            ]
        )

    @app_commands.command(name="status", description="Check your current study session status")
    async def check_status(self, interaction: discord.Interaction):
        """Check the status of your current study session with interactive controls"""
        try:
            user_id = interaction.user.id
            guild_id = interaction.guild_id
            session_key = (user_id, guild_id)

            # Check if user has an active session
            if session_key not in self.active_sessions:
                # Check if on cooldown
                cooldown = await self.db.get_cooldown(user_id, guild_id)
                if cooldown:
                    cooldown_end = datetime.fromisoformat(cooldown[0])
                    if cooldown_end > get_current_time():
                        time_left = format_remaining_time(cooldown_end)
                        embed = await create_embed(
                            title="On Cooldown ⏲️",
                            description=f"You're on cooldown! You can start a new session in {time_left}.",
                            color=discord.Color.gold()
                        )
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                        return

                embed = await create_embed(
                    title="No Active Session",
                    description="You don't have an active study session. Use `/start` to begin one.",
                    color=discord.Color.light_grey()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Create the session control buttons
            view = SessionControlButtons(self, user_id, guild_id)

            # Create the status embed
            embed = await self.create_status_embed(session_key)

            # Send the response with buttons
            await interaction.response.send_message(embed=embed, view=view)

        except Exception as e:
            logger.error(f"Error checking status: {e}")
            traceback.print_exc()
            await interaction.response.send_message(
                "An error occurred while checking your session status.",
                ephemeral=True
            )

    @app_commands.command(name="leaderboard", description="View the daily study leaderboard")
    async def show_leaderboard(self, interaction: discord.Interaction):
        """Show the daily study leaderboard with enhanced visuals"""
        try:
            guild_id = interaction.guild_id
            current_date = get_current_date_ist()

            # Get goal information if enabled
            goal_info = await self.db.get_guild_goal(guild_id)
            goal_enabled = goal_info and goal_info[1]
            goal_minutes = goal_info[0] if goal_info else 0

            # Get leaderboard data
            leaderboard_data = await self.db.get_leaderboard(guild_id, current_date)

            if not leaderboard_data:
                embed = await create_embed(
                    title="📅 Today's Leaderboard",
                    description="No one has studied today yet! Be the first one to start a session. 🚀",
                    color=discord.Color.blue()
                )
                await interaction.response.send_message(embed=embed)
                return

            # Prepare leaderboard entries with medals and emojis
            entries = []
            medal_emojis = ["🥇", "🥈", "🥉"]

            # Calculate total study time for all users
            total_minutes = sum(study_time for _, study_time, _ in leaderboard_data)
            total_sessions = sum(sessions for _, _, sessions in leaderboard_data)

            for i, (user_id, study_time, sessions_completed) in enumerate(leaderboard_data[:10]):
                try:
                    member = interaction.guild.get_member(user_id)
                    name = member.display_name if member else f"User {user_id}"

                    # Add medal for top 3
                    rank_display = medal_emojis[i] if i < 3 else f"`{i+1}.`"

                    # Create a visual progress indicator
                    if goal_enabled and goal_minutes > 0:
                        percentage = get_percentage_of_goal(study_time, goal_minutes)
                        progress_blocks = min(10, int(percentage / 10))
                        progress_bar = "▰" * progress_blocks + "▱" * (10 - progress_blocks)

                        # Add fire emoji for users who completed their goal
                        goal_indicator = " 🔥" if percentage >= 100 else ""

                        entries.append(
                            f"{rank_display} **{name}**{goal_indicator}\n" +
                            f"⏱️ {format_duration(study_time)} ({sessions_completed} sessions)\n" +
                            f"📊 `{progress_bar}` {percentage}% of goal\n"
                        )
                    else:
                        # Show study time relative to top performer
                        if i == 0 and len(leaderboard_data) > 1:  # If this is the top performer and there's more than one person
                            entries.append(
                                f"{rank_display} **{name}** 👑\n" +
                                f"⏱️ {format_duration(study_time)} ({sessions_completed} sessions)\n"
                            )
                        else:
                            # Add a small sparkle for users with multiple sessions
                            session_indicator = " ✨" if sessions_completed > 2 else ""
                            entries.append(
                                f"{rank_display} **{name}**{session_indicator}\n" +
                                f"⏱️ {format_duration(study_time)} ({sessions_completed} sessions)\n"
                            )
                except Exception as user_error:
                    logger.error(f"Error processing user {user_id} for leaderboard: {user_error}")

            # Create leaderboard embed
            formatted_date = datetime.now(IST).strftime('%d %b %Y')

            title = f"📅 Study Leaderboard for {formatted_date}"
            if goal_enabled:
                title += f" (Goal: {format_duration(goal_minutes)})"

            # Calculate server stats
            server_stats = f"**Server Stats:** {format_duration(total_minutes)} studied across {total_sessions} sessions today 💪"

            description = server_stats + "\n\n" + "\n".join(entries)

            embed = await create_embed(
                title=title,
                description=description,
                color=discord.Color.blue(),
                footer=f"✨ Resets at midnight IST | Use /summary to see previous days' stats"
            )

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"Error showing leaderboard: {e}")
            traceback.print_exc()
            await interaction.response.send_message(
                "An error occurred while fetching the leaderboard.",
                ephemeral=True
            )

    @app_commands.command(name="summary", description="View study summary for a specific date")
    @app_commands.describe(date="Optional date in DD-MM-YYYY format (defaults to yesterday)")
    async def show_summary(self, interaction: discord.Interaction, date: str = None):
        """Show the study summary for a specific date (defaults to yesterday)"""
        try:
            guild_id = interaction.guild_id

            # Parse date parameter or default to yesterday
            if date:
                target_date = get_date_from_string(date)
                if not target_date:
                    await interaction.response.send_message(
                        "Invalid date format. Please use DD-MM-YYYY format (e.g., 15-04-2025).",
                        ephemeral=True
                    )
                    return
            else:
                target_date = get_previous_date_ist()

            # Format for display
            display_date = datetime.strptime(target_date, '%Y-%m-%d').strftime('%d %b %Y')

            # Get stats for the selected date
            stats = await self.db.get_all_stats_for_date(guild_id, target_date)

            if not stats:
                await interaction.response.send_message(
                    f"📊 No one studied on {display_date}.",
                    ephemeral=True
                )
                return

            # Get goal information
            goal_info = await self.db.get_guild_goal(guild_id)
            goal_enabled = goal_info and goal_info[1]
            goal_minutes = goal_info[0] if goal_info else 0

            # Prepare summary entries
            entries = []
            total_time = 0
            total_sessions = 0

            # Add medal emojis for top performers
            medal_emojis = ["🥇", "🥈", "🥉"]

            for i, (user_id, study_time, sessions_completed) in enumerate(sorted(stats, key=lambda x: x[1], reverse=True)):
                try:
                    member = interaction.guild.get_member(user_id)
                    name = member.display_name if member else f"User {user_id}"

                    # Add medal for top 3
                    rank_display = medal_emojis[i] if i < 3 else f"{i+1}."

                    if goal_enabled and goal_minutes > 0:
                        percentage = get_percentage_of_goal(study_time, goal_minutes)
                        goal_indicator = "🔥" if percentage >= 100 else ""
                        entries.append(
                            f"{rank_display} **{name}** {goal_indicator}\n" +
                            f"⏱️ {format_duration(study_time)} ({sessions_completed} sessions) - {percentage}% of goal"
                        )
                    else:
                        # Add sparkle for multiple sessions
                        session_indicator = "✨" if sessions_completed > 2 else ""
                        entries.append(
                            f"{rank_display} **{name}** {session_indicator}\n" +
                            f"⏱️ {format_duration(study_time)} ({sessions_completed} sessions)"
                        )

                    total_time += study_time
                    total_sessions += sessions_completed
                except Exception as user_error:
                    logger.error(f"Error processing user {user_id} for summary: {user_error}")

            # Create summary embed
            title = f"📅 Study Summary for {display_date}"
            if goal_enabled:
                title += f" (Goal: {format_duration(goal_minutes)})"

            description = "\n\n".join(entries)

            # Calculate average if there are entries
            avg_time = total_time // len(stats) if stats else 0

            embed = await create_embed(
                title=title,
                description=description,
                color=discord.Color.purple(),
                fields=[
                    ("⏱️ Total Study Time", format_duration(total_time), True),
                    ("🔢 Total Sessions", str(total_sessions), True),
                    ("📊 Average per User", format_duration(avg_time), True)
                ],
                footer="✨ Use /leaderboard to see today's stats | Format: DD-MM-YYYY"
            )

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"Error showing summary: {e}")
            traceback.print_exc()
            await interaction.response.send_message(
                "An error occurred while fetching the summary.",
                ephemeral=True
            )

    @app_commands.command(name="stats", description="View your personal study statistics")
    async def show_stats(self, interaction: discord.Interaction):
        """Show personalized study statistics for the user"""
        try:
            user_id = interaction.user.id
            guild_id = interaction.guild_id

            # Get current date and date 7 days ago
            current_date = get_current_date_ist()
            today = datetime.strptime(current_date, '%Y-%m-%d')
            week_start = (today - timedelta(days=6)).strftime('%Y-%m-%d')  # Last 7 days (including today)

            # Get user's current day stats
            today_stats = await self.db.get_user_stats(user_id, guild_id, current_date)
            today_time = today_stats[0] if today_stats else 0
            today_sessions = today_stats[1] if today_stats else 0

            # Gather all stats for the week
            week_total_time = 0
            week_total_sessions = 0
            daily_stats = []

            # Get daily stats for the past week
            for i in range(7):
                day_date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
                day_stats = await self.db.get_user_stats(user_id, guild_id, day_date)

                if day_stats:
                    day_time, day_sessions = day_stats
                    week_total_time += day_time
                    week_total_sessions += day_sessions

                    # Format date for display
                    display_date = (today - timedelta(days=i)).strftime('%a, %d %b')
                    daily_stats.append((display_date, day_time, day_sessions))

            # Check if user has any stats at all
            if week_total_time == 0:
                embed = await create_embed(
                    title="📊 Your Study Statistics",
                    description=f"You haven't recorded any study sessions yet. Use `/start` to begin your first session!",
                    color=discord.Color.blue()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Get current active session info
            session_key = (user_id, guild_id)
            active_session_info = ""
            if session_key in self.active_sessions:
                session_data = self.active_sessions[session_key]
                is_paused = session_data['is_paused']
                status = "⏸️ Paused" if is_paused else "✅ Active"

                # Calculate elapsed time
                now = get_current_time()
                paused_duration = session_data['paused_duration']
                if is_paused and session_data['pause_time']:
                    paused_duration += int((now - session_data['pause_time']).total_seconds())

                elapsed = now - session_data['start_time']
                elapsed_seconds = int(elapsed.total_seconds()) - paused_duration
                elapsed_minutes = max(0, elapsed_seconds // 60)

                remaining_time = format_remaining_time(session_data['end_time'])
                active_session_info = f"**Current Session**: {status}\n" + \
                                     f"⏱️ Elapsed: {format_duration(elapsed_minutes)}\n" + \
                                     f"⏳ Remaining: {remaining_time}\n\n"

            # Create weekly progress visualization
            week_progress = "\n".join([
                f"**{date}**: {'▓' * (min(10, time // 15))}{'░' * (10 - min(10, time // 15))} {format_duration(time)}" 
                for date, time, _ in daily_stats
            ])

            # Get goal information
            goal_info = await self.db.get_guild_goal(guild_id)
            goal_enabled = goal_info and goal_info[1]
            goal_minutes = goal_info[0] if goal_info else 0

            goal_progress = ""
            if goal_enabled and goal_minutes > 0:
                today_percentage = get_percentage_of_goal(today_time, goal_minutes)
                progress_blocks = min(10, int(today_percentage / 10))
                progress_bar = "█" * progress_blocks + "░" * (10 - progress_blocks)

                goal_progress = f"**Daily Goal Progress**: {today_percentage}%\n" + \
                               f"[{progress_bar}] {format_duration(today_time)}/{format_duration(goal_minutes)}\n\n"

            # Build the embed
            embed = await create_embed(
                title=f"📊 Study Statistics for {interaction.user.display_name}",
                description=f"{active_session_info}{goal_progress}**Weekly Overview**:\n{week_progress}",
                color=discord.Color.blue(),
                fields=[
                    ("⏱️ Today", f"{format_duration(today_time)} ({today_sessions} sessions)", True),
                    ("📅 This Week", f"{format_duration(week_total_time)} ({week_total_sessions} sessions)", True),
                    ("🏆 Daily Average", format_duration(week_total_time // 7), True),
                ],
                footer="✨ Keep up the good work! Use /start to begin a new session."
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error showing user stats: {e}")
            traceback.print_exc()
            await interaction.response.send_message(
                "An error occurred while fetching your statistics.",
                ephemeral=True
            )

    @app_commands.command(name="setgoal", description="Set a daily study goal for the server (admin only)")
    @app_commands.describe(minutes="Goal duration in minutes")
    @app_commands.default_permissions(administrator=True)
    async def set_goal(self, interaction: discord.Interaction, minutes: int):
        """Set a daily study goal for the server"""
        try:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "You need administrator permissions to use this command.",
                    ephemeral=True
                )
                return

            guild_id = interaction.guild_id
            user_id = interaction.user.id

            if minutes <= 0:
                await interaction.response.send_message(
                    "Goal duration must be positive!",
                    ephemeral=True
                )
                return

            # Set goal in database (enabled by default)
            await self.db.set_guild_goal(guild_id, minutes, user_id, True)

            embed = await create_embed(
                title="Study Goal Set",
                description=f"The daily study goal for this server has been set to {format_duration(minutes)}.",
                color=discord.Color.green(),
                footer="Goal is now enabled. Use /enablegoal or /disablegoal to toggle it."
            )

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"Error setting goal: {e}")
            await interaction.response.send_message(
                "An error occurred while setting the goal.",
                ephemeral=True
            )

    @app_commands.command(name="enablegoal", description="Enable the daily study goal (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def enable_goal(self, interaction: discord.Interaction):
        """Enable the daily study goal"""
        try:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "You need administrator permissions to use this command.",
                    ephemeral=True
                )
                return

            guild_id = interaction.guild_id

            # Get current goal
            goal_info = await self.db.get_guild_goal(guild_id)
            if not goal_info:
                await interaction.response.send_message(
                    "No study goal has been set for this server yet. Use `/setgoal` first.",
                    ephemeral=True
                )
                return

            goal_minutes = goal_info[0]
            is_enabled = goal_info[1]

            if is_enabled:
                await interaction.response.send_message(
                    f"The study goal of {format_duration(goal_minutes)} is already enabled.",
                    ephemeral=True
                )
                return

            # Enable goal
            await self.db.toggle_goal(guild_id, True)

            embed = await create_embed(
                title="Study Goal Enabled",
                description=f"The daily study goal of {format_duration(goal_minutes)} has been enabled.",
                color=discord.Color.green()
            )

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"Error enabling goal: {e}")
            await interaction.response.send_message(
                "An error occurred while enabling the goal.",
                ephemeral=True
            )

    @app_commands.command(name="disablegoal", description="Disable the daily study goal (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def disable_goal(self, interaction: discord.Interaction):
        """Disable the daily study goal"""
        try:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "You need administrator permissions to use this command.",
                    ephemeral=True
                )
                return

            guild_id = interaction.guild_id

            # Get current goal
            goal_info = await self.db.get_guild_goal(guild_id)
            if not goal_info:
                await interaction.response.send_message(
                    "No study goal has been set for this server yet. Use `/setgoal` first.",
                    ephemeral=True
                )
                return

            goal_minutes = goal_info[0]
            is_enabled = goal_info[1]

            if not is_enabled:
                await interaction.response.send_message(
                    f"The study goal of {format_duration(goal_minutes)} is already disabled.",
                    ephemeral=True
                )
                return

            # Disable goal
            await self.db.toggle_goal(guild_id, False)

            embed = await create_embed(
                title="Study Goal Disabled",
                description=f"The daily study goal of {format_duration(goal_minutes)} has been disabled.",
                color=discord.Color.red()
            )

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"Error disabling goal: {e}")
            await interaction.response.send_message(
                "An error occurred while disabling the goal.",
                ephemeral=True
            )

async def setup(bot: StoicBot):
    """Load the cog"""
    await bot.add_cog(StudySession(bot))