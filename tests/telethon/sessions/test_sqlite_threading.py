import threading
import tempfile
import os
from telethon.sessions.sqlite import SQLiteSession


def test_concurrent_cursor_access():
    """M-12: Multiple threads accessing _cursor concurrently must not corrupt state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, 'test')
        session = SQLiteSession(path)
        # Force initialization
        session._cursor().close()
        errors = []

        def worker():
            try:
                for _ in range(50):
                    c = session._cursor()
                    c.execute("SELECT 1")
                    c.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threading errors: {errors}"
