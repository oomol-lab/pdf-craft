import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pdf_craft
from PIL import Image
from pdf_craft import (
    DeepSeekOCR2LocalConfig,
    DeepSeekOCR2VendorConfig,
    DeepSeekOCRLocalConfig,
    DeepSeekOCRVendorConfig,
    UnlimitedOCRLocalConfig,
    UnlimitedOCRVendorConfig,
)
from pdf_craft.functions import predownload_models
from pdf_craft.ocr_config import ensure_ocr_config
from pdf_craft.pdf.page_extractor import PageExtractorNode
from pdf_craft_tool.runtime import create_ocr_config_from_env


class TestOCRConfig(unittest.TestCase):
    def test_options_create_deepseek_ocr_local_config(self):
        config = ensure_ocr_config(None, "models", True)

        self.assertIsInstance(config, DeepSeekOCRLocalConfig)
        assert isinstance(config, DeepSeekOCRLocalConfig)
        self.assertEqual(config.models_cache_path, (Path.cwd() / "models").resolve())
        self.assertTrue(config.local_only)

    def test_local_device_numbers_are_immutable_tuple(self):
        devices = [0, 1]
        for config_cls in (
            DeepSeekOCRLocalConfig,
            DeepSeekOCR2LocalConfig,
            UnlimitedOCRLocalConfig,
        ):
            with self.subTest(config_cls=config_cls.__name__):
                config = config_cls(enable_devices_numbers=devices)
                self.assertEqual(config.enable_devices_numbers, (0, 1))

        devices.append(2)
        config = DeepSeekOCRLocalConfig(enable_devices_numbers=devices)
        self.assertEqual(config.enable_devices_numbers, (0, 1, 2))

    def test_vendor_config_repr_hides_credentials(self):
        deepseek = DeepSeekOCRVendorConfig(
            base_url="https://example.com",
            api_key="secret-key",
            model="model",
        )
        deepseek2 = DeepSeekOCR2VendorConfig(
            base_url="https://example.com",
            api_key="secret-key-2",
            model="model",
        )
        unlimited = UnlimitedOCRVendorConfig(
            ak="secret-ak",
            sk="secret-sk",
        )

        self.assertNotIn("secret-key", repr(deepseek))
        self.assertNotIn("secret-key-2", repr(deepseek2))
        self.assertNotIn("secret-ak", repr(unlimited))
        self.assertNotIn("secret-sk", repr(unlimited))

    def test_ocr_config_cannot_mix_with_models_cache_options(self):
        config = DeepSeekOCRVendorConfig(
            base_url="https://example.com",
            api_key="key",
            model="model",
        )

        with self.assertRaisesRegex(ValueError, "ocr cannot be combined"):
            ensure_ocr_config(config, "models", False)

    def test_local_deepseek_ocr_uses_doc_page_extractor_factory(self):
        extractor = Mock()
        with patch(
            "doc_page_extractor.extractor.create_deepseek_ocr_page_extractor",
            return_value=extractor,
        ) as factory:
            node = PageExtractorNode(
                DeepSeekOCRLocalConfig(
                    models_cache_path="models",
                    local_only=True,
                    enable_devices_numbers=[0],
                )
            )
            node.load_models()

        factory.assert_called_once_with(
            ocr_model="deepseek-ocr",
            model_path=(Path.cwd() / "models").resolve(),
            local_only=True,
            enable_devices_numbers=(0,),
        )
        extractor.load_ocr_model.assert_called_once_with()

    def test_local_deepseek_ocr2_uses_doc_page_extractor_factory(self):
        extractor = Mock()
        with patch(
            "doc_page_extractor.extractor.create_deepseek_ocr_page_extractor",
            return_value=extractor,
        ) as factory:
            node = PageExtractorNode(
                DeepSeekOCR2LocalConfig(
                    models_cache_path="models",
                    local_only=True,
                    enable_devices_numbers=[1],
                )
            )
            node.load_models()

        factory.assert_called_once_with(
            ocr_model="deepseek-ocr2",
            model_path=(Path.cwd() / "models").resolve(),
            local_only=True,
            enable_devices_numbers=(1,),
        )
        extractor.load_ocr_model.assert_called_once_with()

    def test_local_unlimited_ocr_uses_doc_page_extractor_factory(self):
        extractor = Mock()
        with patch(
            "doc_page_extractor.extractor.create_unlimited_ocr_page_extractor",
            return_value=extractor,
        ) as factory:
            node = PageExtractorNode(
                UnlimitedOCRLocalConfig(
                    models_cache_path="models",
                    local_only=True,
                    enable_devices_numbers=[0, 1],
                )
            )
            node.load_models()

        factory.assert_called_once_with(
            model_path=(Path.cwd() / "models").resolve(),
            local_only=True,
            enable_devices_numbers=(0, 1),
        )
        extractor.load_ocr_model.assert_called_once_with()

    def test_local_ocr_missing_optional_runtime_has_install_hint(self):
        with patch(
            "doc_page_extractor.extractor.create_deepseek_ocr_page_extractor",
            side_effect=ModuleNotFoundError("No module named 'transformers'"),
        ):
            node = PageExtractorNode(DeepSeekOCRLocalConfig())
            with self.assertRaisesRegex(RuntimeError, "pdf-craft\\[local\\]"):
                node.load_models()

    def test_local_deepseek_ocr2_rejects_tiny_before_upstream_model_code(self):
        node = PageExtractorNode(DeepSeekOCR2LocalConfig(models_cache_path="models"))
        with self.assertRaisesRegex(ValueError, "deepseek-ocr2-local.*tiny.*base"):
            node.image2page(
                image=Image.new("RGB", (10, 10)),
                page_index=1,
                asset_hub=Mock(),
                ocr_size="tiny",
                includes_footnotes=False,
                includes_raw_image=False,
                plot_path=None,
                max_tokens=None,
                max_output_tokens=None,
                device_number=None,
                aborted=lambda: False,
            )

    def test_vendor_deepseek_ocr_uses_doc_page_extractor_factory(self):
        extractor = Mock()
        with patch(
            "doc_page_extractor.extractor.create_deepseek_ocr_vendor_page_extractor",
            return_value=extractor,
        ) as factory:
            node = PageExtractorNode(
                DeepSeekOCRVendorConfig(
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

    def test_vendor_deepseek_ocr2_uses_doc_page_extractor_factory(self):
        extractor = Mock()
        with patch(
            "doc_page_extractor.extractor.create_deepseek_ocr2_vendor_page_extractor",
            return_value=extractor,
        ) as factory:
            node = PageExtractorNode(
                DeepSeekOCR2VendorConfig(
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

    def test_vendor_unlimited_ocr_uses_doc_page_extractor_factory(self):
        extractor = Mock()
        with patch(
            "doc_page_extractor.extractor.create_unlimited_ocr_vendor_page_extractor",
            return_value=extractor,
        ) as factory:
            node = PageExtractorNode(
                UnlimitedOCRVendorConfig(
                    ak="ak",
                    sk="sk",
                    base_url="https://unlimited.example.com",
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
        self.assertEqual(upstream_config.base_url, "https://unlimited.example.com")
        self.assertEqual(upstream_config.poll_interval_seconds, 1.5)
        self.assertEqual(upstream_config.timeout_seconds, 60)

    def test_vendor_config_cannot_download_or_load_models(self):
        node = PageExtractorNode(
            UnlimitedOCRVendorConfig(
                ak="ak",
                sk="sk",
            )
        )

        with self.assertRaisesRegex(RuntimeError, "local OCR"):
            node.download_models(None)
        with self.assertRaisesRegex(RuntimeError, "local OCR"):
            node.load_models()

    def test_predownload_models_accepts_local_config(self):
        extractor = Mock()
        with patch(
            "doc_page_extractor.extractor.create_deepseek_ocr_page_extractor",
            return_value=extractor,
        ):
            predownload_models(
                ocr=DeepSeekOCRLocalConfig(models_cache_path="models"),
                revision="rev",
            )

        extractor.download_ocr_model.assert_called_once_with("rev")

    def test_pdf_craft_package_does_not_export_env_loader(self):
        self.assertFalse(hasattr(pdf_craft, "create_ocr_config_from_env"))
        self.assertTrue(hasattr(pdf_craft, "OCRMode"))

    def test_script_env_loader_defaults_to_deepseek_ocr_local(self):
        with patch.dict(os.environ, {}, clear=True):
            config = create_ocr_config_from_env()

        self.assertEqual(
            config,
            DeepSeekOCRLocalConfig(
                models_cache_path="models-cache",
                local_only=True,
            ),
        )

    def test_script_env_loader_creates_deepseek_ocr2_local_config(self):
        env = {
            "PDF_CRAFT_OCR_MODE": "deepseek-ocr2-local",
            "PDF_CRAFT_DEEPSEEK_MODELS_CACHE_PATH": "models",
            "PDF_CRAFT_DEEPSEEK_LOCAL_ONLY": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            config = create_ocr_config_from_env()

        self.assertEqual(
            config,
            DeepSeekOCR2LocalConfig(
                models_cache_path="models",
                local_only=False,
            ),
        )

    def test_script_env_loader_creates_deepseek_ocr_vendor_config(self):
        env = {
            "PDF_CRAFT_OCR_MODE": "deepseek-ocr-vendor",
            "PDF_CRAFT_DEEPSEEK_OCR_BASE_URL": "https://example.com",
            "PDF_CRAFT_DEEPSEEK_OCR_API_KEY": "key",
            "PDF_CRAFT_DEEPSEEK_OCR_MODEL": "model",
            "PDF_CRAFT_DEEPSEEK_OCR_MAX_TOKENS": "123",
        }
        with patch.dict(os.environ, env, clear=True):
            config = create_ocr_config_from_env()

        self.assertEqual(
            config,
            DeepSeekOCRVendorConfig(
                base_url="https://example.com",
                api_key="key",
                model="model",
                max_tokens=123,
            ),
        )

    def test_script_env_loader_creates_deepseek_ocr2_vendor_config(self):
        env = {
            "PDF_CRAFT_OCR_MODE": "deepseek-ocr2-vendor",
            "PDF_CRAFT_DEEPSEEK_OCR2_BASE_URL": "https://example.com",
            "PDF_CRAFT_DEEPSEEK_OCR2_API_KEY": "key",
            "PDF_CRAFT_DEEPSEEK_OCR2_MODEL": "model",
            "PDF_CRAFT_DEEPSEEK_OCR2_TEMPERATURE": "0.1",
            "PDF_CRAFT_DEEPSEEK_OCR2_TOP_P": "0.9",
        }
        with patch.dict(os.environ, env, clear=True):
            config = create_ocr_config_from_env()

        self.assertEqual(
            config,
            DeepSeekOCR2VendorConfig(
                base_url="https://example.com",
                api_key="key",
                model="model",
                temperature=0.1,
                top_p=0.9,
            ),
        )

    def test_script_env_loader_creates_unlimited_ocr_local_config(self):
        env = {
            "PDF_CRAFT_OCR_MODE": "unlimited-ocr-local",
            "PDF_CRAFT_UNLIMITED_MODELS_CACHE_PATH": "models",
            "PDF_CRAFT_UNLIMITED_LOCAL_ONLY": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            config = create_ocr_config_from_env()

        self.assertEqual(
            config,
            UnlimitedOCRLocalConfig(
                models_cache_path="models",
                local_only=True,
            ),
        )

    def test_script_env_loader_creates_unlimited_ocr_vendor_config(self):
        env = {
            "PDF_CRAFT_OCR_MODE": "unlimited-ocr-vendor",
            "PDF_CRAFT_UNLIMITED_OCR_ACCESS_KEY": "ak",
            "PDF_CRAFT_UNLIMITED_OCR_SECRET_KEY": "sk",
            "PDF_CRAFT_UNLIMITED_OCR_BASE_URL": "https://unlimited.example.com",
            "PDF_CRAFT_UNLIMITED_OCR_POLL_INTERVAL_SECONDS": "1.5",
        }
        with patch.dict(os.environ, env, clear=True):
            config = create_ocr_config_from_env()

        self.assertEqual(
            config,
            UnlimitedOCRVendorConfig(
                ak="ak",
                sk="sk",
                base_url="https://unlimited.example.com",
                poll_interval_seconds=1.5,
            ),
        )


if __name__ == "__main__":
    unittest.main()
