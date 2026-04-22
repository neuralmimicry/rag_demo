import os
import pytest
from unittest.mock import MagicMock, patch
from refiner.file_converter import FileConverter
from refiner.llm_providers import LLMProvider, LLMResponse

@pytest.fixture
def mock_llm():
    llm = MagicMock(spec=LLMProvider)
    llm.name = "mock"
    llm.predict.return_value = LLMResponse(text="This is a description of the image.", raw={})
    llm.transcribe.return_value = "This is a transcription of the audio."
    return llm

def test_convert_txt(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("Hello World", encoding="utf-8")
    converter = FileConverter()
    assert converter.convert(str(f)) == "Hello World"

def test_convert_html(tmp_path):
    f = tmp_path / "test.html"
    f.write_text("<html><body><p>Hello <b>World</b></p></body></html>", encoding="utf-8")
    converter = FileConverter()
    result = converter.convert(str(f))
    assert "Hello" in result
    assert "World" in result

def test_convert_docx(tmp_path):
    from docx import Document
    f = tmp_path / "test.docx"
    doc = Document()
    doc.add_paragraph("Hello Docx")
    doc.save(str(f))
    
    converter = FileConverter()
    assert "Hello Docx" in converter.convert(str(f))

def test_convert_pdf(tmp_path):
    # pypdf is harder to create from scratch without other libs like reportlab
    # We'll just mock the reader for PDF if we can't easily create one
    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4\n%...") # Dummy PDF header
    
    with patch("file_converter.PdfReader") as mock_reader_cls:
        mock_reader = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Hello PDF"
        mock_reader.pages = [mock_page]
        mock_reader_cls.return_value = mock_reader
        
        converter = FileConverter()
        assert "Hello PDF" in converter.convert(str(f))


def test_extract_pdf_preserves_pages_and_block_types(tmp_path):
    f = tmp_path / "structured.pdf"
    f.write_bytes(b"%PDF-1.4\n%...")

    with patch("file_converter.PdfReader") as mock_reader_cls:
        mock_reader = MagicMock()
        page_one = MagicMock()
        page_one.extract_text.return_value = "System Overview\n\nRetry logic handles transient failures."
        page_one.images = []
        page_two = MagicMock()
        page_two.extract_text.return_value = "Status  Count\nSuccess  5\nFailure  1"
        page_two.images = []
        mock_reader.pages = [page_one, page_two]
        mock_reader_cls.return_value = mock_reader

        converter = FileConverter()
        extraction = converter.extract(str(f), mime_type="application/pdf")

    assert extraction.metadata["page_count"] == 2
    assert "[Page 1]" in extraction.text
    assert "[Page 2]" in extraction.text
    assert any(element.element_type == "heading" and element.page == 1 for element in extraction.elements)
    assert any(element.element_type == "table" and element.page == 2 for element in extraction.elements)


def test_extract_pdf_uses_vision_fallback_for_image_backed_page(tmp_path, mock_llm):
    f = tmp_path / "scanned.pdf"
    f.write_bytes(b"%PDF-1.4\n%...")

    with patch("file_converter.PdfReader") as mock_reader_cls:
        mock_reader = MagicMock()
        page = MagicMock()
        page.extract_text.return_value = ""
        image = MagicMock()
        image.data = b"fake-image"
        image.name = "page1.png"
        page.images = [image]
        mock_reader.pages = [page]
        mock_reader_cls.return_value = mock_reader
        mock_llm.predict.return_value = LLMResponse(text="Recovered scanned page text", raw={})

        converter = FileConverter(llm=mock_llm)
        extraction = converter.extract(str(f), mime_type="application/pdf")

    assert "Recovered scanned page text" in extraction.text
    assert extraction.metadata["vision_fallback_pages"] == 1
    assert any(element.metadata.get("source_kind") == "vision_fallback" for element in extraction.elements)

def test_convert_odf(tmp_path):
    from odf.opendocument import OpenDocumentText
    from odf.text import P
    f = tmp_path / "test.odt"
    textdoc = OpenDocumentText()
    p = P(text="Hello ODT")
    textdoc.text.addElement(p)
    textdoc.save(str(f))
    
    converter = FileConverter()
    # teletype.extractText might return it with some extra whitespace or structure
    assert "Hello ODT" in converter.convert(str(f))

def test_describe_image(tmp_path, mock_llm):
    from PIL import Image
    f = tmp_path / "test.png"
    img = Image.new('RGB', (100, 100), color = 'red')
    img.save(str(f))
    
    converter = FileConverter(llm=mock_llm)
    result = converter.convert(str(f))
    assert "--- Image Description: test.png ---" in result
    assert "This is a description of the image." in result
    mock_llm.predict.assert_called_once()

def test_transcribe_audio(tmp_path, mock_llm):
    f = tmp_path / "test.mp3"
    f.write_bytes(b"dummy audio data")
    
    converter = FileConverter(llm=mock_llm)
    result = converter.convert(str(f))
    assert "--- Transcription: test.mp3 ---" in result
    assert "This is a transcription of the audio." in result
    mock_llm.transcribe.assert_called_once_with(str(f), timeout=None)

def test_read_source_binary_url(mock_llm):
    from refiner.topic_researcher import TopicResearcher
    with patch("refiner.topic_researcher.requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"dummy pdf content"
        mock_resp.headers = {"Content-Type": "application/pdf"}
        mock_session.get.return_value = mock_resp
        
        with patch("refiner.topic_researcher.get_provider") as mock_get_provider:
            mock_get_provider.return_value = mock_llm
            # Mock the structured extraction API used by TopicResearcher.
            with patch("file_converter.FileConverter.extract") as mock_extract:
                mock_extract.return_value = MagicMock(text="Converted PDF Content", elements=[])

                researcher = TopicResearcher("http://jira", ("u", "p"), "openai")
                researcher._read_cache = MagicMock(return_value=None)
                researcher._write_cache = MagicMock()
                result = researcher._read_source("https://example.com/test.pdf")

            assert result == "Converted PDF Content"
            mock_session.get.assert_called()
            mock_extract.assert_called_once()
