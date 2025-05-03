import aiosqlite
import logging
import datetime
import os
from typing import List, Dict, Tuple, Optional, Any

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Ensure database directory exists
os.makedirs(os.path.dirname(os.path.abspath(__file__)), exist_ok=True)

DB_PATH = "data/study_sessions.db"


class StudyDatabase:
    """
    Handles all database operations for the StudySession cog.
    Uses aiosqlite for non-blocking database access.
    """

    async def _ensure_tables_exist(self, db: aiosqlite.Connection) -> None:
        """
        Ensure all required database tables exist.
        Including study_goals table for tracking guild study goals.
        """
        await db.execute('''
        CREATE TABLE IF NOT EXISTS study_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            duration INTEGER NOT NULL,  -- in seconds
            date TEXT NOT NULL,
            timestamp INTEGER NOT NULL
        )
        ''')
        
        await db.execute('''
        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            total_time INTEGER NOT NULL DEFAULT 0,  -- in seconds
            UNIQUE(user_id, guild_id, date)
        )
        ''')
        await db.execute('''
        CREATE TABLE active_sessions (
            user_id BIGINT PRIMARY KEY,
            start_time DOUBLE PRECISION NOT NULL,
            end_time DOUBLE PRECISION NOT NULL,
            guild_id BIGINT NOT NULL,
            original_duration INTEGER NOT NULL,
            voice_channel_id BIGINT NOT NULL,
            text_channel_id BIGINT NOT NULL
        )
        ''')
        
        # Create study_goals table for tracking guild daily goals
        await db.execute('''
        CREATE TABLE IF NOT EXISTS study_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL UNIQUE,
            goal_minutes INTEGER NOT NULL DEFAULT 60,
            is_enabled BOOLEAN NOT NULL DEFAULT 0
        )
        ''')
        
        await db.commit()

    async def add_completed_session(self, user_id: int, guild_id: int, duration: int) -> None:
        """
        Record a completed study session in the database.
        
        Args:
            user_id: Discord user ID
            guild_id: Discord guild (server) ID
            duration: Session duration in seconds
        """
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            timestamp = int(datetime.datetime.now().timestamp())
            
            async with aiosqlite.connect(DB_PATH) as db:
                await self._ensure_tables_exist(db)
                
                # Add to sessions table
                await db.execute(
                    "INSERT INTO study_sessions (user_id, guild_id, duration, date, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (user_id, guild_id, duration, today, timestamp)
                )
                
                # Update daily stats
                await db.execute('''
                INSERT INTO daily_stats (user_id, guild_id, date, total_time)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id, date) 
                DO UPDATE SET total_time = total_time + ?
                ''', (user_id, guild_id, today, duration, duration))
                
                await db.commit()
                
            logger.debug(f"Added completed session for user {user_id} in guild {guild_id} with duration {duration}s")
        except Exception as e:
            logger.error(f"Error adding completed session to database: {e}")
            raise

    async def get_daily_leaderboard(self, guild_id: int, limit: int = 10) -> List[Tuple[int, int]]:
        """
        Get the daily leaderboard for a specific guild.
        
        Args:
            guild_id: Discord guild (server) ID
            limit: Maximum number of users to return
            
        Returns:
            List of tuples with (user_id, total_time_in_seconds)
        """
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            
            async with aiosqlite.connect(DB_PATH) as db:
                await self._ensure_tables_exist(db)
                
                cursor = await db.execute(
                    "SELECT user_id, total_time FROM daily_stats WHERE guild_id = ? AND date = ? ORDER BY total_time DESC LIMIT ?",
                    (guild_id, today, limit)
                )
                
                results = await cursor.fetchall()
                return results
                
        except Exception as e:
            logger.error(f"Error getting daily leaderboard: {e}")
            return []

    async def get_summary(self, guild_id: int, days: int = 7) -> Dict[str, List[Tuple[int, int]]]:
        """
        Get summary data for previous days.
        
        Args:
            guild_id: Discord guild (server) ID
            days: Number of previous days to fetch
            
        Returns:
            Dictionary mapping date string to list of (user_id, total_time) tuples
        """
        try:
            result = {}
            today = datetime.datetime.now().date()
            
            async with aiosqlite.connect(DB_PATH) as db:
                await self._ensure_tables_exist(db)
                
                for i in range(1, days + 1):
                    date = today - datetime.timedelta(days=i)
                    date_str = date.strftime("%Y-%m-%d")
                    
                    cursor = await db.execute(
                        "SELECT user_id, total_time FROM daily_stats WHERE guild_id = ? AND date = ? ORDER BY total_time DESC LIMIT 5",
                        (guild_id, date_str)
                    )
                    
                    day_results = await cursor.fetchall()
                    result[date_str] = day_results
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting summary: {e}")
            return {}

    async def reset_daily_stats(self) -> None:
        """
        Reset all daily statistics.
        Called at midnight to start tracking a new day.
        Note: This doesn't delete data, just ensures new tracking starts.
        """
        try:
            logger.info("Daily stats reset - new day started")
            # No action needed since we're tracking with dates
            # New entries will be created with today's date
        except Exception as e:
            logger.error(f"Error resetting daily stats: {e}")

    async def set_guild_study_goal(self, guild_id: int, goal_minutes: int) -> bool:
        """
        Set the daily study goal for a guild.
        
        Args:
            guild_id: Discord guild (server) ID
            goal_minutes: Goal duration in minutes
            
        Returns:
            True if successful, False otherwise
        """
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await self._ensure_tables_exist(db)
                
                # Insert or update goal
                await db.execute('''
                INSERT INTO study_goals (guild_id, goal_minutes)
                VALUES (?, ?)
                ON CONFLICT(guild_id) 
                DO UPDATE SET goal_minutes = ?
                ''', (guild_id, goal_minutes, goal_minutes))
                
                await db.commit()
                logger.debug(f"Set study goal for guild {guild_id} to {goal_minutes} minutes")
                return True
                
        except Exception as e:
            logger.error(f"Error setting guild study goal: {e}")
            return False
            
    async def enable_guild_study_goal(self, guild_id: int) -> bool:
        """
        Enable the daily study goal challenge for a guild.
        
        Args:
            guild_id: Discord guild (server) ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await self._ensure_tables_exist(db)
                
                # Check if goal exists, create default if not
                cursor = await db.execute(
                    "SELECT goal_minutes FROM study_goals WHERE guild_id = ?",
                    (guild_id,)
                )
                result = await cursor.fetchone()
                
                if not result:
                    # Create default goal (60 minutes)
                    await db.execute(
                        "INSERT INTO study_goals (guild_id, goal_minutes, is_enabled) VALUES (?, 60, 1)",
                        (guild_id,)
                    )
                else:
                    # Enable existing goal
                    await db.execute(
                        "UPDATE study_goals SET is_enabled = 1 WHERE guild_id = ?",
                        (guild_id,)
                    )
                
                await db.commit()
                logger.debug(f"Enabled study goal for guild {guild_id}")
                return True
                
        except Exception as e:
            logger.error(f"Error enabling guild study goal: {e}")
            return False
            
    async def disable_guild_study_goal(self, guild_id: int) -> bool:
        """
        Disable the daily study goal challenge for a guild.
        
        Args:
            guild_id: Discord guild (server) ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await self._ensure_tables_exist(db)
                
                # Disable goal
                await db.execute(
                    "UPDATE study_goals SET is_enabled = 0 WHERE guild_id = ?",
                    (guild_id,)
                )
                
                await db.commit()
                logger.debug(f"Disabled study goal for guild {guild_id}")
                return True
                
        except Exception as e:
            logger.error(f"Error disabling guild study goal: {e}")
            return False
    
    async def get_guild_study_goal(self, guild_id: int) -> Optional[Tuple[int, bool]]:
        """
        Get the daily study goal for a guild.
        
        Args:
            guild_id: Discord guild (server) ID
            
        Returns:
            Tuple of (goal_minutes, is_enabled) or None if not set
        """
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await self._ensure_tables_exist(db)
                
                cursor = await db.execute(
                    "SELECT goal_minutes, is_enabled FROM study_goals WHERE guild_id = ?",
                    (guild_id,)
                )
                
                result = await cursor.fetchone()
                if not result:
                    return None
                
                return result[0], bool(result[1])
                
        except Exception as e:
            logger.error(f"Error getting guild study goal: {e}")
            return None
    
    async def get_challenge_leaderboard(self, guild_id: int, limit: int = 10) -> List[Tuple[int, int, float]]:
        """
        Get the daily goal challenge leaderboard for a specific guild.
        Ranks users by percentage of goal completed.
        
        Args:
            guild_id: Discord guild (server) ID
            limit: Maximum number of users to return
            
        Returns:
            List of tuples with (user_id, total_time_in_seconds, percentage_completed)
        """
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            
            async with aiosqlite.connect(DB_PATH) as db:
                await self._ensure_tables_exist(db)
                
                # Get goal minutes
                cursor = await db.execute(
                    "SELECT goal_minutes FROM study_goals WHERE guild_id = ?",
                    (guild_id,)
                )
                result = await cursor.fetchone()
                
                if not result:
                    # No goal set, return empty list instead of regular leaderboard
                    # We can't return the regular leaderboard as it has a different return type
                    return []
                
                goal_seconds = result[0] * 60
                
                # Get daily stats with percentage
                cursor = await db.execute(f'''
                SELECT 
                    user_id, 
                    total_time, 
                    CAST(total_time AS FLOAT) / {goal_seconds} * 100 AS percentage
                FROM daily_stats 
                WHERE guild_id = ? AND date = ? 
                ORDER BY percentage DESC
                LIMIT ?
                ''', (guild_id, today, limit))
                
                results = await cursor.fetchall()
                return results
                
        except Exception as e:
            logger.error(f"Error getting challenge leaderboard: {e}")
            return []
    
    async def get_user_stats(self, user_id: int, guild_id: int) -> Optional[Tuple[int, int, int, int, int]]:
        """
        Get statistics for a specific user.
        
        Args:
            user_id: Discord user ID
            guild_id: Discord guild (server) ID
            
        Returns:
            Tuple of (daily_time, weekly_time, total_time, daily_rank, total_sessions)
        """
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            week_start = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
            
            async with aiosqlite.connect(DB_PATH) as db:
                await self._ensure_tables_exist(db)
                
                # Get daily time
                cursor = await db.execute(
                    "SELECT total_time FROM daily_stats WHERE user_id = ? AND guild_id = ? AND date = ?",
                    (user_id, guild_id, today)
                )
                daily_result = await cursor.fetchone()
                daily_time = daily_result[0] if daily_result else 0
                
                # Get weekly time
                cursor = await db.execute(
                    "SELECT SUM(total_time) FROM daily_stats WHERE user_id = ? AND guild_id = ? AND date >= ?",
                    (user_id, guild_id, week_start)
                )
                weekly_result = await cursor.fetchone()
                weekly_time = weekly_result[0] if weekly_result and weekly_result[0] else 0
                
                # Get total time
                cursor = await db.execute(
                    "SELECT SUM(duration) FROM study_sessions WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id)
                )
                total_result = await cursor.fetchone()
                total_time = total_result[0] if total_result and total_result[0] else 0
                
                # Get daily rank
                cursor = await db.execute('''
                SELECT rank FROM (
                    SELECT user_id, RANK() OVER (ORDER BY total_time DESC) as rank
                    FROM daily_stats
                    WHERE guild_id = ? AND date = ?
                ) WHERE user_id = ?
                ''', (guild_id, today, user_id))
                rank_result = await cursor.fetchone()
                daily_rank = rank_result[0] if rank_result else None
                
                # Get total sessions
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM study_sessions WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id)
                )
                sessions_result = await cursor.fetchone()
                total_sessions = sessions_result[0] if sessions_result else 0
                
                if daily_time == 0 and weekly_time == 0 and total_time == 0 and total_sessions == 0:
                    return None
                    
                return daily_time, weekly_time, total_time, daily_rank, total_sessions
            
    
                
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return None


    async def save_active_session(self, user_id: int, session_data: dict):
        """Save session to database"""
        query = """
            INSERT INTO active_sessions 
            (user_id, start_time, end_time, guild_id, original_duration, 
            voice_channel_id, text_channel_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id) DO UPDATE SET
                start_time = EXCLUDED.start_time,
                end_time = EXCLUDED.end_time,
                guild_id = EXCLUDED.guild_id,
                original_duration = EXCLUDED.original_duration,
                voice_channel_id = EXCLUDED.voice_channel_id,
                text_channel_id = EXCLUDED.text_channel_id
        """
        await self.bot.pool.execute(
            query,
            user_id,
            session_data['start_time'],
            session_data['end_time'],
            session_data['guild_id'],
            session_data['original_duration'],
            session_data['voice_channel_id'],
            session_data['text_channel_id']
        )

    async def get_active_sessions(self) -> List[dict]:
        """Retrieve all active sessions"""
        query = "SELECT * FROM active_sessions"
        return await self.bot.pool.fetch(query)

    async def delete_active_session(self, user_id: int):
        """Remove session from database"""
        query = "DELETE FROM active_sessions WHERE user_id = $1"
        await self.bot.pool.execute(query, user_id)