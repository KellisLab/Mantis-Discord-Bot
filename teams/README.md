# Teams

## Package layout

- `models.py` defines team, membership, join-request, and close-vote tables.
- `service.py` contains synchronous transactional rules and authorization.
- `discord.py` owns channels, managed messages, directory buttons, and persistent
  views.
- `commands.py` registers `/team` commands and translates Discord interactions
  into service calls.

Alembic revisions remain under `migrations/versions`, where the migration runner
expects them. Team command registration is explicit in `bot.py` because this
feature spans more than a single standalone slash-command module.

The database-backed team workflow provides `/team create`, `edit`, `add`,
`remove`, `set-rank`, `transfer-lead`, `leave`, and `close`. It maintains a
managed message in every team channel and one logical directory in `#teams`.
The directory uses a TOC message plus paged detail messages with join buttons,
so descriptions and membership lists are not truncated into one Discord message.
Directory, join-request, and close-vote buttons survive bot restarts.
Deleting an active close-vote message cancels that attempt, including when the
missing message is discovered after a restart. Its votes remain as audit
history, and a later `/team close` starts a fresh attempt.

By default, the bot finds the `Teams` category and `#teams` channel by name and
creates the category when needed. Set `TEAMS_CATEGORY_ID` and
`TEAMS_DIRECTORY_CHANNEL_ID` to Discord snowflake IDs to select them explicitly.
The bot needs Manage Channels and Manage Roles to create channels and reconcile
member/role overwrites. It also needs Manage Messages, Read Message History,
Send Messages, Embed Links, and the archive workflow's attachment permissions in
the relevant channels.

Every team channel receives an explicit overwrite for the exact Discord role
`AllTeams`. Members with that role can view the channel, read history, send
messages, react, attach/embed content, and write or create threads. The bot
reconciles this overwrite at startup and whenever it refreshes a team channel.

Team channels are private by default: their channel-level `@everyone` overwrite
explicitly denies View Channel. Every current team member whose profile has a
valid Discord ID receives an individual read/write overwrite. Reconciliation
runs after membership changes and at startup, adding newly eligible members and
removing stale individual overwrites while preserving unrelated role-based
moderation overwrites.

## Note on name parsing

Member full names contain a given name followed by a simple or compound surname.
Substantive parts begin uppercase, while later letters preserve spellings such
as `McDonald DeMarco`. Apostrophes, hyphens, Unicode letters, and lowercase
surname particles are accepted—for example, `Thomas de Chillaz`. Extra internal
spaces and malformed punctuation are rejected.
