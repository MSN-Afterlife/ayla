from bot.client import create_bot
from bot.config import load_settings


def main() -> None:
    settings = load_settings()
    bot = create_bot(settings)
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
