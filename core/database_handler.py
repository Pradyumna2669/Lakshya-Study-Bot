import aiosqlite
import firebase_admin
from firebase_admin import credentials, db
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, AsyncGenerator

class DatabaseHandler:
    def __init__(self):
        self.db_path = "data/moderation.db"
        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)
        self.conn = None
        self.firebase_app = None
        self.firebase_ref = None

    async def initialize(self):
        """Initialize async components"""
        await self.connect()
        await self._initialize_firebase()

    async def _initialize_firebase(self):
        """Initialize Firebase connection"""
        try:
            cred = credentials.Certificate("firebase-service-account.json")
            self.firebase_app = firebase_admin.initialize_app(cred, {
                'databaseURL': 'https://YOUR_PROJECT.firebaseio.com'
            })
            self.firebase_ref = db.reference('servers')
            logging.info("Firebase connection established")
        except Exception as e:
            logging.error(f"Firebase init failed: {e}")

    async def _setup_firebase_listener(self):
        """Setup Firebase realtime listener"""
        def callback(event):
            asyncio.create_task(self._process_firebase_update(event))

        self.firebase_ref.listen(callback)

    async def _process_firebase_update(self, event):
        """Handle incoming Firebase updates"""
        if not event.data:  # Ignore delete events
            return

        guild_id = event.path.split('/')[-1]
        if not guild_id.isdigit():
            return

        async with self._sync_lock:
            try:
                await self.connect()
                await self.conn.execute('''
                    INSERT OR REPLACE INTO server_config 
                    (guild_id, mod_role_id, admin_role_id, elder_role_id, 
                     mute_role_id, compliance_role_id, afk_channel_id, ticket_category_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    int(guild_id),
                    event.data.get('mod_role_id', 0),
                    event.data.get('admin_role_id', 0),
                    event.data.get('elder_role_id', 0),
                    event.data.get('mute_role_id', 0),
                    event.data.get('compliance_role_id', 0),
                    event.data.get('afk_channel_id', 0),
                    event.data.get('ticket_category_id', 0)
                ))
                await self.conn.commit()
                logging.info(f"Synced config for guild {guild_id} from Firebase")
            except Exception as e:
                logging.error(f"Firebase sync error: {e}")

    async def connect(self):
        """Establish and maintain database connection"""
        if not self.conn:
            self.conn = await aiosqlite.connect(self.db_path)
            self.conn.row_factory = aiosqlite.Row
            await self._initialize_tables()

    async def close(self):
        """Properly close database connection"""
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def _initialize_tables(self):
        """Create tables with proper schema"""
        await self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS server_config (
                guild_id INTEGER PRIMARY KEY,
                mod_role_id INTEGER NOT NULL,
                admin_role_id INTEGER NOT NULL,
                elder_role_id INTEGER NOT NULL,
                mute_role_id INTEGER NOT NULL,
                compliance_role_id INTEGER NOT NULL,
                afk_channel_id INTEGER NOT NULL,
                log_channel_id INTEGER NOT NULL,
                ticket_category_id INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS monitored_vcs (
                guild_id INTEGER,
                vc_id INTEGER,
                PRIMARY KEY (guild_id, vc_id)
            );
            
            CREATE TABLE IF NOT EXISTS voice_logging (
                guild_id INTEGER PRIMARY KEY,
                enabled BOOLEAN DEFAULT FALSE,
                log_channel_id INTEGER
            );
                                      
            CREATE TABLE IF NOT EXISTS exam_countdowns (
                guild_id INTEGER,
                name TEXT,
                date TEXT,  
                channel_id INTEGER
            );                 

            CREATE TABLE IF NOT EXISTS sticky_messages (
                guild_id INTEGER NOT NULL,
                source_channel_id INTEGER NOT NULL,
                target_channel_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                message_id INTEGER,
                PRIMARY KEY (guild_id, source_channel_id)
            );
                                      
            CREATE TABLE IF NOT EXISTS ticket_config (
                guild_id INTEGER PRIMARY KEY,
                category_id INTEGER NOT NULL,
                support_role_id INTEGER NOT NULL,
                log_channel_id INTEGER NOT NULL,
                max_tickets INTEGER DEFAULT 3
            );

            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                claimed_by INTEGER DEFAULT NULL,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP DEFAULT NULL,
                reason TEXT
            );

            CREATE INDEX IF NOT EXISTS warnings_guild_user 
            ON warnings (guild_id, user_id);
        ''')
        await self.conn.commit()

    # Server Configuration
    async def get_server_config(self, guild_id: int) -> Optional[Dict]:
        await self.connect()
        async with self.conn.execute(
            "SELECT * FROM server_config WHERE guild_id = ?", 
            (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def set_server_config(self, guild_id: int, **config):
        """Updated with Firebase sync"""
        await self.connect()
        
        required_fields = {
            'mod_role_id': 0,
            'admin_role_id': 0,
            'elder_role_id': 0,
            'mute_role_id': 0,
            'compliance_role_id': 0,
            'afk_channel_id': 0,
            'log_channel_id': 0,
            'ticket_category_id': 0
        }

        # Update with provided values
        for field in required_fields:
            if field in config:
                required_fields[field] = config[field]

        # Prepare SQL
        columns = ['guild_id'] + list(required_fields.keys())
        values = [guild_id] + list(required_fields.values())

        try:
            # Update SQLite
            await self.conn.execute(f'''
                INSERT OR REPLACE INTO server_config 
                ({', '.join(columns)})
                VALUES ({', '.join(['?']*len(values))})
            ''', values)
            await self.conn.commit()

            # Sync to Firebase (excluding log_channel_id)
            if self.firebase_ref:
                fb_data = {
                    k: v for k, v in required_fields.items() 
                    if k != 'log_channel_id'
                }
                self.firebase_ref.child(str(guild_id)).set(fb_data)

            return True
        except Exception as e:
            logging.error(f"Config save error: {e}")
            return False

    # Warnings System
    async def add_warning(self, guild_id: int, user_id: int, 
                        moderator_id: int, reason: str):
        await self.connect()
        await self.conn.execute('''
            INSERT INTO warnings 
            (guild_id, user_id, moderator_id, reason)
            VALUES (?, ?, ?, ?)
        ''', (guild_id, user_id, moderator_id, reason))
        await self.conn.commit()

    async def get_warnings(self, guild_id: int, user_id: int) -> List[Dict]:
        await self.connect()
        async with self.conn.execute('''
            SELECT reason, created_at FROM warnings 
            WHERE guild_id = ? AND user_id = ?
            ORDER BY created_at DESC
        ''', (guild_id, user_id)) as cursor:
            return [dict(row) async for row in cursor]

    async def delete_warnings(self, guild_id: int, user_id: int, 
                            count: int) -> int:
        await self.connect()
        async with self.conn.execute('''
            DELETE FROM warnings 
            WHERE rowid IN (
                SELECT rowid FROM warnings 
                WHERE guild_id = ? AND user_id = ?
                ORDER BY created_at ASC 
                LIMIT ?
            )
        ''', (guild_id, user_id, count)) as cursor:
            changes = cursor.rowcount
        await self.conn.commit()
        return changes

    # Voice Channel Monitoring
    async def add_monitored_vc(self, guild_id: int, vc_id: int):
        await self.connect()
        await self.conn.execute(
            "INSERT OR IGNORE INTO monitored_vcs VALUES (?, ?)", 
            (guild_id, vc_id)
        )
        await self.conn.commit()

    async def remove_monitored_vc(self, guild_id: int, vc_id: int):
        await self.connect()
        await self.conn.execute(
            "DELETE FROM monitored_vcs WHERE guild_id = ? AND vc_id = ?", 
            (guild_id, vc_id)
        )
        await self.conn.commit()

    async def get_monitored_vcs(self, guild_id: int) -> List[int]:
        await self.connect()
        async with self.conn.execute(
            "SELECT vc_id FROM monitored_vcs WHERE guild_id = ?", 
            (guild_id,)
        ) as cursor:
            return [row['vc_id'] async for row in cursor]

    async def get_all_monitored_vcs(self) -> List[Dict]:
        await self.connect()
        async with self.conn.execute(
            "SELECT guild_id, vc_id FROM monitored_vcs"
        ) as cursor:
            return [dict(row) async for row in cursor]

    # Ticket System
    async def create_ticket(self, ticket_id: str, guild_id: int, 
                          user_id: int, channel_id: int):
        await self.connect()
        await self.conn.execute('''
            INSERT INTO tickets 
            (ticket_id, guild_id, user_id, channel_id)
            VALUES (?, ?, ?, ?)
        ''', (ticket_id, guild_id, user_id, channel_id))
        await self.conn.commit()

    async def close_ticket(self, ticket_id: str, reason: str):
        await self.connect()
        await self.conn.execute('''
            UPDATE tickets 
            SET status = 'closed',
                closed_at = ?,
                reason = ?
            WHERE ticket_id = ?
        ''', (datetime.utcnow(), reason, ticket_id))
        await self.conn.commit()

    async def get_ticket(self, ticket_id: str) -> Optional[Dict]:
        await self.connect()
        async with self.conn.execute(
            "SELECT * FROM tickets WHERE ticket_id = ?", 
            (ticket_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_ticket_by_channel(self, channel_id: int) -> Optional[Dict]:
        await self.connect()
        async with self.conn.execute(
            "SELECT * FROM tickets WHERE channel_id = ?", 
            (channel_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    # Utility Methods
    async def execute(self, query: str, params: tuple = ()):
        await self.connect()
        await self.conn.execute(query, params)
        await self.conn.commit()
        
    async def enable_voice_logging(self, guild_id: int, channel_id: int):
        """Enable voice logging for a server and set the log channel"""
        await self.connect()
        await self.conn.execute('''
            INSERT OR REPLACE INTO voice_logging 
            (guild_id, enabled, log_channel_id)
            VALUES (?, TRUE, ?)
        ''', (guild_id, channel_id))
        await self.conn.commit()

    async def disable_voice_logging(self, guild_id: int):
        """Disable voice logging for a server"""
        await self.connect()
        await self.conn.execute('''
            UPDATE voice_logging 
            SET enabled = FALSE 
            WHERE guild_id = ?
        ''', (guild_id,))
        await self.conn.commit()

    async def get_voice_logging_config(self, guild_id: int) -> Optional[dict]:
        """Get the voice logging configuration for a server"""
        await self.connect()
        async with self.conn.execute(
            "SELECT enabled, log_channel_id FROM voice_logging WHERE guild_id = ?", 
            (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def is_voice_logging_enabled(self, guild_id: int) -> bool:
        """Check if voice logging is enabled for a server"""
        config = await self.get_voice_logging_config(guild_id)
        return config.get('enabled', False) if config else False

    async def get_voice_log_channel(self, guild_id: int) -> Optional[int]:
        """Get the voice log channel ID for a server"""
        config = await self.get_voice_logging_config(guild_id)
        return config.get('log_channel_id') if config else None
    
    # Exam Countdown System
    async def fetchall(self, query: str, params: tuple = ()) -> List[Dict]:
        await self.connect()
        async with self.conn.execute(query, params) as cursor:
            return [dict(row) async for row in cursor]

    async def add_exam_countdown(self, guild_id: int, name: str, date: str, channel_id: int):
        query = """
        INSERT OR REPLACE INTO exam_countdowns (guild_id, name, date, channel_id)
        VALUES (?, ?, ?, ?)
        """
        await self.execute(query, (guild_id, name, date, channel_id))
    
    async def get_all_exam_countdowns(self):
        query = "SELECT * FROM exam_countdowns"
        rows = await self.fetchall(query)
        return [
            {
                "guild_id": row["guild_id"],
                "name": row["name"],
                "date": row["date"],
                "channel_id": row["channel_id"]
            }
            for row in rows
        ]

    async def remove_exam_countdown(self, guild_id: int, name: str):
        query = "DELETE FROM exam_countdowns WHERE guild_id = ? AND name = ?"
        await self.execute(query, (guild_id, name))

   # Add these methods to your DatabaseHandler class

    async def fetchone(self, query: str, params: tuple = ()) -> Optional[dict]:
        """Execute query and return first result"""
        await self.connect()
        async with self.conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_sticky(self, guild_id: int, source_channel_id: int) -> Optional[dict]:
        """Get sticky message configuration"""
        query = """
        SELECT * FROM sticky_messages 
        WHERE guild_id = ? AND source_channel_id = ?
        """
        return await self.fetchone(query, (guild_id, source_channel_id))

    async def get_all_stickies(self) -> List[dict]:
        """Get all sticky messages across all servers"""
        query = "SELECT * FROM sticky_messages"
        return await self.fetchall(query)

    async def set_sticky(self, guild_id: int, source_channel_id: int, 
                    target_channel_id: int, title: str, 
                    description: str, message_id: int):
        """Create or update a sticky message"""
        query = """
        INSERT OR REPLACE INTO sticky_messages 
        (guild_id, source_channel_id, target_channel_id, title, description, message_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """
        await self.execute(query, (guild_id, source_channel_id, target_channel_id, 
                                title, description, message_id))

    async def update_sticky_message_id(self, guild_id: int, 
                                    source_channel_id: int, message_id: int):
        """Update existing sticky's message ID"""
        query = """
        UPDATE sticky_messages 
        SET message_id = ? 
        WHERE guild_id = ? AND source_channel_id = ?
        """
        await self.execute(query, (message_id, guild_id, source_channel_id))

    async def remove_sticky(self, guild_id: int, source_channel_id: int):
        """Remove a sticky message configuration"""
        query = """
        DELETE FROM sticky_messages 
        WHERE guild_id = ? AND source_channel_id = ?
        """
        await self.execute(query, (guild_id, source_channel_id))
    
    # Ticket System
    # Add to existing Ticket System section
    async def set_ticket_config(self, guild_id: int, category_id: int, 
                            support_role_id: int, log_channel_id: int, 
                            max_tickets: int):
        await self.execute('''
            INSERT OR REPLACE INTO ticket_config 
            (guild_id, category_id, support_role_id, log_channel_id, max_tickets)
            VALUES (?, ?, ?, ?, ?)
        ''', (guild_id, category_id, support_role_id, log_channel_id, max_tickets))

    async def get_ticket_config(self, guild_id: int) -> Optional[dict]:
        return await self.fetchone(
            "SELECT * FROM ticket_config WHERE guild_id = ?",
            (guild_id,)
        )

    async def get_user_tickets(self, guild_id: int, user_id: int, 
                            status: str = "open") -> List[dict]:
        return await self.fetchall(
            "SELECT * FROM tickets WHERE guild_id = ? AND user_id = ? AND status = ?",
            (guild_id, user_id, status)
        )

    async def get_guild_tickets(self, guild_id: int) -> List[dict]:
        return await self.fetchall(
            "SELECT * FROM tickets WHERE guild_id = ?",
            (guild_id,)
        )

    # Update existing create_ticket method to include voice channel
    async def create_ticket(self, guild_id: int, user_id: int, 
                        vc_id: int, channel_id: int) -> int:
        ticket_id = f"{guild_id}-{user_id}-{int(datetime.now().timestamp())}"
        await self.execute('''
            INSERT INTO tickets 
            (ticket_id, guild_id, user_id, vc_id, channel_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (ticket_id, guild_id, user_id, vc_id, channel_id, datetime.utcnow()))
        return ticket_id

    # Update existing close_ticket method to use channel_id
    async def close_ticket(self, channel_id: int, reason: str):
        await self.execute('''
            UPDATE tickets 
            SET status = 'closed', closed_at = ?, reason = ?
            WHERE channel_id = ?
        ''', (datetime.utcnow(), reason, channel_id))