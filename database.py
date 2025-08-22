import sqlite3
import uuid
import datetime

DB_NAME = 'licenses.db'

def init_db():
    """Initializes the database and creates/updates the table."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    print("Initializing database...")

    # Create table if it doesn't exist
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS license_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            duration_minutes INTEGER NOT NULL,
            allowed_id_number TEXT,
            is_active INTEGER DEFAULT 0,
            start_time TIMESTAMP,
            active_id_number TEXT,
            active_receiver_email TEXT
        )
    ''')

    # Add new columns if they don't exist (for backward compatibility)
    try:
        cursor.execute("ALTER TABLE license_keys ADD COLUMN active_id_number TEXT")
        cursor.execute("ALTER TABLE license_keys ADD COLUMN active_receiver_email TEXT")
        print("Added new columns for active user tracking.")
    except sqlite3.OperationalError:
        # This will fail if the columns already exist, which is fine.
        print("Columns for active user tracking already exist.")
        pass

    conn.commit()
    conn.close()
    print("Database initialized successfully.")

def generate_key(duration_minutes, allowed_id_number=None):
    """Generates a new unique license key."""
    new_key = str(uuid.uuid4())
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if allowed_id_number and not allowed_id_number.strip():
        allowed_id_number = None
    cursor.execute(
        "INSERT INTO license_keys (key, duration_minutes, allowed_id_number) VALUES (?, ?, ?)",
        (new_key, duration_minutes, allowed_id_number)
    )
    conn.commit()
    conn.close()
    return new_key

def get_key(key):
    """Retrieves a license key's data."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM license_keys WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def activate_key(key, id_number, receiver_email):
    """Marks a key as active, sets its start time, and logs the user."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    start_time = datetime.datetime.now()
    cursor.execute(
        "UPDATE license_keys SET is_active = 1, start_time = ?, active_id_number = ?, active_receiver_email = ? WHERE key = ?",
        (start_time, id_number, receiver_email, key)
    )
    conn.commit()
    conn.close()

def deactivate_key(key):
    """Marks a key as inactive and clears the active user info."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE license_keys SET is_active = 0, active_id_number = NULL, active_receiver_email = NULL WHERE key = ?",
        (key,)
    )
    conn.commit()
    conn.close()

def get_active_keys():
    """Retrieves all currently active keys for the license manager."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM license_keys WHERE is_active = 1")
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        row_dict = dict(row)
        # Ensure start_time is a datetime object for calculations
        if row_dict.get('start_time'):
            row_dict['start_time'] = datetime.datetime.fromisoformat(row_dict['start_time'])
        results.append(row_dict)
    return results

def get_all_keys():
    """Retrieves all license keys for the admin dashboard."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM license_keys ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def delete_key(key_id):
    """Deletes a key from the database."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM license_keys WHERE id = ?", (key_id,))
    conn.commit()
    conn.close()

def extend_key_duration(key_id, minutes_to_add):
    """Adds time to a key's start time, effectively extending it."""
    key_data = get_key_by_id(key_id)
    if not key_data or not key_data.get('start_time'):
        return # Can't extend a key that hasn't started

    start_time = datetime.datetime.fromisoformat(key_data['start_time'])
    new_start_time = start_time - datetime.timedelta(minutes=minutes_to_add)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE license_keys SET start_time = ? WHERE id = ?", (new_start_time, key_id))
    conn.commit()
    conn.close()

def make_key_lifetime(key_id):
    """Sets a key's duration to lifetime."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE license_keys SET duration_minutes = -1 WHERE id = ?", (key_id,))
    conn.commit()
    conn.close()

def get_key_by_id(key_id):
    """Helper function to get key data by its primary ID."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM license_keys WHERE id = ?", (key_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

if __name__ == '__main__':
    init_db()
