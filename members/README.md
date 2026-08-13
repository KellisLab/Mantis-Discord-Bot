# Members

## Package layout

- `models.py` defines canonical member profiles, progression stages, and
  special-role flags.
- `service.py` contains validation and synchronous transactional operations.
- `commands.py` registers `/create-profile` and `/member` commands and adapts
  Discord interactions onto the service layer.

Member command registration is explicit in `bot.py`, matching the `teams`
feature package.

## `/member import`

`/member import` accepts one UTF-8 CSV attachment up to 2 MB and creates
unlinked member profiles. It does not link Discord accounts; members claim an
imported profile later with `/create-profile` using the same email address.

The CSV must have an `email` header, and every imported row must have a nonblank
email. All other supported fields are optional:

| Column | Required | Behavior |
| --- | --- | --- |
| `email` | Yes | Creates the profile and identifies exact duplicates. |
| `full_name` | No | Must pass the member name validation when present. |
| `github_username` | No | Stored as the member's GitHub username. |
| `whatsapp` | No | Must include a country calling code; common international prefixes and bare country codes are accepted and normalized to E.164. |
| `stage` | No | Defaults to `preboarding` when blank or omitted. |

Valid `stage` values are `preboarding`, `onboarding`, `cartographer`,
`navigator`, `savant`, `admiral`, `developer`, `engineer`, and `architect`.

For example:

```csv
email,full_name,github_username,whatsapp,stage
ada@example.com,Ada Lovelace,ada,+44 20 7946 0018,engineer
grace@example.com,Grace Hopper,ghopper,,
```

Headers are case-insensitive. Surrounding whitespace is ignored, and spaces or
hyphens in header names are treated as underscores. Unknown columns are
ignored.

Rows are processed independently:

- a valid new email is created as an unlinked profile;
- an email that already exists is counted as skipped and is not updated;
- a row with a blank email, invalid stage, invalid name, or invalid WhatsApp
  number is counted as an error;
- one skipped or invalid row does not roll back valid rows.

The completion message reports only totals for created, skipped, and errored
rows. Correct errored rows in the source CSV and import them again; already
created rows will be safely skipped.
