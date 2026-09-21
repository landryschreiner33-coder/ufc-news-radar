"""How the database connection is configured, and how mistakes are reported.

There are two ways to point this app at PostgreSQL. Both are supported, and
the only difference between them is how much punctuation the operator has to
get right by hand.

**1. One connection string** - ``DATABASE_URL``. It works as a real
environment variable, in ``.env``, and as a top-level Streamlit secret::

    DATABASE_URL = "postgresql://user:password@host:5432/dbname"

**2. The fields on their own** - a ``[database]`` section, which is the shape
every hosting provider prints on its connection page and the shape Streamlit
Community Cloud's Secrets box is built for::

    [database]
    host = "aws-1-eu-west-2.pooler.example.com"
    port = 5432
    database = "postgres"
    username = "postgres.abcdefghijkl"
    password = "..."

The URL is assembled here, with every value percent-encoded. That encoding is
the reason this module exists: a password containing ``@``, ``/``, ``#`` or
``:`` produces a wrong - or, worse, a *valid but different* - URL when it is
pasted into a connection string by hand, and the failure it eventually causes
names the wrong host and looks like a network problem.

Rules
-----
* ``DATABASE_URL`` wins when both are present, so an existing deployment
  never changes behaviour by gaining a section.
* Every required field is checked at start-up, not at the first query.
* Nothing here returns, logs or displays the password. Errors name the
  *field* that is wrong; :func:`safe_url` masks the value.
* A configured database is never silently swapped for the local SQLite file.
  Something that cannot be understood raises - see ``database/backends.py``
  for why that matters more than starting successfully. ``DATABASE_URL`` is a
  name other software uses too, so ``UFC_RADAR_DATABASE_URL`` is checked first
  and a ``sqlite://`` value there means "leave this app on its own file".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import quote, urlencode, urlunsplit

from utils.config import load_env, secret, secrets_section

#: The secrets section this module reads.
SECTION = "database"
#: The connection-string variables, in the order they win.
URL_VARIABLES: Tuple[str, ...] = ("UFC_RADAR_DATABASE_URL", "DATABASE_URL")

#: Where a resolved configuration came from (shown on the Settings page).
SOURCE_NONE = "none"
SOURCE_SECTION = f"[{SECTION}] secrets section"

DEFAULT_PORT = 5432

#: Fields that must be present before a URL can be assembled. ``port`` is not
#: here because PostgreSQL's own default is a sane one.
REQUIRED_FIELDS: Tuple[str, ...] = ("host", "database", "username", "password")

#: The keys this module understands. Anything else in the section is passed
#: through as a connection parameter (``sslmode``, ``connect_timeout``, ...),
#: because providers keep inventing those and hard-coding a list would mean
#: editing this file every time one does.
CORE_FIELDS: Tuple[str, ...] = ("host", "port", "database", "username", "password", "url")

#: Every provider prints these fields under a slightly different name.
ALIASES: Dict[str, str] = {
    "hostname": "host",
    "server": "host",
    "user": "username",
    "dbname": "database",
    "db": "database",
    "name": "database",
    "passwd": "password",
    "pwd": "password",
    "connection_string": "url",
    "uri": "url",
}

#: A hostname, or a bracketed IPv6 literal. Deliberately strict: a value that
#: is not one of these is a pasted connection string or a typo, and saying so
#: now is kinder than a DNS error twenty seconds into start-up.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_IPV6_RE = re.compile(r"^\[[0-9A-Fa-f:.]+\]$")
#: The same address written without the brackets a URL needs - it is a host,
#: so it is accepted and bracketed rather than called a typo.
_BARE_IPV6_RE = re.compile(r"^[0-9A-Fa-f:]*:[0-9A-Fa-f:.]*$")

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://")
_POSTGRES_SCHEMES = ("postgresql", "postgres")


class DatabaseConfigError(RuntimeError):
    """The database configuration is missing something or cannot be understood.

    Always safe to show to the user: the message names fields, never values.
    """


@dataclass(frozen=True)
class DatabaseConfig:
    """The resolved answer to "which database, and who said so?"."""

    #: A PostgreSQL URL, or None when the local SQLite file should be used.
    url: Optional[str] = None
    #: ``DATABASE_URL``, the section name, or SOURCE_NONE.
    source: str = SOURCE_NONE

    @property
    def is_postgres(self) -> bool:
        return bool(self.url)

    @property
    def description(self) -> str:
        """One line for the screen. Contains no password - see safe_url."""
        if not self.url:
            return "Local SQLite file"
        return f"PostgreSQL {safe_url(self.url)} (from {self.source})"


def safe_url(url: str) -> str:
    """A connection string with the password removed - never log the real one."""
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url or "")


# ------------------------------------------------------------ assembling ---
def normalise_section(values: Mapping[str, Any]) -> Dict[str, Any]:
    """Lower-case the keys and apply the provider aliases."""
    cleaned: Dict[str, Any] = {}
    for key, value in values.items():
        name = str(key).strip().lower()
        name = ALIASES.get(name, name)
        cleaned[name] = value
    return cleaned


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _check_host(host: str) -> str:
    """Validate the host without ever echoing something that hides a password."""
    if "://" in host or "@" in host:
        # Echoing this would print the password of a pasted connection string.
        raise DatabaseConfigError(
            f"The 'host' value in the [{SECTION}] section looks like a whole "
            "connection string rather than a host name. Put the connection "
            "string in DATABASE_URL, or give host/port/database/username/"
            "password as separate fields."
        )
    if any(character.isspace() for character in host) or "/" in host:
        raise DatabaseConfigError(
            f"The 'host' value in the [{SECTION}] section is not a valid host "
            "name: it contains a space or a slash. Use just the host, for "
            "example host = \"db.example.com\"."
        )
    if _IPV6_RE.match(host):
        return host
    if _BARE_IPV6_RE.match(host):
        return f"[{host}]"
    if not _HOSTNAME_RE.match(host):
        raise DatabaseConfigError(
            f"The 'host' value in the [{SECTION}] section is not a valid host "
            f"name: {host!r}. Use just the host, for example "
            "host = \"db.example.com\"."
        )
    return host


def _check_port(value: Any) -> int:
    if _text(value) == "":
        return DEFAULT_PORT
    try:
        port = int(float(_text(value)))
    except (TypeError, ValueError):
        raise DatabaseConfigError(
            f"The 'port' value in the [{SECTION}] section is not a number. "
            f"Use port = {DEFAULT_PORT} (the PostgreSQL default) or the port "
            "your provider gives you."
        ) from None
    if not 1 <= port <= 65535:
        raise DatabaseConfigError(
            f"The 'port' value in the [{SECTION}] section must be between 1 "
            f"and 65535. Use port = {DEFAULT_PORT} unless your provider says "
            "otherwise."
        )
    return port


def url_from_section(values: Mapping[str, Any]) -> str:
    """Assemble a PostgreSQL URL from ``[database]`` fields.

    Raises :class:`DatabaseConfigError` - with a message that names fields and
    never quotes a password - if anything required is missing or malformed.
    """
    section = normalise_section(values)

    # A section may also just carry the whole string, which some providers
    # give you as "URI". Honour it rather than making the user take it apart.
    given = _text(section.get("url"))
    if given:
        url = checked_url(given, f"the 'url' value in the [{SECTION}] section")
        if url is None:
            raise DatabaseConfigError(
                f"The 'url' value in the [{SECTION}] section points at SQLite, "
                "not PostgreSQL. Remove it to use the local file."
            )
        return url

    missing = [field for field in REQUIRED_FIELDS if not _text(section.get(field))]
    if missing:
        raise DatabaseConfigError(
            f"The [{SECTION}] section is missing: {', '.join(missing)}. "
            f"It needs {', '.join(REQUIRED_FIELDS)} (port is optional and "
            f"defaults to {DEFAULT_PORT}). Copy the values from your database "
            "provider's connection page. If your database needs no password, "
            "use DATABASE_URL instead - this section always requires one."
        )

    host = _check_host(_text(section["host"]))
    port = _check_port(section.get("port"))

    # Percent-encoding is why this function exists: "p@ss/word" is a perfectly
    # ordinary password and a broken URL.
    username = quote(_text(section["username"]), safe="")
    password = quote(_text(section["password"]), safe="")
    database = quote(_text(section["database"]), safe="")

    extras = {
        key: _text(value)
        for key, value in sorted(section.items())
        if key not in CORE_FIELDS and _text(value)
    }
    query = urlencode(extras) if extras else ""
    return urlunsplit(("postgresql", f"{username}:{password}@{host}:{port}", f"/{database}", query, ""))


def checked_url(value: str, origin: str) -> Optional[str]:
    """Validate a connection string. None means "use the SQLite file".

    The value is never echoed: a malformed connection string usually still
    contains a real password.
    """
    text = _text(value)
    if not text:
        return None
    scheme_match = _SCHEME_RE.match(text)
    scheme = (scheme_match.group(1).lower() if scheme_match else "")
    if scheme in _POSTGRES_SCHEMES:
        return text
    if scheme.startswith("sqlite"):
        return None
    if not scheme:
        raise DatabaseConfigError(
            f"{origin} is not a connection string: it has no scheme. A "
            "PostgreSQL URL looks like "
            "postgresql://user:password@host:5432/dbname - or use a "
            f"[{SECTION}] section and give the fields separately."
        )
    raise DatabaseConfigError(
        f"{origin} is not a PostgreSQL connection string: it starts with "
        f"'{scheme}://'. This app stores its data in PostgreSQL or SQLite. "
        f"Use postgresql://..., or a [{SECTION}] section. If that setting "
        f"belongs to a different application, set {URL_VARIABLES[0]}="
        "sqlite:// to keep this app on its own local file."
    )


# ------------------------------------------------------------- resolving ---
def resolve() -> DatabaseConfig:
    """Which database this process should use, and where that was configured.

    Worked out fresh on every call. It is consulted once per connection, and
    everything it reads is already in memory - the environment, and Streamlit's
    own parsed secrets - so there is nothing here worth caching and a cache
    would go stale exactly when it matters: the moment somebody corrects the
    setting that is broken.
    """
    load_env()

    # 1. A connection string, from the environment, .env, or a top-level
    #    Streamlit secret. Highest priority so nothing that works today stops.
    for name in URL_VARIABLES:
        value = secret(name)
        if value:
            url = checked_url(value, name)
            return DatabaseConfig(url=url, source=name if url else SOURCE_NONE)

    # 2. The [database] section of the Streamlit secrets. Streamlit only
    #    copies top-level string secrets into the environment, so a section
    #    has to be read from st.secrets directly - which is exactly why this
    #    lookup exists and why it cannot be done with os.getenv.
    section = secrets_section(SECTION)
    if section:
        return DatabaseConfig(url=url_from_section(section), source=SOURCE_SECTION)

    return DatabaseConfig(url=None, source=SOURCE_NONE)


def database_url() -> Optional[str]:
    """The configured PostgreSQL URL, or None for the local SQLite file."""
    return resolve().url


def describe_source() -> str:
    """Where the current configuration came from, safe to put on screen."""
    return resolve().source
