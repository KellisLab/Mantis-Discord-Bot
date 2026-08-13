"""Database operations for canonical member profiles."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import phonenumbers
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from database import get_session
from users import Stage, User

DISCORD_MENTION = re.compile(r"<@!?(\d+)>")
# A part may contain Unicode letters separated by apostrophes or common hyphen
# characters. Capitalization is checked separately because compound surnames
# legitimately include lowercase particles such as "de" and "van".
FULL_NAME_PART = re.compile(r"^[^\W\d_]+(?:['’\-‐‑][^\W\d_]+)*$", re.UNICODE)
SURNAME_PARTICLES = frozenset(
    {
        "al",
        "ap",
        "ben",
        "bin",
        "da",
        "de",
        "del",
        "della",
        "den",
        "der",
        "di",
        "dos",
        "du",
        "el",
        "la",
        "le",
        "van",
        "von",
    }
)


class MemberServiceError(ValueError):
    """A member operation could not be completed as requested."""


class DuplicateEmailError(MemberServiceError):
    """The requested email already belongs to a member."""


class DiscordAlreadyLinkedError(MemberServiceError):
    """The Discord account is already linked to a different member."""


class MemberNotFoundError(MemberServiceError):
    """No member matched an identifier."""


class AmbiguousMemberError(MemberServiceError):
    """A full name matched more than one member."""


class InvalidStageError(MemberServiceError):
    """A stage value is not supported."""


@dataclass(frozen=True)
class ProfileResult:
    member: User
    created: bool
    previous_discord_id: str | None = None


@dataclass(frozen=True)
class ImportResult:
    created: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass(frozen=True)
class StageImportResult:
    updated: int = 0
    errors: int = 0
    discord_ids: tuple[str, ...] = ()


def parse_stage(value: str | Stage | None) -> Stage:
    """Parse a command or CSV stage, defaulting blank values to preboarding."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return Stage.PREBOARDING
    if isinstance(value, Stage):
        return value
    try:
        return Stage(value.strip().casefold())
    except ValueError as error:
        valid = ", ".join(stage.value for stage in Stage)
        raise InvalidStageError(f"Stage must be one of: {valid}.") from error


def parse_boolean(value: str, field_name: str) -> bool:
    """Parse an explicit CSV boolean without toggle-style ambiguity."""

    normalized = value.strip().casefold()
    if normalized in {"true", "yes", "1", "enabled", "enable"}:
        return True
    if normalized in {"false", "no", "0", "disabled", "disable"}:
        return False
    raise MemberServiceError(
        f"{field_name} must be true/false, yes/no, 1/0, or enabled/disabled."
    )


def _required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise MemberServiceError(f"{field_name} is required.")
    return normalized


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def normalize_full_name(value: str | None) -> str | None:
    """Validate a given name followed by a simple or compound surname.

    Names are not silently rewritten. The given name and substantive surname
    parts start uppercase; known surname particles may remain lowercase.
    """

    if value is None or not value.strip():
        return None
    parts = value.split(" ")
    valid_shape = (
        value == value.strip()
        and len(parts) >= 2
        and all(FULL_NAME_PART.fullmatch(part) is not None for part in parts)
    )
    if valid_shape:
        given_name, *surname = parts
        valid_capitalization = given_name[0].isupper() and surname[-1][0].isupper()
        valid_capitalization = valid_capitalization and all(
            part[0].isupper() or part.casefold() in SURNAME_PARTICLES
            for part in surname[:-1]
        )
    else:
        valid_capitalization = False

    if not valid_capitalization:
        raise MemberServiceError(
            "Full name must contain an uppercase given name and surname. "
            "Compound surnames, lowercase surname particles, apostrophes, and "
            "hyphens are allowed."
        )
    return value


def normalize_whatsapp_number(
    value: str | None,
    *,
    flexible_international_format: bool = False,
) -> str | None:
    """Validate a WhatsApp number and return its canonical E.164 form.

    Interactive entry requires an explicit leading ``+``. Imports may omit the
    plus or use the common ``00``/``011`` international dialing prefixes, but
    still need a country calling code because the CSV has no origin country.
    """

    normalized = _optional(value)
    if normalized is None:
        return None

    candidate = normalized
    if flexible_international_format:
        if candidate.startswith("00"):
            candidate = f"+{candidate[2:]}"
        elif candidate.startswith("011"):
            candidate = f"+{candidate[3:]}"
        elif not candidate.startswith("+") and not candidate.casefold().startswith(
            "tel:"
        ):
            candidate = f"+{candidate}"
    elif not candidate.startswith("+"):
        raise MemberServiceError(
            "WhatsApp number must be a full international number beginning with "
            "+ and the country calling code (for example, +44 20 7946 0018)."
        )

    try:
        parsed = phonenumbers.parse(candidate, None)
    except phonenumbers.NumberParseException as error:
        raise MemberServiceError(
            "WhatsApp number is not a recognizable international phone number."
        ) from error

    if parsed.extension:
        raise MemberServiceError("WhatsApp number cannot include an extension.")
    # ``is_possible_number`` applies country-specific structural rules without
    # rejecting reserved/example ranges or newly allocated mobile prefixes.
    if not phonenumbers.is_possible_number(parsed):
        raise MemberServiceError(
            "WhatsApp number is not possible for its country calling code."
        )

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _detached(session: Any, member: User) -> User:
    session.refresh(member)
    session.expunge(member)
    return member


def create_or_link_profile(
    *,
    discord_id: str | int,
    email: str,
    full_name: str | None = None,
    github_username: str | None = None,
    whatsapp_number: str | None = None,
) -> ProfileResult:
    """Create a member or link/update the profile found by exact email only."""

    normalized_discord_id = str(discord_id)
    normalized_email = _required(email, "Email")

    with get_session() as session:
        linked_member = session.exec(
            select(User).where(User.discord_id == normalized_discord_id)
        ).one_or_none()
        email_member = session.exec(
            select(User).where(User.email == normalized_email)
        ).one_or_none()

        if linked_member is not None:
            raise DiscordAlreadyLinkedError(
                "You already have a member profile. `/create-profile` can only be "
                "used once."
            )
        if email_member is not None and email_member.discord_id is not None:
            raise DiscordAlreadyLinkedError(
                "That member profile is already linked to a Discord account."
            )

        created = email_member is None
        previous_discord_id = (
            email_member.discord_id if email_member is not None else None
        )
        member = email_member or User(
            email=normalized_email,
            discord_id=normalized_discord_id,
        )
        member.discord_id = normalized_discord_id

        # Optional fields are patch semantics: omitted values stay unchanged.
        if full_name is not None:
            member.full_name = normalize_full_name(full_name)
        if github_username is not None:
            member.github_username = _optional(github_username)
        if whatsapp_number is not None:
            member.whatsapp_number = normalize_whatsapp_number(whatsapp_number)

        session.add(member)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise MemberServiceError(
                "The profile could not be linked because its email or Discord "
                "account was claimed concurrently."
            ) from error
        return ProfileResult(
            member=_detached(session, member),
            created=created,
            previous_discord_id=previous_discord_id,
        )


def add_member(
    *,
    email: str,
    full_name: str | None = None,
    github_username: str | None = None,
    whatsapp_number: str | None = None,
    stage: str | Stage | None = None,
    flexible_phone_format: bool = False,
) -> User:
    """Create an unlinked member, rejecting an exact duplicate email."""

    normalized_email = _required(email, "Email")
    with get_session() as session:
        existing = session.exec(
            select(User.id).where(User.email == normalized_email)
        ).first()
        if existing is not None:
            raise DuplicateEmailError(
                f"A member with email {normalized_email!r} already exists."
            )

        member = User(
            email=normalized_email,
            full_name=normalize_full_name(full_name),
            github_username=_optional(github_username),
            whatsapp_number=normalize_whatsapp_number(
                whatsapp_number,
                flexible_international_format=flexible_phone_format,
            ),
            stage=parse_stage(stage),
        )
        session.add(member)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise DuplicateEmailError(
                f"A member with email {normalized_email!r} already exists."
            ) from error
        return _detached(session, member)


def _discord_id_from_identifier(identifier: str) -> str | None:
    match = DISCORD_MENTION.fullmatch(identifier)
    if match is not None:
        return match.group(1)
    return identifier if identifier.isdecimal() else None


def _resolve_member_in_session(
    session: Any,
    identifier: str,
    *,
    discord_id: str | int | None = None,
) -> User:
    normalized = _required(identifier, "Identifier")

    resolved_discord_id = (
        str(discord_id)
        if discord_id is not None
        else _discord_id_from_identifier(normalized)
    )
    if resolved_discord_id is not None:
        member = session.exec(
            select(User).where(User.discord_id == resolved_discord_id)
        ).one_or_none()
        if member is not None:
            return member

    member = session.exec(select(User).where(User.email == normalized)).one_or_none()
    if member is not None:
        return member

    github_members = session.exec(
        select(User).where(func.lower(User.github_username) == normalized.casefold())
    ).all()
    if len(github_members) > 1:
        raise AmbiguousMemberError(
            f"More than one member uses GitHub username {normalized!r}; "
            "use email or Discord."
        )
    if github_members:
        return github_members[0]

    members = session.exec(select(User).where(User.full_name == normalized)).all()
    if len(members) > 1:
        raise AmbiguousMemberError(
            f"More than one member is named {normalized!r}; use email or @Discord."
        )
    if members:
        return members[0]
    raise MemberNotFoundError(f"No member matched {normalized!r}.")


def resolve_member(
    identifier: str,
    *,
    discord_id: str | int | None = None,
) -> User:
    """Resolve Discord, exact email, GitHub username, then exact full name."""

    with get_session() as session:
        member = _resolve_member_in_session(
            session,
            identifier,
            discord_id=discord_id,
        )
        return _detached(session, member)


def set_member_stage(
    identifier: str,
    stage: str | Stage,
    *,
    discord_id: str | int | None = None,
) -> User:
    with get_session() as session:
        member = _resolve_member_in_session(
            session,
            identifier,
            discord_id=discord_id,
        )
        member.stage = parse_stage(stage)
        session.add(member)
        session.commit()
        return _detached(session, member)


def toggle_member_flag(
    identifier: str,
    flag: str,
    *,
    discord_id: str | int | None = None,
) -> User:
    if flag not in {"is_leadership", "is_journey_mentor"}:
        raise ValueError(f"Unsupported member flag: {flag}")

    with get_session() as session:
        member = _resolve_member_in_session(
            session,
            identifier,
            discord_id=discord_id,
        )
        setattr(member, flag, not getattr(member, flag))
        session.add(member)
        session.commit()
        return _detached(session, member)


def kick_member(
    identifier: str,
    *,
    discord_id: str | int | None = None,
) -> User:
    """Reset progression and special roles without deleting or unlinking."""

    with get_session() as session:
        member = _resolve_member_in_session(
            session,
            identifier,
            discord_id=discord_id,
        )
        member.stage = Stage.PREBOARDING
        member.is_leadership = False
        member.is_journey_mentor = False
        session.add(member)
        session.commit()
        return _detached(session, member)


def import_members(rows: Iterable[Mapping[str, str | None]]) -> ImportResult:
    """Import CSV-shaped rows, committing valid rows independently."""

    created = skipped = errors = 0
    for row in rows:
        try:
            email = _required(row.get("email") or "", "Email")
            parsed_stage = parse_stage(row.get("stage"))
        except MemberServiceError:
            errors += 1
            continue

        try:
            add_member(
                email=email,
                full_name=row.get("full_name"),
                github_username=row.get("github_username"),
                whatsapp_number=row.get("whatsapp"),
                stage=parsed_stage,
                flexible_phone_format=True,
            )
        except DuplicateEmailError:
            skipped += 1
        except MemberServiceError:
            errors += 1
        else:
            created += 1

    return ImportResult(created=created, skipped=skipped, errors=errors)


def import_member_stages(
    rows: Iterable[Mapping[str, str | None]],
) -> StageImportResult:
    """Update stages from CSV-shaped rows, committing valid rows independently."""

    updated = errors = 0
    discord_ids: set[str] = set()
    for row in rows:
        try:
            identifier = _required(row.get("identifier") or "", "Identifier")
            stage = parse_stage(_required(row.get("stage") or "", "Stage"))
            member = set_member_stage(identifier, stage)
        except MemberServiceError:
            errors += 1
        else:
            updated += 1
            if member.discord_id is not None:
                discord_ids.add(member.discord_id)

    return StageImportResult(
        updated=updated,
        errors=errors,
        discord_ids=tuple(sorted(discord_ids)),
    )
