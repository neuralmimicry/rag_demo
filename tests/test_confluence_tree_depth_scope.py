import os
import io
import datetime as dt

import pytest


def _page(PageInfo, id: str, title: str, depth: int | None, parent: str | None, children: list[str] | None = None):
    p = PageInfo(
        id=id,
        title=title,
        url=f"http://example/wiki/pages/{id}/{title}",
        last_updated=dt.datetime.now(dt.timezone.utc),
        author="tester",
        labels=["test"],
    )
    p.depth = depth
    p.parent_id = parent
    p.ancestors = [] if (depth in (0, None)) else ["x"] * int(depth)
    p.children = list(children or [])
    return p


def test_tree_depth_scopes_analysis_but_not_baseline(tmp_path, monkeypatch):
    from confluence_analysis import analyze_space_and_write_report, PageInfo

    # Build synthetic pages: depths 0,1,2,4,10 and unknown (None)
    r = _page(PageInfo, "r", "root", 0, None, ["c1"])  # root
    c1 = _page(PageInfo, "c1", "child1", 1, "r", ["g1"])  # depth 1
    g1 = _page(PageInfo, "g1", "grand1", 2, "c1", [])  # depth 2
    d4 = _page(PageInfo, "d4", "deep4", 4, "g1", [])  # depth 4
    d10 = _page(PageInfo, "d10", "deep10", 10, "d4", [])  # depth 10
    u = _page(PageInfo, "u", "unknown", None, "c1", [])  # unknown depth

    pages_full = [r, c1, g1, d4, d10, u]

    # Monkeypatch network-bound functions
    monkeypatch.setattr("confluence_analysis.fetch_space", lambda base_url, auth, space_key: {"name": "Test Space", "key": space_key})
    monkeypatch.setattr("confluence_analysis.fetch_space_pages", lambda base_url, auth, space_key: pages_full)
    # Do not alter hierarchy (depths already set)
    monkeypatch.setattr("confluence_analysis.enrich_pages_with_ancestors", lambda base_url, auth, pages: None)
    # Avoid page body fetching
    monkeypatch.setattr("confluence_analysis.get_page_text", lambda base_url, auth, space_key, p: "lorem ipsum" * 10)

    out = tmp_path / "report.html"
    analyze_space_and_write_report(
        base_url="https://example.atlassian.net",
        auth=("u", "p"),
        space_key="TEST",
        output_html=str(out),
        use_rovo=False,
        dry_run=True,
        tree_max_depth=2,
    )

    html = out.read_text(encoding="utf-8")

    # Baseline total should reflect full set (6)
    assert "total_pages" in html
    assert ">6<" in html  # value cell in the baseline metrics table

    # Interactive table should include only titles within scope (≤2) plus unknown
    for title in ("root", "child1", "grand1", "unknown"):
        assert title in html
    # Deep ones should not appear in the table
    for title in ("deep4", "deep10"):
        assert title not in html

    # Two hierarchy sections should be present
    assert "Hierarchy Summary — Full Space" in html
    assert "Hierarchy Summary — Analysis scope (≤ 2)" in html
