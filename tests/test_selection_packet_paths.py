import pytest

from trading_agent.backtest.campaign_integrity import _selection_packet_file


@pytest.mark.parametrize("identifier", ["..", ".", "", "../other", "a/b", "a\\b", "C:other", "a\x00b"])
def test_packet_rejects_path_like_identifiers(tmp_path, identifier):
    with pytest.raises(ValueError, match="unsafe"):
        _selection_packet_file(tmp_path, identifier)


@pytest.mark.parametrize("linked_directory", [False, True])
def test_packet_rejects_links_outside_source(tmp_path, linked_directory):
    root = tmp_path / "packet"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "campaign_plan.json").write_text("{}")
    if linked_directory:
        (root / "strategy").symlink_to(outside, target_is_directory=True)
        parts = ("strategy", "campaign_plan.json")
    else:
        (root / "selection_decision_f0.json").symlink_to(outside / "campaign_plan.json")
        parts = ("selection_decision_f0.json",)
    with pytest.raises(ValueError, match="escapes"):
        _selection_packet_file(root, *parts)


def test_packet_accepts_contained_file(tmp_path):
    path = tmp_path / "strategy" / "campaign_plan.json"
    path.parent.mkdir()
    path.write_text("{}")
    assert _selection_packet_file(tmp_path, "strategy", path.name) == path.resolve()
