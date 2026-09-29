from __future__ import annotations

import hashlib
import inspect
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


class AylaLifeInertnessTests(unittest.TestCase):
    def test_imports_are_side_effect_free(self):
        from ayla_life import legacy_bridge
        from ayla_life.economy import EconomicKernel
        from ayla_life.legacy_bridge import canonical_legacy_content_payload

        self.assertTrue(inspect.isclass(EconomicKernel))
        self.assertTrue(callable(canonical_legacy_content_payload))
        self.assertTrue(hasattr(legacy_bridge, "LegacyEconomyReader"))

    def test_kernel_requires_explicit_database_path(self):
        from ayla_life.economy import EconomicKernel

        self.assertIsNotNone(inspect.signature(EconomicKernel).parameters["database_path"].default)
        self.assertEqual(inspect.signature(EconomicKernel).parameters["database_path"].default, inspect.Parameter.empty)

    def test_imports_do_not_create_or_change_a_legacy_database(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "levels.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE economy_profiles (user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL)")
                db.execute("INSERT INTO economy_profiles VALUES (7, 123)")
                db.commit()
            before = hashlib.sha256(path.read_bytes()).digest()
            __import__("ayla_life")
            __import__("ayla_life.economy")
            __import__("ayla_life.legacy_bridge")
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT balance FROM economy_profiles WHERE user_id=7").fetchone()[0], 123)

    def test_production_command_sources_do_not_reference_kernel(self):
        root = Path(__file__).resolve().parents[1]
        sources = [*root.joinpath("bot").rglob("*.py"), root / "main.py", *root.joinpath("site-api").rglob("*.py")]
        self.assertFalse(any("EconomicKernel" in path.read_text(encoding="utf-8") or "ayla_life" in path.read_text(encoding="utf-8") for path in sources))


if __name__ == "__main__":
    unittest.main()
