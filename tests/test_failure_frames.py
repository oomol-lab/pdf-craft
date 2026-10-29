import pickle
import unittest
from pdf_craft._policy import error_details


class FailureFramesTest(unittest.TestCase):
    def test_locations_survive_serialization_without_locals(self):
        private_document = "private document content"
        details = None
        try:
            raise ValueError(private_document)
        except ValueError as error:
            details = pickle.loads(pickle.dumps(error_details(error)))
        assert details is not None
        self.assertEqual(details.frames[-1]["function"], "test_locations_survive_serialization_without_locals")
        self.assertNotIn(private_document, repr(details.frames))
        self.assertEqual(set(details.frames[-1]), {"filename", "function", "lineno"})

    def test_wrapped_error_includes_root_frames_and_stops_at_cycles(self):
        def root_failure():
            private_value = "private root content"
            raise ValueError(private_value)
        details = None
        try:
            root_failure()
        except ValueError as root:
            wrapper = RuntimeError("wrapper without its own traceback")
            wrapper.__cause__ = root
            root.__cause__ = wrapper
            details = error_details(wrapper)
        assert details is not None
        self.assertIn("root_failure", [frame["function"] for frame in details.frames])
        self.assertNotIn("private root content", repr(details.frames))
        self.assertLess(len(details.frames), 10)

    def test_implicit_context_is_included_unless_suppressed(self):
        def original():
            raise ValueError("original")
        try:
            original()
        except ValueError as root:
            wrapper = RuntimeError("outer")
            wrapper.__context__ = root
            self.assertIn("original", [frame["function"] for frame in error_details(wrapper).frames])
            wrapper.__suppress_context__ = True
            self.assertEqual(error_details(wrapper).frames, ())
