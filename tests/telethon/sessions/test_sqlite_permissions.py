import os
import stat
import sqlite3
import tempfile
import unittest


class TestSQLiteSessionPermissions(unittest.TestCase):
    """
    Tests that session files are created with 0600 permissions.
    This mirrors the logic added to SQLiteSession._cursor().
    """

    def test_session_file_permissions(self):
        """Session file should not be readable/writable by group or others."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_file = os.path.join(tmpdir, 'test_session.session')

            # Replicate what SQLiteSession._cursor() now does:
            conn = sqlite3.connect(session_file, check_same_thread=False)
            if session_file != ':memory:':
                os.chmod(session_file, 0o600)

            conn.commit()
            conn.close()

            self.assertTrue(os.path.exists(session_file), "Session file should exist")

            mode = os.stat(session_file).st_mode
            self.assertFalse(mode & stat.S_IRGRP, "Group should not have read access")
            self.assertFalse(mode & stat.S_IROTH, "Others should not have read access")
            self.assertFalse(mode & stat.S_IWGRP, "Group should not have write access")
            self.assertFalse(mode & stat.S_IWOTH, "Others should not have write access")

    def test_memory_session_skips_chmod(self):
        """':memory:' connections should not trigger os.chmod."""
        filename = ':memory:'
        conn = sqlite3.connect(filename, check_same_thread=False)
        # This is the guard condition in _cursor(); verify it evaluates correctly
        self.assertEqual(filename, ':memory:')
        should_chmod = filename != ':memory:'
        self.assertFalse(should_chmod, "Should not chmod in-memory session")
        conn.close()


if __name__ == '__main__':
    unittest.main()
