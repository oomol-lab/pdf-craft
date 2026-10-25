from epub_generator import BookMeta, LaTeXRender, TableRender

from .error import (
    InterruptedError,
    NoUsableOCRPagesError,
    NoUsableFillPagesError,
    OCRBillingError,
    OCRFatalError,
    OCRError,
    PDFError,
)
from .assertions import (
    assert_pdf_readable, assert_pdf_renderable, assert_pdf_editable, assert_epub_readable,
)
from .functions import predownload_models
from .concurrency import (
    AsyncExecutor,
    AsyncResultIterator,
    CapacityLease,
    CapacityProvider,
    ConcurrentExecutor,
    ExecutionOutcome,
    ExecutionReport,
    FixedCapacity,
    NonContinuableError,
    OperationError,
    OperationResult,
    RateLimitedError,
)
from .craft import AsyncPDFCraft, ExtractionOptions, PDFOptions
from .sync import PDFCraft
from .pipeline.pdf import (
    PDFPatcher,
    PDFInlineFormula,
    PDFReplacement,
    PDFReplacementRegion,
    PDFTranslationPipeline,
    EraseOptions,
    FontResolution,
    PatchTextOptions,
    PatchTextStyle,
    QTextParagraphFiller,
)
from .transformer import (
    AnchoredContent,
    AnchoredContentExtractionTransformer,
    AnchoredContentTransformer,
    AnchoredContentTranslation,
    AnchoredContentXMLTransformer,
    NarrativeXMLTransformer,
    ExtractionTransformer,
    SubmitKind,
    XMLTranslator,
    TranslationEvent,
    TranslationEventKind,
    TranslationItemKind,
)
from .llm import LLM, create_llm_request
from .jev import JEV, create_jev_request
from .pdf.vendor_ocr import (
    OCRImageURLResolver,
    VendorOCRInput,
    VendorOCRRequest,
    VendorOCRResponse,
    create_vendor_ocr_request,
)
from .footnote import FootnoteOptions, FootnoteRefinement
from .metering import AbortedCheck, InterruptedKind, OCRTokensMetering
from .ocr_config import (
    DeepSeekOCR2LocalConfig,
    DeepSeekOCR2VendorConfig,
    DeepSeekOCRLocalConfig,
    DeepSeekOCRVendorConfig,
    LocalOCRConfig,
    OCRConfig,
    OCRMode,
    VendorOCRConfig,
    UnlimitedOCRLocalConfig,
    UnlimitedOCRVendorConfig,
)
from .pdf import (
    AsyncPDFDocument,
    AsyncPDFHandler,
    DeepSeekOCRSize,
    OCREvent,
    OCREventKind,
    PDFDocumentMetadata,
    pdf_pages_count,
)
from .document import PDFCraftExtraction, RenderMode, SourceLocation, TranslationInfo
from .extractor import PDFExtractor
from .renderer import EpubRenderer, MarkdownRenderer

from .pipeline.epub.events import EpubFragmentContent, EpubTranslation

from .document.access import ExtractionContext, PageGeometry

from .document.paragraph import (
    SourceRegion, ParagraphContent, PDFSourceRegion, PDFParagraphContent,
    TextLine, FormulaPlacement, EraseArea,
)

from .document.metadata import MetadataContent
from .document import DocumentMetadata, DocumentAuthor

from .policies import (
    RetryPolicy, RetryScope, ErrorPolicy, ErrorPolicyInput, ErrorCallback,
    FailureCategory, FailureContext, FailureLocation, FailureError,
    AllFallbackError, ErrorCallbackError,
)
