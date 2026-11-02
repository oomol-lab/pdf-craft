# JEV review prompts

Each question is a directly readable English template. `# Instructions`, `# True` and
`# False` mark the three provider fields; the headings themselves are not sent. Blank lines
separate individual instructions. Python owns question selection, batching and score aggregation.

Keep the wording in prose. Use ordinary variable interpolation and small conditions only when
necessary; do not turn templates into JSON builders or macro libraries. Raw document evidence
keeps its original language and is not translated by these templates.

Small supplementary rules and evidence descriptions are marked as fragments below. They add
scope to an existing question; they do not replace its main body.

## Body review

| Template | Purpose |
| --- | --- |
| [closed_predecessor_frame.jinja](body/closed_predecessor_frame.jinja) | Whether the exact left grammatical frame is complete. |
| [closed_predecessor_main_reading.jinja](body/closed_predecessor_main_reading.jinja) | Whether that left source genuinely belongs to main reading. |
| [closed_quoted_frame.jinja](body/closed_quoted_frame.jinja) | Additional scope for the closed-quotation proof (fragment). |
| [concrete_join.jinja](body/concrete_join.jinja) | Audit the concrete source actually joined into main text. |
| [conditional_command_required.jinja](body/conditional_command_required.jinja) | Whether a command completes its preceding numbered condition. |
| [nonreading_page_trace.jinja](body/nonreading_page_trace.jinja) | Whether a punctuation-only source is non-reading scan noise. |
| [one_numbered_instruction.jinja](body/one_numbered_instruction.jinja) | Whether a condition and command form one numbered instruction. |
| [ownership.jinja](body/ownership.jinja) | Retain or exclude one source with footnote extraction disabled. |
| [prior_illustration_unit.jinja](body/prior_illustration_unit.jinja) | Whether the selected predecessor is a separate illustration-associated unit. |
| [publisher_apparatus.jinja](body/publisher_apparatus.jinja) | Whether a retained source is publisher/page apparatus. |
| [same_example_setup.jinja](body/same_example_setup.jinja) | Whether an introduction and its immediate setup form one example. |
| [target_leaves_illustration.jinja](body/target_leaves_illustration.jinja) | Whether the target is main reading outside the predecessor caption. |

## Footnote review

| Template | Purpose |
| --- | --- |
| [caption_reading.jinja](footnote/caption_reading.jinja) | Main-reading/caption role when the current source is classified as a citation. |
| [check_scope.jinja](footnote/check_scope.jinja) | Bind a question to its exact named check (fragment). |
| [definition.jinja](footnote/definition.jinja) | Footnote definition identity, numbering and membership. |
| [footnote_role.jinja](footnote/footnote_role.jinja) | Positive evidence that a whole source belongs to an ancillary footnote stream. |
| [furniture.jinja](footnote/furniture.jinja) | Positive evidence of recurring page apparatus. |
| [main_reading.jinja](footnote/main_reading.jinja) | Positive evidence of the main reading role. |
| [main_section.jinja](footnote/main_section.jinja) | Additional constraints for raw heading candidates (fragment). |
| [new_note.jinja](footnote/new_note.jinja) | An explicitly marked new footnote opening. |
| [note_gap.jinja](footnote/note_gap.jinja) | Natural-paragraph continuity inside the footnote stream. |
| [recitation.jinja](footnote/recitation.jinja) | Additional scope for independent speech or verse (fragment). |
| [reference.jinja](footnote/reference.jinja) | Existing printed reference identity and target correspondence. |
| [reference_location.jinja](footnote/reference_location.jinja) | Literal edit and original-reference-location evidence (fragment). |
| [unmarked_note.jinja](footnote/unmarked_note.jinja) | Additional scope for continuing autonomous commentary (fragment). |

## Shared questions and evidence descriptions

| Template | Purpose |
| --- | --- |
| [attribution.jinja](shared/attribution.jinja) | Attribution. |
| [caption_starts_here.jinja](shared/caption_starts_here.jinja) | Caption starts here. |
| [coordinates.jinja](shared/coordinates.jinja) | Description of normalized boxes and estimated text scale (fragment). |
| [dependency_completion.jinja](shared/dependency_completion.jinja) | Dependency completion. |
| [detached_image_strip.jinja](shared/detached_image_strip.jinja) | Detached image strip. |
| [different_dialogue_speaker.jinja](shared/different_dialogue_speaker.jinja) | Different dialogue speaker. |
| [displayed_number.jinja](shared/displayed_number.jinja) | Displayed number. |
| [ended_utterance_boundary.jinja](shared/ended_utterance_boundary.jinja) | Ended utterance boundary. |
| [fresh_spoken_turn.jinja](shared/fresh_spoken_turn.jinja) | Fresh spoken turn. |
| [gap_syntax.jinja](shared/gap_syntax.jinja) | Mandatory grammatical continuity at a proposed paragraph break. |
| [gap_word.jinja](shared/gap_word.jinja) | Mandatory word continuity at a proposed paragraph break. |
| [image_caption_role.jinja](shared/image_caption_role.jinja) | Image caption role. |
| [incoming_join.jinja](shared/incoming_join.jinja) | Main-text predecessor lookup for the shared incoming-edge protocol. |
| [joined_word_complete.jinja](shared/joined_word_complete.jinja) | Joined word complete. |
| [left_frame_complete.jinja](shared/left_frame_complete.jinja) | Left frame complete. |
| [left_word_complete.jinja](shared/left_word_complete.jinja) | Left word complete. |
| [math_unit_boundary.jinja](shared/math_unit_boundary.jinja) | Math unit boundary. |
| [prior_narrative_role.jinja](shared/prior_narrative_role.jinja) | Prior narrative role. |
| [quotation_boundary.jinja](shared/quotation_boundary.jinja) | Quotation boundary. |
| [redundancy.jinja](shared/redundancy.jinja) | Redundancy. |
| [reference_position.jinja](shared/reference_position.jinja) | Reference position. |
| [reporting_join.jinja](shared/reporting_join.jinja) | Reporting join. |
| [retained_reading_position.jinja](shared/retained_reading_position.jinja) | Retained reading position. |
| [seam_predecessor.jinja](shared/seam_predecessor.jinja) | Seam predecessor. |
| [seam_word.jinja](shared/seam_word.jinja) | Seam word. |
| [section_member.jinja](shared/section_member.jinja) | Section member. |
| [section_sequence.jinja](shared/section_sequence.jinja) | Section sequence. |
| [selected_whole_passage.jinja](shared/selected_whole_passage.jinja) | Selected whole passage. |
| [text_encoding.jinja](shared/text_encoding.jinja) | Lossless literal_parts decoding instructions (fragment). |
