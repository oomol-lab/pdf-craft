# pylint: disable=protected-access

from pathlib import Path
from typing import cast
from ...extractor.chapter.chapter import Reference
from os.path import relpath
from ...document import PDFCraftExtraction, RenderMode
from ...runtime import IO_DOMAIN, worker_context, temporary_directory, run_atomic_cancellable
from ..output import publish


class MarkdownRenderer:
    """Render a PDFCraftExtraction to Markdown through one async implementation."""

    async def render(self, extraction: PDFCraftExtraction, output_path: Path,
                     assets_path: Path | None = None, cover_path: Path | None = None,
                     aborted=lambda: False, *, mode: RenderMode = RenderMode.SOURCE,
                     translation_id: str | None = None, on_body=None, on_footnote=None,
                     translator=None, window=1, with_anchored=True) -> None:
        from ...document.stream_view import content_view
        from ...renderer.paragraph import paragraph_notifications, display_values
        from ...markdown.render.layouts import render_layouts
        from ...markdown.render.render import _open_output
        from ...metering import check_aborted
        from shutil import copy2
        from contextlib import aclosing
        async with content_view(extraction, mode=mode, translation_id=translation_id,
                                translator=translator, window=window,
                                with_anchored=with_anchored, aborted=aborted) as (view, pages, numbers, items):
            cover = cover_path
            if cover is None and await IO_DOMAIN.run(view.cover.exists):
                cover = view.cover
            destination = assets_path or Path("assets")
            if not destination.is_absolute():
                destination = output_path.parent / destination
            async with temporary_directory("pdf-craft-markdown-") as directory:
                staged = Path(directory)
                body_path, notes_path = staged / "body.md", staged / "footnotes.md"
                assets = staged / "assets"
                await IO_DOMAIN.run(assets.mkdir)
                async with paragraph_notifications(pages, view.assets, numbers, on_body, on_footnote) as notify:
                    async with worker_context(_open_output(body_path)) as body, worker_context(_open_output(notes_path)) as notes:
                        have_body, have_notes = False, False
                        seen_notes = set()
                        async with aclosing(notify.iterate(items)) as incoming:
                            async for item in incoming:
                                check_aborted(aborted)
                                values = display_values(item, mode)
                                if values:
                                    is_note = isinstance(item.value, Reference)
                                    if is_note and cast(Reference, item.value).id in seen_notes:
                                        continue
                                    flow = cast(Reference, values[0]).flow_items if is_note else values
                                    if is_note:
                                        seen_notes.add(cast(Reference, item.value).id)
                                    parts = await IO_DOMAIN.run(lambda flow=flow, level=item.level: ''.join(render_layouts(
                                        flow, view.assets, assets, Path(relpath(destination, output_path.parent)),
                                        level, numbers)))
                                    if parts:
                                        if is_note:
                                            await IO_DOMAIN.run(notes.write, '\n\n' + f'[^{numbers[cast(Reference, item.value).id]}]:  ' + parts)
                                            have_notes = True
                                        else:
                                            await IO_DOMAIN.run(body.write, ('\n\n' if have_body else '') + parts)
                                            have_body = True
                                await notify.append(item)
                        if have_notes:
                            await IO_DOMAIN.run(notes.flush)
                            def append_notes():
                                body.write('\n\n---\n\n## References')
                                with notes_path.open(encoding='utf-8') as source:
                                    while chunk := source.read(65536):
                                        body.write(chunk)
                            await IO_DOMAIN.run(append_notes)
                if cover is not None:
                    await IO_DOMAIN.run(copy2, cover, assets / cover.name)
                check_aborted(aborted)
                def commit(bridge):
                    resources = [(path, destination / path.relative_to(assets))
                                 for path in assets.rglob("*") if path.is_file()]
                    publish([*resources, (body_path, output_path)], bridge.commit)
                await run_atomic_cancellable(IO_DOMAIN, commit)
