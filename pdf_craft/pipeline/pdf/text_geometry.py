"""Interpret source character geometry without changing fonts or graphics.

pdfminer owns font/CMap parsing and PDF graphics/text state. Edits retain the
original character bytes; deleted glyph advances become TJ positioning values.
"""
# pdfminer initializes interpreter state in init_state, not __init__.
# pylint: disable=attribute-defined-outside-init
from copy import copy
from dataclasses import dataclass, field
from typing import Any

from pdfminer.cmapdb import IdentityCMap, IdentityCMapByte
from pdfminer.layout import LTChar
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.pdftypes import PDFObjRef, dict_value, list_value, resolve1, stream_value
from pdfminer.psparser import literal_name
from pdfminer.utils import mult_matrix, translate_matrix


@dataclass
class TextEdit:
    values: list[Any]
    removed: int


@dataclass
class EditFrame:
    events: list[tuple[str, Any]] = field(default_factory=list)
    removed: int = 0


def encoded_glyphs(font, data: bytes):
    """Split an encoded string using the font CMap, never Unicode re-encoding."""
    if not font.is_multibyte() or isinstance(font.cmap, IdentityCMapByte):
        return [(bytes([code]), code) for code in data]
    if isinstance(font.cmap, IdentityCMap):
        if len(data) % 2:
            raise ValueError('Incomplete two-byte PDF character code')
        return [(data[i:i+2], int.from_bytes(data[i:i+2], 'big')) for i in range(0, len(data), 2)]
    tree = getattr(font.cmap, 'code2cid', None)
    if not isinstance(tree, dict):
        raise ValueError('Unsupported PDF character mapping for text removal')
    node, start, result = tree, 0, []
    for index, code in enumerate(data):
        node = node.get(code)
        if isinstance(node, int):
            result.append((data[start:index+1], node))
            start, node = index+1, tree
        elif not isinstance(node, dict):
            raise ValueError('Unmapped PDF character code cannot be safely removed')
    if start != len(data):
        raise ValueError('Incomplete PDF character mapping')
    return result


def _append(values, value):
    if values and isinstance(value, bytes) and isinstance(values[-1], bytes):
        values[-1] += value
    elif values and isinstance(value, (int, float)) and isinstance(values[-1], (int, float)):
        values[-1] += value
    else:
        values.append(value)


class TextRemovalInterpreter(PDFPageInterpreter):
    """Record text and Form invocation edits in their original stream order."""

    def __init__(self, manager, device, masks=()):
        super().__init__(manager, device)
        self.masks = masks
        self.frame = EditFrame()
        self.inherited: Any = None
        self.child = None

    def dup(self):
        child = type(self)(self.rsrcmgr, self.device, self.masks)
        child.inherited = (copy(self.textstate), copy(self.graphicstate))
        self.child = child
        return child

    def init_state(self, ctm):
        super().init_state(ctm)
        if self.inherited is not None:
            self.textstate, self.graphicstate = self.inherited

    def do_Do(self, xobjid_arg):
        source = stream_value(self.xobjmap[literal_name(xobjid_arg)])
        if source.objid in self.parent_stream_ids or source.objid in self.stream_ids:
            raise ValueError('Circular Form XObject cannot be safely edited')
        self.child = None
        super().do_Do(xobjid_arg)
        child = self.child.frame if self.child is not None else None
        self.frame.events.append(('form', child))
        if child is not None:
            self.frame.removed += child.removed

    def do__w(self, aw, ac, s):
        # PDF's double-quote operator includes T*, unlike pdfminer 20260107's
        # implementation. Retain that positioning both in analysis and output.
        self.do_Tw(aw)
        self.do_Tc(ac)
        self.do_T_a()
        self.do_TJ([s])

    def do_gs(self, name):
        # pdfminer leaves gs unimplemented. Of the ExtGState parameters, Font
        # changes text placement. Resolve the font object, not a page Font name:
        # it need not occur in the page's /Font resource dictionary at all.
        states = dict_value(self.resources.get('ExtGState'))
        state = dict_value(states[literal_name(name)])
        if 'Font' in state:
            values = list_value(state['Font'])
            if len(values) != 2:
                raise ValueError('Invalid ExtGState Font')
            reference, size = values
            size = resolve1(size)
            if isinstance(size, bool) or not isinstance(size, (int, float)):
                raise ValueError('Invalid ExtGState font size')
            self.textstate.font = self.rsrcmgr.get_font(
                reference.objid if isinstance(reference, PDFObjRef) else None,
                dict_value(reference),
            )
            self.textstate.fontsize = size

    def do_TJ(self, seq: Any):
        state = self.textstate
        font = state.font
        if font is None:
            raise ValueError('Cannot locate PDF text without its font')
        size, scale = state.fontsize, state.scaling * .01
        vertical = font.is_vertical()
        axis_scale = 1. if vertical else scale
        x, y = state.linematrix
        matrix = mult_matrix(state.matrix, self.ctm)
        values, removed = [], 0
        for item in seq:
            if isinstance(item, (int, float)):
                advance = -item * size * .001 * axis_scale
                if vertical:
                    y += advance
                else:
                    x += advance
                _append(values, item)
                continue
            if not isinstance(item, bytes):
                raise ValueError('Invalid PDF text sequence')
            for raw, cid in encoded_glyphs(font, item):
                char = LTChar(translate_matrix(matrix, (x, y)), font, size, scale,
                              state.rise, '', font.char_width(cid), font.char_disp(cid),
                              self.graphicstate.ncs, self.graphicstate)
                hit = any(char.x0 <= right and char.x1 >= left and char.y0 <= top and char.y1 >= bottom
                          for left, bottom, right, top in self.masks)
                spacing = state.charspace + (state.wordspace if raw == b' ' else 0.)
                advance = font.char_width(cid) * size + spacing
                if hit:
                    if state.render >= 4:
                        raise ValueError('Intersecting text used for clipping cannot be deleted without changing graphics')
                    if size == 0 and spacing:
                        raise ValueError('Zero-size spaced text cannot be safely repositioned')
                    _append(values, -1000 * advance / size if size else 0.)
                    removed += 1
                else:
                    _append(values, raw)
                if vertical:
                    y += advance
                else:
                    x += advance * scale
        state.linematrix = (x, y)
        self.frame.events.append(('text', TextEdit(values, removed)))
        self.frame.removed += removed
