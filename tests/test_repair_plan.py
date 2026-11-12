"""Current model protocol has one identity, order and destination per decision."""
import json
from unittest import TestCase, IsolatedAsyncioTestCase
from pydantic import ValidationError

from pdf_craft.extractor.chapter.repair_plan import RepairPlan, materialize_plan
from pdf_craft.extractor.chapter.page_repair import repair_page_with_llm, _compile_repaired_page
from tests.test_asset_editing import scene, roundtrip, assets


class RepairPlanTests(TestCase):
    def test_ignored_has_no_irrelevant_operations_and_retired_fields_are_rejected(self):
        minimal = dict(layouts=[dict(layout_id='s', destination=dict(kind='ignored'))])
        plan = RepairPlan.model_validate(minimal)
        item = materialize_plan(plan, 9).layouts[0]
        self.assertEqual(item.ownership, 'ignored')
        self.assertFalse(item.preserve_as_image or item.continues_from_previous)
        self.assertIsNone(item.paragraph_role)
        for field, value in [('as_image', True), ('join', True), ('note_id', 'n'), ('heading_level', 2)]:
            bad = dict(layouts=[dict(layout_id='s', destination=dict(kind='ignored', **{field:value}))])
            with self.subTest(field=field), self.assertRaises(ValidationError):
                RepairPlan.model_validate(bad)
        for field, value in [('page_index', 9), ('citations', []), ('stream_assessment', '')]:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                RepairPlan.model_validate(dict(minimal, **{field:value}))
        with self.assertRaises(ValidationError):
            RepairPlan.model_validate(dict(layouts=[dict(layout_id='s', destination=dict(kind='body'),
                                                        continues_to_next=True)]))

    def test_reference_identity_survives_note_reordering_without_member_updates(self):
        data = dict(layouts=[dict(layout_id='s', destination=dict(kind='note', note_id='b'))],
            notes=[dict(operation='define', note_id='a', mark='23'),
                   dict(operation='define', note_id='b', mark='24')],
            references=[dict(layout_id='body', note_id='b', anchor=dict(before='',mark='24',after=''))])
        for order, expected in ((data['notes'],2),(list(reversed(data['notes'])),1)):
            resolved=materialize_plan(RepairPlan.model_validate(dict(data,notes=order)),1)
            self.assertEqual(resolved.layouts[0].citation_index,expected)
            self.assertEqual(resolved.references[0].citation_index,expected)
            self.assertEqual(resolved.citations[expected-1].mark,'24')

    def test_asset_destination_inherits_the_final_asset_note(self):
        plan=RepairPlan.model_validate(dict(layouts=[
            dict(layout_id='image',destination=dict(kind='note',note_id='n')),
            dict(layout_id='caption',destination=dict(kind='asset',asset_id='image',slot='caption'))],
            notes=[dict(operation='define',note_id='n',mark='3')]))
        resolved=materialize_plan(plan,1)
        self.assertEqual([x.citation_id for x in resolved.layouts],['n','n'])
        self.assertEqual([x.ownership for x in resolved.layouts],['citation','citation'])

    def test_continuing_note_and_continuing_paragraph_are_independent(self):
        plan=RepairPlan.model_validate(dict(layouts=[
            dict(layout_id='s',destination=dict(kind='note',note_id='tail',join=False))],
            notes=[dict(operation='continue',note_id='tail')]))
        resolved=materialize_plan(plan,1)
        self.assertTrue(resolved.citations[0].continues_previous)
        self.assertFalse(resolved.layouts[0].continues_from_previous)
        self.assertIsNone(resolved.citations[0].index)
        with self.assertRaises(ValidationError):
            RepairPlan.model_validate(dict(layouts=[],notes=[dict(operation='continue',note_id='tail',mark='3')]))

    def test_joined_heading_parts_inherit_the_single_paragraph_descriptor(self):
        source=scene(('text',(0,0,100,20),'A heading'),('text',(0,25,100,40),'continued'))
        ids=[f'layout-{x.document_order}' for x in source.layouts]
        plan=RepairPlan.model_validate(dict(layouts=[
            dict(layout_id=ids[0],destination=dict(kind='body',heading_level=2)),
            dict(layout_id=ids[1],destination=dict(kind='body',join=True))]))
        result=_compile_repaired_page(source,materialize_plan(plan,1))
        self.assertEqual([(x.paragraph_role,x.paragraph_level) for x in result.layouts],
                         [('heading',2),('heading',2)])
        chapter=roundtrip(result)
        from pdf_craft.extractor.chapter.chapter import TextFlowItem
        item=chapter.flow_items[0]
        assert isinstance(item,TextFlowItem)
        self.assertEqual(item.role,'heading')
        self.assertEqual(item.level,2)

    def test_unknown_or_duplicate_targets_are_rejected(self):
        for data in [dict(layouts=[dict(layout_id='s',destination=dict(kind='note',note_id='missing'))]),
                     dict(layouts=[],notes=[dict(operation='define',note_id='n'),dict(operation='define',note_id='n')]),
                     dict(layouts=[dict(layout_id='s',destination=dict(kind='asset',asset_id='missing',slot='title'))])]:
            with self.assertRaisesRegex(ValueError,'REPAIR_TARGET'):
                materialize_plan(RepairPlan.model_validate(data),1)


class PlanLoopTests(IsolatedAsyncioTestCase):
    async def test_new_destination_operations_reach_real_compiler_and_output(self):
        source=scene(('image',(0,0,100,20),''),('text',(0,25,100,40),'Note: figure.'),
                     ('text',(0,45,100,60),'Body.'),('text',(0,65,100,80),'Discarded note diagram.'))
        calls=[]
        async def request(messages,_index,_maximum):
            packet=json.loads(messages[1].message)
            schema=packet['output_contract']
            self.assertNotIn('page_index',schema['properties'])
            layout_schema=schema['$defs']['LayoutDecision']
            self.assertIn('reason',layout_schema['required'])
            self.assertIn('Before choosing destination',layout_schema['properties']['reason']['description'])
            values=packet['target_page']['layouts'];calls.append(packet)
            return json.dumps(dict(layouts=[
                dict(layout_id=values[0]['layout_id'],destination=dict(kind='body')),
                dict(layout_id=values[1]['layout_id'],destination=dict(kind='asset',asset_id=values[0]['layout_id'],slot='caption')),
                dict(layout_id=values[2]['layout_id'],destination=dict(kind='body',join=True)),
                dict(layout_id=values[3]['layout_id'],destination=dict(kind='ignored'))]))
        result=await repair_page_with_llm(previous_page=None,target_page=source,next_page=None,
            previous_pass_probability=None,next_pass_probability=None,request=request)
        # Although requested for model deliberation, missing reasoning cannot
        # cause a retry when the actual operations are complete and valid.
        self.assertEqual(len(calls),1)
        chapter=roundtrip(result)
        self.assertEqual(assets(chapter)[0].caption,['Note: figure.'])
        self.assertNotIn('Discarded note diagram.',str(chapter))
        # The materialized compiler retains all source accounting and geometry.
        self.assertEqual(len(result.layouts),4)
        self.assertEqual(result.layouts[-1].ownership,'ignored')
