"""Compile source PDF pages into a non-interactive visual base layer.

The patcher must not leave the original page below its translated text.  PDF
text remains selectable even when a rectangle visually covers it, and page
annotations are interactive independently of the content stream.  This module
therefore separates annotations from a document before Ghostscript turns the
remaining page display list into a font-free PDF.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import shutil
import subprocess
from typing import Any, Protocol


class VisualBaseCompiler(Protocol):
    """The local compiler boundary used by :class:`PDFPatcher`."""

    def compile(self, source_path: Path, target_path: Path) -> None:
        """Compile ``source_path`` into a visual-only PDF at ``target_path``."""


class GhostscriptVisualBaseCompiler:
    """Create a PDF visual layer without source text objects or annotations.

    ``pdfwrite`` interprets the page before writing it again.  With
    ``-dNoOutputFonts`` it writes text as linework (or as bitmap glyphs when
    necessary), so viewers cannot select the source text in the result.
    Annotations are deliberately removed before invoking this compiler and
    restored by :func:`reattach_annotations` after translated text is drawn.
    """

    def __init__(self, executable: str | Path | None = None) -> None:
        self.executable = str(executable) if executable is not None else None

    def compile(self, source_path: Path, target_path: Path) -> None:
        """Compile an annotation-free source PDF to a visual-only PDF."""
        executable = self._find_executable()
        command = [
            executable,
            "-q",
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=pdfwrite",
            "-dNoOutputFonts",
            "-dPreserveAnnots=false",
            "-dShowAnnots=false",
            "-dNO_PDFMARK_OUTLINES",
            "-sUseOCR=Never",
            f"-sOutputFile={target_path}",
            str(source_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            detail = error.stderr.strip() or error.stdout.strip() or "unknown Ghostscript failure"
            raise RuntimeError(f"Ghostscript could not compile the PDF visual base: {detail}") from error
        if not target_path.is_file():  # pragma: no cover - defensive against broken external tools.
            raise RuntimeError("Ghostscript did not produce a PDF visual base")

    def _find_executable(self) -> str:
        if self.executable is not None:
            executable = shutil.which(self.executable)
            if executable is not None:
                return executable
            raise RuntimeError(
                f"Ghostscript executable is not available: {self.executable}. "
                "Install Ghostscript and make its command visible on PATH."
            )
        for candidate in ("gs", "gswin64c", "gswin32c"):
            executable = shutil.which(candidate)
            if executable is not None:
                return executable
        raise RuntimeError(
            "PDF patching requires Ghostscript to compile a non-interactive visual base. "
            "Install Ghostscript and make `gs` (or `gswin64c`) visible on PATH."
        )


def extract_annotations(reader: Any) -> tuple[tuple[Any, ...], ...]:
    """Return the page-level ``/Annots`` objects in page order.

    This intentionally does not classify annotation subtypes.  Annotation is
    the product boundary: every entry in ``/Annots`` is lifted above the
    translated text, regardless of whether it is a link, a widget, markup, or
    a reader note.
    """
    annotations_by_page: list[tuple[Any, ...]] = []
    for page in reader.pages:
        annotations = page.get("/Annots")
        annotations_by_page.append(tuple(annotations) if annotations is not None else ())
    return tuple(annotations_by_page)


def write_annotation_free_copy(reader: Any, target_path: Path) -> None:
    """Write the source pages without their page-level Annotation arrays."""
    import pypdf
    from pypdf.generic import NameObject

    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
        target_page: Any = writer.pages[-1]
        # These page-level entries can trigger actions or article navigation.
        # They are not Annotation objects and must not survive in the visual
        # base. The writer starts a new document root, so catalog-level actions
        # and outlines are not inherited either.
        for key in ("/Annots", "/AA", "/B"):
            name = NameObject(key)
            if name in target_page:
                del target_page[name]
    with target_path.open("wb") as output:
        writer.write(output)


def reattach_annotations(
    writer: Any,
    annotations_by_page: Iterable[Iterable[Any]],
    source_page_references: Iterable[Any],
    source_acroform: Any | None = None,
    source_named_destinations: dict[str, Any] | None = None,
) -> None:
    """Attach every source Annotation to its corresponding output page.

    Annotation dictionaries are cloned into the new document only after all
    visual, erase, and translation layers have been composed.  Ignoring ``/P``
    while cloning prevents the source page (and its selectable text) from
    being pulled into the output through a back-reference.  The relevant
    annotation graph then receives the new page reference.
    """
    from pypdf.generic import ArrayObject, NameObject

    annotation_pages = tuple(tuple(page_annotations) for page_annotations in annotations_by_page)
    if len(annotation_pages) != len(writer.pages):
        raise ValueError("annotation page count does not match output pages")
    source_pages = tuple(source_page_references)
    if len(source_pages) != len(writer.pages):
        raise ValueError("source page count does not match output pages")
    target_pages = {
        _reference_key(source_page): target_page.indirect_reference
        for source_page, target_page in zip(source_pages, writer.pages, strict=True)
    }

    lifted_widgets: dict[tuple[str, int, int], tuple[Any, Any]] = {}
    for target_page, source_annotations in zip(writer.pages, annotation_pages, strict=True):
        if not source_annotations:
            continue
        page_reference = target_page.indirect_reference
        if page_reference is None:  # pragma: no cover - PdfWriter pages are always indirect.
            raise RuntimeError("output page has no indirect reference")
        copied_annotations = ArrayObject()
        for source_annotation in source_annotations:
            ignored_fields = ("/P", "/Dest", "/A", "/AA")
            if _is_widget(source_annotation):
                # A Widget /Parent starts its AcroForm field tree, which is
                # rebuilt selectively below. Other Annotation /Parent links
                # (notably Popup → Text) are annotation relationships and
                # must remain intact.
                ignored_fields += ("/Parent", "/Kids")
            cloned = source_annotation.clone(writer, ignore_fields=ignored_fields)
            reference = _annotation_reference(writer, cloned)
            _restore_annotation_actions(
                source_annotation, reference, writer, target_pages, source_named_destinations,
            )
            _rebind_annotation_page(reference, page_reference)
            if _is_widget(source_annotation):
                lifted_widgets[_object_key(source_annotation)] = (source_annotation, reference)
            copied_annotations.append(reference)
        target_page[NameObject("/Annots")] = copied_annotations
    if source_acroform is not None and lifted_widgets:
        _reattach_acroform(
            writer, source_acroform, target_pages, source_named_destinations, lifted_widgets,
        )


def _annotation_reference(writer: Any, annotation: Any) -> Any:
    """Return an indirect reference for an annotation cloned into ``writer``."""
    from pypdf.generic import IndirectObject

    if isinstance(annotation, IndirectObject):
        return annotation
    reference = getattr(annotation, "indirect_reference", None)
    if reference is not None:
        return reference
    return writer._add_object(annotation)  # pylint: disable=protected-access


def _reattach_acroform(
    writer: Any,
    source_acroform: Any,
    target_pages: dict[tuple[int, int], Any],
    source_named_destinations: dict[str, Any] | None,
    lifted_widgets: dict[tuple[str, int, int], tuple[Any, Any]],
) -> None:
    """Rebuild only the form ancestry required by lifted Widget Annotations.

    ``/AcroForm`` is catalog-level interaction state, so cloning it wholesale
    would silently retain unrelated fields, XFA data, and form actions.  Start
    with the Widgets explicitly retained from page ``/Annots`` instead.  Their
    ``/Parent`` chains are the only field nodes placed in the new ``/Fields``
    tree; the original page Widget remains the endpoint already attached to
    the output page.
    """
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject

    source = _dereference(source_acroform)
    if not isinstance(source, DictionaryObject):
        return
    field_sources, parent_by_child = _selected_field_ancestors(lifted_widgets)
    target_fields: dict[tuple[str, int, int], Any] = {}
    for key, field_source in field_sources.items():
        target = _copy_field_without_structure(field_source, writer)
        target_fields[key] = writer._add_object(target)  # pylint: disable=protected-access
        _restore_annotation_actions(
            field_source, target, writer, target_pages, source_named_destinations,
        )

    for widget_key, (_source_widget, widget_reference) in lifted_widgets.items():
        widget = _dereference(widget_reference)
        if not isinstance(widget, DictionaryObject):
            continue
        # Widget cloning intentionally excludes /Parent and /Kids so unrelated
        # source form fields cannot enter the output object graph.
        widget.pop(NameObject("/Parent"), None)
        parent_key = parent_by_child.get(widget_key)
        if parent_key is not None:
            widget[NameObject("/Parent")] = target_fields[parent_key]

    for child_key, parent_key in parent_by_child.items():
        if child_key not in target_fields:
            continue
        child = _dereference(target_fields[child_key])
        if isinstance(child, DictionaryObject):
            child[NameObject("/Parent")] = target_fields[parent_key]

    for parent_key, parent_reference in target_fields.items():
        parent_source = field_sources[parent_key]
        children = ArrayObject()
        for child_source in _field_children(parent_source):
            child_key = _object_key(child_source)
            if child_key in target_fields and parent_by_child.get(child_key) == parent_key:
                children.append(target_fields[child_key])
            elif child_key in lifted_widgets and parent_by_child.get(child_key) == parent_key:
                children.append(lifted_widgets[child_key][1])
        parent = _dereference(parent_reference)
        if isinstance(parent, DictionaryObject):
            if children:
                parent[NameObject("/Kids")] = children
            else:
                parent.pop(NameObject("/Kids"), None)

    acroform = DictionaryObject()
    for key in ("/DA", "/DR", "/Q", "/NeedAppearances", "/SigFlags"):
        if key in source:
            acroform[NameObject(key)] = _clone_field_value(source.raw_get(key), writer)
    roots = _selected_field_roots(source, target_fields, lifted_widgets, parent_by_child)
    if roots:
        acroform[NameObject("/Fields")] = ArrayObject(roots)
    calculation_order = _selected_calculation_order(source, target_fields, lifted_widgets)
    if calculation_order:
        acroform[NameObject("/CO")] = calculation_order
    reference = writer._add_object(acroform)  # pylint: disable=protected-access
    writer._root_object[NameObject("/AcroForm")] = reference  # pylint: disable=protected-access


def _selected_field_ancestors(
    lifted_widgets: dict[tuple[str, int, int], tuple[Any, Any]],
) -> tuple[dict[tuple[str, int, int], Any], dict[tuple[str, int, int], tuple[str, int, int]]]:
    """Collect only the ``/Parent`` chains which make lifted Widgets fields."""
    fields: dict[tuple[str, int, int], Any] = {}
    parent_by_child: dict[tuple[str, int, int], tuple[str, int, int]] = {}
    for widget_key, (source_widget, _target_widget) in lifted_widgets.items():
        child_key = widget_key
        current = _raw_get(_dereference(source_widget), "/Parent")
        seen: set[tuple[str, int, int]] = set()
        while current is not None:
            field_key = _object_key(current)
            if field_key in seen:
                raise RuntimeError("cyclic PDF form field parent chain")
            seen.add(field_key)
            fields[field_key] = current
            parent_by_child[child_key] = field_key
            child_key = field_key
            current = _raw_get(_dereference(current), "/Parent")
    return fields, parent_by_child


def _copy_field_without_structure(source_field: Any, writer: Any) -> Any:
    """Copy a field dictionary without source page or sibling field links."""
    from pypdf.generic import DictionaryObject, NameObject

    source = _dereference(source_field)
    target = DictionaryObject()
    if not isinstance(source, DictionaryObject):
        return target
    for key, value in source.items():
        if str(key) in {"/P", "/Parent", "/Kids", "/Dest", "/A", "/AA"}:
            continue
        target[NameObject(key)] = _clone_field_value(value, writer)
    return target


def _clone_field_value(value: Any, writer: Any) -> Any:
    """Clone non-structural form data into the output document."""
    clone = getattr(value, "clone", None)
    return clone(writer) if callable(clone) else value


def _field_children(source_field: Any) -> tuple[Any, ...]:
    """Return direct ``/Kids`` entries without traversing unrelated fields."""
    from pypdf.generic import ArrayObject

    children = _raw_get(_dereference(source_field), "/Kids")
    return tuple(children) if isinstance(children, ArrayObject) else ()


def _selected_field_roots(
    source_acroform: Any,
    target_fields: dict[tuple[str, int, int], Any],
    lifted_widgets: dict[tuple[str, int, int], tuple[Any, Any]],
    parent_by_child: dict[tuple[str, int, int], tuple[str, int, int]],
) -> list[Any]:
    """Keep root order while omitting every source field not tied to a Widget."""
    from pypdf.generic import ArrayObject

    roots: list[Any] = []
    source_fields = _raw_get(source_acroform, "/Fields")
    if isinstance(source_fields, ArrayObject):
        for source_field in source_fields:
            key = _object_key(source_field)
            if key in target_fields and key not in parent_by_child:
                roots.append(target_fields[key])
            elif key in lifted_widgets and key not in parent_by_child:
                roots.append(lifted_widgets[key][1])
    for key, field in target_fields.items():
        if key not in parent_by_child and field not in roots:
            roots.append(field)
    for key, (_source_widget, widget) in lifted_widgets.items():
        if key not in parent_by_child and widget not in roots:
            roots.append(widget)
    return roots


def _selected_calculation_order(
    source_acroform: Any,
    target_fields: dict[tuple[str, int, int], Any],
    lifted_widgets: dict[tuple[str, int, int], tuple[Any, Any]],
) -> Any:
    """Carry calculation order only for fields that remain in the output."""
    from pypdf.generic import ArrayObject

    source_order = _raw_get(source_acroform, "/CO")
    if not isinstance(source_order, ArrayObject):
        return ArrayObject()
    selected = ArrayObject()
    for source_field in source_order:
        key = _object_key(source_field)
        if key in target_fields:
            selected.append(target_fields[key])
        elif key in lifted_widgets:
            selected.append(lifted_widgets[key][1])
    return selected


def _is_widget(annotation: Any) -> bool:
    """Whether a page Annotation is a form Widget endpoint."""
    return str(_dereference(annotation).get("/Subtype", "")) == "/Widget"


def _dereference(value: Any) -> Any:
    """Resolve an indirect pypdf object while preserving direct values."""
    return value.get_object() if hasattr(value, "get_object") else value


def _raw_get(value: Any, key: str) -> Any | None:
    """Get a raw PDF value so page/field references keep their identity."""
    if hasattr(value, "raw_get"):
        try:
            return value.raw_get(key)
        except KeyError:
            return None
    return value.get(key) if hasattr(value, "get") else None


def _object_key(value: Any) -> tuple[str, int, int]:
    """Build a stable key for direct or indirect PDF field objects."""
    reference = value if hasattr(value, "idnum") else getattr(value, "indirect_reference", None)
    if reference is not None:
        return ("indirect", reference.idnum, reference.generation)
    return ("direct", id(_dereference(value)), 0)


def _restore_annotation_actions(
    source_annotation: Any,
    target_annotation: Any,
    writer: Any,
    target_pages: dict[tuple[int, int], Any],
    source_named_destinations: dict[str, Any] | None,
) -> None:
    """Restore Annotation destinations without retaining source page objects."""
    from pypdf.generic import DictionaryObject, NameObject

    source = source_annotation.get_object()
    target = target_annotation.get_object()
    if not isinstance(source, DictionaryObject) or not isinstance(target, DictionaryObject):
        return
    if "/Dest" in source:
        destination = _clone_destination(
            source.raw_get("/Dest"), writer, target_pages, source_named_destinations,
        )
        if destination is not None:
            target[NameObject("/Dest")] = destination
    if "/A" in source:
        target[NameObject("/A")] = _clone_action(
            source.raw_get("/A"), writer, target_pages, source_named_destinations,
        )
    if "/AA" in source:
        target[NameObject("/AA")] = _clone_additional_actions(
            source.raw_get("/AA"), writer, target_pages, source_named_destinations,
        )


def _clone_additional_actions(
    source_actions: Any,
    writer: Any,
    target_pages: dict[tuple[int, int], Any],
    source_named_destinations: dict[str, Any] | None,
) -> Any:
    """Clone an Annotation's additional action dictionary without old page refs."""
    from pypdf.generic import DictionaryObject, NameObject

    source = source_actions.get_object() if hasattr(source_actions, "get_object") else source_actions
    if not isinstance(source, DictionaryObject):
        return source.clone(writer) if hasattr(source, "clone") else source
    cloned = DictionaryObject()
    for key, action in source.items():
        cloned[NameObject(key)] = _clone_action(
            action, writer, target_pages, source_named_destinations,
        )
    return cloned


def _clone_action(
    source_action: Any,
    writer: Any,
    target_pages: dict[tuple[int, int], Any],
    source_named_destinations: dict[str, Any] | None,
) -> Any:
    """Clone an action, remapping a GoTo destination when it names a source page."""
    from pypdf.generic import DictionaryObject, NameObject

    source = source_action.get_object() if hasattr(source_action, "get_object") else source_action
    if not isinstance(source, DictionaryObject):
        return source.clone(writer) if hasattr(source, "clone") else source
    cloned = source.clone(writer, ignore_fields=("/D",))
    cloned_reference = _annotation_reference(writer, cloned)
    cloned_object = cloned_reference.get_object()
    if "/D" in source:
        names = source_named_destinations if str(source.get("/S", "")) == "/GoTo" else None
        destination = _clone_destination(source.raw_get("/D"), writer, target_pages, names)
        if destination is not None:
            cloned_object[NameObject("/D")] = destination
    return cloned_reference


def _clone_destination(
    source_destination: Any,
    writer: Any,
    target_pages: dict[tuple[int, int], Any],
    source_named_destinations: dict[str, Any] | None,
) -> Any:
    """Clone a destination, replacing only source-page references with output pages."""
    from pypdf.generic import ArrayObject, IndirectObject

    source = source_destination.get_object() if isinstance(source_destination, IndirectObject) else source_destination
    if source is None:
        return None
    if not isinstance(source, ArrayObject):
        if source_named_destinations is not None:
            named_destination = _named_destination(source, source_named_destinations)
            if named_destination is not None:
                destination_array = getattr(named_destination, "dest_array", named_destination)
                return _clone_destination(destination_array, writer, target_pages, None)
        clone = getattr(source, "clone", None)
        return clone(writer) if callable(clone) else source
    cloned = ArrayObject()
    for index, value in enumerate(source):
        raw_value = value
        if index == 0 and isinstance(raw_value, IndirectObject):
            target_page = target_pages.get(_reference_key(raw_value))
            if target_page is not None:
                cloned.append(target_page)
                continue
        cloned.append(raw_value.clone(writer) if hasattr(raw_value, "clone") else raw_value)
    return cloned


def _named_destination(source: Any, source_named_destinations: dict[str, Any]) -> Any | None:
    """Look up a PDF name/string destination without copying the source Names tree."""
    name = str(source)
    if name in source_named_destinations:
        return source_named_destinations[name]
    if name.startswith("/"):
        return source_named_destinations.get(name[1:])
    return source_named_destinations.get(f"/{name}")


def _reference_key(reference: Any) -> tuple[int, int]:
    """Return a stable object key for a page indirect reference."""
    return (reference.idnum, reference.generation)


def _rebind_annotation_page(annotation: Any, page_reference: Any) -> None:
    """Set ``/P`` on an Annotation and its same-page popup/reply nodes."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

    visited: set[tuple[int, int]] = set()

    def visit(value: Any) -> None:
        if isinstance(value, IndirectObject):
            key = (value.idnum, value.generation)
            if key in visited:
                return
            visited.add(key)
            value = value.get_object()
        if isinstance(value, ArrayObject):
            for item in value:
                visit(item)
            return
        if not isinstance(value, DictionaryObject):
            return
        if "/Subtype" in value:
            value[NameObject("/P")] = page_reference
        # A Widget field's /Parent and /Kids can span pages. Those widgets are
        # each rebound when their own /Annots entry is processed; traversing
        # their field hierarchy here would incorrectly move all of them to the
        # current page.
        for key in ("/Popup", "/IRT"):
            if key in value:
                visit(value.raw_get(key))

    visit(annotation)
