"""One Upbit notice read shared by the Demo and live roots (PERP_LIVE_R0.md section 5 item 5): the live run writes
the page it read, the Demo runs take it while it is fresh, and the live run never reads one."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tests.test_perp_krwfade import note
from tools.krwfade import feed as kf

REPO = Path(__file__).resolve().parents[1]
NOW = 1_762_934_400_000
PAGE = [note(101, "AAA"), note(102, "BBB", title="[거래] BBB(BBB) KRW 마켓 디지털 자산 추가")]


@pytest.fixture
def demo(monkeypatch):
    """scripts/live_perp_demo.py as a module, with .env never read."""

    import importlib.util

    spec = importlib.util.spec_from_file_location("live_perp_demo_share_test", REPO / "scripts" / "live_perp_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "load_dotenv", lambda *a, **k: None)
    return mod


@pytest.fixture
def share(tmp_path) -> Path:
    d = tmp_path / "perp_notice_share"
    d.mkdir()
    d.chmod(0o755)
    return d / "upbit_page1.json"


def test_a_written_page_is_read_back_while_fresh(share):
    assert kf.write_share(share, PAGE, NOW)
    assert share.stat().st_mode & 0o777 == 0o644
    assert [p.name for p in share.parent.iterdir()] == [share.name]           # no temporary file left
    assert kf.read_share(share, NOW + kf.SHARE_MAX_AGE_MS) == PAGE
    assert kf.read_share(share, NOW - kf.SHARE_SKEW_MS) == PAGE
    assert kf.read_share(share, NOW + kf.SHARE_MAX_AGE_MS + 1) is None       # stale: read Upbit itself
    assert kf.read_share(share, NOW - kf.SHARE_SKEW_MS - 1) is None          # stamped too far ahead


def test_a_missing_or_malformed_page_is_not_taken(share):
    assert kf.read_share(share, NOW) is None
    good = {"read_ms": NOW, "notices": [{"id": 1, "title": "t", "first_listed_at_ms": 5, "listed_at_ms": 6}]}
    share.write_text(json.dumps(good))
    assert kf.read_share(share, NOW) == [kf.Notice(1, "t", 5, 6)]
    bad = [
        "not json",
        json.dumps([]),
        json.dumps({"notices": good["notices"]}),
        json.dumps({**good, "read_ms": str(NOW)}),
        json.dumps({**good, "read_ms": True}),
        json.dumps({**good, "notices": [{**good["notices"][0], "id": True}]}),
        json.dumps({**good, "notices": [{**good["notices"][0], "id": "1"}]}),
        json.dumps({**good, "notices": [{"id": 1, "title": "t", "first_listed_at_ms": 5}]}),
        json.dumps({**good, "notices": ["x"]}),
    ]
    for body in bad:
        share.write_text(body)
        assert kf.read_share(share, NOW) is None, body


def test_the_page_is_written_only_into_a_directory_of_this_user_that_others_cannot_write(tmp_path, share):
    assert not kf.write_share(tmp_path / "absent" / "upbit_page1.json", PAGE, NOW)
    share.parent.chmod(0o775)
    assert not kf.write_share(share, PAGE, NOW) and not share.exists()
    share.parent.chmod(0o757)
    assert not kf.write_share(share, PAGE, NOW) and not share.exists()
    share.parent.chmod(0o755)
    link = tmp_path / "link"
    link.symlink_to(share.parent, target_is_directory=True)
    assert not kf.write_share(link / "upbit_page1.json", PAGE, NOW) and not share.exists()
    if os.getuid() != 0:
        assert share.parent.stat().st_uid == os.getuid()


class Upbit:
    """Stands in for ``kf.Feed``: counts the reads of Upbit."""

    reads: list[int] = []

    def page(self):
        Upbit.reads.append(1)
        return [note(201, "CCC")]


@pytest.fixture
def upbit(monkeypatch):
    Upbit.reads = []
    monkeypatch.setattr(kf, "Feed", Upbit)
    return Upbit


def test_the_demo_takes_a_fresh_shared_page_and_asks_upbit_nothing(demo, upbit, share):
    kf.write_share(share, PAGE, int(time.time() * 1000))
    box: dict = {"info": None, "notices": None}
    notices, _symbols, _row = demo.krw_sources(box, share_read=share)
    assert notices() == notices() == PAGE and upbit.reads == [] and box["notices_from"] == "share"


def test_the_demo_reads_upbit_itself_when_the_shared_page_is_stale_or_missing(demo, upbit, share):
    box: dict = {"info": None, "notices": None}
    notices, _s, _r = demo.krw_sources(box, share_read=share)
    assert notices() == [note(201, "CCC")] and upbit.reads == [1] and box["notices_from"] == "upbit"
    kf.write_share(share, PAGE, int(time.time() * 1000) - kf.SHARE_MAX_AGE_MS - 5_000)
    box = {"info": None, "notices": None}
    notices, _s, _r = demo.krw_sources(box, share_read=share)
    assert notices() == [note(201, "CCC")] and upbit.reads == [1, 1]
    assert json.loads(share.read_text())["notices"][0]["id"] == 101           # a Demo run never writes it


def test_the_live_run_reads_upbit_itself_and_writes_what_it_read(demo, upbit, share):
    kf.write_share(share, PAGE, int(time.time() * 1000))                       # a fresh page already there
    box: dict = {"info": None, "notices": None}
    notices, _s, _r = demo.krw_sources(box, share_write=share)
    assert notices() == notices() == [note(201, "CCC")] and upbit.reads == [1]  # never the shared page
    written = json.loads(share.read_text())
    assert [n["id"] for n in written["notices"]] == [201] and abs(written["read_ms"] - time.time() * 1000) < 60_000


def test_a_failed_upbit_read_writes_nothing(demo, monkeypatch, share):
    class Down:
        def page(self):
            raise kf.FeedBlocked("status 403")

    monkeypatch.setattr(kf, "Feed", Down)
    notices, _s, _r = demo.krw_sources({"info": None, "notices": None}, share_write=share)
    with pytest.raises(kf.FeedBlocked):
        notices()
    assert not share.exists()


def test_the_runners_pass_the_share_its_own_way(demo, monkeypatch, tmp_path):
    """Demo: --krw-notice-share as share_read. Live: --notice-share as share_write."""

    import importlib.util

    seen: list[dict] = []

    def capture(box, **kw):
        seen.append(kw)
        raise SystemExit(0)

    monkeypatch.setattr(demo, "krw_sources", capture)
    with pytest.raises(SystemExit):
        demo.main(["--krw", "--krw-notice-share", str(tmp_path / "s.json"), "--root", str(tmp_path / "d")])
    spec = importlib.util.spec_from_file_location("live_perp_r0_share_test", REPO / "scripts" / "live_perp_r0.py")
    r0 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(r0)
    monkeypatch.setattr(r0, "krw_sources", capture)
    with pytest.raises(SystemExit):
        r0.main(["--env-file", str(tmp_path / "x"), "--root", str(tmp_path / "l"), "--notice-share",
                 str(tmp_path / "s.json")])
    assert seen == [{"share_read": tmp_path / "s.json"}, {"share_write": tmp_path / "s.json"}]


def test_every_wrapper_names_the_same_share_and_only_the_live_one_writes_it():
    ops = REPO / "scripts" / "ops"
    live = (ops / "run_perp_live.sh").read_text()
    demo = [(ops / n).read_text() for n in ("run_perp_demo.sh", "run_flow_flush.sh")]
    path = "/var/tmp/perp_notice_share/upbit_page1.json"
    assert f"--notice-share {path}" in live and "--krw-notice-share" not in live
    for text in demo:
        assert text.count(f"--krw-notice-share {path}") == text.count("--krw --pair-usd 500")
        assert "--notice-share " not in text.replace("--krw-notice-share ", "")
