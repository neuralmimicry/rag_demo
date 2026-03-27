"""Subset of odf.teletype used by file conversion."""


def extractText(node) -> str:
    if node is None:
        return ""
    elements = getattr(node, "elements", None)
    if isinstance(elements, list):
        return "\n".join(getattr(el, "text", str(el)) for el in elements)
    return getattr(node, "text", str(node))
