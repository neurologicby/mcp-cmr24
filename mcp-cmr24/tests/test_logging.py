import unittest

from log_config import redact


class LoggingTests(unittest.TestCase):
    def test_secrets_are_redacted(self):
        text = redact("Authorization: Bearer abc123 authkey=secret")
        self.assertNotIn("abc123", text)
        self.assertNotIn("secret", text)
        self.assertGreaterEqual(text.count("[REDACTED]"), 2)
