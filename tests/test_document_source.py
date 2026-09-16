from dataclasses import fields
from inspect import signature

from pdf_craft.document import SourceLocation, source_location


def test_source_location_uses_v3_source_order_and_bbox_fields():
    location = source_location(page_index=3, source_order=7, bbox=(1, 2, 30, 40))

    assert location == SourceLocation(3, (1, 2, 30, 40), 7)
    assert location.source_order == 7
    assert [field.name for field in fields(SourceLocation)] == ["page_index", "bbox", "source_order"]
    assert set(signature(source_location).parameters) == {"page_index", "source_order", "bbox"}
