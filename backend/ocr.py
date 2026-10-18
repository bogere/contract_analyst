"""PDF text extraction with OCR fallback for scanned pages."""

from __future__ import annotations

import os
import shutil
import warnings
from pathlib import Path
from types import ModuleType

import pdfplumber
from PIL import Image

MINIMUM_CONTRACT_TEXT = 50
MAX_OCR_PAGES = 100
DEFAULT_OCR_LANGUAGE = "eng+chi_sim"
IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
MAX_IMAGE_PIXELS = 50_000_000


class OCRUnavailableError(RuntimeError):
    """Raised when a scanned PDF needs OCR but its runtime is unavailable."""


class OCRPageLimitError(ValueError):
    """Raised when a scanned PDF exceeds the supported OCR page count."""


def extract_pdf_text(path: Path) -> str:
    """Extract embedded PDF text and OCR pages that have no searchable text."""
    with pdfplumber.open(path) as pdf:
        page_texts = [(page.extract_text() or "").strip() for page in pdf.pages]
        ocr_page_indexes = [index for index, text in enumerate(page_texts) if not text]
        if sum(map(len, page_texts)) < MINIMUM_CONTRACT_TEXT:
            ocr_page_indexes = list(range(len(page_texts)))

        if not ocr_page_indexes:
            return "\n\n".join(text for text in page_texts if text)
        if len(ocr_page_indexes) > MAX_OCR_PAGES:
            raise OCRPageLimitError(
                f"This scanned PDF has more than {MAX_OCR_PAGES} pages requiring OCR. "
                "Split it into smaller files and try again."
            )

        pytesseract, language = _load_tesseract()

        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise OCRUnavailableError(
                "This PDF requires OCR. Install the PDF rendering dependency and try again."
            ) from exc
        with pdfium.PdfDocument(path) as renderer:
            if len(renderer) != len(page_texts):
                raise ValueError("Could not match PDF pages for OCR.")
            for index in ocr_page_indexes:
                page = renderer[index]
                try:
                    bitmap = page.render(scale=2)
                    try:
                        image = bitmap.to_pil()
                        try:
                            recognized_text = _recognize_image(image, pytesseract, language)
                        finally:
                            image.close()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                if len(recognized_text) > len(page_texts[index]):
                    page_texts[index] = recognized_text

    return "\n\n".join(text for text in page_texts if text)


def extract_image_text(path: Path) -> str:
    """OCR an uploaded image or multi-page TIFF containing a scanned contract."""
    pytesseract, language = _load_tesseract()
    page_texts = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source_image:
                frame_count = int(getattr(source_image, "n_frames", 1))
                if frame_count > MAX_OCR_PAGES:
                    raise OCRPageLimitError(
                        f"This image has more than {MAX_OCR_PAGES} pages requiring OCR. "
                        "Split it into smaller files and try again."
                    )
                for frame_index in range(frame_count):
                    source_image.seek(frame_index)
                    if source_image.width * source_image.height > MAX_IMAGE_PIXELS:
                        raise ValueError(f"Image exceeds the {MAX_IMAGE_PIXELS:,}-pixel OCR limit.")
                    source_image.load()
                    image = source_image.convert("RGB")
                    try:
                        page_texts.append(_recognize_image(image, pytesseract, language))
                    finally:
                        image.close()
    except Image.DecompressionBombError as exc:
        raise ValueError(f"Image exceeds the {MAX_IMAGE_PIXELS:,}-pixel OCR limit.") from exc
    except Image.DecompressionBombWarning as exc:
        raise ValueError(f"Image exceeds the {MAX_IMAGE_PIXELS:,}-pixel OCR limit.") from exc
    return "\n\n".join(text for text in page_texts if text)


def _load_tesseract() -> tuple[ModuleType, str]:
    try:
        import pytesseract
    except ImportError as exc:
        raise OCRUnavailableError(
            "OCR dependencies are not installed. Install the Python OCR dependency "
            "and the Tesseract executable with its configured language data."
        ) from exc

    tesseract_cmd = pytesseract.pytesseract.tesseract_cmd
    if not shutil.which(tesseract_cmd):
        raise OCRUnavailableError(
            "Tesseract OCR is not installed or is not on PATH. Install Tesseract "
            "with its configured language data and try again."
        )

    language = (os.getenv("CONTRACTGUARD_OCR_LANG") or DEFAULT_OCR_LANGUAGE).strip()
    return pytesseract, language or DEFAULT_OCR_LANGUAGE


def _recognize_image(image: Image.Image, pytesseract: ModuleType, language: str) -> str:
    try:
        return pytesseract.image_to_string(image, lang=language).strip()
    except (pytesseract.TesseractError, pytesseract.TesseractNotFoundError) as exc:
        raise OCRUnavailableError(
            "Tesseract could not read this image. Check that the configured "
            f"OCR language data ({language}) is installed."
        ) from exc
