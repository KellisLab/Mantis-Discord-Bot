"""Canonical member profile management for Mantis.

The package is split by responsibility:

* ``models`` defines durable member profile and progression state.
* ``service`` owns validation and transactional member operations.
* ``commands`` registers member-related slash commands and adapts Discord
  interactions onto the service layer.
"""
