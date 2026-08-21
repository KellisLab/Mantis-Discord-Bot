# Mantis Discord Bot

A Discord bot that integrates with GitHub Projects to help teams manage and view project tasks directly from Discord.

Hosted via https://github.com/KellisLab/MantisAPI

## Features

This bot provides various Discord slash commands to help teams manage their workflow and projects. Key capabilities include:

- **GitHub Integration**: Seamlessly connect with GitHub Projects and organizational data
- **Team Collaboration**: Channel-based project management and task coordination  
- **AI-Powered Tools**: Advanced features for productivity and automation
- **Real-time Updates**: Live data synchronization and notifications

## Commands

The bot supports multiple slash commands for different functionalities. Use `/help` in Discord to see all available commands and their usage.

Feature-specific implementation and operator notes live with their packages:

- [`members/README.md`](members/README.md) documents member profiles and the
  `/member import` CSV contract.
- [`teams/README.md`](teams/README.md) documents the persistent team workflow.

## Setup

### Prerequisites

- `uv` for local development (it installs the matching Python version)
- Docker only if you prefer container-based development
- .env file provided by @DemonizedCrush

### Installation

#### Method 1: Local Python with hot reload (recommended for development)

This repository uses Python 3.11 to match the Docker image. With `uv`
installed, create the local environment and install all dependencies:

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
```

VS Code automatically uses `.venv` for Python analysis and terminals. Reload
the VS Code window if it was already open, then start the bot with automatic
restart on Python changes:

```bash
./scripts/dev.sh
```

You can also run the VS Code task **Run bot (hot reload)** from the Command
Palette. Stop the bot with `Ctrl+C`.

Format the code and automatically fix lint issues with Ruff:

```bash
ruff format .
ruff check --fix .
```

#### Method 2: Docker

1. Clone the repository
2. Create your `.env` file with the required environment variables
3. Run with Docker Compose:

```bash
docker compose watch
```

Docker Compose starts the bot with the `DATABASE_URL` from `.env`. In
production-oriented setups this should point at RDS. The bundled PostgreSQL
service is only for local test databases and stores rows in the named
`postgres_data` volume across restarts.

Set `USER_ROLE_SYNC_ENABLED=false` in `.env` for developer-mode runs that should
not automatically modify Discord roles. `docker compose watch` syncs `.env` and
restarts the bot when that value changes.

#### Method 3: Local Python without hot reload

1. Clone the repository
2. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

3. Create your `.env` file with the required environment variables
4. Run the bot:

```bash
python bot.py
```

## Database

`DATABASE_URL` is the production database URL used by the bot and Alembic
migrations. For RDS, include SSL mode in the URL when required by the instance:

```bash
DATABASE_URL=postgresql+psycopg://user:password@your-rds-endpoint.amazonaws.com:5432/mantis?sslmode=require
```

Team integration tests do not use `DATABASE_URL` directly. They set
`DATABASE_URL` from `TEAM_TEST_DATABASE_URL` before importing database code, and
default to the local database
`postgresql+psycopg://mantis:mantis@localhost:5432/mantis`.

To run the local PostgreSQL test database with Docker Compose:

```bash
docker compose up -d db
DATABASE_URL=postgresql+psycopg://mantis:mantis@localhost:5432/mantis \
  alembic upgrade head
```

Apply migrations to the configured `DATABASE_URL` with:

```bash
alembic upgrade head
```

Create a new migration after changing a SQLModel table with:

```bash
alembic revision --autogenerate -m "describe the change"
```

The reusable data-storage layer is in `storage.py`. Values are JSON objects
addressed by a namespace and key:

```python
from database import get_session
from storage import get_value, set_value

with get_session() as session:
    set_value(session, "reminders", "last-run", {"status": "complete"})
    record = get_value(session, "reminders", "last-run")
```

## Contributions

Please create a Pull Request for others to review your changes. We have a development bot in the Internal Discord Server. Please ask @DemonizedCrush for the bot token and the developer role in the Discord to test out your changes.
