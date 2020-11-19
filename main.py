# coding=utf-8
import argparse
from email.message import EmailMessage
from getpass import getpass
import io
import re
import smtplib
import ssl
import sys
from typing import Any, Optional, Callable

from bs4 import BeautifulSoup
import requests
import toml


OptionalString = Optional[str]
PageType = requests.models.Response


def _page_text(page: PageType) -> str:
    """Return the specified `page`'s text"""
    return page.text


def _dump_a_page_to_file(page: PageType, file_name: str):
    """Dump the text of `page` into `file_name`
    It might raise all kinds of io errors...
    """
    with io.open(file_name, mode="w", encoding="utf-8") as dump_file:
        dump_file.write(_page_text(page))


def _mail_error(cfg: dict, error_message: str, subject: str, page: PageType = None):
    """Send an error email message according to `cfg`"""
    context = ssl.create_default_context()
    sender = cfg["sender"]

    with smtplib.SMTP_SSL("smtp.gmail.com", port=465, context=context) as server:
        server.login(sender, cfg["pass"])
        msg = EmailMessage()
        if page is None:
            msg.set_content(error_message)
        else:
            msg.set_content("{}\n\n{}".format(error_message, _page_text(page)))
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = cfg["recipient"]

        server.send_message(msg)


class TeveClubLink:
    """The hattrick link abstraction"""

    HEADER = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                      " (KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36"
    }
    SERVER_URL = "https://teveclub.hu"
    TEVE_LINK = "myteve.pet"
    TEACH_LINK = "tanit.pet"
    LOGOUT_LINK = "logout.pet"
    SESSION = None

    @classmethod
    def request(
            cls, link: OptionalString = None, use_headers: bool = True,
            method: str = "get", data: Any = None) -> PageType:
        """Request the download of the link using the live session and return
        the HTML response
        Raise an exception if the final response code isn't 200 (OK).
        The `link` can be None to just send the request to the `SERVER_URL`,
        otherwise it must be a sub-link.
        If `method` is not a valid session method, AttributeError will be
        raised
        """
        session_method = getattr(cls.SESSION, method)

        server_url = cls.SERVER_URL
        link_url = server_url if link is None else "{}/{}".format(server_url, link)

        params = {}
        if use_headers:
            params["headers"] = cls.HEADER
        if data is not None:
            params["data"] = data

        response = session_method(link_url, **params)
        response.raise_for_status()

        return response

    @classmethod
    def start_session(cls):
        """Start the live session"""
        cls.SESSION = requests.Session()

    @classmethod
    def close_session(cls):
        """End the live session"""
        if cls.SESSION is not None:
            cls.SESSION.__exit__()


def _parse_login_status(response: PageType):
    """Parse login status from the response page and return True for logged in
    and False otherwise"""
    login_failure_pattern = r"Valami baj van!"
    return not re.search(login_failure_pattern, _page_text(response))


def _parse_teach_success(response: PageType):
    teach_success_pattern = "A tevédet ma már tanítottad"
    return re.search(teach_success_pattern, _page_text(response))


def _parse_feed_success(response: PageType):
    feeding_is_still_possible = r"Adok neki .* napra elég ennivalót"
    return not re.search(feeding_is_still_possible, _page_text(response))


def _ensure_login(mail_cfg: dict, response: PageType):
    """If login failed based on the `response` send a mail using the `mail_cfg`"""
    logged_in = _parse_login_status(response)
    if logged_in:
        print("Bent vagyunk! :)")
    else:
        _mail_error(mail_cfg, "Nem sikerült bejelentkezni!?", "Automatikus belépési hiba!!!", response)


def _get_name():
    """Get the camel's name from the user"""
    name = None
    while name is None:
        try:
            name = input("Teve neve: ")
        except SyntaxError:
            name = None
        if name == "":
            name = None
        if name is None:
            print("Kérlek adj meg egy igazi nevet")
    return name


def _get_from_user_if_none(maybe_none_value: OptionalString, get_from_user_function: Callable):
    """Get the value from the user if it was None originally"""
    if maybe_none_value is None:
        value = get_from_user_function()
    else:
        value = maybe_none_value
    return value


class Teve:
    LOGIN_FORM = {
        "x": "26",
        "y": "22",
        "login": "Gyere!",
    }

    NAME_FIELD = "tevenev"
    PASSWORD_FIELD = "pass"

    def __init__(self, config: dict):
        self.name = config["teve"]["name"]
        self.password = config["teve"]["pass"]
        self.logged_in = False
        self.mail_cfg = config["mail"]

    def __enter__(self):
        try:
            TeveClubLink.start_session()

            self.LOGIN_FORM[self.NAME_FIELD] = _get_from_user_if_none(self.name, _get_name)
            self.LOGIN_FORM[self.PASSWORD_FIELD] = _get_from_user_if_none(self.password, getpass)

            print("Bejelentkezés... ", end="")
            response = TeveClubLink.request(method="post", data=self.LOGIN_FORM)
            response.raise_for_status()
            _ensure_login(self.mail_cfg, response)
            self.logged_in = True
        except Exception:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.logged_in:
            TeveClubLink.request(TeveClubLink.LOGOUT_LINK)
            self.logged_in = False
            print("Kijelentkeztem! :)")

        TeveClubLink.close_session()

    def etet(self):
        print("Etetés... ", end="")
        response = TeveClubLink.request(TeveClubLink.TEVE_LINK)
        soup = BeautifulSoup(_page_text(response), "html.parser")
        max_kaja = self._max_option(soup, "kaja")
        max_pia = self._max_option(soup, "pia")

        if max_kaja == 0 and max_pia == 0:
            print("OK (nem éhes és nem is szomjas)")
        else:
            form_data = {
                "kaja": str(max_kaja),
                "pia": str(max_pia),
                "etet": "Mehet!",
            }
            response = TeveClubLink.request(TeveClubLink.TEVE_LINK, method="post", data=form_data)
            success = _parse_feed_success(response)
            if success:
                print("OK")
            else:
                self._print_page_error(response)
                _mail_error(self.mail_cfg, "Nem sikerült az etetés!", "Automatikus etetési kalamajka!", response)

    @staticmethod
    def _max_option(soup, name: str):
        options = soup.find(attrs={"name": name})
        if options is None:
            return 0
        return max([int(option.text) for option in options.find_all("option")])

    def tanit(self):
        print("Tanítás... ", end="")
        form_data = {
            "farmdoit": "tanit",
            "learn": "Tanulj teve!"
        }

        response = TeveClubLink.request(TeveClubLink.TEACH_LINK, method="post", data=form_data)
        success = _parse_teach_success(response)
        if success:
            print("OK")
        else:
            self._print_page_error(response)
            _mail_error(self.mail_cfg, "Nem sikerült a tanítás!", "Automatikus tanítási fennforgattyú!", response)

    @staticmethod
    def _print_page_error(page):
        print("ERROR: {}".format(_page_text(page)))


def parse_args():
    config_example = """Configuration Example:
[teve]
name = "Macska"
pass = "SoseTudodMeg"

[mail]
sender = "xyz@mail.com"
pass = "NemMondomEl"
recipient = "mokus@mail.com"
"""
    parser = argparse.ArgumentParser(
        description='TeveClub Automatizáció',
        epilog=config_example,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-c", "--config", required=True, help="A konfig file (lasd a lenti peldat)",
                        metavar='config.toml')

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    config = toml.load(args.config)

    try:
        teve = Teve(config)
        with teve:
            teve.etet()
            teve.tanit()
    except Exception as exc:
        _mail_error(
            config["mail"],
            "Ismeretlen fatálas hiba történt!!!",
            "Elpusztult az egész!!! ÁÁÁÁ!!!\n\n{}".format(exc)
        )
        raise
