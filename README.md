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

- Python 3.10+
- .env file provided by @DemonizedCrush

### Installation

#### Method 1: Docker (Recommended)

1. Clone the repository
2. Create your `.env` file with the required environment variables
3. Run with Docker Compose:

```bash
docker compose up --build
```

#### Method 2: Local Python

1. Clone the repository
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create your `.env` file with the required environment variables
4. Run the bot:

```bash
python bot.py
```

## Contributions

Please create a Pull Request for others to review your changes. We have a development bot in the Internal Discord Server. Please ask @DemonizedCrush for the bot token and the developer role in the Discord to test out your changes.
