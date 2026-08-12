"""Automatic registration for standalone slash-command modules."""

from importlib import import_module
from pkgutil import iter_modules

from discord.ext import commands

from slash_commands.access import handle_access_denied


def setup(bot: commands.Bot) -> None:
    """Import and register every public module in this package."""
    previous_error_handler = bot.tree.on_error

    async def handle_slash_command_error(interaction, error):
        if await handle_access_denied(interaction, error):
            return
        await previous_error_handler(interaction, error)

    bot.tree.on_error = handle_slash_command_error

    for module_info in iter_modules(__path__):
        if module_info.name.startswith("_") or module_info.name == "access":
            continue

        module = import_module(f"{__name__}.{module_info.name}")
        register = getattr(module, "setup", None)
        if register is None:
            raise RuntimeError(
                f"Slash-command module {module.__name__!r} must define setup(bot)."
            )

        register(bot)
