import importlib
import sys
import types
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from PIL import Image  # type: ignore[import-not-found]

from pdf_craft import GLMOCRServiceConfig, ServiceOCRConfig
from pdf_craft.common import AssetHub
from pdf_craft.pdf.page_extractor import PageExtractorNode


try:
    importlib.import_module("doc_page_extractor.adapters.glmocr")
    _upstream_extractor = importlib.import_module("doc_page_extractor.extractor")
except ImportError:
    _HAS_UPSTREAM_GLM_OCR = False
else:
    _HAS_UPSTREAM_GLM_OCR = hasattr(
        _upstream_extractor, "create_glm_ocr_service_page_extractor"
    )


class _UpstreamGLMOCRServiceConfig:
    def __init__(self, endpoint_url, api_key, timeout_seconds):
        self.endpoint_url = endpoint_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds


class _Response:
    status_code = 200

    def __init__(self, value):
        self._value = value

    def json(self):
        return self._value


class TestGLMOCRServiceConfig(unittest.TestCase):
    def test_defaults_are_frozen_and_secret_is_not_repr(self):
        config = GLMOCRServiceConfig(api_key="secret")

        self.assertEqual(
            config.endpoint_url,
            "http://127.0.0.1:5002/glmocr/parse",
        )
        self.assertEqual(config.timeout_seconds, 180)
        self.assertNotIn("secret", repr(config))
        with self.assertRaises(FrozenInstanceError):
            config.timeout_seconds = 10  # type: ignore[misc]
        self.assertIs(ServiceOCRConfig, GLMOCRServiceConfig)

    def test_endpoint_must_be_http_or_https(self):
        for endpoint_url in (
            "",
            "localhost:5002/glmocr/parse",
            "ftp://localhost:5002/glmocr/parse",
            "http://",
            "https://",
            "http://:5002/glmocr/parse",
            "http://user@:5002/glmocr/parse",
        ):
            with self.subTest(endpoint_url=endpoint_url):
                with self.assertRaisesRegex(ValueError, "endpoint_url"):
                    GLMOCRServiceConfig(endpoint_url=endpoint_url)

        self.assertEqual(
            GLMOCRServiceConfig(
                endpoint_url="  HTTPS://example.test/glmocr/parse  "
            ).endpoint_url,
            "HTTPS://example.test/glmocr/parse",
        )

    def test_timeout_must_be_finite_and_positive(self):
        for timeout_seconds in (0, -1, float("inf"), float("nan"), True, "180"):
            with self.subTest(timeout_seconds=timeout_seconds):
                with self.assertRaisesRegex(ValueError, "timeout_seconds"):
                    GLMOCRServiceConfig(  # type: ignore[arg-type]
                        timeout_seconds=timeout_seconds  # type: ignore[arg-type]
                    )


class TestGLMOCRPageExtractorIntegration(unittest.TestCase):
    def test_factory_wiring_maps_to_upstream_config_lazily(self):
        factory = Mock(return_value=object())
        upstream_extractor = types.ModuleType("doc_page_extractor.extractor")
        setattr(
            upstream_extractor,
            "create_glm_ocr_service_page_extractor",
            factory,
        )
        upstream_glmocr = types.ModuleType("doc_page_extractor.adapters.glmocr")
        setattr(upstream_glmocr, "GLMOCRServiceConfig", _UpstreamGLMOCRServiceConfig)

        with patch.dict(
            sys.modules,
            {
                "doc_page_extractor.extractor": upstream_extractor,
                "doc_page_extractor.adapters.glmocr": upstream_glmocr,
            },
        ):
            node = PageExtractorNode(
                GLMOCRServiceConfig(
                    endpoint_url="https://example.test/glmocr/parse",
                    api_key="secret",
                    timeout_seconds=12,
                )
            )
            self.assertIsNotNone(node._get_page_extractor())  # pylint: disable=protected-access

        factory.assert_called_once()
        config = factory.call_args.args[0]
        self.assertIsInstance(config, _UpstreamGLMOCRServiceConfig)
        self.assertEqual(config.endpoint_url, "https://example.test/glmocr/parse")
        self.assertEqual(config.api_key, "secret")
        self.assertEqual(config.timeout_seconds, 12)

    def test_missing_upstream_factory_has_actionable_import_error(self):
        upstream_extractor = types.ModuleType("doc_page_extractor.extractor")
        upstream_glmocr = types.ModuleType("doc_page_extractor.adapters.glmocr")
        setattr(upstream_glmocr, "GLMOCRServiceConfig", _UpstreamGLMOCRServiceConfig)

        with patch.dict(
            sys.modules,
            {
                "doc_page_extractor.extractor": upstream_extractor,
                "doc_page_extractor.adapters.glmocr": upstream_glmocr,
            },
        ):
            with self.assertRaisesRegex(
                ImportError,
                "paired upstream.*create_glm_ocr_service_page_extractor",
            ):
                PageExtractorNode(GLMOCRServiceConfig())._get_page_extractor()  # pylint: disable=protected-access

    def test_service_mode_cannot_load_or_download_local_models(self):
        node = PageExtractorNode(GLMOCRServiceConfig())

        with self.assertRaisesRegex(RuntimeError, "local OCR"):
            node.download_models(None)
        with self.assertRaisesRegex(RuntimeError, "local OCR"):
            node.load_models()

    @unittest.skipUnless(
        _HAS_UPSTREAM_GLM_OCR,
        "requires the paired doc-page-extractor GLM-OCR integration",
    )
    @patch("requests.post")
    def test_structured_service_response_reaches_pdf_craft_page_layouts(self, post):
        post.return_value = _Response(
            {
                "json_result": [
                    [
                        {
                            "index": 0,
                            "label": "text",
                            "native_label": "text",
                            "content": "A structured page",
                            "bbox_2d": [100, 100, 900, 300],
                        }
                    ]
                ],
                "markdown_result": "A structured page",
                "usage": {},
            }
        )
        node = PageExtractorNode(GLMOCRServiceConfig(api_key="secret"))

        with TemporaryDirectory() as directory:
            page = node.image2page(
                image=Image.new("RGB", (1000, 2000), "white"),
                page_index=1,
                asset_hub=AssetHub(Path(directory)),
                ocr_size="tiny",
                includes_footnotes=False,
                includes_raw_image=False,
                plot_path=None,
                max_tokens=None,
                max_output_tokens=None,
                device_number=None,
                aborted=lambda: False,
            )

        self.assertEqual(len(page.body_layouts), 1)
        self.assertEqual(page.body_layouts[0].ref, "text")
        self.assertEqual(page.body_layouts[0].text, "A structured page")
        self.assertEqual(page.body_layouts[0].det, (100, 200, 900, 600))
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer secret",
        )


if __name__ == "__main__":
    unittest.main()
