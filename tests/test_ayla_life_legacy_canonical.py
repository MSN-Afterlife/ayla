from __future__ import annotations

import unittest

from ayla_life.legacy_bridge.canonical import (
    canonical_legacy_content_payload,
    canonical_legacy_schema_payload,
    legacy_content_hash,
    legacy_schema_fingerprint,
)


class CanonicalIdentityTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"user_id": 9000000000000000001, "balance": 0, "daily_streak": 3, "last_daily_at": None, "updated_at": 1700000000},
            {"user_id": 42, "balance": 1234, "daily_streak": 0, "last_daily_at": 1699999999, "updated_at": 1700000000},
        ]
        self.schema = [
            {"cid": 0, "name": "user_id", "type": " integer ", "notnull": 0, "default": None, "pk": 1},
            {"cid": 1, "name": "balance", "type": "INTEGER", "notnull": 1, "default": " 0 ", "pk": 0},
            {"cid": 2, "name": "daily_streak", "type": "INTEGER", "notnull": 1, "default": "0", "pk": 0},
            {"cid": 3, "name": "last_daily_at", "type": "INTEGER", "notnull": 0, "default": None, "pk": 0},
            {"cid": 4, "name": "updated_at", "type": "INTEGER", "notnull": 1, "default": None, "pk": 0},
        ]

    def test_golden_payload_and_digest(self):
        payload = canonical_legacy_content_payload(self.rows)
        self.assertEqual(payload.decode(), '[{"user_id":42,"balance":1234,"daily_streak":0,"last_daily_at":1699999999,"updated_at":1700000000},{"user_id":9000000000000000001,"balance":0,"daily_streak":3,"last_daily_at":null,"updated_at":1700000000}]')
        self.assertEqual(legacy_content_hash(self.rows), "1abbe0c3685dd4c881193418914962da47b65ef8f7fc10ea7a7998865c12a8ed")

    def test_row_order_and_schema_casing_do_not_change_identity(self):
        self.assertEqual(legacy_content_hash(self.rows), legacy_content_hash(list(reversed(self.rows))))
        self.assertEqual(legacy_schema_fingerprint(self.schema), legacy_schema_fingerprint(list(reversed(self.schema))))

    def test_semantic_fields_change_content_identity(self):
        for field in ("user_id", "balance", "daily_streak", "last_daily_at", "updated_at"):
            changed = [dict(row) for row in self.rows]
            changed[0][field] = (changed[0][field] or 0) + 1
            self.assertNotEqual(legacy_content_hash(self.rows), legacy_content_hash(changed), field)

    def test_relevant_schema_changes_change_fingerprint(self):
        changed = [dict(column) for column in self.schema]
        changed[1]["notnull"] = 0
        self.assertNotEqual(legacy_schema_fingerprint(self.schema), legacy_schema_fingerprint(changed))
        self.assertIn(b'"table":"economy_profiles"', canonical_legacy_schema_payload(self.schema))


if __name__ == "__main__":
    unittest.main()
