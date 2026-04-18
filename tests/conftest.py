import os

# MUST be set before any telethon import — allows pyaes fallback in test environments.
# This makes "tests tolerate pyaes, runtime does not" mechanically enforced.
os.environ.setdefault("TELETHON_ALLOW_PYAES", "1")
