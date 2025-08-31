import os
from dotenv import load_dotenv

# Force reload of environment variables
load_dotenv(override=True)  # override=True ensures new values overwrite existing ones

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
M4M_DISCORD_API_KEY = os.getenv("M4M_DISCORD_API_KEY")

if not GITHUB_TOKEN or not DISCORD_TOKEN:
    raise RuntimeError("Make sure GITHUB_TOKEN and DISCORD_TOKEN are set in your environment!")

if not OPENAI_API_KEY:
    print("Warning: OPENAI_API_KEY not set. AI commands will not work.")
if not ASSISTANT_ID:
    print("Warning: ASSISTANT_ID not set. AI commands will not work.")
if not M4M_DISCORD_API_KEY:
    print("Warning: M4M_DISCORD_API_KEY not set. GitHub-Discord mapping will not work.")

# ─── API Configuration ──────────────────────────────────────────────────────
GRAPHQL_URL = "https://api.github.com/graphql"
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# ─── GitHub Organization ─────────────────────────────────────────────────────
GITHUB_ORG_NAME = "KellisLab"

# ─── Pagination Settings ────────────────────────────────────────────────────
ITEMS_PER_PAGE = 100  # Max allowed by GitHub for project items
PROJECTS_PER_PAGE = 20

# ─── Project Field Configuration ─────────────────────────────────────────────
STATUS_FIELD_NAME = "Status"
UNASSIGNED_STATUS_NAME = "No Status / Other"
DEFAULT_STATUS = "Todo"

# ─── Display Limits ─────────────────────────────────────────────────────────
MAX_ITEMS_TO_DISPLAY = 50
DISCORD_FIELD_CHAR_LIMIT = 1020  # Safety margin below Discord's 1024 limit

# ─── Reminder System Configuration ──────────────────────────────────────────
REMINDER_CHANNEL_ID = 1398706671089352744  # Channel to send reminders to
STALE_ISSUE_DAYS = 7  # Days of inactivity before issue reminder
STALE_PR_DAYS = 5     # Days of inactivity before PR reminder
REMINDER_REPOS = ["Mantis", "MantisAPI", "Mantis-Discord-Bot"]
MAX_REMINDER_SUMMARY_FILES = 3

# ─── Django API Configuration ──────────────────────────────────────────────
DJANGO_API_BASE_URL = "https://mantiscluster.csail.mit.edu"
MEMBER_MAPPING_CACHE_DURATION = 7200  # Cache for 2 hours (in seconds)
DM_RATE_LIMIT_DELAY = 1.0  # Delay between DMs in seconds to avoid rate limits

# ─── Discord Transcript Configuration ──────────────────────────────────────
TRANSCRIPT_CHANNELS = [1376149453714489384, 1395060200485945364, 1376198969641926898, 1386084813672550476, 1377114667947786331, 1378386977585627156, 1376150724546924605, 1376189017552457728, 1376187613521907844, 1376189045784449156, 1376188978117476412, 1376188776015200349, 1376188897750683759, 1376188939861495960, 1377227630012530808, 1376188850086608927, 1376187727019511929, 1376187980091494441, 1376189005753876551, 1376188828460515359, 1376188808239906816, 1376187452150124624, 1376187657318830100, 1376188100371550423, 1376187517099053167, 1376187391760535582, 1376187416510988348, 1376187997191409714, 1376188671497338950, 1376188606703468737, 1376188639977013348, 1386814686904979557]  # Channel IDs to generate transcripts for
TRANSCRIPT_HOURS_BACK = 24  # Hours of message history to analyze
TRANSCRIPT_MIN_MESSAGES = 2  # Minimum messages required to generate a transcript
TRANSCRIPT_SCHEDULE_HOUR = 0  # Hour of day (UTC) to run daily transcript generation

# ─── Webhook Forwarding configuration ───────────────────────────────────────────────
MYREPOBOT_ID = 1166718780311879750
SOURCE_CHANNEL_ID = 1376206488686563350

# ─── Channel Project Mapping ─────────────────────────────────────────────────
CHANNEL_PROJECT_MAPPING = {
    1376189017552457728: 2, #Agents
    1376187613521907844: 2,
    1376189005753876551: 12, #Integrations
    1376188828460515359: 12,
    1376188808239906816: 12,
    1376187391760535582: 7, #Embeddings
    1376187416510988348: 7,
    1376187997191409714: 7,
    1376189045784449156: 25, #Journeys
    1376188978117476412: 25,
    1376188776015200349: 25,
    1376188671497338950: 22, #Science
    1376188606703468737: 22,
    1376188639977013348: 22,
    1376188850086608927: 6, #Compute
    1376187727019511929: 6,
    1376187980091494441: 6,
    1376187657318830100: 4, #Backbone
    1376188100371550423: 9, #Maps
    1376187517099053167: 9,
    1376187452150124624: 9,
}

# ─── GraphQL Fragments For Projects ───────────────────────────────────────────────
PROJECT_FIELDS_FRAGMENT = """
  id
  title
  url
  fields(first: 20) {
    nodes {
      __typename
      ... on ProjectV2Field {
        id
        name
      }
      ... on ProjectV2SingleSelectField {
        id
        name
        options {
          id
          name
        }
      }
      ... on ProjectV2IterationField {
        id
        name
      }
    }
  }
  items(first: $itemsPerPage, after: $cursor, orderBy: {field: POSITION, direction: ASC}) {
    pageInfo {
      endCursor
      hasNextPage
    }
    nodes {
      id
      content {
        __typename
        ... on DraftIssue {
          title
          createdAt
        }
        ... on Issue {
          title
          url
          number
          createdAt
        }
        ... on PullRequest {
          title
          url
          number
          createdAt
        }
      }
      fieldValues(first: 10) {
        nodes {
          __typename # What type of field value is this?
          ... on ProjectV2ItemFieldSingleSelectValue {
            name # This is the option name, e.g., "Todo", "In Progress"
            field { # This is ProjectV2FieldConfiguration (a union)
              __typename # What type of field is this item's value for?
              # We need to access 'id' from this union to match with the main Status field.
              ... on ProjectV2Field { id name }
              ... on ProjectV2SingleSelectField { id name }
              # Add other types here if a status could be linked via another field type
            }
          }
          # Add other ProjectV2ItemField...Value types here if needed for other purposes
        }
      }
    }
  }
"""
# ─── Custom M4M Task/Mentor Agent Configuration ───────────────────────────────────

# Task/mentor finder
M4M_MENTOR_LIST = "https://docs.google.com/spreadsheets/d/128HP4RuiJdRqe9Ukd9HboEgBq6GuA37N2vdy2ej07ok/export?format=csv&gid=887541815"

# Find participant for task
M4M_PARTICIPANT_LIST = "https://docs.google.com/spreadsheets/d/1b67AQxc2EN9bi5aynEfsKdltZ1kklUuqLQoO4Vs5sOA/export?format=csv&gid=887541815"
M4M_ONLY_CONSIDER_AFFILIATION = True # If True, will consider members who have no team affiliation in the Google Sheet as Inactive