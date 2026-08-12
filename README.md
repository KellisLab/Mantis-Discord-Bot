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

#### Method 2: Docker

1. Clone the repository
2. Create your `.env` file with the required environment variables
3. Run with Docker Compose:

```bash
docker compose watch
```

Docker Compose starts PostgreSQL, waits for it to become healthy, applies all
Alembic migrations, and then starts the bot. Database rows are kept in the
named `postgres_data` volume across container restarts.

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

No new environment variables are required for PostgreSQL. Docker Compose uses
an internal development database configuration, persists its data in the
`postgres_data` volume, and only exposes PostgreSQL on the host's loopback
interface. The existing `.env` continues to contain only the bot credentials.

When running Python outside Docker, the default connection in `database.py`
connects to this same PostgreSQL container on `localhost:5432`.

Apply migrations locally with:

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
