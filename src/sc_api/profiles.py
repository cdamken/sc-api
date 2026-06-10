"""Multi-account profile management.

Layout on disk:

    ~/.sc-api/
    ├── active            ← text file with the active profile name (an email)
    └── profiles/
        ├── carlos@damken.com/
        │   ├── meta.json   ← email, name, person_id, portfolio_ids (no secrets)
        │   └── cookies.txt ← Mozilla cookie jar imported from Chrome
        └── ...

A "profile" is identified by email address. We never store the password —
authentication is entirely cookie-based.

Mirrors `tr_api.profiles` but keyed by email instead of phone, and stores
the discovered personId + portfolioIds in meta.json so we don't have to
re-scrape the cockpit every run.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .exceptions import NoActiveProfile, ProfileNotFound

# RFC 5322 simplified — good enough for filesystem keys.
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

BASE_DIR = Path.home() / ".sc-api"
PROFILES_DIR = BASE_DIR / "profiles"
ACTIVE_FILE = BASE_DIR / "active"


@dataclass
class Profile:
    """Metadata for one Scalable account."""
    email: str                               # Profile key
    name: str | None = None                  # Friendly display name
    person_id: str | None = None             # GraphQL "personId" / cockpit userId
    portfolio_ids: list[str] = field(default_factory=list)
    savings_ids: list[str] = field(default_factory=list)
    created_at: str | None = None            # ISO 8601 timestamp
    updated_at: str | None = None

    @property
    def dir(self) -> Path:
        return PROFILES_DIR / self.email

    @property
    def meta_file(self) -> Path:
        return self.dir / "meta.json"

    @property
    def cookies_file(self) -> Path:
        return self.dir / "cookies.txt"

    @property
    def credentials_file(self) -> Path:
        """Path where the user's email + password are stored (mode 0600).

        Matches the pattern of `~/.pytr/credentials` (Trade Republic) and
        `~/.gbm-mx/credentials` (GBM): plaintext JSON with restrictive perms.
        Stored ONLY when the user opts in (default on local dev to enable
        auto-relogin; never on ownCloud where ICrypto + DB live instead).
        """
        return self.dir / "credentials.json"

    @property
    def default_portfolio_id(self) -> str | None:
        """First portfolio (most users only have one)."""
        return self.portfolio_ids[0] if self.portfolio_ids else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _validate_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise ValueError(
            f"Invalid email: {email!r}. Use the address you log in to "
            "Scalable Capital with."
        )
    return email


def _ensure_dirs() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def create(email: str, name: str | None = None) -> Profile:
    """Create a new profile directory. Returns the Profile.

    Idempotent: if a profile for `email` exists, returns it as-is without
    overwriting (use `update_identity` to refresh person_id / portfolio_ids).
    """
    email = _validate_email(email)
    _ensure_dirs()

    existing_meta = PROFILES_DIR / email / "meta.json"
    if existing_meta.is_file():
        return load(email)

    prof = Profile(email=email, name=name, created_at=_now_iso(), updated_at=_now_iso())
    prof.dir.mkdir(parents=True, exist_ok=True)
    _write_meta(prof)
    return prof


def load(email: str) -> Profile:
    """Load a profile by email. Raises ProfileNotFound if missing."""
    email = _validate_email(email)
    meta = PROFILES_DIR / email / "meta.json"
    if not meta.is_file():
        raise ProfileNotFound(f"No profile for {email} (expected at {meta})")
    data = json.loads(meta.read_text(encoding="utf-8"))
    # Tolerate older meta files missing newer fields.
    data.setdefault("portfolio_ids", [])
    data.setdefault("savings_ids", [])
    return Profile(**data)


def list_all() -> list[Profile]:
    """Return every profile on disk, sorted by email."""
    if not PROFILES_DIR.is_dir():
        return []
    out: list[Profile] = []
    for d in sorted(PROFILES_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta = d / "meta.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            data.setdefault("portfolio_ids", [])
            data.setdefault("savings_ids", [])
            out.append(Profile(**data))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def remove(email: str) -> None:
    """Delete a profile directory entirely. Also clears `active` if it pointed here."""
    email = _validate_email(email)
    target = PROFILES_DIR / email
    if target.is_dir():
        shutil.rmtree(target)
    if get_active_email() == email:
        ACTIVE_FILE.unlink(missing_ok=True)


def update_identity(
    profile: Profile,
    *,
    person_id: str | None = None,
    portfolio_ids: list[str] | None = None,
    savings_ids: list[str] | None = None,
) -> Profile:
    """Update the discovered identity fields and persist meta.json.

    Called after `discovery.discover_identity(client)` returns the IDs
    scraped from the cockpit.
    """
    if person_id is not None:
        profile.person_id = person_id
    if portfolio_ids is not None:
        profile.portfolio_ids = list(portfolio_ids)
    if savings_ids is not None:
        profile.savings_ids = list(savings_ids)
    profile.updated_at = _now_iso()
    _write_meta(profile)
    return profile


def _write_meta(profile: Profile) -> None:
    profile.meta_file.write_text(
        json.dumps(asdict(profile), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Credentials (for auto-relogin)
# ---------------------------------------------------------------------------
def save_credentials(profile: Profile, email: str, password: str) -> None:
    """Persist email + password to {profile_dir}/credentials.json mode 0600.

    Same pattern as `~/.pytr/credentials` and `~/.gbm-mx/credentials`:
    plaintext on-disk but locked to the user account via filesystem perms.
    The whole point is to enable auto-relogin when cookies expire — without
    these creds, every cookie expiry forces the user back to the Settings
    page to retype password.

    For multi-user ownCloud, this file is NOT used — credentials live in
    `oc_preferences` encrypted with `ICrypto` instead.
    """
    profile.dir.mkdir(parents=True, exist_ok=True)
    payload = {"email": email, "password": password}
    profile.credentials_file.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8",
    )
    profile.credentials_file.chmod(0o600)


def load_credentials(profile: Profile) -> dict | None:
    """Return {email, password} if saved, else None."""
    if not profile.credentials_file.is_file():
        return None
    try:
        return json.loads(profile.credentials_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def clear_credentials(profile: Profile) -> None:
    """Forget stored password (e.g. on logout)."""
    profile.credentials_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Active profile pointer
# ---------------------------------------------------------------------------
def set_active(email: str) -> Profile:
    """Mark a profile as active. Returns the profile (also verifies it exists)."""
    prof = load(email)  # raises if missing
    _ensure_dirs()
    ACTIVE_FILE.write_text(prof.email + "\n", encoding="utf-8")
    return prof


def get_active_email() -> str | None:
    if not ACTIVE_FILE.is_file():
        return None
    txt = ACTIVE_FILE.read_text(encoding="utf-8").strip()
    return txt or None


def get_active() -> Profile:
    email = get_active_email()
    if not email:
        raise NoActiveProfile(
            "No active profile set. Run `sc-api auth import --email <addr>` "
            "to create one."
        )
    return load(email)
