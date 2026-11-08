"""The Upbit notice list of book KRW: one public GET, one allowlisted URL, no key.

    GET https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade

The only call this module makes. The host, the path, the method and every parameter with its value are fixed:
anything else is refused before a byte leaves (``check_url``, ``Feed.request``). No proxy, no redirect, no cookie, no
credential; a generic User-Agent; a 10 s timeout. The research list was fetched the same way, page by page
(data/notice_feasibility_20261108/scripts/fetch.py); the book reads page 1 only, the 20 notices most recently listed or
edited.

A notice is parsed to ``Notice(id, title, first_listed_at_ms, listed_at_ms)``. Upbit stamps both times in KST with an
explicit offset ("2026-10-27T16:30:00+09:00"); they are converted to UTC ms here, and a time without an offset is
refused, so a stamp is never read nine hours off. ``first_listed_at`` is the notice time of the rule: an edit appends
a follow-up to the title and moves ``listed_at`` only.

Any failure (network, status, body, a notice that does not parse) raises ``FeedError``; the book records it and its
exits still run. A 429 or 418 (Upbit asking to slow down) is tried once more after ``RETRY_WAIT_S``, then raised as
``FeedRateLimited``: the 5-minute pre-check then skips the notice list for that tick (its store and ledger reasons
are read before the list, so an exit or an unfinished order still gets its signed run). Research description, not
advice.
"""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit

HOST = "api-manager.upbit.com"
PATH = "/api/v1/announcements"
PARAMS = (("os", "web"), ("page", "1"), ("per_page", "20"), ("category", "trade"))
URL = f"https://{HOST}{PATH}?{urlencode(PARAMS)}"
METHOD = "GET"
TIMEOUT_S = 10.0
USER_AGENT = "Mozilla/5.0"          # generic; nothing that names this program or its owner
MAX_BYTES = 1 << 22                 # a page of 20 notices is about 6 kB
RATE_LIMITED = (418, 429)           # Upbit asks to slow down
RETRY_WAIT_S = 10.0                 # one more try after this wait, then FeedRateLimited

Transport = Callable[[str, float], tuple[int, bytes]]


class FeedError(Exception):
    """The notice list could not be read or parsed. Every failure of this module raises it."""


class FeedRefused(FeedError, ValueError):
    """A call outside the allowlist: another host, path, parameter, value or method."""


class FeedRateLimited(FeedError):
    """Upbit answered 429 or 418 twice in a row: skip the list for now and ask again at a later run."""


@dataclass(frozen=True)
class Notice:
    id: int
    title: str
    first_listed_at_ms: int     # the notice time of the rule (UTC ms)
    listed_at_ms: int           # the last listing or edit (UTC ms)


def check_url(url: str) -> None:
    """The allowlist: exactly ``URL``. Raises ``FeedRefused`` otherwise."""

    p = urlsplit(url)
    if p.scheme != "https" or p.netloc != HOST or p.path != PATH or p.fragment or p.username or p.password:
        raise FeedRefused(f"not the allowed notice list: {p.scheme}://{p.netloc}{p.path}")
    if sorted(parse_qsl(p.query, keep_blank_values=True)) != sorted(PARAMS):
        raise FeedRefused(f"not the allowed parameters: {p.query}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(url: str, timeout: float) -> tuple[int, bytes]:
    """One GET of the allowlisted URL. No proxy whatever the environment says; a redirect comes back as its status."""

    check_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    req = urllib.request.Request(url, method=METHOD, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with opener.open(req, timeout=min(timeout, TIMEOUT_S)) as r:
            body = r.read(MAX_BYTES)
            sent = r.headers.get("Content-Length")
            if sent is not None and len(body) != int(sent):
                raise http.client.IncompleteRead(body, int(sent) - len(body))
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read(MAX_BYTES)


def parse_time(text: object) -> int:
    """UTC ms of an Upbit stamp with an explicit offset; refuses a stamp without one."""

    if not isinstance(text, str):
        raise FeedError(f"not a time: {text!r}")
    try:
        t = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FeedError(f"not a time: {text!r}") from exc
    if t.tzinfo is None or t.utcoffset() is None:
        raise FeedError(f"a time without an offset: {text!r}")
    return int(round(t.timestamp() * 1000))


def parse(body: bytes) -> list[Notice]:
    """The notices of one page, as the endpoint lists them (newest ``listed_at`` first)."""

    try:
        raw = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FeedError("a body that is not JSON") from exc
    if not isinstance(raw, dict) or raw.get("success") is not True or not isinstance(raw.get("data"), dict):
        raise FeedError("not a successful notice list")
    notes = raw["data"].get("notices")
    if not isinstance(notes, list):
        raise FeedError("no notice list in the body")
    out = []
    for n in notes:
        if not isinstance(n, dict) or not isinstance(n.get("title"), str) or isinstance(n.get("id"), bool):
            raise FeedError("a notice without an id or a title")
        try:
            nid = int(n["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FeedError("a notice without an id") from exc
        out.append(Notice(nid, n["title"], parse_time(n.get("first_listed_at")), parse_time(n.get("listed_at"))))
    return out


@dataclass
class Feed:
    """The live notice list. ``transport`` is replaced in tests, so no test reaches the network."""

    transport: Transport = transport
    timeout: float = TIMEOUT_S
    sleep: Callable[[float], None] = time.sleep     # the wait before the one retry after a 429/418

    def request(self, method: str, url: str) -> bytes:
        """The one gate every call goes through: GET of the allowlisted URL, status 200, or ``FeedError``.

        A 429 or 418 raises ``FeedRateLimited``, a ``FeedError``."""

        if method != METHOD:
            raise FeedRefused(f"method {method!r} is not allowed")
        check_url(url)
        try:
            status, body = self.transport(url, min(float(self.timeout), TIMEOUT_S))
        except FeedError:
            raise
        except Exception as exc:          # network, timeout, a body cut short: any failure is a FeedError
            raise FeedError(type(exc).__name__) from exc
        if status in RATE_LIMITED:
            raise FeedRateLimited(f"status {status}")
        if status != 200:
            raise FeedError(f"status {status}")
        return body

    def page(self) -> list[Notice]:
        """Page 1 of the trade notices: the 20 most recently listed or edited.

        After a 429 or 418 it waits ``RETRY_WAIT_S`` and asks once more; a second one raises ``FeedRateLimited``.
        """

        try:
            body = self.request(METHOD, URL)
        except FeedRateLimited:
            self.sleep(RETRY_WAIT_S)
            body = self.request(METHOD, URL)
        return parse(body)
