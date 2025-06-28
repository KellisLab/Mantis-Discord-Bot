# Mantis Discord Bot

A Discord bot that integrates with GitHub Projects to help teams manage and view project tasks directly from Discord. Future plans include adding ManolisGPT right into Discord.

Hosted via https://github.com/KellisLab/MantisAPI

## Features

- **View Project Tasks**: Display tasks from specific GitHub Projects (from KellisLab org)
- **Status Filtering**: Filter tasks by status (To Do, In Progress, In Review, Done)
- **Channel-Based Projects**: Automatically determine which project to display based on the Discord channel (so teams can easily pull up their tasks)
- **Real-time Data**: Fetches live data from GitHub Projects using GraphQL API

## Commands

- `/project_tasks <number> [status]` - View tasks from a specific GitHub Project number
- `/tasks [status]` - View tasks from the project associated with the current channel
- `/projects` - View all of the projects in the organization
- `/help` - Display bot usage information

## Setup

### Prerequisites

- Python 3.11+
- Discord Bot Token
- GitHub Personal Access Token with appropriate permissions to view projects

### Environment Variables

Create a `.env` file in the root directory with the following variables:

```env
DISCORD_TOKEN=your_discord_bot_token
GITHUB_TOKEN=your_github_personal_access_token
```

### Installation

#### Method 1: Docker (Recommended)

1. Clone the repository
2. Create your `.env` file with the required environment variables
3. Run with Docker Compose:

```bash
docker compose up
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
