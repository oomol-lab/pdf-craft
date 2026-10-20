"""Public source values and actual incremental consumers (no hosted calls)."""

# Each subtest awaits its tasks before the next iteration.
# pylint: disable=cell-var-from-loop,protected-access
import asyncio
import gc
import tempfile
import unittest
import weakref
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from xml.etree.ElementTree import Element, SubElement

from pdf_craft import (PageGeometry, SourceRegion, ParagraphContent, AsyncPDFCraft,
                       ConcurrentExecutor, FixedCapacity, LLM, XMLTranslator)
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import (Chapter, SourceTextFragment, TextFlowItem,
                                                SourceAsset, StandaloneAsset, Reference, encode)
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask, SubmitKind
from tests.extraction_helpers import make_extraction


def text(index, value, page=1):
    return TextFlowItem('body', -1, [SourceTextFragment(page, index, (0,index*20,90,index*20+15), [value])])


def translator():
    llm = LLM('unused', 'https://example.invalid', 'unused', 'o200k_base')
    return XMLTranslator(llm,llm,'zh',None,False,2,5,500,
                         executor=ConcurrentExecutor(FixedCapacity(2)))


async def translated(_translator, inline_segments, **_kwargs):
    values=[]
    for segment in inline_segments:
        target=segment.create_element()
        for node in target.iter():
            if node.text and node.text.strip():
                node.text='译:'+node.text
            if node.tail and node.tail.strip():
                node.tail='译:'+node.tail
        values.append((segment.parent, list(search_text_segments(segment.assign_attributes(target)))))
    return values


class SourceTests(unittest.TestCase):
    def test_map_uses_actual_dimensions_not_requested_dpi(self):
        region=SourceRegion(PageGeometry(3,1000,2000,300),8,(100,200,400,800))
        self.assertEqual(region.map_bbox((333,600)),(33.300000000000004,60.,133.20000000000002,240.))
        self.assertEqual(region.bbox,(100,200,400,800))
        with self.assertRaises(ValueError):
            region.map_bbox((0,600))


class ParagraphTests(unittest.IsolatedAsyncioTestCase):
    def test_markdown_output_name_cannot_alias_body_or_footnote_spool(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source')
            body = 'Body-start ' + 'Body ' * 20000 + 'Body-end.'
            note = Reference(1, 1, '*', [text(1, 'Footnote.')])
            save_xml(encode(Chapter(None, -1, [text(0, body)], [note])),
                     root / 'source/chapters/chapter_head.xml')
            source._export(root / 'source.pcex')
            # A self-copy regression can block a worker forever: isolate it,
            # bound runtime and cap file growth on platforms with resource.
            script = '''
import asyncio
import sys
try:
    import resource
except ImportError:
    pass
else:
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024, 4 * 1024 * 1024))
from pdf_craft import AsyncPDFCraft
asyncio.run(AsyncPDFCraft().render_markdown(sys.argv[1], sys.argv[2]))
'''
            for filename in ('notes.md', 'body.md', 'footnotes.md'):
                with self.subTest(filename=filename):
                    output = root / filename
                    subprocess.run([sys.executable, '-c', script, str(root / 'source.pcex'), str(output)],
                                   check=True, capture_output=True, text=True, timeout=10)
                    rendered = output.read_text(encoding='utf-8')
                    self.assertEqual(rendered, body + '\n\n---\n\n## References\n\n[^1]:  Footnote.')

    async def test_epub_repeated_footnote_anchors_across_and_within_paragraphs(self):
        from zipfile import ZipFile
        from xml.etree.ElementTree import fromstring
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source', with_toc=True)
            note = Reference(1, 1, '*', [text(2, 'Footnote content.')])
            first, second = text(0, 'First paragraph.'), text(1, 'Second paragraph.')
            cast(SourceTextFragment, first.children[0]).content.extend([note, ' and another reference ', note])
            cast(SourceTextFragment, second.children[0]).content.append(note)
            save_xml(encode(Chapter(None, -1, [first, second], [note])),
                     root / 'source/chapters/chapter_head.xml')
            heading, following = text(3, 'Next chapter'), text(4, 'Following paragraph.')
            heading.role, heading.level = 'heading', 0
            cast(SourceTextFragment, following.children[0]).content.append(note)
            save_xml(encode(Chapter(7, 0, [heading, following], [note])),
                     root / 'source/chapters/chapter_7.xml')
            output = root / 'book.epub'
            await AsyncPDFCraft().render_epub(source, output)
            with ZipFile(output) as archive:
                document = fromstring(archive.read('OEBPS/Text/head.xhtml'))
                chapter_documents = [fromstring(archive.read(name)) for name in archive.namelist()
                                     if name.startswith('OEBPS/Text/') and name.endswith('.xhtml')
                                     and name != 'OEBPS/Text/head.xhtml']
            ids = [node.get('id') for node in document.iter() if node.get('id')]
            self.assertEqual(len(ids), len(set(ids)))
            references = [node for node in document.iter() if node.get('href') == '#fn-1']
            self.assertEqual([node.get('id') for node in references], ['ref-1', 'ref-1-2', 'ref-1-3'])
            self.assertEqual(ids.count('fn-1'), 1)
            definition = next(node for node in document.iter() if node.get('id') == 'fn-1')
            self.assertIn('Footnote content.', ''.join(definition.itertext()))
            backlinks = [link for node in definition.iter()
                         if (link := node.get('href')) is not None and link.startswith('#')]
            self.assertIn('#ref-1', backlinks)
            self.assertTrue(all(link[1:] in ids for link in backlinks))
            following_document = next(value for value in chapter_documents
                                      if 'Following paragraph.' in ''.join(value.itertext()))
            following_references = [node for node in following_document.iter() if node.get('href') == '#fn-1']
            self.assertEqual([node.get('id') for node in following_references], ['ref-1'])
            following_ids = [node.get('id') for node in following_document.iter() if node.get('id')]
            self.assertEqual(len(following_ids), len(set(following_ids)))
            self.assertIn('fn-1', following_ids)

    async def test_furniture_heading_cache_does_not_replace_note_with_same_index(self):
        from pdf_craft.document.stream_view import content_view
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source', with_toc=True)
            heading = text(0, 'Heading.')
            heading.role, heading.level = 'heading', 0
            note = Reference(3, 1, '*', [text(2, 'Footnote.')])
            save_xml(encode(Chapter(None, -1, [heading, text(1, 'Body.')], [note])),
                     root / 'source/chapters/chapter_head.xml')
            calls = []
            async def mappings(instance, inline_segments, **kwargs):
                calls.extend(part.text for segment in inline_segments for part in segment)
                return await translated(instance, inline_segments, **kwargs)
            with patch.object(XMLTranslator, '_translate_inline_segments_async', new=mappings):
                async with content_view(source, translator=translator(), with_furniture=True) as (_, _, _, items):
                    values = [item.value async for item in items]
            self.assertEqual(calls.count('Heading.'), 1)
            self.assertIn('Body.', calls)
            self.assertIn('Footnote.', calls)
            self.assertIsInstance(values[-1], Reference)
            self.assertEqual(cast(Reference, values[-1]).mark, '*')

    async def test_consumers_render_before_translation_eof_and_share_public_content(self):
        for kind in ('markdown','epub','pcex'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                source=make_extraction(root/'source',page_pixel_sizes={1:(100,100),2:(100,100)},with_toc=True)
                paragraph=text(0,'First.')
                paragraph.children.append(SourceTextFragment(2,4,(0,0,90,15),['Continued.']))
                note=Reference(2,1,'*',[text(1,'Note.',2),text(2,'More note.',2)])
                source_chapter=Chapter(None,-1,[paragraph,text(1,'Second.')],[note])
                save_xml(encode(source_chapter),root/'source/chapters/chapter_head.xml')
                release,received=asyncio.Event(),asyncio.Event()
                callbacks,notes,rendered=[],[],[]
                async def mappings(instance,inline_segments,**kwargs):
                    if any(part.text=='Second.' for segment in inline_segments for part in segment):
                        await release.wait()
                    return await translated(instance,inline_segments,**kwargs)
                async def callback(content):
                    self.assertIsInstance(content,ParagraphContent)
                    callbacks.append(content)
                    received.set()
                async def run():
                    craft=AsyncPDFCraft()
                    options: dict[str, Any]=dict(translator=translator(),window=2,with_anchored=False,
                                 on_body=callback,on_footnote=notes.append)
                    if kind=='pcex':
                        await craft.translate_extraction(source,root/'book.pcex',**options)
                    else:
                        await getattr(craft,'render_'+kind)(source,root/('book.'+('md' if kind=='markdown' else 'epub')),**options)
                # Observe real conversion, not just notification, before EOF.
                from pdf_craft.renderer.markdown import renderer as md
                from pdf_craft.renderer.epub import content as epub
                from pdf_craft.markdown.render.layouts import render_layouts
                from pdf_craft.renderer.epub.render import _convert_chapter_to_epub
                def md_render(*args,**kwargs):
                    rendered.append(True)
                    return render_layouts(*args,**kwargs)
                def epub_render(*args,**kwargs):
                    rendered.append(True)
                    return _convert_chapter_to_epub(*args,**kwargs)
                # The Markdown renderer imports render_layouts locally.
                target='pdf_craft.markdown.render.layouts.render_layouts' if kind=='markdown' else 'pdf_craft.renderer.epub.content._convert_chapter_to_epub'
                wrapper=md_render if kind=='markdown' else epub_render
                del md,epub
                with patch.object(XMLTranslator,'_translate_inline_segments_async',new=mappings), patch(target,new=wrapper):
                    task=asyncio.create_task(run())
                    try:
                        await asyncio.wait_for(received.wait(),3)
                        self.assertFalse(task.done())
                        if kind!='pcex':
                            self.assertTrue(rendered)
                        self.assertEqual([r.page.page_index for r in callbacks[0].regions],[1,2])
                        self.assertEqual(callbacks[0].regions[1].source_order_in_page,4)
                        self.assertIn('First.',callbacks[0].original)
                        callbacks[0].regions.clear()
                    finally:
                        release.set()
                        await task
                self.assertEqual(len(notes),2)
                self.assertTrue(all(isinstance(note,ParagraphContent) for note in notes))

    async def test_no_callbacks_do_not_build_public_paragraphs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=make_extraction(root/'source')
            save_xml(encode(Chapter(None,-1,[text(0,'Source.')])),root/'source/chapters/chapter_head.xml')
            with patch('pdf_craft.renderer.paragraph.ParagraphNotifier.content',side_effect=AssertionError('unused')):
                await AsyncPDFCraft().render_markdown(source,root/'book.md')

    async def test_asset_context_is_preserved_across_item_translation(self):
        from pdf_craft.document.content import read_content
        from pdf_craft.transformer.content import translate_content
        heading=TextFlowItem('heading',0,[SourceTextFragment(1,0,(0,0,90,10),['Heading'])])
        asset=StandaloneAsset(SourceAsset(1,'image',(0,40,90,60),title=['Caption']))
        inline=text(2,'Body context')
        inline.children.append(SourceAsset(1,'table',(0,60,90,70),content=['<table><tr><td>X</td></tr></table>']))
        subheading = text(3, 'Subheading')
        subheading.role, subheading.level = 'heading', 1
        subheading.children.append(SourceAsset(1,'image',(0,75,90,85),title=['Chart']))
        chapter = Chapter(None, -1, [heading, asset, inline, subheading])
        contexts=[]
        async def slots(values,_transformer):
            contexts.extend(slot.payload.context for slot in values)
            return [None for _ in values]
        class Identity:
            async def transform(self,chapter,**_kwargs):
                return chapter
            def _anchored_transformer(self):
                return None
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            save_xml(encode(chapter),root/'chapter_head.xml')
            with patch('pdf_craft.transformer.content._translate_slots_async',new=slots):
                items=[item async for item in translate_content(read_content(root),Identity(),2,[],[],with_anchored=True)]
            self.assertEqual(len(items),5)
            from pdf_craft.transformer.anchored_translation import _asset_slots
            self.assertEqual(contexts, [slot.payload.context for slot in _asset_slots(chapter)])

    async def test_detached_completed_documents_do_not_pin_original_doms(self):
        release,last=asyncio.Event(),asyncio.Event()
        roots=[]
        async def tasks():
            for index in range(12):
                root=Element('body')
                SubElement(root,'p').text=f'Document {index}.'
                roots.append(weakref.ref(root))
                yield TranslationTask(root,SubmitKind.REPLACE,index)
        async def mappings(instance,inline_segments,**kwargs):
            source=''.join(part.text for segment in inline_segments for part in segment)
            if source=='Document 0.':
                await release.wait()
            if source=='Document 11.':
                last.set()
            return await translated(instance,inline_segments,**kwargs)
        results=[]
        async def result(value):
            results.append(value[1])
        with patch.object(XMLTranslator,'_translate_inline_segments_async',new=mappings):
            task=asyncio.create_task(translator().translate_elements(tasks(),window=2,on_result=result,collect_results=False))
            try:
                await asyncio.wait_for(last.wait(),3)
                gc.collect()
                self.assertLessEqual(sum(ref() is not None for ref in roots),4)
            finally:
                release.set()
                await task
        self.assertEqual(results,list(range(12)))

    async def test_pdf_exports_final_cross_page_layout_as_one_paragraph(self):
        import pypdf
        from pdf_craft import PDFParagraphContent, PDFSourceRegion
        from pdf_craft.transformer.translation_coverage import write_narrative_coverage, NarrativeCoverage
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=make_extraction(root/'source',page_pixel_sizes={1:(300,400),2:(300,400)})
            paragraph=TextFlowItem('body',-1,[
                SourceTextFragment(1,4,(20,30,280,60),['Translated content ' * 10]),
                SourceTextFragment(2,0,(20,30,280,120),['continued.']),
                SourceTextFragment(2,1,(20,180,280,220),['']),
            ])
            save_xml(encode(Chapter(None,-1,[paragraph])),root/'source/chapters/chapter_head.xml')
            write_narrative_coverage(root/'source/translation.xml',[NarrativeCoverage('head',1,4,'translated')])
            pdf=root/'source.pdf'
            writer=pypdf.PdfWriter()
            writer.add_blank_page(300,400)
            writer.add_blank_page(300,400)
            with pdf.open('wb') as file:
                writer.write(file)
            values=[]
            await AsyncPDFCraft().patch_pdf_with_extraction(pdf,source,root/'output.pdf',on_body=values.append)
            self.assertEqual(len(values),1)
            value=values[0]
            self.assertIsInstance(value,PDFParagraphContent)
            self.assertEqual(len(value.regions),3)
            self.assertTrue(all(isinstance(region,PDFSourceRegion) for region in value.regions))
            self.assertEqual([region.source_order_in_page for region in value.regions],[4,0,1])
            self.assertTrue(value.regions[0].lines)
            self.assertTrue(value.regions[0].erasures)
            self.assertTrue(all(region.font_size is not None for region in value.regions if region.lines))
            output=pypdf.PdfReader(root/'output.pdf')
            self.assertIn('Translated',''.join(page.extract_text() for page in output.pages))

    async def test_pdf_composes_and_notifies_before_last_translation_and_cancels_on_callback_failure(self):
        import pypdf
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                source=make_extraction(root/'source',page_pixel_sizes={n:(100,100) for n in (1,2,3)})
                save_xml(encode(Chapter(None,-1,[text(0,'First.',1),text(0,'Second.',2),text(0,'Last.',3)])),
                         root/'source/chapters/chapter_head.xml')
                pdf=root/'source.pdf'
                writer=pypdf.PdfWriter()
                for _ in range(3):
                    writer.add_blank_page(100,100)
                with pdf.open('wb') as file:
                    writer.write(file)
                output=root/'output.pdf'
                output.write_bytes(b'existing output')
                events = []
                release,seen=asyncio.Event(),asyncio.Event()
                async def mappings(instance,inline_segments,**kwargs):
                    if any(part.text=='Last.' for segment in inline_segments for part in segment):
                        await release.wait()
                    return await translated(instance,inline_segments,**kwargs)
                async def callback(content):
                    self.assertTrue(content.regions[0].lines)
                    seen.set()
                    if fail:
                        raise ValueError('preview failed')
                with patch.object(XMLTranslator,'_translate_inline_segments_async',new=mappings):
                    task=asyncio.create_task(AsyncPDFCraft().translate_pdf(
                        pdf,source,output,translator=translator(),window=3,on_body=callback,
                        on_translation_event=events.append))
                    try:
                        await asyncio.wait_for(seen.wait(),8)
                        if fail:
                            with self.assertRaisesRegex(ValueError,'preview failed'):
                                await asyncio.wait_for(task,3)
                            self.assertEqual(output.read_bytes(),b'existing output')
                        else:
                            self.assertFalse(task.done())
                            self.assertEqual(output.read_bytes(),b'existing output')
                    finally:
                        release.set()
                        if not task.done():
                            await task
                if not fail:
                    self.assertEqual(len(pypdf.PdfReader(output).pages),3)
                    from pdf_craft.transformer.events import TranslationEventKind
                    self.assertEqual(events[-1].kind, TranslationEventKind.COMPLETE)
                    self.assertGreater(events[-1].total_characters, 0)
                    self.assertEqual(events[-1].completed_characters, events[-1].total_characters)

    def test_formula_preview_uses_final_qt_lines_and_keeps_vector_atoms_separate(self):
        from pdf_craft.pipeline.pdf.text_layout import QTextParagraphFiller, PatchTextOptions
        from pdf_craft.pipeline.pdf.models import PDFReplacement, PDFInlineFormula
        from pdf_craft.pipeline.pdf.inline_formula import FormulaFragment
        from pdf_craft.pipeline.pdf.preview import lines_for_placement
        class FormulaRenderer:
            available=True
            def render(self,_latex,_font_size):
                return FormulaFragment(b'vector',10.,10.,2.)
        filler=QTextParagraphFiller(PatchTextOptions(max_font_size=10,min_font_size=10))
        filler._formula_renderer=cast(Any, FormulaRenderer())
        replacement=PDFReplacement(1,(10,10,290,50),'Before \ufffc after.',(300,400),
                                   inline_formulas=(PDFInlineFormula('x^2'),))
        fitted=filler.fit(replacement,{1:(300.,400.)})
        placement=fitted.placements[0]
        lines=lines_for_placement(filler,placement)
        self.assertEqual(len(lines),2)
        self.assertEqual(lines[0].baseline, lines[1].baseline)
        self.assertGreater(lines[1].x, placement.formula_draws[0].x)
        self.assertEqual(len(placement.formula_draws),1)
        self.assertTrue(all('\ufffc' not in line.text and '\u00a0' not in line.text for line in lines))
        self.assertIn('Before',''.join(line.text for line in lines))
        self.assertTrue(all(line.baseline > placement.line_tops[0] for line in lines))

    async def test_stored_bilingual_layer_callback_keeps_original_separate(self):
        from pdf_craft import RenderMode
        from tests.test_pcex_translation_rendering import _source, _add_translation
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=_source(root/'source')
            _add_translation(root/'source','english-a','EN')
            for kind in ('markdown','epub'):
                received=[]
                output=root/('book.'+('md' if kind=='markdown' else 'epub'))
                await getattr(AsyncPDFCraft(),'render_'+kind)(source,output,mode=RenderMode.BILINGUAL,on_body=received.append)
                paragraph=next(value for value in received if 'EN:原正文' in value.text)
                self.assertTrue(paragraph.original.startswith('原正文'))
                self.assertEqual(paragraph.text.count('原正文'),1)
                self.assertEqual(paragraph.regions[0].page.page_index,1)
