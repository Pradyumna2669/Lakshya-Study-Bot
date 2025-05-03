
import os
import sqlite3
import json
import logging
from typing import List, Dict, Optional, Any, Tuple

logger = logging.getLogger(__name__)

# Database path
DB_PATH = "data/tickets.db"

def setup_database():
    """Initialize the database and create necessary tables if they don't exist"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create server configurations table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS server_config (
        server_id INTEGER PRIMARY KEY,
        admin_roles TEXT,
        transcript_channel_id INTEGER,
        ticket_counter INTEGER DEFAULT 0,
        github_repo TEXT,
        ticket_category_id INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Add ticket_category_id column if it doesn't exist
    cursor.execute('''
    PRAGMA table_info(server_config)
    ''')
    columns = cursor.fetchall()
    if not any(col[1] == 'ticket_category_id' for col in columns):
        cursor.execute('''
        ALTER TABLE server_config ADD COLUMN ticket_category_id INTEGER DEFAULT 0
        ''')
    
    # Create ticket types table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ticket_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER,
        name TEXT,
        description TEXT,
        assigned_roles TEXT,
        color INTEGER,
        modal_enabled BOOLEAN DEFAULT 0,
        modal_questions TEXT,
        FOREIGN KEY (server_id) REFERENCES server_config (server_id)
    )
    ''')

    # Add modal columns if they don't exist
    cursor.execute('''
    PRAGMA table_info(ticket_types)
    ''')
    columns = cursor.fetchall()
    if not any(col[1] == 'modal_enabled' for col in columns):
        cursor.execute('''
        ALTER TABLE ticket_types ADD COLUMN modal_enabled BOOLEAN DEFAULT 0
        ''')
    if not any(col[1] == 'modal_questions' for col in columns):
        cursor.execute('''
        ALTER TABLE ticket_types ADD COLUMN modal_questions TEXT
        ''')
    
    # Create active tickets table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS active_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER,
        channel_id INTEGER,
        ticket_number INTEGER,
        creator_id INTEGER,
        ticket_type_id INTEGER,
        claimed_by INTEGER,
        status TEXT DEFAULT 'open',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (server_id) REFERENCES server_config (server_id),
        FOREIGN KEY (ticket_type_id) REFERENCES ticket_types (id)
    )
    ''')
    
    # Create cooldowns table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_cooldowns (
        user_id INTEGER,
        server_id INTEGER,
        last_ticket TIMESTAMP,
        PRIMARY KEY (user_id, server_id)
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database setup completed")

def get_server_config(server_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve server configuration"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT * FROM server_config WHERE server_id = ?",
        (server_id,)
    )
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        result = dict(row)
        # Parse JSON fields
        if result.get('admin_roles'):
            result['admin_roles'] = json.loads(result['admin_roles'])
        return result
    
    return None

def save_server_config(
    server_id: int,
    admin_roles: List[int],
    transcript_channel_id: int,
    github_repo: str,
    ticket_category_id: int = 0
) -> bool:
    """Save or update server configuration"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            '''
            INSERT INTO server_config 
                (server_id, admin_roles, transcript_channel_id, github_repo, ticket_category_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(server_id) DO UPDATE SET
                admin_roles = ?,
                transcript_channel_id = ?,
                github_repo = ?,
                ticket_category_id = ?
            ''',
            (
                server_id,
                json.dumps(admin_roles),
                transcript_channel_id,
                github_repo,
                ticket_category_id,
                json.dumps(admin_roles),
                transcript_channel_id,
                github_repo,
                ticket_category_id
            )
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error saving server config: {e}")
        conn.rollback()
        conn.close()
        return False

def get_ticket_types(server_id: int) -> List[Dict[str, Any]]:
    """Get all ticket types for a server"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT * FROM ticket_types WHERE server_id = ?",
        (server_id,)
    )
    
    rows = cursor.fetchall()
    conn.close()
    
    ticket_types = []
    for row in rows:
        ticket_type = dict(row)
        # Parse JSON fields
        if ticket_type.get('assigned_roles'):
            ticket_type['assigned_roles'] = json.loads(ticket_type['assigned_roles'])
        
        # Parse modal questions
        if ticket_type.get('modal_questions'):
            try:
                ticket_type['modal_questions'] = json.loads(ticket_type['modal_questions'])
            except json.JSONDecodeError:
                ticket_type['modal_questions'] = []
        else:
            ticket_type['modal_questions'] = []
        
        # Convert modal_enabled from integer to boolean
        ticket_type['modal_enabled'] = bool(ticket_type.get('modal_enabled', 0))
        
        ticket_types.append(ticket_type)
    
    return ticket_types

def get_ticket_type(server_id: int, type_id: int) -> Optional[Dict[str, Any]]:
    """Get a specific ticket type by ID"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT * FROM ticket_types WHERE id = ? AND server_id = ?",
        (type_id, server_id)
    )
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
    
    ticket_type = dict(row)
    
    # Parse JSON fields
    if ticket_type.get('assigned_roles'):
        ticket_type['assigned_roles'] = json.loads(ticket_type['assigned_roles'])
    
    # Parse modal questions
    if ticket_type.get('modal_questions'):
        try:
            ticket_type['modal_questions'] = json.loads(ticket_type['modal_questions'])
        except json.JSONDecodeError:
            ticket_type['modal_questions'] = []
    else:
        ticket_type['modal_questions'] = []
    
    # Convert modal_enabled from integer to boolean
    ticket_type['modal_enabled'] = bool(ticket_type.get('modal_enabled', 0))
    
    return ticket_type

def set_modal_questions(server_id: int, type_id: int, enabled: bool, questions: List[str]) -> bool:
    """Set or update modal questions for a ticket type"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """
            UPDATE ticket_types
            SET modal_enabled = ?, modal_questions = ?
            WHERE id = ? AND server_id = ?
            """,
            (1 if enabled else 0, json.dumps(questions), type_id, server_id)
        )
        
        conn.commit()
        conn.close()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error setting modal questions: {e}")
        conn.rollback()
        conn.close()
        return False

def save_ticket_type(
    server_id: int,
    name: str,
    description: str,
    assigned_roles: List[int],
    color: int,
    modal_enabled: bool = False,
    modal_questions: Optional[List[str]] = None,
    type_id: Optional[int] = None
) -> int:
    """Save or update a ticket type and return its ID"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Convert modal_questions list to JSON if provided
        questions_json = json.dumps(modal_questions or [])
        
        if type_id:
            # Update existing ticket type
            cursor.execute(
                '''
                UPDATE ticket_types 
                SET name = ?, description = ?, assigned_roles = ?, color = ?,
                    modal_enabled = ?, modal_questions = ?
                WHERE id = ? AND server_id = ?
                ''',
                (name, description, json.dumps(assigned_roles), color,
                 1 if modal_enabled else 0, questions_json, type_id, server_id)
            )
            new_id = type_id
        else:
            # Create new ticket type
            cursor.execute(
                '''
                INSERT INTO ticket_types 
                (server_id, name, description, assigned_roles, color, modal_enabled, modal_questions)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (server_id, name, description, json.dumps(assigned_roles), color,
                 1 if modal_enabled else 0, questions_json)
            )
            new_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        return new_id or -1  # Return -1 if new_id is None
    except Exception as e:
        logger.error(f"Error saving ticket type: {e}")
        conn.rollback()
        conn.close()
        return -1

def delete_ticket_type(server_id: int, type_id: int) -> bool:
    """Delete a ticket type"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "DELETE FROM ticket_types WHERE id = ? AND server_id = ?",
            (type_id, server_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error deleting ticket type: {e}")
        conn.rollback()
        conn.close()
        return False

def create_ticket(
    server_id: int,
    channel_id: int,
    creator_id: int,
    ticket_type_id: int
) -> Tuple[int, int]:
    """Create a new ticket and return (ticket db id, ticket number)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Increment the server's ticket counter
        cursor.execute(
            """
            UPDATE server_config
            SET ticket_counter = ticket_counter + 1
            WHERE server_id = ?
            RETURNING ticket_counter
            """,
            (server_id,)
        )
        
        result = cursor.fetchone()
        if not result:
            # If server config doesn't exist, create it with counter = 1
            cursor.execute(
                """
                INSERT INTO server_config (server_id, ticket_counter)
                VALUES (?, 1)
                """,
                (server_id,)
            )
            ticket_number = 1
        else:
            ticket_number = result[0]
        
        # Create the ticket entry
        cursor.execute(
            """
            INSERT INTO active_tickets 
            (server_id, channel_id, ticket_number, creator_id, ticket_type_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (server_id, channel_id, ticket_number, creator_id, ticket_type_id)
        )
        
        ticket_id = cursor.lastrowid
        
        # Update user cooldown
        cursor.execute(
            """
            INSERT OR REPLACE INTO user_cooldowns (user_id, server_id, last_ticket)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (creator_id, server_id)
        )
        
        conn.commit()
        conn.close()
        return (ticket_id, ticket_number)
    except Exception as e:
        logger.error(f"Error creating ticket: {e}")
        conn.rollback()
        conn.close()
        return (-1, -1)

def get_ticket_by_channel(channel_id: int) -> Optional[Dict[str, Any]]:
    """Get ticket information by channel ID"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute(
        """
        SELECT t.*, tt.name as type_name, tt.assigned_roles 
        FROM active_tickets t
        JOIN ticket_types tt ON t.ticket_type_id = tt.id
        WHERE t.channel_id = ?
        """,
        (channel_id,)
    )
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        ticket = dict(row)
        if ticket.get('assigned_roles'):
            ticket['assigned_roles'] = json.loads(ticket['assigned_roles'])
        return ticket
    
    return None

def claim_ticket(channel_id: int, user_id: int) -> bool:
    """Claim a ticket"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """
            UPDATE active_tickets
            SET claimed_by = ?, status = 'claimed'
            WHERE channel_id = ?
            """,
            (user_id, channel_id)
        )
        
        conn.commit()
        conn.close()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error claiming ticket: {e}")
        conn.rollback()
        conn.close()
        return False

def close_ticket(channel_id: int) -> Optional[Dict[str, Any]]:
    """Close a ticket and return its data"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # Get ticket data before deleting
        cursor.execute(
            """
            SELECT t.*, tt.name as type_name, sc.transcript_channel_id, sc.github_repo
            FROM active_tickets t
            JOIN ticket_types tt ON t.ticket_type_id = tt.id
            JOIN server_config sc ON t.server_id = sc.server_id
            WHERE t.channel_id = ?
            """,
            (channel_id,)
        )
        
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return None
        
        # Update ticket status
        cursor.execute(
            """
            UPDATE active_tickets
            SET status = 'closed'
            WHERE channel_id = ?
            """,
            (channel_id,)
        )
        
        ticket_data = dict(row)
        
        conn.commit()
        conn.close()
        return ticket_data
    except Exception as e:
        logger.error(f"Error closing ticket: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return None

def check_user_cooldown(user_id: int, server_id: int, cooldown_seconds: int = 300) -> bool:
    """
    Check if user is on cooldown (True = can create ticket, False = on cooldown)
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        """
        SELECT (strftime('%s', 'now') - strftime('%s', last_ticket)) as elapsed_seconds
        FROM user_cooldowns
        WHERE user_id = ? AND server_id = ?
        """,
        (user_id, server_id)
    )
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return True  # No previous ticket, no cooldown
    
    elapsed_seconds = row[0]
    return elapsed_seconds > cooldown_seconds

def delete_ticket_record(channel_id: int) -> bool:
    """Delete ticket from active_tickets table after it's fully closed"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "DELETE FROM active_tickets WHERE channel_id = ?",
            (channel_id,)
        )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error deleting ticket record: {e}")
        conn.rollback()
        conn.close()
        return False

def set_ticket_category(server_id: int, category_id: int) -> bool:
    """Set or update the category where tickets should be created"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """
            UPDATE server_config
            SET ticket_category_id = ?
            WHERE server_id = ?
            """,
            (category_id, server_id)
        )
        
        # If no server config exists yet, create one
        if cursor.rowcount == 0:
            cursor.execute(
                """
                INSERT INTO server_config (server_id, ticket_category_id)
                VALUES (?, ?)
                """,
                (server_id, category_id)
            )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error setting ticket category: {e}")
        conn.rollback()
        conn.close()
        return False

def get_ticket_category(server_id: int) -> int:
    """Get the category ID where tickets should be created"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        """
        SELECT ticket_category_id
        FROM server_config
        WHERE server_id = ?
        """,
        (server_id,)
    )
    
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0]:
        return row[0]
    
    return 0  # Return 0 if no category set (will create in guild root)
