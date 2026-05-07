import re
import fitz
import logging
import os
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_unstructured import UnstructuredLoader
from unstructured.partition.utils.constants import PartitionStrategy
from langchain_core.documents import Document
from . import base_loader 
import pdf2image
from typing import List, Generator
import instructor
from groq import Groq
from urllib.parse import urlparse
from app.models.bill import BillExtraction
import httpx
import tempfile

load_dotenv(dotenv_path='.env')
langchainApiKey = os.getenv('LANGCHAIN_API_KEY')
client = instructor.from_groq(Groq())

os.environ['LANGCHAIN_TRACING_V2'] = 'true'
os.environ['LANGCHAIN_PROJECT'] = 'default'
os.environ['LANGCHAIN_API_KEY'] = langchainApiKey
os.environ['LANGCHAIN_ENDPOINT'] = 'https://api.smith.langchain.com'
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MIN_TEXT_REQ = 60

class PDFLoader(base_loader.BaseLoader):

    def __init__(self, filePath:str, chunk_size:int=500, chunk_overlap:int=50):
        super().__init__(filePath)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        logger.info(f'Loading text from: {self.file_path}')

    
    async def get_local_path(self, path_or_url: str) -> str:
        """Checks if path is a URL; if so, downloads it and returns local path."""
        parsed = urlparse(path_or_url)
        if parsed.scheme not in ['http', 'https', 'ftp']:
            return path_or_url # This is a path

        # It's a Telegram URL, let's download it
        print(f"📥 Downloading file from Telegram...")
        async with httpx.AsyncClient() as client:
            response = await client.get(path_or_url)
            if response.status_code != 200:
                raise RuntimeError(f"Failed to download file: {response.status_code}")
            
            # Save to a temp file
            ext = os.path.splitext(parsed.path)[1] or ".pdf"
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
            temp_file.write(response.content)
            temp_file.close()
            return temp_file.name
    

    def detect_type(self, samplePages:int = 3)->str:
        try:
            with fitz.open(self.file_path) as docs:

                for page in docs[:samplePages]:
                    text = page.get_text().strip()
                    if len(text) > MIN_TEXT_REQ:
                        return 'Text-based PDF'
                
        except Exception as e:
            logger.error(f"Error reading text-based PDF: {e}")


        try:
            images = pdf2image.convert_from_path(
                self.file_path, 
                first_page=1, 
                last_page=1)
            if images:
                return "Image-based PDF"
        except Exception as e:
            logger.error(f'Error reading image-based PDF: {e}')

        return 'Unknown PDF type'
    
    def _extract_text_pdf(self)->Generator[Document, None, None]:

        try:
            loader = PyPDFLoader(self.file_path)
            allPages = loader.load()

            if not allPages:
                raise ValueError('PyPDFLoader returned no pages')
            
            for page in allPages:
                yield page

            logger.info("PyPDFLoader succeeded")
        
        except Exception as e:
            print(f"PyPDFLoader failed: {e}. Falling back to PyMuPDF...")


        try:
            pdfDoc = fitz.open(self.file_path)

            for page_num in range(pdfDoc.page_count):
                page = pdfDoc[page_num]
                content = page.get_text()

                if not content.strip():
                    continue


                document = Document(
                    page_content=content,
                    metadata = {
                        'source':self.file_path,
                        'page': page_num
                }
                )

                yield document
            pdfDoc.close()

        except Exception as e:
            raise RuntimeError(f"Both PyPDFLoader and PyMuPDF failed to extract text: {e}")


    def _process_image_page(self, page)->Document:

        return page
    
    def _extract_image_pdf(self)->Generator[Document, None, None]:
        try:
            loader = UnstructuredLoader(
                file_path=self.file_path, 
                strategy=PartitionStrategy.HI_RES)
            all_pages = loader.load()
            if not all_pages:
                raise ValueError("Unstructured returned no pages")

            for page in all_pages:
                yield page

        except Exception as e:
            raise RuntimeError(f"OCR extraction failed: {e}")
        

    async def get_text(self):

        doc_type = self.detect_type()
        if doc_type == 'Text-based PDF':
            pages = list(self._extract_text_pdf())
        elif doc_type == "Image-based PDF":
            pages = list(self._extract_image_pdf())
        else:
            return ""

        return "\n".join([p.page_content for p in pages])
    

    async def extract_bill_info(self) -> BillExtraction:

        self.file_path = await self.get_local_path(self.file_path)
        
        full_text =await self.get_text()

        if not full_text.strip():
            raise ValueError("No text could be extracted from this PDF.")
        
        return client.chat.completions.create(
            model='llama-3.1-8b-instant',
            response_model=BillExtraction,
            messages=[
                {"role": "system", "content": "You are an expert financial auditor. Extract bill details accurately Note if information does not have the rrequired infomation return ''."},
                {"role": "user", "content": f"Extract details from this bill text:\n\n{full_text}"}
            ],
        )

