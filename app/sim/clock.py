"""Persistent UTC simulation clock; never reads wall time."""
from datetime import datetime, timedelta, timezone
import math

START = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)

class SimClock:
    def __init__(self, connection):
        self.connection = connection
        connection.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT OR IGNORE INTO kv VALUES ('sim_now', ?)", (START.isoformat(),))
        connection.commit()

    def now(self):
        return datetime.fromisoformat(self.connection.execute("SELECT value FROM kv WHERE key='sim_now'").fetchone()[0])

    def advance(self, hours):
        if not math.isfinite(hours) or hours < 0:
            raise ValueError('hours must be finite and nonnegative')
        now = self.now() + timedelta(hours=hours)
        self.connection.execute("UPDATE kv SET value=? WHERE key='sim_now'", (now.isoformat(),))
        self.connection.commit()
        return now
