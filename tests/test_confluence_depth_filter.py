import datetime as dt

from refiner.confluence_analysis import PageInfo, filter_pages_by_max_depth, compute_hierarchy_metrics


def _page(id: str, title: str, depth: int, parent: str | None, children: list[str] | None = None):
    p = PageInfo(
        id=id,
        title=title,
        url=f"http://example/wiki/pages/{id}/{title}",
        last_updated=dt.datetime.now(dt.timezone.utc),
        author="tester",
        labels=["test"],
    )
    # Simulate enriched fields
    if depth == 0:
        p.ancestors = []
        p.parent_id = None
    else:
        # We don't need full ancestors chain for this test; only parent and depth are used
        p.ancestors = ["root"][:depth]  # placeholder ids
        p.parent_id = parent
    p.depth = depth
    p.children = list(children or [])
    return p


def build_tree():
    # r (0)
    # ├─ c1 (1)
    # │   └─ g1 (2)
    # │       └─ gg (3)
    # └─ c2 (1)
    r = _page("r", "root", 0, None, ["c1", "c2"])
    c1 = _page("c1", "child1", 1, "r", ["g1"])
    c2 = _page("c2", "child2", 1, "r", [])
    g1 = _page("g1", "grand1", 2, "c1", ["gg"]) 
    gg = _page("gg", "great", 3, "g1", [])
    return [r, c1, c2, g1, gg]


def test_filter_depth_1_trims_children_and_counts_leaves():
    pages = build_tree()
    filtered = filter_pages_by_max_depth(pages, 1)
    ids = {p.id for p in filtered}
    assert ids == {"r", "c1", "c2"}
    # Children should be trimmed to kept set
    root = next(p for p in filtered if p.id == "r")
    c1 = next(p for p in filtered if p.id == "c1")
    c2 = next(p for p in filtered if p.id == "c2")
    assert set(root.children) == {"c1", "c2"}
    assert c1.children == []  # g1 trimmed out
    assert c2.children == []
    # Leaf count should reflect trimmed children
    metrics = compute_hierarchy_metrics(filtered)
    assert metrics["leaf_pages"] == 2  # c1 and c2


def test_filter_depth_0_keeps_only_root_and_makes_it_leaf():
    pages = build_tree()
    filtered = filter_pages_by_max_depth(pages, 0)
    assert [p.id for p in filtered] == ["r"]
    metrics = compute_hierarchy_metrics(filtered)
    assert metrics["total_pages"] == 1
    assert metrics["leaf_pages"] == 1  # root becomes a leaf after trimming


def test_filter_depth_2_is_inclusive_and_keeps_depth_2_nodes():
    pages = build_tree()
    filtered = filter_pages_by_max_depth(pages, 2)
    ids = {p.id for p in filtered}
    # Should include depths 0,1,2 (inclusive) => r, c1, c2, g1
    assert ids == {"r", "c1", "c2", "g1"}
    # Children: g1 should have gg trimmed out
    g1 = next(p for p in filtered if p.id == "g1")
    assert g1.children == []
    # Leaves: c2 and g1
    metrics = compute_hierarchy_metrics(filtered)
    assert metrics["leaf_pages"] == 2


def test_unknown_depth_pages_are_not_dropped():
    # Start from baseline tree and add an unknown-depth page under c2
    pages = build_tree()
    # Find c2 and append unknown child
    c2 = next(p for p in pages if p.id == "c2")
    from refiner.confluence_analysis import PageInfo
    u = PageInfo(
        id="u",
        title="unknown",
        url="http://example/wiki/pages/u/unknown",
        last_updated=dt.datetime.now(dt.timezone.utc),
        author="tester",
        labels=["test"],
    )
    u.depth = None  # depth unknown
    u.parent_id = "c2"
    u.ancestors = []
    u.children = []
    c2.children.append("u")
    pages.append(u)

    filtered = filter_pages_by_max_depth(pages, 1)
    ids = {p.id for p in filtered}
    # Unknown-depth page should be kept
    assert "u" in ids
    # Children trimming should retain u under c2 (since u is kept)
    c2f = next(p for p in filtered if p.id == "c2")
    assert c2f.children == ["u"]
    # Leaf count should reflect that c2 has a child and u is a leaf
    metrics = compute_hierarchy_metrics(filtered)
    assert metrics["leaf_pages"] == 2  # c1 and u
