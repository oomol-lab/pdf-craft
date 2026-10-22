"""Disk task contexts and stable node identities for detached reorder results."""
import pickle
from pathlib import Path

ROOT_KEY = '__pdf_craft_stream_root'
NODE_KEY = '__pdf_craft_stream_node'


def tag_source(root, identity):
    root.set(ROOT_KEY, str(identity))
    for index, element in enumerate(root.iter()):
        element.set(NODE_KEY, str(index))


def source_identity(root):
    return int(root.get(ROOT_KEY, str(id(root))))


def rebind(root, origin, block):
    """Bind deserialized source pointers to the current document only."""
    by_key = {node.get(NODE_KEY): node for node in root.iter() if node.get(NODE_KEY) is not None}
    def node(value):
        return by_key.get(value.get(NODE_KEY), value)
    for segment in origin:
        segment.parent_stack = [node(parent) for parent in segment.parent_stack]
    # InlineSegment caches its own stacks separately from its text leaves.
    def visit(segment):
        segment._parent_stack = [node(parent) for parent in segment.parent_stack]  # pylint: disable=protected-access
        for child in segment.children:
            if hasattr(child, 'children'):
                visit(child)
    visit(origin)
    return node(block)


def strip_source(root):
    for node in root.iter():
        for key in list(node.attrib):
            if key.startswith("__pdf_craft_"):
                node.attrib.pop(key, None)
        node.attrib.pop(ROOT_KEY, None)
        node.attrib.pop(NODE_KEY, None)


class TaskStore:
    """No task payload or original document stays resident while awaiting order."""
    def __init__(self, root: Path):
        self.root = root
        self.next_identity = 0
        self.cache = None
        self.identity = None

    def allocate(self):
        self.next_identity += 1
        return self.next_identity

    def save(self, identity, task):
        with (self.root / str(identity)).open('wb') as file:
            pickle.dump(task, file, protocol=pickle.HIGHEST_PROTOCOL)

    def load(self, identity):
        if self.identity != identity:
            with (self.root / str(identity)).open('rb') as file:
                self.cache = pickle.load(file)
            self.identity = identity
        return self.cache

    def pop(self, identity):
        task = self.load(identity)
        (self.root / str(identity)).unlink()
        self.cache = self.identity = None
        return task


def unit_owner(origin):
    """A PCEX paragraph is atomic; EPUB retains its existing fragment owner."""
    return next((node for node in origin.head.parent_stack if node.tag == 'text'), origin.parent)


def unit_key(owner):
    fragment = owner.find('fragment') if owner.tag == 'text' else None
    if fragment is not None:
        return f'{fragment.get("page_index")}:{fragment.get("source_order")}'
    return owner.get(NODE_KEY) or str(id(owner))


def owner_outcomes(root, mappings):
    del mappings
    return {str(node.get('__pdf_craft_unit')): node.get('__pdf_craft_missing') != '1'
            for node in root.iter() if node.get('__pdf_craft_unit') is not None}
