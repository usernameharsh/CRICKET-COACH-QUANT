import sqlite3
import pandas as pd
from datetime import datetime

class DatabaseManager:
    def __init__(self, db_name="unsafal_log.db"):
        self.db_name = db_name
        self._initialize_db()

    def _initialize_db(self):
        """Creates the session table if it doesn't already exist."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Create a table to store session summaries
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_date TEXT,
                session_time TEXT,
                total_deliveries INTEGER,
                legal_deliveries INTEGER,
                max_extension REAL
            )
        ''')
        conn.commit()
        conn.close()

    def log_session(self, total_deliveries, legal_deliveries, max_extension):
        """Saves a completed session to the database."""
        # Don't log empty sessions where the bowler didn't throw anything
        if total_deliveries == 0:
            return 
            
        now = datetime.now()
        date_str = now.strftime("%b %d, %Y") # e.g., May 25, 2026
        time_str = now.strftime("%H:%M")     # e.g., 14:30

        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sessions (session_date, session_time, total_deliveries, legal_deliveries, max_extension)
            VALUES (?, ?, ?, ?, ?)
        ''', (date_str, time_str, total_deliveries, legal_deliveries, round(max_extension, 1)))
        
        conn.commit()
        conn.close()
        print(f"[BACKEND] Session logged: {total_deliveries} deliveries.")

    def get_all_sessions(self):
        """Retrieves all sessions as a standard Python list of dictionaries (for Session History)."""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row # Allows column access by name
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM sessions ORDER BY id DESC')
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]

    def get_sessions_dataframe(self):
        """Retrieves all sessions as a Pandas DataFrame (for Player Stats graph)."""
        conn = sqlite3.connect(self.db_name)
        df = pd.read_sql_query('SELECT * FROM sessions ORDER BY id ASC', conn)
        conn.close()
        return df
    
    def clear_all_sessions(self):
        """Deletes all recorded sessions from the database."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Delete all rows from the table
        cursor.execute('DELETE FROM sessions')
        
        # Reset the auto-increment ID counter back to 1
        cursor.execute('DELETE FROM sqlite_sequence WHERE name="sessions"')
        
        conn.commit()
        conn.close()
        print("[BACKEND] All session history cleared.")
        