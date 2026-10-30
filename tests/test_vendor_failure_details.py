import pickle
import unittest

import httpx

from pdf_craft._policy import error_details
from pdf_craft.error import OCRError
from pdf_craft.pdf.vendor_ocr import _checked_response


class VendorFailureDetailsTest(unittest.TestCase):
    def test_machine_reason_and_each_cause_survive_detachment(self):
        response = httpx.Response(
            400,
            json={"error": {"code": "image_url_unsafe", "type": "unsafe_request",
                            "message": "private provider content"}},
            headers={"x-request-id": "provider-request"},
            request=httpx.Request("POST", "https://provider.invalid/v1"),
        )
        error = None
        try:
            _checked_response(response, 7, "OCR")
        except Exception as original:
            outer = OCRError("page failed", 7, 1)
            outer.__cause__ = original
            error = pickle.loads(pickle.dumps(error_details(outer)))
        assert error is not None
        self.assertEqual(error.code, "400")
        self.assertEqual(error.http_status, 400)
        self.assertEqual(error.provider_code, "image_url_unsafe")
        self.assertEqual(error.provider_type, "unsafe_request")
        self.assertEqual(error.request_id, "provider-request")
        self.assertEqual(error.exception_chain[0].type, "pdf_craft.error.OCRError")
        self.assertEqual(error.exception_chain[-1].type, "httpx.HTTPStatusError")
        self.assertTrue(any(item.frames for item in error.exception_chain))

    def test_unstructured_or_malformed_errors_do_not_invent_provider_codes(self):
        for body in ([], {"error": "image_url_unsafe"},
                     {"error": {"code": 123, "type": []}}, "not json"):
            with self.subTest(body=body):
                response = httpx.Response(400, json=body,
                                          request=httpx.Request("POST", "https://provider.invalid"))
                error = None
                try:
                    _checked_response(response, 1, "OCR")
                except Exception as original:
                    error = error_details(original)
                assert error is not None
                self.assertEqual(error.http_status, 400)
                self.assertIsNone(error.provider_code)
                self.assertIsNone(error.provider_type)
