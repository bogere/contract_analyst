from __future__ import annotations

import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from backend import ocr


class FakeTesseractError(RuntimeError):
    pass


class FakeTesseractNotFoundError(OSError):
    pass


class FakeBitmap:
    def __init__(self, image: MagicMock) -> None:
        self.image = image
        self.closed = False

    def to_pil(self) -> MagicMock:
        return self.image

    def close(self) -> None:
        self.closed = True


class FakePdfPage:
    def __init__(self, bitmap: FakeBitmap) -> None:
        self.bitmap = bitmap
        self.closed = False

    def render(self, *, scale: int) -> FakeBitmap:
        if scale != 2:
            raise AssertionError("OCR rendering should use a readable resolution.")
        return self.bitmap

    def close(self) -> None:
        self.closed = True


class FakePdfRenderer:
    def __init__(self, pages: list[FakePdfPage]) -> None:
        self.pages = pages
        self.closed = False

    def __len__(self) -> int:
        return len(self.pages)

    def __getitem__(self, index: int) -> FakePdfPage:
        return self.pages[index]

    def __enter__(self) -> FakePdfRenderer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True


class PdfOCRTests(unittest.TestCase):
    def make_pdfplumber(self, page_texts: list[str]) -> tuple[MagicMock, MagicMock]:
        pdf = MagicMock()
        pdf.pages = [MagicMock() for _ in page_texts]
        for page, text in zip(pdf.pages, page_texts):
            page.extract_text.return_value = text
        pdf_context = MagicMock()
        pdf_context.__enter__.return_value = pdf
        pdf_context.__exit__.return_value = None
        return pdf, pdf_context

    def test_preserves_native_text_without_loading_ocr(self) -> None:
        native_text = "A text-readable contract with searchable terms." * 2
        _, pdf_context = self.make_pdfplumber([native_text])

        with patch.object(ocr.pdfplumber, "open", return_value=pdf_context):
            extracted = ocr.extract_pdf_text(Path("contract.pdf"))

        self.assertEqual(extracted, native_text)

    def test_ocr_extracts_scanned_page_and_releases_render_resources(self) -> None:
        _, pdf_context = self.make_pdfplumber([""])
        image = MagicMock()
        bitmap = FakeBitmap(image)
        page = FakePdfPage(bitmap)
        renderer = FakePdfRenderer([page])
        recognized_text = ("Scanned contract text " * 5).strip()
        tesseract = SimpleNamespace(
            pytesseract=SimpleNamespace(tesseract_cmd="tesseract"),
            image_to_string=MagicMock(return_value=recognized_text),
            TesseractError=FakeTesseractError,
            TesseractNotFoundError=FakeTesseractNotFoundError,
        )
        pdfium = SimpleNamespace(PdfDocument=MagicMock(return_value=renderer))

        with (
            patch.object(ocr.pdfplumber, "open", return_value=pdf_context),
            patch.dict(sys.modules, {"pypdfium2": pdfium, "pytesseract": tesseract}),
            patch.object(ocr.shutil, "which", return_value="/usr/bin/tesseract"),
            patch.dict("os.environ", {"CONTRACTGUARD_OCR_LANG": "eng"}),
        ):
            extracted = ocr.extract_pdf_text(Path("scan.pdf"))

        self.assertEqual(extracted, recognized_text)
        tesseract.image_to_string.assert_called_once_with(image, lang="eng")
        image.close.assert_called_once()
        self.assertTrue(bitmap.closed)
        self.assertTrue(page.closed)
        self.assertTrue(renderer.closed)

    def test_renders_a_real_scanned_pdf_before_sending_image_to_ocr(self) -> None:
        image_pdf = BytesIO()
        Image.new("RGB", (300, 400), "white").save(image_pdf, format="PDF")
        tesseract = SimpleNamespace(
            pytesseract=SimpleNamespace(tesseract_cmd="tesseract"),
            image_to_string=MagicMock(return_value="Scanned text " * 6),
            TesseractError=FakeTesseractError,
            TesseractNotFoundError=FakeTesseractNotFoundError,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scanned.pdf"
            path.write_bytes(image_pdf.getvalue())
            with (
                patch.dict(sys.modules, {"pytesseract": tesseract}),
                patch.object(ocr.shutil, "which", return_value="/usr/bin/tesseract"),
            ):
                extracted = ocr.extract_pdf_text(path)

        self.assertEqual(extracted, ("Scanned text " * 6).strip())
        tesseract.image_to_string.assert_called_once()
        image = tesseract.image_to_string.call_args.args[0]
        self.assertIsInstance(image, Image.Image)

    def test_ocr_extracts_uploaded_image_files(self) -> None:
        image_data = BytesIO()
        Image.new("RGB", (300, 400), "white").save(image_data, format="PNG")
        recognized_text = ("Scanned image contract text " * 4).strip()
        tesseract = SimpleNamespace(
            pytesseract=SimpleNamespace(tesseract_cmd="tesseract"),
            image_to_string=MagicMock(return_value=recognized_text),
            TesseractError=FakeTesseractError,
            TesseractNotFoundError=FakeTesseractNotFoundError,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "contract.png"
            path.write_bytes(image_data.getvalue())
            with (
                patch.dict(sys.modules, {"pytesseract": tesseract}),
                patch.object(ocr.shutil, "which", return_value="/usr/bin/tesseract"),
            ):
                extracted = ocr.extract_image_text(path)

        self.assertEqual(extracted, recognized_text)
        tesseract.image_to_string.assert_called_once()
        image = tesseract.image_to_string.call_args.args[0]
        self.assertIsInstance(image, Image.Image)
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (300, 400))

    def test_ocr_extracts_each_page_of_a_multipage_tiff(self) -> None:
        tiff_data = BytesIO()
        first_page = Image.new("RGB", (50, 70), "white")
        second_page = Image.new("RGB", (50, 70), "white")
        first_page.save(tiff_data, format="TIFF", save_all=True, append_images=[second_page])
        tesseract = SimpleNamespace(
            pytesseract=SimpleNamespace(tesseract_cmd="tesseract"),
            image_to_string=MagicMock(side_effect=["First contract page", "Second contract page"]),
            TesseractError=FakeTesseractError,
            TesseractNotFoundError=FakeTesseractNotFoundError,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "contract.tiff"
            path.write_bytes(tiff_data.getvalue())
            with (
                patch.dict(sys.modules, {"pytesseract": tesseract}),
                patch.object(ocr.shutil, "which", return_value="/usr/bin/tesseract"),
            ):
                extracted = ocr.extract_image_text(path)

        self.assertEqual(extracted, "First contract page\n\nSecond contract page")
        self.assertEqual(tesseract.image_to_string.call_count, 2)

    def test_rejects_images_over_pixel_limit(self) -> None:
        image_data = BytesIO()
        Image.new("RGB", (10, 10), "white").save(image_data, format="PNG")
        tesseract = SimpleNamespace(
            pytesseract=SimpleNamespace(tesseract_cmd="tesseract"),
            TesseractError=FakeTesseractError,
            TesseractNotFoundError=FakeTesseractNotFoundError,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.png"
            path.write_bytes(image_data.getvalue())
            with (
                patch.dict(sys.modules, {"pytesseract": tesseract}),
                patch.object(ocr.shutil, "which", return_value="/usr/bin/tesseract"),
                patch.object(ocr, "MAX_IMAGE_PIXELS", 50),
            ):
                with self.assertRaisesRegex(ValueError, "pixel OCR limit"):
                    ocr.extract_image_text(path)

    def test_converts_tesseract_language_failures_to_actionable_errors(self) -> None:
        _, pdf_context = self.make_pdfplumber([""])
        tesseract = SimpleNamespace(
            pytesseract=SimpleNamespace(tesseract_cmd="tesseract"),
            image_to_string=MagicMock(side_effect=FakeTesseractError("language data missing")),
            TesseractError=FakeTesseractError,
            TesseractNotFoundError=FakeTesseractNotFoundError,
        )
        renderer = FakePdfRenderer([FakePdfPage(FakeBitmap(MagicMock()))])
        pdfium = SimpleNamespace(PdfDocument=MagicMock(return_value=renderer))

        with (
            patch.object(ocr.pdfplumber, "open", return_value=pdf_context),
            patch.dict(sys.modules, {"pypdfium2": pdfium, "pytesseract": tesseract}),
            patch.object(ocr.shutil, "which", return_value="/usr/bin/tesseract"),
        ):
            with self.assertRaisesRegex(ocr.OCRUnavailableError, "configured OCR language data"):
                ocr.extract_pdf_text(Path("scan.pdf"))

    def test_reports_missing_tesseract_instead_of_silently_skipping_ocr(self) -> None:
        _, pdf_context = self.make_pdfplumber([""])
        tesseract = SimpleNamespace(pytesseract=SimpleNamespace(tesseract_cmd="tesseract"))
        pdfium = SimpleNamespace(PdfDocument=MagicMock())

        with (
            patch.object(ocr.pdfplumber, "open", return_value=pdf_context),
            patch.dict(sys.modules, {"pypdfium2": pdfium, "pytesseract": tesseract}),
            patch.object(ocr.shutil, "which", return_value=None),
        ):
            with self.assertRaisesRegex(ocr.OCRUnavailableError, "Tesseract OCR"):
                ocr.extract_pdf_text(Path("scan.pdf"))

    def test_rejects_pdfs_with_too_many_pages_needing_ocr(self) -> None:
        _, pdf_context = self.make_pdfplumber([""] * (ocr.MAX_OCR_PAGES + 1))

        with patch.object(ocr.pdfplumber, "open", return_value=pdf_context):
            with self.assertRaisesRegex(ocr.OCRPageLimitError, "100 pages"):
                ocr.extract_pdf_text(Path("large-scan.pdf"))


if __name__ == "__main__":
    unittest.main()
