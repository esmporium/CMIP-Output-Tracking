"""
Sending notifications about what a tracked search found

The driver script holds the configuration (who to email, which search).
This module holds only the mechanics of getting a message out,
so that the credentials are read in one place
and the sending can be tested without a driver.
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

DEFAULT_SMTP_PORT: int = 587
"""
Port used if `SMTP_PORT` is not set

The submission port, i.e. the one to use with STARTTLS.
"""


class MissingEmailSettingError(ValueError):
    """
    Raised when a required email setting is not in the environment
    """

    def __init__(self, name: str) -> None:
        """
        Initialise the error

        Parameters
        ----------
        name
            Name of the environment variable which is missing
        """
        self.name = name
        """Name of the environment variable which is missing"""

        super().__init__(
            f"{name} is not set. "
            "Put it in the .env file at the root of this repository "
            "(see .env.example), or export it before running."
        )


@dataclass(frozen=True)
class EmailSettings:
    """
    Everything needed to send mail, i.e. the parts we keep out of the driver script

    Built by [from_env][(c).from_env],
    because these are secrets and so do not belong in a tracked file.
    """

    host: str
    """Host name of the SMTP server"""

    sender: str
    """Address the notifications are sent from"""

    port: int = DEFAULT_SMTP_PORT
    """Port to connect to `host` on"""

    username: str | None = None
    """
    User name to authenticate with, or `None` to not authenticate

    `None` is the right answer for a mail relay listening on the server itself,
    which authenticates nothing because only the server can reach it.
    """

    password: str | None = None
    """Password to authenticate with, ignored when `username` is `None`"""

    use_starttls: bool = True
    """
    Whether to upgrade the connection to TLS before sending

    Leave this on for anything which crosses a network.
    Turn it off only for a relay on `localhost`, which typically does not offer it.
    """

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EmailSettings:
        """
        Read the settings from the environment

        Parameters
        ----------
        env
            Environment to read from.
            `None`, the default, reads the process's own environment,
            which is where
            [python-dotenv](https://pypi.org/project/python-dotenv)
            puts the contents of a `.env` file.

        Returns
        -------
        :
            The settings

        Raises
        ------
        MissingEmailSettingError
            `SMTP_HOST` or `EMAIL_SENDER` is not set
        """
        source = os.environ if env is None else env

        def required(name: str) -> str:
            """Get a setting which has no sensible default"""
            value = source.get(name, "").strip()
            if not value:
                raise MissingEmailSettingError(name)

            return value

        username = source.get("SMTP_USERNAME", "").strip()
        password = source.get("SMTP_PASSWORD", "")

        return cls(
            host=required("SMTP_HOST"),
            sender=required("EMAIL_SENDER"),
            port=int(source.get("SMTP_PORT", "").strip() or DEFAULT_SMTP_PORT),
            username=username or None,
            password=password or None,
            # Anything but an explicit "false" leaves TLS on,
            # so a typo in the .env file cannot quietly send the password in clear.
            use_starttls=source.get("SMTP_STARTTLS", "true").strip().lower() != "false",
        )


def send_email(
    settings: EmailSettings,
    *,
    recipients: Sequence[str],
    subject: str,
    body: str,
) -> None:
    """
    Send one plain-text email

    Parameters
    ----------
    settings
        How to connect to the mail server, and who the mail is from

    recipients
        Addresses to send to.
        All of them are put in `To`, i.e. they can all see each other.

    subject
        Subject line

    body
        Body of the message, as plain text.

        Sent as both plain text and HTML, see below.

    Raises
    ------
    ValueError
        `recipients` is empty
    """
    if not recipients:
        msg = "No recipients given, so there is nobody to notify"
        raise ValueError(msg)

    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject

    # base64 rather than the default quoted-printable, which soft-wraps at 76
    # characters. The body is a table of columns wider than that, so the default
    # silently folds every row in half and the columns stop lining up.
    message.set_content(body, cte="base64")

    # The same text again, wrapped so that it is shown in a monospaced font. A
    # `text/plain` part is at the mercy of whatever font the reader's client uses,
    # and a proportional one throws the columns out however they were encoded.
    # Clients which prefer plain text still get the part above, untouched.
    message.add_alternative(
        "<html><body>"
        f'<pre style="font-family: monospace; font-size: 13px">{escape(body)}</pre>'
        "</body></html>",
        subtype="html",
        cte="base64",
    )

    with smtplib.SMTP(settings.host, settings.port) as server:
        if settings.use_starttls:
            server.starttls()

        if settings.username is not None:
            server.login(settings.username, settings.password or "")

        server.send_message(message)
