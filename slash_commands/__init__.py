"""Automatic registration for standalone slash-command modules."""

from importlib import import_module
from pkgutil import iter_modules

from discord.ext import commands


def setup(bot: commands.Bot) -> None:
    """Import and register every public module in this package."""
    for module_info in iter_modules(__path__):
        if module_info.name.startswith("_"):
            continue

        module = import_module(f"{__name__}.{module_info.name}")
        register = getattr(module, "setup", None)
        if register is None:
            raise RuntimeError(
                f"Slash-command module {module.__name__!r} must define setup(bot)."
            )

        register(bot)
