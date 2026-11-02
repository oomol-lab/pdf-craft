"""Atomic structural-review questions; text and boxes remain read-only evidence."""

from copy import deepcopy
from typing import Any

_TEMPLATES: dict[str, dict[str, Any]] = {'gap_word': {'type': 'noul',
              'instructions': ['Audit ONLY the new-paragraph break at checks.{name}. Full raw neighboring '
                               'evidence is local_raw_evidence. Find the nearest genuine main-text '
                               'predecessor among preceding_source_candidates; ancillary notes, headers and '
                               'page numbers may be misclassified and must not hide it. Do not choose an '
                               'arbitrary older main-text passage or anything from next_page.',
                               'A standalone formula/equation is a hard boundary. Images and tables do not '
                               'force a break. Do not invent missing OCR text or unprovided indentation.',
                               'Does this proposed paragraph break preserve whole words? Check the tail of '
                               'the real predecessor against the target opening for a word split across the '
                               'seam, including Chinese multi-character words and words broken by a page '
                               'transition. Ignore other sentence-level and paragraph-style issues in this '
                               'question.'],
              'criteria': {'true': 'Whole words are preserved: this break does not cut one word into pieces.',
                           'false': 'A single word is cut into two pieces by this break; the target begins '
                                    'with the missing end of that word.'}},
 'gap_syntax': {'type': 'noul',
                'instructions': ['Audit ONLY the new-paragraph break at checks.{name}. Full raw neighboring '
                                 'evidence is local_raw_evidence. Find the nearest genuine main-text '
                                 'predecessor among preceding_source_candidates; ancillary notes, headers '
                                 'and page numbers may be misclassified and must not hide it. Do not choose '
                                 'an arbitrary older main-text passage or anything from next_page.',
                                 'A standalone formula/equation is a hard boundary. Images and tables do not '
                                 'force a break. Do not invent missing OCR text or unprovided indentation.',
                                 'Does this proposed paragraph break preserve mandatory syntactic units? '
                                 'This is about an unfinished construction, not whether the passages could '
                                 'form one paragraph. A condition still requiring its main clause, a verb or '
                                 'preposition lacking its obligatory complement, or a similarly unfinished '
                                 'construction must not be split.',
                                 'Complete independent clauses, successive derivation steps, separate '
                                 'variable definitions, separate exercise items and independent quotations '
                                 'can be different paragraphs. A conjunction, shared topic, colon or '
                                 'semicolon alone does not require joining. A mathematical expression inside '
                                 'a text layout does not automatically complete an unfinished surrounding '
                                 'clause. Ignore merely word-internal splitting, which is checked '
                                 'separately.',
                                 'A failure requires that joining to the nearest true predecessor actually '
                                 'completes ITS unfinished construction. An odd or incomplete-looking target '
                                 'opening alone is not enough: do not join it to a complete unrelated '
                                 'sentence to hide missing OCR words.',
                                 'Respect ordinary language and literary conventions: Chinese can omit '
                                 'subjects; dialogue replies, stage directions and narration after a '
                                 'quotation can stand alone. An ellipsis, short utterance or implicit '
                                 'subject is not by itself an unfinished mandatory construction. Check a '
                                 'specific left-to-right grammatical dependency, not general coherence or '
                                 'stylistic continuity.'],
                'criteria': {'true': 'No unfinished mandatory syntactic construction is split; this new '
                                     'paragraph is permissible under this specific test.',
                             'false': 'The target supplies an obligatory missing part of the preceding '
                                      'unfinished construction; this paragraph break cuts that '
                                      'construction.'}},
 'redundancy': {'type': 'noul',
                'instructions': ['Audit only the retained whole text source at checks.{name}. Does it '
                                 'contribute its own printed content, rather than being a completely '
                                 'redundant overlapping OCR copy of another retained source?',
                                 'Full raw text and bounding boxes are in '
                                 'local_raw_evidence.target_page.layouts. Candidate same-page overlapping '
                                 'source orders are listed in this check; overlap alone is NOT a defect.',
                                 'A redundant copy requires ALL of the target text to be contained as one '
                                 'continuous passage in another retained source, AND both boxes to cover the '
                                 'same printed region. The target can be ignored without losing any unique '
                                 'main text. The larger block with unique content must remain.',
                                 'Repeated themes, shared phrases, partial duplicate prefixes with unique '
                                 'prose, different printed locations, and actual author quotations are not '
                                 'proof of redundant OCR. Do not grade footnote ownership, paragraph joins, '
                                 'OCR spelling or missing text in this question.',
                                 'Compare the complete target with the complete other source, including long '
                                 'block endings; a larger source may contain the target at its end. Preserve '
                                 'sources unless whole-source redundancy is established.',
                                 'complete_literal_containment_candidates records mechanically verified '
                                 'contiguous full-text containment, after collapsing only ASCII whitespace. '
                                 'Equal copies are compared with an earlier retained source so that at least '
                                 'one representative remains. These are literal facts, not duplicate labels: '
                                 'you still need to judge the printed occurrence and preserve unique '
                                 'content.',
                                 'Different adjacent narrow vertical columns may have boxes that overlap '
                                 'slightly at their x edges. Such edge bleed does not identify the same '
                                 'printed occurrence; repeated OCR words copied into distinct column '
                                 'locations do not establish a redundant copy of ONE printed region. Do not '
                                 'certify deletion from repetition plus an edge contact alone.',
                                 'Conversely, a horizontal text block may share a passage with a larger '
                                 'overlapping narrative block even when the OCR bbox is imperfect at the '
                                 'last lines. Do not require one bbox center to be inside the other: compare '
                                 'the complete literal passage, the shared horizontal footprint and the '
                                 'actual source organization.'],
                'criteria': {'true': 'This source is nonredundant, or full harmless removal as an '
                                     'overlapping OCR duplicate is not established.',
                             'false': 'This entire source is a redundant overlapping OCR copy already wholly '
                                      'represented in another retained source; ignoring it loses no unique '
                                      'content.'}},
 'section_sequence': {'type': 'noul',
                      'instructions': ['Locate the matching notes/commentary heading for checks.{name}. Does '
                                       'it begin a separate section in the MAIN READING SEQUENCE, rather '
                                       'than an ancillary footnote area underneath continuing main prose?',
                                       'Use the full local_raw_evidence and column geometry. Follow ordinary '
                                       'column reading order: the left column can finish a discussion and '
                                       'the right column can then start its notes section at the top. Main '
                                       'prose and notes sharing a physical page does not make the notes a '
                                       'page-bottom apparatus.',
                                       'A notes section may start near the end of one column and continue in '
                                       'the next. Its entries can explain and cite the preceding discussion: '
                                       'that relationship is normal for endnotes and does not itself make '
                                       'them ancillary footnotes.',
                                       'In contrast, a small note area below ongoing main prose, separated '
                                       'from that prose in its own footer stream, is ancillary even if '
                                       'headed Notes. A chapter title, numbering, small type, the word Notes '
                                       'alone, or an assumed unseen separator cannot establish a new main '
                                       'section. Determine the actual location in the column reading '
                                       'sequence.',
                                       'This asks about the SECTION START only, not the spelling, paragraph '
                                       'joins or correctness of individual notes. If no matching heading and '
                                       'independent section sequence can be established, answer FALSE.'],
                      'criteria': {'true': 'A matching heading starts a distinct notes/commentary section in '
                                           'the main reading sequence.',
                                   'false': 'That independent main-sequence section start is not '
                                            'established; it may instead be an ancillary note area.'}},
 'section_member': {'type': 'noul',
                    'instructions': ['For checks.{name}, does this entire source belong to the '
                                     'notes/commentary section introduced by a matching supplied heading '
                                     'candidate?',
                                     'Use the complete target-page raw text, source order and column '
                                     'positions. The source can be that heading itself, or note/commentary '
                                     'entries under it. Entries may continue into the next column. A heading '
                                     'over unrelated main prose does not establish section membership.',
                                     'This asks only which section contains the target. It does NOT decide '
                                     'whether the section is main text or ancillary footnotes; that is a '
                                     'separate question. Do not certify a mixed block that includes text '
                                     'outside the section. Do not grade paragraph joins, missing OCR words, '
                                     'or note-reference completeness. If the matching heading and '
                                     'whole-source membership cannot be established, answer FALSE.'],
                    'criteria': {'true': 'The whole target is the matching section heading or belongs to the '
                                         'entries of that headed notes/commentary section.',
                                 'false': 'The required whole-source membership is not established.'}},
 'attribution': {'type': 'noul',
                 'instructions': ['Assess the entire target source at checks.{name}. Is this block an '
                                  'author/editor/translator attribution or signature attached to a nearby '
                                  'main reading section, rather than page furniture or an explanatory '
                                  'footnote?',
                                  'Use complete local_raw_evidence for the target, the neighboring section '
                                  'headings and text, and placement. A standalone signature after the end of '
                                  'an entry, article, foreword or its bibliography can be main reading '
                                  'content. Personal initials can serve as such a signature; do not require '
                                  'their expanded name.',
                                  'The whole block must function as the attribution. Merely mentioning an '
                                  'author inside a long footnote, listing citations, or ending a mixed '
                                  'body/photograph-caption block with a credit is not sufficient. Do not '
                                  'certify a mixed block as an attribution.',
                                  'Page numbers, chapter folios, repeated running headers and isolated '
                                  'variables are not authorial signatures. Capital letters or periods alone '
                                  'do not prove authorship; establish the block function from its local '
                                  'section placement. An ancillary photo credit is not an author signature '
                                  'for a main reading section.',
                                  'No prior model score is provided. Do not evaluate paragraph joins, word '
                                  'spelling, or whether the section should be reformatted. If a main-reading '
                                  'attribution cannot be established, answer FALSE.'],
                 'criteria': {'true': 'The complete source functions as an author/editor/translator '
                                      'attribution or signature for a nearby main reading section.',
                              'false': 'That whole-source main-reading attribution function is not '
                                       'established; it may be page furniture, a footnote, mixed text or '
                                       'another kind of content.'}},
 'quotation_boundary': {'type': 'noul',
                        'instructions': ['Assess only the proposed NEW paragraph at checks.{name}. Is this '
                                         'boundary permissible as an independently presented quotation, or '
                                         'as a new narration/utterance after a completed quoted utterance?',
                                         'The check supplies delimiter/nearby-text cues only to locate the '
                                         'question. They are not a verdict or a confirmed predecessor. Use '
                                         'full local_raw_evidence and the genuine nearest main-text '
                                         'predecessor; OCR ownership hints can be wrong. Do not infer '
                                         'nonexistent text, indentation or a fixed predecessor from a hint.',
                                         'A reporting/speech introduction ending in a colon can be followed '
                                         'by a complete independently presented quoted utterance in a new '
                                         'paragraph. This is a permitted quotation convention, not an '
                                         'obligatory syntax error. Distinguish such an utterance or '
                                         'quotation block from a merely quoted term inside a larger '
                                         'unfinished grammatical construction.',
                                         'After a completed quoted utterance and its completed surrounding '
                                         'reporting clause, a subsequent narration or new utterance may '
                                         'stand independently. A fragment-looking new opening does not '
                                         'demonstrate that a word or obligatory part was left unfinished on '
                                         'the left. Do not disguise missing OCR characters in a new '
                                         'narrative by appending it to a finished previous quotation.',
                                         'Do not certify this exception for a word cut within an unfinished '
                                         'quotation, a quoted term that is only one part of an unfinished '
                                         'external clause, or an ancillary note misidentified as main text. '
                                         'Require the actual complete quotation/utterance convention and no '
                                         'compulsory unfinished left construction.',
                                         'This is only a boundary exception check. It does not approve '
                                         'source ownership, authorize missing-text repair, or pardon another '
                                         'gap. If the legitimate quotation/narration boundary cannot be '
                                         'established, answer FALSE.'],
                        'criteria': {'true': 'The specific new-paragraph boundary is permissible under the '
                                             'complete independent quotation or '
                                             'completed-utterance-to-new-narration convention.',
                                     'false': 'That specific quotation-boundary exception is not '
                                              'established; the existing mandatory word/syntax concern is '
                                              'not resolved by this test.'}},
 'left_word_complete': {'type': 'noul',
                        'instructions': ['At checks.{name}, does the preceding main-text block already end '
                                         'at a whole-word boundary?',
                                         'Use complete local_raw_evidence and preceding_source_index to '
                                         'locate the nearest genuine SAME-PAGE preceding main-text block. '
                                         'Delimiter cues and immediate_paragraph_hint are fallible locators, '
                                         'not a confirmed predecessor. Exclude ancillary material and do not '
                                         'invent missing text.',
                                         'This checks a property of the LEFT block. Do not evaluate whether '
                                         'the target opening is well written or complete. Missing OCR '
                                         'characters in a new target do not create an unfinished left word '
                                         'or clause.',
                                         'Inspect the end of that preceding block, including the end of its '
                                         'quoted utterance. The missing suffix of an actual lexical stem '
                                         'would mean an incomplete word. A finished word followed by '
                                         'sentence-ending punctuation and a closing quote is not a word '
                                         'prefix waiting for the target.',
                                         'Do not infer word incompleteness merely because the target starts '
                                         'with a fragment. An unresolved outside-quotation clause is a '
                                         'syntax issue, not a word-internal issue. End-of-line hyphenation '
                                         'or an actually unfinished quoted word must still be treated as '
                                         'incomplete.'],
                        'criteria': {'true': 'The genuine preceding block ends at a whole-word boundary; no '
                                             'left lexical stem awaits a suffix from the target.',
                                     'false': 'That whole-word ending is not established, or an actual '
                                              'unfinished lexical stem remains on the left.'}},
 'left_frame_complete': {'type': 'noul',
                         'instructions': ['At checks.{name}, is the preceding reporting/sentence frame '
                                          'already syntactically complete OUTSIDE its finished quotation?',
                                          'Use complete local_raw_evidence and preceding_source_index to '
                                          'locate the nearest genuine SAME-PAGE preceding main-text block. '
                                          'Delimiter cues and immediate_paragraph_hint are fallible '
                                          'locators, not a confirmed predecessor. Exclude ancillary material '
                                          'and do not invent missing text.',
                                          'This checks a property of the LEFT block. Do not evaluate whether '
                                          'the target opening is well written or complete. Missing OCR '
                                          'characters in a new target do not create an unfinished left word '
                                          'or clause.',
                                          'A reporting verb can have its complete object supplied by the '
                                          'finished quoted utterance already present in that block. After '
                                          'that complete reporting sentence, subsequent narration need not '
                                          'be its obligatory complement. Do not judge the new target as an '
                                          'object that the left frame is still missing merely because it '
                                          'begins oddly.',
                                          'A finished quote does NOT automatically finish an outer '
                                          'conditional or subordinate clause. If the surrounding frame still '
                                          'requires its main clause or another obligatory component after '
                                          'the quote, answer FALSE. Distinguish that outside dependency from '
                                          'a fully completed reporting sentence.',
                                          'Do not require prose to continue in one paragraph for topical '
                                          'coherence, and do not repair author wording or missing OCR. If '
                                          'the actual outside frame cannot be determined from the supplied '
                                          'text, answer FALSE.'],
                         'criteria': {'true': 'The outside reporting/grammatical frame is complete; it is '
                                              'not awaiting a mandatory clause or complement supplied by the '
                                              'target.',
                                      'false': 'An outside-quotation grammatical dependency remains, or the '
                                               'complete outside frame is not established.'}},
 'detached_image_strip': {'type': 'noul',
                          'instructions': ['Assess only the PAGE ORGANIZATION around the target at '
                                           'checks.{name}: does this whole text form a separate narrow strip '
                                           'at the outer edge of, or overlaid on, a page-dominating '
                                           'illustration?',
                                           'Use local_raw_evidence.target_page.layouts and normalized '
                                           'image/text boxes. This asks about spatial organization, not '
                                           'whether the words tell a story. A complete narrative sentence '
                                           'can be printed as a strip with an illustration, without '
                                           'quotation marks or a Figure label.',
                                           'Distinguish a text strip around an illustration occupying the '
                                           'main page area from ordinary running text in a sustained body '
                                           'column with a picture inserted alongside it. The mere presence '
                                           'of an image is insufficient. Do not certify a mixed block '
                                           'containing both a sustained body column and ancillary '
                                           'illustration text.',
                                           'Only use the supplied boxes and full local raw evidence. Do not '
                                           'invent glyph positions, indentation, borders or a photographer '
                                           'credit absent from the supplied text. This spatial fact alone '
                                           'does not authorize a paragraph split or deleting any text.'],
                          'criteria': {'true': 'The entire target is organized as a separate narrow text '
                                               'strip at the edge of or over a dominant illustration, rather '
                                               'than a sustained running-text column.',
                                       'false': 'That separate strip organization is not established; the '
                                                'text may form ordinary running prose or a mixed block.'}},
 'prior_narrative_role': {'type': 'noul',
                          'instructions': ['At checks.{name}, does at least one listed matching '
                                           'PREVIOUS-page source function as ongoing main narrative which '
                                           'already contains the complete target passage and then continues '
                                           'with further narrative?',
                                           'The listed full literal occurrences and remaining text are '
                                           'read-only string-match facts. Locate those complete sources in '
                                           'local_raw_evidence.previous_page.paragraph_boundary and classify '
                                           'their function, rather than trusting ownership tags. Do not '
                                           'assume the last array item is correct main text.',
                                           'TRUE requires actual main narrative, not an earlier illustration '
                                           'caption, footnote, header or isolated repeated title. The '
                                           'matching source must continue beyond the copied passage: the '
                                           'target is not simply supplying words missing at its end. Do not '
                                           'infer anything from outside the provided neighboring pages. This '
                                           'asks about the previous source only, not the target caption '
                                           'role.'],
                          'criteria': {'true': 'A matched previous source is ongoing main narrative that '
                                               'already contains the complete target passage and has further '
                                               'narrative after it.',
                                       'false': 'Such a matched ongoing main narrative source is not '
                                                'established; the match may be ancillary or insufficient.'}},
 'selected_whole_passage': {'type': 'noul',
                            'instructions': ['At checks.{name}, does the target reproduce a COMPLETE '
                                             'readable passage already present inside the listed earlier '
                                             'source, rather than supply a missing word-ending or unfinished '
                                             "expression at that earlier source's end?",
                                             'The supplied '
                                             'earlier_whole_text_occurrences_with_following_text records '
                                             'exact complete-target string matches after ASCII-whitespace '
                                             'normalization, with text still following the match. Use the '
                                             'complete previous-page source and target raw text to interpret '
                                             'that measured match; it is not a paragraph-boundary verdict.',
                                             'TRUE requires a whole independently readable sentence or '
                                             'passage being replayed from within earlier text. A single '
                                             'numeral, isolated name or title, diagram label, lexical '
                                             'suffix, shared short phrase or missing OCR text is not '
                                             'sufficient. The target need not have explicit quotation marks '
                                             'or attribution.',
                                             'This asks whether the target is a complete selected passage, '
                                             'not whether any page has a caption or whether the target '
                                             'should be deleted. Different printed occurrences are not '
                                             'redundant OCR. Do not infer material outside the supplied '
                                             'pages. If a complete replayed passage is not established, '
                                             'answer FALSE.'],
                            'criteria': {'true': 'The target is a complete readable passage replayed from '
                                                 'within the earlier text, not its missing final word or '
                                                 'obligatory continuation.',
                                         'false': 'Such a complete replayed passage is not established; the '
                                                  'match may be only a fragment, label or an actual '
                                                  'continuation.'}},
 'image_caption_role': {'type': 'noul',
                        'instructions': ['Classify only the FUNCTION of target source at checks.{name}: is '
                                         'it illustration-associated caption text or a selected excerpt '
                                         'printed with an image, rather than ordinary running narrative?',
                                         'Use full local_raw_evidence and the image/text positions. A '
                                         'literary excerpt accompanying a photograph can have no Figure '
                                         'label and no quotation marks. A small strip above a nearly '
                                         'full-page image can still be image-associated text. The words may '
                                         'repeat an earlier narrative passage.',
                                         'Do not decide from image size or text repetition alone. Ordinary '
                                         'body prose can continue across a page with an image. Do not '
                                         'mistake diagram labels, an ordinary chapter heading, a mixed '
                                         'body/caption block or missing text for a whole caption/excerpt. '
                                         'This question asks text role only, not whether a continuation flag '
                                         'is correct.'],
                        'criteria': {'true': 'The whole target text functions as caption/excerpt material '
                                             'associated with the illustration.',
                                     'false': 'That image-associated whole-text function is not established; '
                                              'it may be running prose or another kind of text.'}},
 'caption_starts_here': {'type': 'noul',
                         'instructions': ['For the image-associated-text candidate at checks.{name}, is its '
                                          'opening a START of its own caption/excerpt unit rather than a '
                                          'continuation of an earlier caption/excerpt?',
                                          'This is a distinct boundary question. Determine whether the '
                                          'target opening forms its own image-associated unit from the full '
                                          'local raw evidence. A whole independently printed sentence or '
                                          'quotation accompanying the current illustration can begin its own '
                                          'unit even when it restates part of the preceding main narrative.',
                                          'Do not require caption text to continue a main prose paragraph '
                                          'merely because its topic or wording also appears in that prose. '
                                          'Conversely a caption can run over pages: an opening which '
                                          'supplies an unfinished word or mandatory construction from an '
                                          'earlier image-associated text is not a new caption unit.',
                                          'The image positions, current gap decision, ownership hints and '
                                          'immediate_paragraph_hint are fallible locators rather than '
                                          'answers. Do not certify the start from punctuation alone, or '
                                          'guess a missing beginning. Do not grade missing OCR characters, '
                                          'other sources or the ownership choice in this question. If a '
                                          'separate start is not established in the supplied neighborhood, '
                                          'answer FALSE.'],
                         'criteria': {'true': 'The target opening starts its own illustration-associated '
                                              'caption/excerpt unit, rather than continuing an earlier such '
                                              'unit.',
                                      'false': 'A distinct start is not established; this may continue an '
                                               'earlier caption/excerpt or lack the necessary evidence.'}},
 'fresh_spoken_turn': {'type': 'noul',
                       'instructions': ['Does the target text at checks.{name} BEGIN a fresh spoken turn, '
                                        'with its own speaker attribution before or within its utterance, '
                                        'rather than finish the preceding utterance or its reporting clause?',
                                        'Use the full three-page local_raw_evidence and '
                                        'preceding_source_index to identify the genuine preceding MAIN-TEXT '
                                        'utterance. Ownership and immediate_paragraph_hint are fallible '
                                        'locators, not a fixed edge. Exclude ancillary material and do not '
                                        'invent participants or text beyond the supplied pages.',
                                        'Ordinary quotation marks can enclose terms, titles or cited '
                                        'passages rather than dialogue. Do not certify a spoken turn from '
                                        'quotation marks, a new sentence, a conjunction or topical coherence '
                                        'alone.',
                                        'A narrative introduction saying a participant answers, replies or '
                                        "speaks, followed by that participant's new utterance, can begin a "
                                        'fresh turn. A reporting clause placed AFTER a preceding quote can '
                                        'instead belong to that preceding utterance and does not establish a '
                                        'new turn.',
                                        'This question asks whether a fresh turn starts at this boundary, '
                                        'not whether its speaker differs from the previous speaker. Do not '
                                        'require a split merely for narration after a quote. If no actual '
                                        'new attributed turn is established, answer FALSE.'],
                       'criteria': {'true': 'This target begins a new attributed spoken turn, rather than '
                                            'completing the previous utterance/reporting clause.',
                                    'false': 'A fresh attributed turn at this boundary is not established.'}},
 'different_dialogue_speaker': {'type': 'noul',
                                'instructions': ['For the target at checks.{name}, is its explicitly '
                                                 'attributed speaker a DIFFERENT participant from the '
                                                 'speaker of the genuine immediately preceding spoken turn?',
                                                 'Use the full three-page local_raw_evidence and '
                                                 'preceding_source_index to identify the genuine preceding '
                                                 'MAIN-TEXT utterance. Ownership and '
                                                 'immediate_paragraph_hint are fallible locators, not a '
                                                 'fixed edge. Exclude ancillary material and do not invent '
                                                 'participants or text beyond the supplied pages.',
                                                 'Ordinary quotation marks can enclose terms, titles or '
                                                 'cited passages rather than dialogue. Do not certify a '
                                                 'spoken turn from quotation marks, a new sentence, a '
                                                 'conjunction or topical coherence alone.',
                                                 'Resolve each speaker from actual reporting words and '
                                                 'nearby dialogue context. A question addressed to another '
                                                 "participant and that participant's expressly attributed "
                                                 'reply can establish different speakers. A continued '
                                                 'utterance by the same person, a new name merely mentioned '
                                                 'in dialogue, or a quoted passage from an external source '
                                                 'does not establish a speaker change.',
                                                 'Do not infer a different speaker solely from different '
                                                 'pronouns, quotation marks, an odd opening or a change of '
                                                 'subject. If either identity or the actual turn sequence '
                                                 'cannot be established from the supplied neighborhood, '
                                                 'answer FALSE. This is a participant-identity question '
                                                 'only, not a typesetting judgment.'],
                                'criteria': {'true': 'The actual preceding and target spoken turns are '
                                                     'attributed to distinct participants.',
                                             'false': 'A change between identifiable speakers is not '
                                                      'established; this may be the same speaker, a '
                                                      'non-dialogue quotation or missing context.'}},
 'joined_word_complete': {'type': 'noul',
                          'instructions': ['For the existing JOIN at checks.{name}, does the target opening '
                                           'supply the literal missing ending of a word that starts at the '
                                           'END of the genuine preceding main-text block?',
                                           'Use full local_raw_evidence and preceding_source_index. Find the '
                                           'actual nearest main-text predecessor; hints and ownership '
                                           'proposals are fallible. Do not skip an intervening genuine main '
                                           'paragraph, invent missing characters, or use a following page as '
                                           'a predecessor.',
                                           'A word can be split at a page edge. In alphabetic scripts this '
                                           'can be a prefix and suffix, with normal line-end hyphenation. In '
                                           'Chinese and other unspaced scripts a single lexical expression '
                                           'can also span the edge. Require an actual contiguous word '
                                           'completion at the immediate seam, not merely related meanings or '
                                           'the possibility of placing two complete words next to each '
                                           'other.',
                                           'Judge the literal word seam before any subsequent punctuation or '
                                           'parenthetical author/editor comments. A comment after the '
                                           'completed word does not undo that word completion. This does not '
                                           "approve the comment's semantics or change its ownership.",
                                           'A heading, new dialogue turn, independent caption/excerpt or a '
                                           'new numbered item must not be certified just because its first '
                                           'characters could accidentally form a compound with earlier text. '
                                           'If the preceding actual source is not the required lexical stem, '
                                           'or the word completion cannot be established, answer FALSE.',
                                           'This narrow proof applies only to this incoming join. It does '
                                           'not approve other sources, other boundaries or OCR transcription '
                                           'quality.'],
                          'criteria': {'true': 'The actual adjacent main-text pieces form one word across '
                                               'this boundary; the existing join preserves that word.',
                                       'false': 'Such a specific word completion is not established; '
                                                'ordinary semantic continuity or accidental concatenation is '
                                                'insufficient.'}}}


_TEMPLATES["reference_position"] = {'type': 'noul',
 'instructions': ['For state.checks.{name} ONLY: find this source by its source_order; do not evaluate any '
                  'other source in this packet.',
                  'Assess ONLY the physical text position of reference replacements in this source, not note '
                  'meaning, targets, paragraph joins or missing OCR.',
                  'literal_edits describes edits from the original raw_text to analysed_text. Each original '
                  'field was removed and each analysed field inserted at that location. A REF replacement '
                  'must occupy the original printed marker position. An insertion of REF where the original '
                  'contained no marker, combined with removing a marker elsewhere, does NOT preserve its '
                  'position.',
                  'Check the literal edit operations and their original_before/original_after context. '
                  'Inline math or Markdown escaping around the SAME marker can change; whitespace changes do '
                  'not relocate a marker. If position is preserved, accept. If it demonstrably moved, '
                  'reject. Insufficient evidence is not a demonstrable error.'],
 'criteria': {'true': 'The replacements preserve original marker positions, or no displacement is '
                      'established.',
              'false': 'A reference token demonstrably occupies a different position from its original '
                       'marker.'}}


_TEMPLATES["retained_reading_position"] = {'type': 'noul',
 'instructions': ['Assess ONLY state.checks.{name}, identified by source_order in '
                  'local_raw_evidence.target_page.layouts. Other sources or questions cannot decide this '
                  'one.',
                  'Does the WHOLE source have a positive, identifiable place in the main reading sequence, '
                  'rather than being page apparatus? Use the original printed arrangement and neighboring '
                  'reading text. bbox_normalized is [left,top,right,bottom] in page fractions from 0 to 1; '
                  'near top=0 or bottom=1 is the physical page edge, not the start or end of the story.',
                  'Require an actual reading-content role, such as a section/volume opening in the reading '
                  'columns or an instructional step among the main instructions. Its mentioning a book name, '
                  'chapter, printing or pages does not turn it into page apparatus. Judge placement and role '
                  'of the entire source, not just familiar content words.',
                  'A marginal folio, running header/footer or publication continuation cue is not main '
                  'reading, even if it names a chapter. On a blank page, a bottom-margin chapter label does '
                  'not become a chapter opening just because it is the first or only source. Conversely a '
                  'genuine section-title leaf can have only its centrally placed title. Do not infer a '
                  'reading role from the source having been retained by OCR.',
                  'Do not judge spelling, paragraph joins or note-reference completeness. Uncertainty about '
                  'an apparatus role alone is not positive proof of main reading. Require concrete '
                  'main-reading placement/function; otherwise answer false.'],
 'criteria': {'true': 'The complete source has a demonstrated main-reading role in this local printed '
                      'sequence.',
              'false': 'Such a complete-source main-reading role is not established; it may be apparatus or '
                       'uncertain.'}}


_TEMPLATES['ended_utterance_boundary'] = {'type': 'noul',
 'instructions': ['At this proposed NEW paragraph boundary, has the preceding spoken turn ended by '
                  'trailing off or interruption, with the target beginning outside that turn?',
                  'Locate the nearest genuine main-reading predecessor in the supplied raw local '
                  'evidence. A closing quote after an ellipsis is only a candidate cue. Do not '
                  'invent missing words, speakers, indentation or a predecessor outside the '
                  'supplied neighborhood.',
                  'A deliberately unfinished utterance can finish a spoken turn: later independent '
                  'narration about an interruption or a new utterance need not grammatically '
                  'complete that abandoned utterance. Do not require the words inside a '
                  'deliberately ended spoken turn to form a complete sentence.',
                  'The outside grammatical frame must nevertheless be complete. If an outer '
                  'conditional or subordinate clause containing the quoted speech still awaits its '
                  'main clause or obligatory complement from the target, answer FALSE. Also reject '
                  'if the target continues the same quoted utterance or merely supplies a missing '
                  'obligatory component rather than begins outside it.',
                  'This proves only that this syntax break is permissible under the ended-turn '
                  'convention, not a word boundary, ownership, other gaps, or OCR correctness. If '
                  'the actual ended-turn-to-independent-content relationship is not established, '
                  'answer FALSE.'],
 'criteria': {'true': 'The spoken turn has ended and the target begins independently outside it; '
                      'no unfinished outer frame requires this join.',
              'false': 'The independent content after an ended turn is not established, or a '
                       'required outside construction still continues.'}}

_TEMPLATES['math_unit_boundary'] = {'type': 'noul',
 'instructions': ['At this specific NEW paragraph boundary, does the target constitute a '
                  'separately presented mathematical statement, variable assignment, numerical '
                  'display, or independently numbered exercise item?',
                  'Use the supplied full local_raw_evidence, source boxes, and the nearest genuine '
                  'predecessor. OCR text labels do not preclude a mathematical display, and '
                  'mathematical notation inside ordinary running prose does not establish one. Do '
                  'not invent indentation, missing text or a different predecessor.',
                  'Successive complete variable assignments may be separately presented even when '
                  'introduced by one imperative or connected by and. An instruction introducing a '
                  'list of separately numbered exercises may stand separately from each item. A '
                  'self-contained mathematical display is a hard paragraph boundary even when '
                  'introduced by running prose.',
                  'Do not certify an ordinary running clause merely because it contains a number '
                  'or inline formula. Reject a target that supplies the missing word ending, a '
                  'temperature/unit fragment embedded in an unfinished prose noun phrase, a '
                  'continued condition whose main clause follows, or another obligatory prose '
                  'complement without an independent mathematical/list-item function.',
                  'Assess the actual independent mathematical/item function, not the truth of the '
                  'mathematics or whether two related passages could be written together. If this '
                  'role is not established, answer FALSE. This proof affects only this syntax '
                  'break; it does not approve ownership, another boundary, or missing OCR.'],
 'criteria': {'true': 'The target is an independently presented mathematical or numbered exercise '
                      'unit; a new paragraph is legitimate under this convention.',
              'false': 'Such an independent unit is not established, or this is a required '
                       'running-prose continuation.'}}


_TEMPLATES['reporting_join'] = {'type': 'noul',
 'instructions': ['For this EXISTING incoming JOIN only, is the target utterance the direct spoken '
                  'content introduced by the immediately preceding reporting frame, rather than a '
                  'fresh independently attributed turn?',
                  'Find the nearest genuine main-reading predecessor using the full supplied local '
                  'raw evidence. A colon and quote delimiters are only selection cues. Establish '
                  'an actual reporting relation, not merely topical continuity, a list, a '
                  'quotation of a term, or two independent speakers.',
                  'A reporting verb with its speaker can introduce the words spoken by that same '
                  'speaker in the following quoted block. Joining that introduction and its direct '
                  'spoken content is legitimate even if printing could also separate the quote. Do '
                  'not demand a split merely because the target begins with an opening quotation '
                  'mark.',
                  'If the preceding frame already contains the full spoken content or the target '
                  'introduces another speaker, another independent utterance, a new heading/item, '
                  'or independent narration, answer FALSE. Do not infer missing speech or depend '
                  'on following pages.',
                  'This proof only permits this specific incoming join; it does not approve source '
                  'ownership, references, other gaps, missing OCR or final styling. If an '
                  'unambiguous introduction-to-direct-speech relation is not established, answer '
                  'FALSE.'],
 'criteria': {'true': 'This join combines one reporting introduction with its own direct spoken '
                      'complement, with no independent turn boundary.',
              'false': 'That single reporting-frame relation is not established, or this joins '
                       'independent turns/content.'}}


def question(kind: str, name: str) -> dict[str, Any]:
    """Create an isolated question for one named source decision."""
    value = deepcopy(_TEMPLATES[kind])
    value["instructions"] = [text.replace("{name}", name) for text in value["instructions"]]
    return value
