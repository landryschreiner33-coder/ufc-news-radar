"""Configuring the database: DATABASE_URL, and the ``[database]`` secrets section.

Streamlit Community Cloud has no ``.env`` file - it has a Secrets box that
takes TOML - so these tests drive the real ``st.secrets`` machinery against a
temporary ``secrets.toml``, the same way the deployed app reads it. Asserting
against a hand-made dict would prove the parsing and nothing about whether
the cloud's secrets actually reach the database layer.

Passwords are checked for absence, not presence: an error message that quotes
the password puts it on screen and into the logs.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from database import db_config
from database.db_config import DatabaseConfigError

pytest.importorskip("streamlit")


@pytest.fixture
def no_database_configuration(monkeypatch):
    """No connection string anywhere - the state a fresh checkout is in."""
    for name in db_config.URL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def streamlit_secrets(tmp_path, no_database_configuration):
    """Write a real secrets.toml and point ``st.secrets`` at it.

    Streamlit copies top-level string secrets into the environment when it
    parses the file, exactly as it does in the cloud, so a test that writes
    DATABASE_URL here is testing the real path and not a shortcut.

    ``secrets_singleton._reset()`` is private because nothing in an app has
    any business calling it; a test that swaps the secrets file does, and
    Streamlit offers no public equivalent.
    """
    from streamlit import config as st_config
    from streamlit.runtime.secrets import secrets_singleton

    previous_files = st_config.get_option("secrets.files")
    path = tmp_path / "secrets.toml"

    def write(body: str):
        path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
        st_config.set_option("secrets.files", [str(path)])
        secrets_singleton._reset()
        return path

    yield write

    secrets_singleton._reset()
    st_config.set_option("secrets.files", previous_files)


# ------------------------------------------------------------ DATABASE_URL -
def test_a_database_url_in_the_environment_is_used_as_given(monkeypatch, no_database_configuration):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db.example.com:5432/radar")
    configured = db_config.resolve()

    assert configured.url == "postgresql://user:pw@db.example.com:5432/radar"
    assert configured.source == "DATABASE_URL"
    assert configured.is_postgres is True


def test_the_older_postgres_scheme_is_accepted(monkeypatch, no_database_configuration):
    """Several providers still hand out postgres:// - psycopg2 accepts both."""
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@db.example.com/radar")

    assert db_config.database_url() == "postgres://user:pw@db.example.com/radar"


def test_a_database_url_in_the_secrets_box_reaches_the_database_layer(streamlit_secrets):
    streamlit_secrets('DATABASE_URL = "postgresql://user:pw@db.example.com:5432/radar"')

    assert db_config.database_url() == "postgresql://user:pw@db.example.com:5432/radar"


def test_no_configuration_anywhere_means_the_local_sqlite_file(streamlit_secrets):
    streamlit_secrets('SOMETHING_ELSE = "1"')
    configured = db_config.resolve()

    assert configured.url is None
    assert configured.is_postgres is False
    assert configured.description == "Local SQLite file"


def test_a_database_url_that_is_not_postgres_fails_loudly(monkeypatch, no_database_configuration):
    """Silently using SQLite instead is the failure this project refuses."""
    monkeypatch.setenv("DATABASE_URL", "mysql://user:hunter2@db.example.com/radar")

    with pytest.raises(DatabaseConfigError) as raised:
        db_config.resolve()
    message = str(raised.value)
    assert "mysql" in message
    assert "hunter2" not in message, "an error must never echo the password"


def test_a_database_url_that_is_not_a_url_at_all_says_so(monkeypatch, no_database_configuration):
    monkeypatch.setenv("DATABASE_URL", "host=db.example.com password=hunter2")

    with pytest.raises(DatabaseConfigError) as raised:
        db_config.resolve()
    assert "hunter2" not in str(raised.value)


def test_an_explicit_sqlite_url_selects_the_file_backend(monkeypatch, no_database_configuration):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/ufc_news_radar.db")
    configured = db_config.resolve()

    assert configured.url is None
    assert configured.is_postgres is False


def test_this_app_can_opt_out_of_a_database_url_that_is_not_its_own(monkeypatch,
                                                                   no_database_configuration):
    """DATABASE_URL is a name other software uses; the app-specific one wins."""
    monkeypatch.setenv("DATABASE_URL", "mysql://user:pw@db.example.com/somebody_elses_app")
    monkeypatch.setenv("UFC_RADAR_DATABASE_URL", "sqlite://")

    assert db_config.resolve().url is None


# ------------------------------------------------------- [database] section -
def test_the_database_section_builds_a_postgres_url(streamlit_secrets):
    """The exact shape a provider's connection page gives you."""
    streamlit_secrets("""
        [database]
        host = "aws-1-eu-west-2.pooler.example.com"
        port = 5432
        database = "postgres"
        username = "postgres.abcdefghijkl"
        password = "a-plain-password"
    """)
    configured = db_config.resolve()

    assert configured.url == (
        "postgresql://postgres.abcdefghijkl:a-plain-password"
        "@aws-1-eu-west-2.pooler.example.com:5432/postgres")
    assert configured.source == db_config.SOURCE_SECTION
    assert configured.is_postgres is True

    parse_dsn = pytest.importorskip("psycopg2.extensions").parse_dsn
    assert parse_dsn(configured.url) == {
        "host": "aws-1-eu-west-2.pooler.example.com",
        "port": "5432",
        "dbname": "postgres",
        "user": "postgres.abcdefghijkl",
        "password": "a-plain-password",
    }


def test_a_password_with_url_punctuation_is_encoded_not_mangled(streamlit_secrets):
    """The reason this module exists: "p@ss/word" is a perfectly valid password.

    The check is made with libpq's own parser rather than by comparing
    strings, because libpq is what eventually reads this URL: if it takes the
    "@" in the password as the start of the host, the app connects to the
    wrong place - or to nowhere - and blames the network.
    """
    parse_dsn = pytest.importorskip("psycopg2.extensions").parse_dsn
    streamlit_secrets("""
        [database]
        host = "db.example.com"
        database = "postgres"
        username = "postgres.abc"
        password = "p@ss/wo:rd?#1"
    """)
    parsed = parse_dsn(db_config.database_url())

    assert parsed["host"] == "db.example.com", "the @ in the password must not become a host"
    assert parsed["password"] == "p@ss/wo:rd?#1"
    assert parsed["user"] == "postgres.abc"
    assert parsed["dbname"] == "postgres"


def test_the_port_is_optional_and_defaults_to_the_postgres_default(streamlit_secrets):
    streamlit_secrets("""
        [database]
        host = "db.example.com"
        database = "postgres"
        username = "postgres"
        password = "pw"
    """)

    assert urlsplit(db_config.database_url()).port == db_config.DEFAULT_PORT


def test_a_port_written_as_a_string_is_accepted(streamlit_secrets):
    """The Secrets box is TOML, and people quote numbers in it."""
    streamlit_secrets("""
        [database]
        host = "db.example.com"
        port = "6543"
        database = "postgres"
        username = "postgres"
        password = "pw"
    """)

    assert urlsplit(db_config.database_url()).port == 6543


def test_the_field_names_other_providers_print_are_accepted(streamlit_secrets):
    streamlit_secrets("""
        [database]
        hostname = "db.example.com"
        dbname = "radar"
        user = "radar_app"
        password = "pw"
    """)
    parts = urlsplit(db_config.database_url())

    assert (parts.hostname, parts.username, parts.path) == ("db.example.com", "radar_app", "/radar")


def test_an_ipv6_host_is_bracketed_for_the_url(streamlit_secrets):
    """A URL needs [brackets] around an IPv6 address; a provider's page does not."""
    parse_dsn = pytest.importorskip("psycopg2.extensions").parse_dsn
    streamlit_secrets("""
        [database]
        host = "2001:db8::1"
        database = "postgres"
        username = "postgres"
        password = "pw"
    """)
    url = db_config.database_url()

    assert "@[2001:db8::1]:" in url
    assert parse_dsn(url)["host"] == "2001:db8::1"


def test_extra_keys_become_connection_parameters(streamlit_secrets):
    """sslmode is required by several hosts; nothing here needs to know that."""
    streamlit_secrets("""
        [database]
        host = "db.example.com"
        database = "postgres"
        username = "postgres"
        password = "pw"
        sslmode = "require"
    """)

    assert db_config.database_url().endswith("/postgres?sslmode=require")


def test_a_section_may_carry_the_whole_url(streamlit_secrets):
    """Some providers print one "URI" field and nothing else."""
    streamlit_secrets("""
        [database]
        url = "postgresql://user:pw@db.example.com:5432/radar"
    """)

    assert db_config.database_url() == "postgresql://user:pw@db.example.com:5432/radar"


def test_an_empty_section_configures_nothing(streamlit_secrets):
    """A stray header is not a half-configured database - there is nothing to use."""
    streamlit_secrets("""
        [database]
    """)

    assert db_config.database_url() is None


# ------------------------------------------------------------- validation --
def test_a_missing_password_is_a_clear_error(streamlit_secrets):
    streamlit_secrets("""
        [database]
        host = "db.example.com"
        port = 5432
        database = "postgres"
        username = "postgres.abc"
    """)

    with pytest.raises(DatabaseConfigError) as raised:
        db_config.resolve()
    named_as_missing = str(raised.value).split("missing:", 1)[1].split(".", 1)[0]

    assert "password" in named_as_missing
    assert "host" not in named_as_missing, (
        "the error names what is missing, not everything the section wanted")


def test_every_missing_required_field_is_named(streamlit_secrets):
    streamlit_secrets("""
        [database]
        host = "db.example.com"
    """)

    with pytest.raises(DatabaseConfigError) as raised:
        db_config.resolve()
    message = str(raised.value)
    for field in ("database", "username", "password"):
        assert field in message


def test_a_blank_password_counts_as_missing(streamlit_secrets):
    streamlit_secrets("""
        [database]
        host = "db.example.com"
        database = "postgres"
        username = "postgres"
        password = "   "
    """)

    with pytest.raises(DatabaseConfigError) as raised:
        db_config.resolve()
    assert "password" in str(raised.value)


def test_an_invalid_host_is_a_clear_error(streamlit_secrets):
    streamlit_secrets("""
        [database]
        host = "db example.com"
        database = "postgres"
        username = "postgres"
        password = "hunter2"
    """)

    with pytest.raises(DatabaseConfigError) as raised:
        db_config.resolve()
    message = str(raised.value)
    assert "host" in message
    assert "hunter2" not in message


def test_a_connection_string_pasted_into_the_host_field_is_caught(streamlit_secrets):
    """A likely mistake, and one that must not print the password back."""
    streamlit_secrets("""
        [database]
        host = "postgresql://postgres:hunter2@db.example.com:5432/postgres"
        database = "postgres"
        username = "postgres"
        password = "hunter2"
    """)

    with pytest.raises(DatabaseConfigError) as raised:
        db_config.resolve()
    message = str(raised.value)
    assert "DATABASE_URL" in message
    assert "hunter2" not in message, "the pasted string contains a password"


def test_an_invalid_port_is_a_clear_error(streamlit_secrets):
    streamlit_secrets("""
        [database]
        host = "db.example.com"
        port = "not-a-number"
        database = "postgres"
        username = "postgres"
        password = "pw"
    """)

    with pytest.raises(DatabaseConfigError) as raised:
        db_config.resolve()
    assert "port" in str(raised.value)


def test_a_port_outside_the_valid_range_is_a_clear_error(streamlit_secrets):
    streamlit_secrets("""
        [database]
        host = "db.example.com"
        port = 99999
        database = "postgres"
        username = "postgres"
        password = "pw"
    """)

    with pytest.raises(DatabaseConfigError) as raised:
        db_config.resolve()
    assert "port" in str(raised.value)


# ---------------------------------------------------------- both at once ---
def test_database_url_wins_when_both_are_configured(streamlit_secrets):
    """Adding a section must never change what a working deployment does."""
    streamlit_secrets("""
        DATABASE_URL = "postgresql://url_user:pw@url-host.example.com:5432/from_url"

        [database]
        host = "section-host.example.com"
        database = "from_section"
        username = "section_user"
        password = "pw"
    """)
    configured = db_config.resolve()

    assert configured.source == "DATABASE_URL"
    assert urlsplit(configured.url).hostname == "url-host.example.com"
    assert "section-host" not in configured.url


def test_the_environment_beats_the_secrets_section(monkeypatch, streamlit_secrets):
    streamlit_secrets("""
        [database]
        host = "section-host.example.com"
        database = "from_section"
        username = "section_user"
        password = "pw"
    """)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@env-host.example.com/from_env")

    assert urlsplit(db_config.database_url()).hostname == "env-host.example.com"


def test_setting_the_environment_variable_takes_effect_immediately(monkeypatch, no_database_configuration):
    """Nothing is cached, so a corrected setting takes effect at once."""
    assert db_config.database_url() is None
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db.example.com/radar")

    assert db_config.database_url() == "postgresql://user:pw@db.example.com/radar"


# -------------------------------------------------- the rest of the app ----
def test_the_password_is_never_shown_when_the_configuration_is_described(streamlit_secrets):
    streamlit_secrets("""
        [database]
        host = "db.example.com"
        database = "postgres"
        username = "postgres"
        password = "hunter2"
    """)
    configured = db_config.resolve()

    assert "hunter2" not in configured.description
    assert "***" in configured.description
    assert "hunter2" not in db_config.safe_url(configured.url)


def test_the_section_reaches_the_backend_that_actually_connects(streamlit_secrets, monkeypatch):
    """The point of the whole exercise: secrets in, PostgreSQL backend out."""
    import database.backends as backends

    streamlit_secrets("""
        [database]
        host = "db.example.com"
        database = "postgres"
        username = "postgres"
        password = "hunter2"
    """)
    # The driver is real here; connecting is not the subject of this test.
    monkeypatch.setattr(backends.PostgresBackend, "driver", staticmethod(lambda: None))

    backend = backends.build_backend("data/unused.db")

    assert isinstance(backend, backends.PostgresBackend)
    assert backend.name == backends.POSTGRES
    assert urlsplit(backend.url).hostname == "db.example.com"
    assert "hunter2" not in backend.describe()


def test_a_misconfigured_section_never_falls_back_to_the_sqlite_file(streamlit_secrets):
    import database.backends as backends

    streamlit_secrets("""
        [database]
        host = "db.example.com"
        username = "postgres"
    """)

    with pytest.raises(DatabaseConfigError):
        backends.build_backend("data/unused.db")


def test_a_local_secrets_file_can_never_be_committed():
    """The [database] section holds a live password; git must not see it.

    .streamlit/config.toml (the theme) is committed and sits in the same
    folder, which is exactly how a secrets file gets swept up by `git add .`.
    """
    ignored = Path(__file__).resolve().parent.parent / ".gitignore"
    patterns = {line.strip() for line in ignored.read_text().splitlines()}

    assert ".streamlit/secrets.toml" in patterns
    assert "secrets.toml" in patterns


def test_the_settings_page_names_the_configuration_source_not_the_url(streamlit_secrets):
    from database.persistence import describe_location

    streamlit_secrets("""
        [database]
        host = "db.example.com"
        database = "postgres"
        username = "postgres"
        password = "hunter2"
    """)
    location = describe_location("data/ufc_news_radar.db")

    assert "PostgreSQL" in location
    assert db_config.SECTION in location
    assert "hunter2" not in location
