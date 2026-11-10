"""Format adapter helpers for explicitly filled formula nodes, never prose scans."""
from copy import deepcopy


def formula_payload(value: str) -> str:
    """Strip only a whole-node math wrapper chosen by the XML engineer."""
    value = value.strip()
    for opening, closing in (('$$', '$$'), ('\\(', '\\)'), ('\\[', '\\]'), ('$', '$')):
        if value.startswith(opening) and value.endswith(closing) and len(value) >= len(opening)+len(closing):
            return value[len(opening):-len(closing)]
    return value


def replace_formulas(root, convert):
    """Convert explicit expression nodes recursively, preserving surrounding text."""
    root = deepcopy(root)
    def visit(node):
        if node.tag == 'expression':
            return convert(node)
        for index, child in enumerate(node):
            node[index] = visit(child)
        return node
    return visit(root)
