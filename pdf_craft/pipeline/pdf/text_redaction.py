"""Remove colliding source text while retaining the original graphic objects."""
# pylint: disable=protected-access
from io import BytesIO
from typing import Any

from pdfminer.pdfdevice import PDFDevice
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pypdf.generic import (
    ArrayObject, ByteStringObject, ContentStream, DecodedStreamObject,
    DictionaryObject, FloatObject, NameObject,
)

from .text_geometry import EditFrame, TextRemovalInterpreter


_TEXT = {b'Tj', b'TJ', b"'", b'"'}


def _remove_alternative_text(operation, resources):
    operands, operator = operation
    if operator != b'BDC':
        return operation
    properties = operands[1]
    if isinstance(properties, NameObject):
        properties = resources.get('/Properties', DictionaryObject()).get_object().get(properties)
    if properties is None:
        return operation
    properties = properties.get_object()
    if not isinstance(properties, DictionaryObject):
        raise ValueError('Invalid marked-content properties')
    cleaned = DictionaryObject({key: value for key, value in properties.items()
                                if key not in ('/ActualText', '/Alt', '/E')})
    return ([operands[0], cleaned], operator)


def _rewrite(content, resources: Any, frame: EditFrame, writer):
    events = iter(frame.events)
    output, scopes, removed = [], [], 0
    replaced_forms = set()
    resources = DictionaryObject(resources)
    for operands, operator in content.operations:
        if operator in (b'BMC', b'BDC'):
            scopes.append((len(output), removed))
        elif operator == b'EMC':
            if not scopes:
                raise ValueError('Unbalanced PDF marked content')
            start, previous = scopes.pop()
            if removed > previous:
                output[start] = _remove_alternative_text(output[start], resources)
        if operator in _TEXT:
            kind, edit = next(events)
            if kind != 'text':
                raise ValueError('PDF text analysis does not match its content stream')
            if edit.removed:
                if operator == b'"':
                    output.extend([([operands[0]], b'Tw'), ([operands[1]], b'Tc')])
                if operator in (b"'", b'"'):
                    output.append(([], b'T*'))
                operands = [ArrayObject(ByteStringObject(value) if isinstance(value, bytes) else FloatObject(value)
                                        for value in edit.values)]
                operator = b'TJ'
                removed += edit.removed
        elif operator == b'Do':
            kind, child = next(events)
            if kind != 'form':
                raise ValueError('PDF Form analysis does not match its content stream')
            if child is not None and child.removed:
                xobjects = DictionaryObject(resources['/XObject'].get_object())
                original: Any = xobjects[operands[0]].get_object()
                child_resources = original.get('/Resources', resources).get_object()
                child_content, child_resources = _rewrite(ContentStream(original, writer), child_resources, child, writer)
                clone = DecodedStreamObject()
                clone.update({key: value for key, value in original.items()
                              if key not in ('/Length', '/Filter', '/DecodeParms')})
                clone.set_data(child_content.get_data())
                clone[NameObject('/Resources')] = child_resources
                index = len(xobjects)
                name = NameObject(f'/PCCText{index}')
                while name in xobjects:
                    index += 1
                    name = NameObject(f'/PCCText{index}')
                replaced_forms.add(operands[0])
                xobjects[name] = writer._add_object(clone)
                resources[NameObject('/XObject')] = xobjects
                operands = [name]
                removed += child.removed
        output.append((operands, operator))
    if scopes or next(events, None) is not None:
        raise ValueError('Incomplete PDF content analysis')
    if replaced_forms:
        xobjects = resources['/XObject'].get_object()
        inherited_forms = any(obj.get_object().get('/Subtype') == '/Form'
                              and '/Resources' not in obj.get_object()
                              for obj in xobjects.values())
        if not inherited_forms:
            used = {args[0] for args, op in output if op == b'Do'}
            for name in replaced_forms - used:
                del xobjects[name]
    content.operations = output
    return content, resources


def remove_masked_text(writer, page, erasures, page_height: float) -> int:
    """Delete every character whose box touches a mask, including invisible text.

    Analyze one writer-owned page in isolation. This preserves original encoded
    glyphs and font resources and lets shared Forms be edited per invocation.
    """
    if not erasures or page.get('/Contents') is None:
        return 0
    masks = tuple((r.rectangle.x, page_height-r.rectangle.bottom,
                   r.rectangle.x+r.rectangle.width, page_height-r.rectangle.top)
                  for r in erasures)
    data = BytesIO()
    writer.write(data)
    data.seek(0)
    parsed = next(PDFPage.get_pages(data))
    manager = PDFResourceManager()
    interpreter = TextRemovalInterpreter(manager, PDFDevice(manager), masks)
    # Erasure rectangles use PDF user coordinates; do not rotate/translate them
    # through PDFPageInterpreter.process_page's display-coordinate conversion.
    interpreter.render_contents(parsed.resources, parsed.contents)
    frame = interpreter.frame
    if frame.removed:
        content = page.get_contents()
        resources: Any = page.get('/Resources', DictionaryObject()).get_object()
        content, resources = _rewrite(content, resources, frame, writer)
        page.replace_contents(content)
        page[NameObject('/Resources')] = resources
    return frame.removed
