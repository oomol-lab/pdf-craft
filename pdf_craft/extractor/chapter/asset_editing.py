"""Source-only projection of editable asset fields and submitted associations."""
from dataclasses import replace

from .analysis_types import (
    AnalysedLayout, AssetAttachment, LayoutAnnotations,
    _AnalysedAssetSource, _AnalysedTextSource,
)


def layout_identity(layout):
    return layout.source_id or f'layout-{layout.document_order}'


def editable_asset_sources(page):
    """Expose display fields as sources, never infer their semantic ownership.

    This is an input codec projection. Original OCR and adopted pages are not
    mutated. Synthetic field ordinals are local source slots, not new rectangles.
    Calling it again on an already projected page is harmless.
    """
    next_order = max((item.source_order for item in page.layouts
                      if item.source_order is not None), default=-1) + 1
    layouts = []
    assets = [item for item in page.layouts if isinstance(item.source, _AnalysedAssetSource)]
    for layout in page.layouts:
        ident = layout_identity(layout)
        layout = replace(layout, source_id=ident)
        source = layout.source
        if isinstance(source, _AnalysedAssetSource) and source.ref in ('image', 'table'):
            slots = ('title', 'caption') if source.ref == 'image' else ('title', 'content', 'caption')
            members = []
            for slot in slots:
                content = getattr(source, slot)
                if not content:
                    continue
                members.append(AnalysedLayout(
                    _AnalysedTextSource(source.page_index, next_order, source.bbox, content),
                    layout.document_order, LayoutAnnotations(
                        layout.ownership, False, False,
                        'body' if layout.ownership != 'ignored' else None,
                        -1 if layout.ownership != 'ignored' else None,
                        layout.citation_id, layout.citation_index,
                        asset_attachment=(AssetAttachment(ident, slot)
                                          if layout.ownership != 'ignored' else None)),
                    source_id=f'{ident}/{slot}'))
                next_order += 1
            layout = replace(layout, source=replace(source, **{slot: () for slot in slots}))
            layouts.extend([layout, *members])
        else:
            target = layout.asset_text_target
            if target is not None and layout.annotations.keep_asset_attachment:
                matches = [asset for asset in assets if
                           (asset.page_index, asset.source.ref, asset.bbox) ==
                           (target.page_index, target.ref, target.bbox)]
                # A provisional geometry match is only an input suggestion. A
                # submitted association always names one stable source identity.
                attachment = (AssetAttachment(layout_identity(matches[0]), target.slot)
                              if len(matches) == 1 else None)
                layout = replace(layout, annotations=replace(
                    layout.annotations, asset_attachment=attachment))
            layouts.append(layout)
    # Field sources share the original asset's location but not its stream ordinal.
    base = min((item.document_order for item in layouts), default=page.page_index << 32)
    return replace(page, layouts=[replace(item, document_order=base + i)
                                  for i, item in enumerate(layouts)])


def reading_sequences(layouts):
    """Partition final decisions before interpreting paragraph connections."""
    from .asset_text import active_asset_text
    items = sorted(layouts, key=lambda item: item.document_order)
    attached = active_asset_text(items)
    groups = {}
    for item in items:
        attachment = item.annotations.asset_attachment
        if id(item) in attached:
            slot = attachment.slot if attachment else item.asset_text_target.slot
            key = ('asset', id(attached[id(item)]), slot)
        elif item.annotations.independent_stream is not None:
            key = ('independent', item.annotations.independent_stream)
        else:
            key = ('main',)
        groups.setdefault(key, []).append(item)
    return list(groups.values())


def validate_asset_operations(page, issues):
    """Validate submitted local relationships, never initial classifications."""
    assets = {layout_identity(item): item for item in page.layouts
              if isinstance(item.source, _AnalysedAssetSource)}
    independent = {}
    attached_groups = {}
    for item in page.layouts:
        annotation = item.annotations
        attachment = annotation.asset_attachment
        if annotation.independent_stream is not None:
            if (item.source_order is None or item.ownership != 'paragraph'
                    or attachment is not None or annotation.preserve_as_image):
                issues.append('[INDEPENDENT_STREAM] Only unattached paragraph text may form a local independent stream')
            independent.setdefault(annotation.independent_stream, []).append(item)
        if attachment is None:
            continue
        attached_groups.setdefault((attachment.asset_id, attachment.slot), []).append(item)
        if attachment.slot not in ('title', 'content', 'caption'):
            issues.append('[ASSET_TEXT] Unknown asset display slot')
        asset = assets.get(attachment.asset_id)
        if (item.source_order is None or item.ownership == 'ignored' or annotation.preserve_as_image
                or asset is None or asset.ownership == 'ignored'
                or asset.source.ref not in ('image', 'table')):
            issues.append('[ASSET_TEXT] Attachment needs retained text and an existing local image/table identity')
        elif (asset.ownership, asset.citation_id) != (item.ownership, item.citation_id):
            issues.append('[ASSET_TEXT] Attached text and asset must share their submitted ownership/citation')
        elif attachment.slot == 'content' and asset.source.ref != 'table':
            issues.append('[ASSET_TEXT] Image content is not a display slot')
    for members in list(independent.values()) + list(attached_groups.values()):
        ordered = sorted(members, key=lambda item: item.document_order)
        if ordered[0].continues_from_previous or ordered[-1].continues_to_next:
            issues.append('[LOCAL_TEXT_STREAM] Asset labels and independent descriptions cannot continue across page boundaries')
