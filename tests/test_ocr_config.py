import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pdf_craft import (
    LocalDeepSeekOCRConfig,
    VendorDeepSeekOCRConfig,
    VendorUnlimitedOCRConfig,
    create_ocr_config_from_env,
)
from pdf_craft.functions import predownload_models
from pdf_craft.ocr_config import ensure_ocr_config
from pdf_craft.pdf.page_extractor import PageExtractorNode
from pdf_craft.transform import Transform


class TestOCRConfig(unittest.TestCase):
    def test_legacy_options_create_local_deepseek_config(self):
        config = ensure_ocr_config(None, "models", True)

        self.assertIsInstance(config, LocalDeepSeekOCRConfig)
        assert isinstance(config, LocalDeepSeekOCRConfig)
        self.assertEqual(config.models_cache_path, (Path.cwd() / "models").resolve())
        self.assertTrue(config.local_only)

    def test_ocr_config_cannot_mix_with_legacy_options(self):
        config = VendorDeepSeekOCRConfig(
            base_url="https://example.com",
            api_key="key",
            model="model",
        )

        with self.assertRaisesRegex(ValueError, "ocr cannot be combined"):
            Transform(
                models_cache_path="models",
                ocr=config,
            )

    def test_local_deepseek_uses_doc_page_extractor_local_factory(self):
        extractor = Mock()
        with patch(
            "doc_page_extractor.extractor.create_page_extractor",
            return_value=extractor,
        ) as factory:
            node = PageExtractorNode(
                LocalDeepSeekOCRConfig(
                    models_cache_path="models",
                    local_only=True,
                    enable_devices_numbers=[0],
                )
            )
            node.load_models()

        factory.assert_called_once_with(
            model_path=(Path.cwd() / "models").resolve(),
            local_only=True,
            enable_devices_numbers=[0],
        )
        extractor.load_models.assert_called_once_with()

    def test_vendor_deepseek_uses_doc_page_extractor_vendor_factory(self):
        extractor = Mock()
        with patch(
            "doc_page_extractor.extractor.create_deepseek_vendor_page_extractor",
            return_value=extractor,
        ) as factory:
            node = PageExtractorNode(
                VendorDeepSeekOCRConfig(
                    base_url="https://example.com",
                    api_key="key",
                    model="model",
                    temperature=0.1,
                    top_p=0.9,
                    max_tokens=123,
                    timeout_seconds=45,
                )
            )
            # pylint: disable=protected-access
            self.assertIs(node._get_page_extractor(), extractor)

        factory.assert_called_once()
        upstream_config = factory.call_args.args[0]
        self.assertEqual(upstream_config.base_url, "https://example.com")
        self.assertEqual(upstream_config.api_key, "key")
        self.assertEqual(upstream_config.model, "model")
        self.assertEqual(upstream_config.temperature, 0.1)
        self.assertEqual(upstream_config.top_p, 0.9)
        self.assertEqual(upstream_config.max_tokens, 123)
        self.assertEqual(upstream_config.timeout_seconds, 45)

    def test_vendor_unlimited_uses_doc_page_extractor_baidu_factory(self):
        extractor = Mock()
        with patch(
            "doc_page_extractor.extractor.create_baidu_page_extractor",
            return_value=extractor,
        ) as factory:
            node = PageExtractorNode(
                VendorUnlimitedOCRConfig(
                    ak="ak",
                    sk="sk",
                    base_url="https://baidu.example.com",
                    poll_interval_seconds=1.5,
                    timeout_seconds=60,
                )
            )
            # pylint: disable=protected-access
            self.assertIs(node._get_page_extractor(), extractor)

        factory.assert_called_once()
        upstream_config = factory.call_args.args[0]
        self.assertEqual(upstream_config.ak, "ak")
        self.assertEqual(upstream_config.sk, "sk")
        self.assertEqual(upstream_config.base_url, "https://baidu.example.com")
        self.assertEqual(upstream_config.poll_interval_seconds, 1.5)
        self.assertEqual(upstream_config.timeout_seconds, 60)

    def test_vendor_config_cannot_download_or_load_models(self):
        node = PageExtractorNode(
            VendorUnlimitedOCRConfig(
                ak="ak",
                sk="sk",
            )
        )

        with self.assertRaisesRegex(RuntimeError, "local DeepSeek OCR"):
            node.download_models(None)
        with self.assertRaisesRegex(RuntimeError, "local DeepSeek OCR"):
            node.load_models()

    def test_predownload_models_accepts_local_config(self):
        extractor = Mock()
        with patch(
            "doc_page_extractor.extractor.create_page_extractor",
            return_value=extractor,
        ):
            predownload_models(
                ocr=LocalDeepSeekOCRConfig(models_cache_path="models"),
                revision="rev",
            )

        extractor.download_models.assert_called_once_with("rev")

    def test_create_ocr_config_from_env_deepseek(self):
        env = {
            "PDF_CRAFT_OCR_MODE": "vendor-deepseek",
            "PDF_CRAFT_DEEPSEEK_BASE_URL": "https://example.com",
            "PDF_CRAFT_DEEPSEEK_API_KEY": "key",
            "PDF_CRAFT_DEEPSEEK_MODEL": "model",
            "PDF_CRAFT_DEEPSEEK_MAX_TOKENS": "123",
        }
        with patch.dict(os.environ, env, clear=True):
            config = create_ocr_config_from_env()

        self.assertEqual(
            config,
            VendorDeepSeekOCRConfig(
                base_url="https://example.com",
                api_key="key",
                model="model",
                max_tokens=123,
            ),
        )

    def test_create_ocr_config_from_env_unlimited(self):
        env = {
            "PDF_CRAFT_OCR_MODE": "vendor-unlimited",
            "PDF_CRAFT_UNLIMITED_AK": "ak",
            "PDF_CRAFT_UNLIMITED_SK": "sk",
            "PDF_CRAFT_UNLIMITED_POLL_INTERVAL_SECONDS": "1.5",
        }
        with patch.dict(os.environ, env, clear=True):
            config = create_ocr_config_from_env()

        self.assertEqual(
            config,
            VendorUnlimitedOCRConfig(
                ak="ak",
                sk="sk",
                poll_interval_seconds=1.5,
            ),
        )

    def test_create_ocr_config_from_upstream_legacy_vendor_env(self):
        env = {
            "DOC_PAGE_EXTRACTOR_BACKEND": "vendor",
            "DOC_PAGE_EXTRACTOR_DEEPSEEK_VENDOR_BASE_URL": "https://example.com",
            "DOC_PAGE_EXTRACTOR_DEEPSEEK_VENDOR_API_KEY": "key",
            "DOC_PAGE_EXTRACTOR_DEEPSEEK_VENDOR_MODEL": "model",
        }
        with patch.dict(os.environ, env, clear=True):
            config = create_ocr_config_from_env()

        self.assertEqual(
            config,
            VendorDeepSeekOCRConfig(
                base_url="https://example.com",
                api_key="key",
                model="model",
            ),
        )

    def test_create_ocr_config_from_upstream_legacy_baidu_env(self):
        env = {
            "DOC_PAGE_EXTRACTOR_BACKEND": "baidu",
            "DOC_PAGE_EXTRACTOR_BAIDU_AK": "ak",
            "DOC_PAGE_EXTRACTOR_BAIDU_SK": "sk",
        }
        with patch.dict(os.environ, env, clear=True):
            config = create_ocr_config_from_env()

        self.assertEqual(
            config,
            VendorUnlimitedOCRConfig(
                ak="ak",
                sk="sk",
            ),
        )


if __name__ == "__main__":
    unittest.main()
