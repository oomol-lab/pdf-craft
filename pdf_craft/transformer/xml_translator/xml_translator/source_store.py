"""Disk task contexts and stable node identities for detached reorder results."""
import pickle
from pathlib import Path
from threading import Lock

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
    """Disk contexts with one cached task, shared by IO workers.

    Progress, mapping callbacks and ordered settlement may access this store
    concurrently. File ownership and the cached identity/value are one
    transaction; returning another caller's cached task corrupts source CFIs.
    """
    def __init__(self, root: Path):
        self.root = root
        self.next_identity = 0
        self._cached = None
        self._lock = Lock()

    def allocate(self):
        # Only the async producer allocates. Never wait for an IO worker's
        # file transaction while running on the event-loop thread.
        self.next_identity += 1
        return self.next_identity

    def save(self, identity, task):
        with self._lock:
            if self._cached is not None and self._cached[0] == identity:
                self._cached = None
            with (self.root / str(identity)).open('wb') as file:
                pickle.dump(task, file, protocol=pickle.HIGHEST_PROTOCOL)

    def load(self, identity):
        with self._lock:
            return self._load_locked(identity)

    def _load_locked(self, identity):
        if self._cached is None or self._cached[0] != identity:
            with (self.root / str(identity)).open('rb') as file:
                task = pickle.load(file)
            self._cached = (identity, task)
        return self._cached[1]

    def pop(self, identity):
        with self._lock:
            task = self._load_locked(identity)
            (self.root / str(identity)).unlink()
            self._cached = None
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
