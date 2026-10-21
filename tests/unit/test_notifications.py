"""
Test the notification mechanics

The settings are tested by passing an environment in, rather than by setting real
environment variables, and the sending is tested against a fake `smtplib.SMTP` so
that nothing here needs a network or a mail server.
"""

from __future__ import annotations

import email
import email.policy

import pytest

from cmip_output_tracker.notifications import (
    DEFAULT_SMTP_PORT,
    EmailSettings,
    MissingEmailSettingError,
    send_email,
)

FULL_ENV = {
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "2525",
    "EMAIL_SENDER": "sender@example.com",
    "SMTP_USERNAME": "user@example.com",
    "SMTP_PASSWORD": "a-password",
    "SMTP_STARTTLS": "true",
}


class FakeSMTP:
    """Stand-in for `smtplib.SMTP` which records what was asked of it"""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.started_tls = False
        self.login_args = None
        self.sent = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.sent = message


@pytest.fixture
def fake_smtp(monkeypatch):
    """Replace `smtplib.SMTP` and hand back the instance which gets built"""
    built = []

    def build(host, port):
        server = FakeSMTP(host, port)
        built.append(server)

        return server

    monkeypatch.setattr("cmip_output_tracker.notifications.smtplib.SMTP", build)

    return built


def test_from_env_reads_everything():
    settings = EmailSettings.from_env(FULL_ENV)

    assert settings == EmailSettings(
        host="smtp.example.com",
        sender="sender@example.com",
        port=2525,
        username="user@example.com",
        password="a-password",
        use_starttls=True,
    )


def test_from_env_defaults():
    settings = EmailSettings.from_env(
        {"SMTP_HOST": "localhost", "EMAIL_SENDER": "sender@example.com"}
    )

    assert settings.port == DEFAULT_SMTP_PORT
    # No credentials means do not try to authenticate, which is what a relay on the
    # server itself wants.
    assert settings.username is None
    assert settings.password is None
    # On unless explicitly turned off.
    assert settings.use_starttls


@pytest.mark.parametrize("value", ["false", "FALSE", " False "])
def test_from_env_starttls_can_be_turned_off(value):
    env = {**FULL_ENV, "SMTP_STARTTLS": value}

    assert not EmailSettings.from_env(env).use_starttls


@pytest.mark.parametrize("value", ["true", "yes", "", "ture"])
def test_from_env_starttls_stays_on_for_anything_else(value):
    # A typo must not be what sends the password in clear.
    env = {**FULL_ENV, "SMTP_STARTTLS": value}

    assert EmailSettings.from_env(env).use_starttls


@pytest.mark.parametrize("missing", ["SMTP_HOST", "EMAIL_SENDER"])
def test_from_env_requires_the_essentials(missing):
    env = {key: value for key, value in FULL_ENV.items() if key != missing}

    with pytest.raises(MissingEmailSettingError) as caught:
        EmailSettings.from_env(env)

    assert caught.value.name == missing


def test_from_env_treats_blank_as_missing():
    with pytest.raises(MissingEmailSettingError):
        EmailSettings.from_env({**FULL_ENV, "SMTP_HOST": "   "})


def test_send_email(fake_smtp):
    send_email(
        EmailSettings.from_env(FULL_ENV),
        recipients=["one@example.com", "two@example.com"],
        subject="a-subject",
        body="a line\nanother line",
    )

    (server,) = fake_smtp
    assert (server.host, server.port) == ("smtp.example.com", 2525)
    assert server.started_tls
    assert server.login_args == ("user@example.com", "a-password")

    assert server.sent["From"] == "sender@example.com"
    assert server.sent["To"] == "one@example.com, two@example.com"
    assert server.sent["Subject"] == "a-subject"


def test_send_email_without_credentials_does_not_authenticate(fake_smtp):
    send_email(
        EmailSettings.from_env(
            {
                "SMTP_HOST": "localhost",
                "SMTP_PORT": "25",
                "EMAIL_SENDER": "sender@example.com",
                "SMTP_STARTTLS": "false",
            }
        ),
        recipients=["one@example.com"],
        subject="a-subject",
        body="a body",
    )

    (server,) = fake_smtp
    assert not server.started_tls
    assert server.login_args is None


def test_send_email_keeps_wide_lines_intact(fake_smtp):
    # The whole point of the message is a table, and the default encoding
    # (quoted-printable) soft-wraps at 76 characters, which would fold every row.
    body = "\n".join(["x" * 100, "y" * 100])

    send_email(
        EmailSettings.from_env(FULL_ENV),
        recipients=["one@example.com"],
        subject="a-subject",
        body=body,
    )

    (server,) = fake_smtp
    # Round-tripped through the wire format, so this checks what a reader gets
    # rather than what we handed over.
    delivered = email.message_from_string(
        server.sent.as_string(), policy=email.policy.default
    )

    plain = delivered.get_body(preferencelist=("plain",))
    assert plain.get_content().rstrip("\n") == body

    # And the same text again, so that it is shown monospaced.
    html = delivered.get_body(preferencelist=("html",))
    assert "x" * 100 in html.get_content()
    assert "<pre" in html.get_content()


def test_send_email_escapes_the_html_part(fake_smtp):
    send_email(
        EmailSettings.from_env(FULL_ENV),
        recipients=["one@example.com"],
        subject="a-subject",
        body="1 < 2 & 3 > 2",
    )

    (server,) = fake_smtp
    html = server.sent.get_body(preferencelist=("html",)).get_content()

    assert "1 &lt; 2 &amp; 3 &gt; 2" in html


def test_send_email_needs_somebody_to_send_to(fake_smtp):
    with pytest.raises(ValueError, match="nobody to notify"):
        send_email(
            EmailSettings.from_env(FULL_ENV),
            recipients=[],
            subject="a-subject",
            body="a body",
        )

    assert not fake_smtp
