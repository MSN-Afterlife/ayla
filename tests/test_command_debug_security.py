import unittest
from types import SimpleNamespace
from unittest.mock import patch

from discord import app_commands
from discord.ext import commands

from bot import command_debug


class FakeTree:
    def walk_commands(self):
        return []

    def error(self, handler):
        self.error_handler = handler
        return handler


class FakeBot:
    def __init__(self):
        self.tree = FakeTree()

    def before_invoke(self, handler):
        return handler

    def after_invoke(self, handler):
        return handler

    def event(self, handler):
        setattr(self, handler.__name__, handler)
        return handler


class FakeContext:
    guild = None
    command = None

    def __init__(self):
        self.messages = []

    async def send(self, message, **kwargs):
        self.messages.append((message, kwargs))


class CommandDebugSecurityTests(unittest.IsolatedAsyncioTestCase):
    def test_redactor_removes_sensitive_values_from_debug_fields(self):
        raw = (
            "authorization: Bearer auth-value, cookie=session-value, token=token-value, "
            "secret=secret-value, password=password-value, code=oauth-value, headers={'X-Key': 'header-value'}"
        )

        safe = command_debug._redact_secrets(raw)

        for value in (
            "auth-value",
            "session-value",
            "token-value",
            "secret-value",
            "password-value",
            "oauth-value",
            "header-value",
        ):
            self.assertNotIn(value, safe)

    def test_detailed_debug_is_disabled_by_default(self):
        guild = SimpleNamespace(id=command_debug.DEBUG_GUILD_ID)
        with patch.object(command_debug, "COMMAND_DEBUG_ENABLED", False):
            self.assertFalse(command_debug._is_debug_guild(guild))

    async def test_prefix_command_error_sends_only_safe_reference_without_file(self):
        bot = FakeBot()
        command_debug.setup_command_debug(bot)
        context = FakeContext()

        await bot.on_command_error(context, commands.CommandError("password=hunter2"))

        self.assertEqual(len(context.messages), 1)
        message, kwargs = context.messages[0]
        self.assertIn("Referencia:", message)
        self.assertNotIn("hunter2", message)
        self.assertNotIn("traceback", message.lower())
        self.assertEqual(kwargs, {})

    async def test_slash_command_error_sends_only_safe_reference_without_file(self):
        bot = FakeBot()
        command_debug.setup_command_debug(bot)
        sent = []

        class FakeResponse:
            def is_done(self):
                return False

            async def send_message(self, message, **kwargs):
                sent.append((message, kwargs))

        interaction = SimpleNamespace(
            guild=None,
            response=FakeResponse(),
            followup=None,
        )

        await bot.tree.error_handler(interaction, app_commands.AppCommandError("secret=unsafe"))

        self.assertEqual(len(sent), 1)
        message, kwargs = sent[0]
        self.assertIn("Referencia:", message)
        self.assertNotIn("unsafe", message)
        self.assertEqual(kwargs, {"ephemeral": True})


if __name__ == "__main__":
    unittest.main()
