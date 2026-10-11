"""Test setup: MOCK mode and a throwaway database, set BEFORE the app is imported."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="aerotwin-test-")
os.environ["MOCK"] = "1"
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
