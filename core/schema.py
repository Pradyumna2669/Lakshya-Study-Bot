import sqlite3
import logging
import os
import asyncio
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Ensure data directory exists
Path("data").mkdir(exist_ok=True)

class Database:
    def __init__(self, db_name="study_session.db"):
        self.db_path = os.path.join("data", db_name)
        self.conn = None
        self.lock = asyncio.Lock()
        self._initialize_db()
        
    def _initialize_db(self):
        """Initialize the database with required tables"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create sessions table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP NOT NULL,
                duration INTEGER NOT NULL,
                is_completed BOOLEAN DEFAULT FALSE,
                is_paused BOOLEAN DEFAULT FALSE,
                pause_time TIMESTAMP,
                paused_duration INTEGER DEFAULT 0,
                voice_leave_time TIMESTAMP
            )
            ''')
            
            # Create study_stats table for daily statistics
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS study_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                study_time INTEGER DEFAULT 0,
                sessions_completed INTEGER DEFAULT 0,
                UNIQUE(user_id, guild_id, date)
            )
            ''')
            
            # Create study_goals table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS study_goals (
                guild_id INTEGER PRIMARY KEY,
                goal_minutes INTEGER NOT NULL,
                is_enabled BOOLEAN DEFAULT FALSE,
                set_by INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # Create cooldown table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS cooldowns (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                end_time TIMESTAMP NOT NULL,
                PRIMARY KEY (user_id, guild_id)
            )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise
    
    async def _get_connection(self):
        """Get a database connection"""
        return sqlite3.connect(self.db_path)
    
    async def execute(self, query, params=None):
        """Execute a database query with a connection lock"""
        async with self.lock:
            conn = await self._get_connection()
            try:
                cursor = conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                conn.commit()
                result = cursor.lastrowid
                return result
            except Exception as e:
                conn.rollback()
                logger.error(f"Database execution error: {e}\nQuery: {query}\nParams: {params}")
                raise
            finally:
                conn.close()
    
    async def fetch_one(self, query, params=None):
        """Fetch a single row from the database"""
        async with self.lock:
            conn = await self._get_connection()
            try:
                cursor = conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                return cursor.fetchone()
            except Exception as e:
                logger.error(f"Database fetch error: {e}\nQuery: {query}\nParams: {params}")
                raise
            finally:
                conn.close()
    
    async def fetch_all(self, query, params=None):
        """Fetch all rows from the database"""
        async with self.lock:
            conn = await self._get_connection()
            try:
                cursor = conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                return cursor.fetchall()
            except Exception as e:
                logger.error(f"Database fetch error: {e}\nQuery: {query}\nParams: {params}")
                raise
            finally:
                conn.close()
    
    # Session Management
    async def create_session(self, user_id, guild_id, channel_id, start_time, end_time, duration):
        """Create a new study session"""
        query = '''
        INSERT INTO sessions 
        (user_id, guild_id, channel_id, start_time, end_time, duration, is_completed, is_paused)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        '''
        return await self.execute(query, (user_id, guild_id, channel_id, start_time, end_time, duration, False, False))
    
    async def get_active_session(self, user_id, guild_id):
        """Get the active session for a user in a guild"""
        query = '''
        SELECT * FROM sessions 
        WHERE user_id = ? AND guild_id = ? AND is_completed = FALSE
        ORDER BY id DESC LIMIT 1
        '''
        return await self.fetch_one(query, (user_id, guild_id))
    
    async def get_all_active_sessions(self):
        """Get all active sessions (for bot restart)"""
        query = "SELECT * FROM sessions WHERE is_completed = FALSE"
        return await self.fetch_all(query)
    
    async def complete_session(self, session_id):
        """Mark a session as completed"""
        query = "UPDATE sessions SET is_completed = TRUE WHERE id = ?"
        await self.execute(query, (session_id,))
    
    async def pause_session(self, session_id, pause_time):
        """Pause a session"""
        query = "UPDATE sessions SET is_paused = TRUE, pause_time = ? WHERE id = ?"
        await self.execute(query, (pause_time, session_id))
    
    async def resume_session(self, session_id, pause_time, paused_duration):
        """Resume a paused session"""
        query = '''
        UPDATE sessions 
        SET is_paused = FALSE, pause_time = NULL, paused_duration = paused_duration + ? 
        WHERE id = ?
        '''
        await self.execute(query, (paused_duration, session_id))
    
    async def update_voice_leave_time(self, session_id, leave_time):
        """Update the time when user left the voice channel"""
        query = "UPDATE sessions SET voice_leave_time = ? WHERE id = ?"
        await self.execute(query, (leave_time, session_id))
    
    async def clear_voice_leave_time(self, session_id):
        """Clear the voice leave time when user rejoins"""
        query = "UPDATE sessions SET voice_leave_time = NULL WHERE id = ?"
        await self.execute(query, (session_id,))
    
    # Study Stats Management
    async def update_study_stats(self, user_id, guild_id, date, study_time, sessions=1):
        """Update study statistics for a user"""
        query = '''
        INSERT INTO study_stats (user_id, guild_id, date, study_time, sessions_completed)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, guild_id, date) 
        DO UPDATE SET study_time = study_time + ?, sessions_completed = sessions_completed + ?
        '''
        await self.execute(query, (user_id, guild_id, date, study_time, sessions, study_time, sessions))
    
    async def get_leaderboard(self, guild_id, date):
        """Get the leaderboard for a guild on a specific date"""
        query = '''
        SELECT user_id, study_time, sessions_completed 
        FROM study_stats 
        WHERE guild_id = ? AND date = ? 
        ORDER BY study_time DESC
        '''
        return await self.fetch_all(query, (guild_id, date))
    
    async def get_user_stats(self, user_id, guild_id, date):
        """Get study stats for a specific user"""
        query = '''
        SELECT study_time, sessions_completed 
        FROM study_stats 
        WHERE user_id = ? AND guild_id = ? AND date = ?
        '''
        return await self.fetch_one(query, (user_id, guild_id, date))
    
    async def get_all_stats_for_date(self, guild_id, date):
        """Get all users' stats for a specific date"""
        query = '''
        SELECT user_id, study_time, sessions_completed 
        FROM study_stats 
        WHERE guild_id = ? AND date = ? 
        '''
        return await self.fetch_all(query, (guild_id, date))
    
    # Goal Management
    async def set_guild_goal(self, guild_id, goal_minutes, set_by, enabled=True):
        """Set a study goal for a guild"""
        query = '''
        INSERT INTO study_goals (guild_id, goal_minutes, is_enabled, set_by)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id) 
        DO UPDATE SET goal_minutes = ?, is_enabled = ?, set_by = ?, created_at = CURRENT_TIMESTAMP
        '''
        await self.execute(query, (guild_id, goal_minutes, enabled, set_by, goal_minutes, enabled, set_by))
    
    async def toggle_goal(self, guild_id, enabled):
        """Enable or disable a guild's study goal"""
        query = "UPDATE study_goals SET is_enabled = ? WHERE guild_id = ?"
        await self.execute(query, (enabled, guild_id))
    
    async def get_guild_goal(self, guild_id):
        """Get the study goal for a guild"""
        query = "SELECT goal_minutes, is_enabled FROM study_goals WHERE guild_id = ?"
        return await self.fetch_one(query, (guild_id,))
    
    # Cooldown Management
    async def set_cooldown(self, user_id, guild_id, end_time):
        """Set a cooldown for a user"""
        query = '''
        INSERT INTO cooldowns (user_id, guild_id, end_time)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, guild_id) 
        DO UPDATE SET end_time = ?
        '''
        await self.execute(query, (user_id, guild_id, end_time, end_time))
    
    async def get_cooldown(self, user_id, guild_id):
        """Get the cooldown for a user"""
        query = "SELECT end_time FROM cooldowns WHERE user_id = ? AND guild_id = ?"
        return await self.fetch_one(query, (user_id, guild_id))
    
    async def remove_cooldown(self, user_id, guild_id):
        """Remove a cooldown for a user"""
        query = "DELETE FROM cooldowns WHERE user_id = ? AND guild_id = ?"
        await self.execute(query, (user_id, guild_id))
