# Teams

## Package layout

- `models.py` defines team, membership, join-request, and close-vote tables.
- `service.py` contains synchronous transactional rules and authorization.
- `discord.py` owns channels, managed messages, reactions, and persistent views.
- `commands.py` registers `/team` commands and translates Discord interactions
  into service calls.

Alembic revisions remain under `migrations/versions`, where the migration runner
expects them. Team command registration is explicit in `bot.py` because this
feature spans more than a single standalone slash-command module.

The database-backed team workflow provides `/team create`, `edit`, `add`,
`remove`, `set-rank`, `transfer-lead`, `leave`, and `close`. It maintains a
managed message in every team channel and one logical directory in `#teams`.
The directory uses a reaction/TOC message plus as many managed detail messages
as needed, so descriptions and membership lists are not truncated into one
Discord message. Join-request and close-vote buttons survive bot restarts.

By default, the bot finds the `Teams` category and `#teams` channel by name and
creates the category when needed. Set `TEAMS_CATEGORY_ID` and
`TEAMS_DIRECTORY_CHANNEL_ID` to Discord snowflake IDs to select them explicitly.
The bot needs Manage Channels, Manage Messages, Read Message History, Add
Reactions, Send Messages, Embed Links, and the archive workflow's attachment
permissions in the relevant channels.

Every team channel receives an explicit overwrite for the exact Discord role
`AllTeams`. Members with that role can view the channel, read history, send
messages, react, attach/embed content, and write or create threads. The bot
reconciles this overwrite at startup and whenever it refreshes a team channel.

## Note on name parsing

Member full names must use exactly two alphabetic parts in `First Last` format.
Only the first letter of each part may be uppercase; middle names, initials,
hyphens, extra internal spaces, and alternate capitalization are rejected.
