"""Persistent team management for Mantis.

The package is intentionally split by responsibility:

* ``models`` defines durable database state.
* ``service`` owns transactional business rules.
* ``discord`` projects committed state into Discord.
* ``commands`` adapts slash commands and events onto those layers.

Keep database decisions out of Discord handlers so failed API calls cannot
silently corrupt team history.
"""
