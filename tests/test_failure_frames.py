import pickle
import unittest
from pdf_craft._policy import error_details


class FailureFramesTest(unittest.TestCase):
    def test_locations_survive_serialization_without_locals(self):
        try:
            private_document = "private document content"
            raise ValueError(private_document)
        except ValueError as error:
            details = pickle.loads(pickle.dumps(error_details(error)))
        self.assertEqual(details.frames[-1]["function"], "test_locations_survive_serialization_without_locals")
        self.assertNotIn(private_document, repr(details.frames))
        self.assertEqual(set(details.frames[-1]), {"filename", "function", "lineno"})
