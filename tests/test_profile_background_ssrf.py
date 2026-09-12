import unittest

from bot.services.profile_background_storage import (
    ProfileBackgroundError,
    PublicOnlyResolver,
    _validate_background_url,
)


class FakeResolver:
    def __init__(self, address):
        self.address = address

    async def resolve(self, host, port, family):
        return [{"host": self.address}]

    async def close(self):
        return None


class ProfileBackgroundSsrFTests(unittest.IsolatedAsyncioTestCase):
    def test_local_and_private_addresses_are_rejected(self):
        for url in (
            "http://127.0.0.1/image.png",
            "http://10.20.30.40/image.png",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/image.png",
            "http://[fc00::1]/image.png",
            "http://[malformed-ipv6/image.png",
        ):
            with self.subTest(url=url), self.assertRaises(ProfileBackgroundError):
                _validate_background_url(url)

    def test_redirect_targets_are_revalidated_and_nonstandard_ports_rejected(self):
        with self.assertRaises(ProfileBackgroundError):
            _validate_background_url("//127.0.0.1/metadata", "https://images.example/start")
        with self.assertRaises(ProfileBackgroundError):
            _validate_background_url("https://images.example:8443/image.png")
        self.assertEqual(
            _validate_background_url("/next.png", "https://images.example/start"),
            "https://images.example/next.png",
        )

    async def test_dns_resolver_rejects_private_answers_before_connecting(self):
        for address in ("192.168.1.10", "::1", "fe80::1"):
            resolver = PublicOnlyResolver()
            resolver._resolver = FakeResolver(address)
            with self.subTest(address=address), self.assertRaises(ProfileBackgroundError):
                await resolver.resolve("attacker.example", 443)

    async def test_dns_resolver_accepts_public_answers(self):
        resolver = PublicOnlyResolver()
        resolver._resolver = FakeResolver("93.184.216.34")

        result = await resolver.resolve("images.example", 443)

        self.assertEqual(result[0]["host"], "93.184.216.34")


if __name__ == "__main__":
    unittest.main()
