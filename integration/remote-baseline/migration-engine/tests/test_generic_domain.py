import unittest
from lib.generic.domain import (
    MigrationIdentity,
    MigrationDataFinding,
    MigrationInspection,
    MigrationPlan,
)


class TestGenericDomain(unittest.TestCase):
    def test_01_valid_identity(self):
        ident = MigrationIdentity(
            discord_user_id="123456789",
            canonical_name="PlayerOne",
            canonical_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            legacy_name="OldPlayer",
            legacy_uuid="11111111-2222-3333-4444-555555555555",
            accounts=[{"platform": "java", "external_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "current_username": "PlayerOne"}],
        )
        self.assertEqual(ident.discord_user_id, "123456789")
        self.assertEqual(ident.canonical_uuid, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        self.assertIsNotNone(ident.java_account())
        self.assertIsNone(ident.bedrock_account())

    def test_02_legacy_uuid_optional_but_validated(self):
        ident = MigrationIdentity(
            discord_user_id="123456789",
            canonical_name="PlayerOne",
            canonical_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            legacy_uuid=None,
        )
        self.assertIsNone(ident.legacy_uuid)

        with self.assertRaises(ValueError):
            MigrationIdentity(
                discord_user_id="123456789",
                canonical_name="PlayerOne",
                canonical_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                legacy_uuid="invalid-uuid-format",
            )

    def test_03_canonical_uuid_missing_raises(self):
        with self.assertRaises(ValueError):
            MigrationIdentity(
                discord_user_id="123456789",
                canonical_name="PlayerOne",
                canonical_uuid="",
            )

    def test_serialization_roundtrip(self):
        ident = MigrationIdentity(
            discord_user_id="123456789",
            canonical_name="PlayerOne",
            canonical_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            legacy_name="OldPlayer",
            legacy_uuid="11111111-2222-3333-4444-555555555555",
            accounts=[{"platform": "bedrock", "external_id": "2533274791234567", "current_username": "BedrockTag"}],
            migration_status="PENDING",
        )
        d = ident.to_dict()
        clone = MigrationIdentity.from_dict(d)
        self.assertEqual(ident, clone)
        self.assertIsNotNone(clone.bedrock_account())


if __name__ == "__main__":
    unittest.main()
