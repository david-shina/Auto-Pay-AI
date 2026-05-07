import os
from path import Path
from langchain_community.document_loaders.image import UnstructuredImageLoader
from dotenv import load_dotenv
import instructor
from groq import Groq
import logging
from . import base_loader
from typing import Generator
from langchain_core.documents import Document
from ..models.bill import BillExtraction
from urllib.parse import urlparse
import requests
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

import os
os.environ["OCR_AGENT"] = "unstructured.partition.utils.ocr_models.tesseract_ocr.OCRAgentTesseract"


class ImageLoader(base_loader.BaseLoader):

    def __init__(self, file_path):
        super().__init__(file_path)


    def detect_type(self)->str:
        ext = os.path.splitext(self.file_path)[1].lower()
        if ext in ['.png', '.jpg', '.jpeg', '.webp']:
            return "Image"
        return "Unknown"
    
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


    def extract_text(self)->Generator[Document, None, None]:
        try:
            loader = UnstructuredImageLoader(
                file_path=self.file_path,
                mode='single'
            )

            allPages = loader.load()

            if not allPages:
                raise ValueError("Unstructured returned no pages")
            
            yield from allPages
        except Exception as e:
            raise

    async def get_text(self):
        text = list(self.extract_text())
        return '\n'.join([p.page_content for p in text])

    async def extract_bill_info(self) -> BillExtraction:

        file_path = await self.get_local_path(self.file_path)

        self.file_path = file_path

        full_text = await self.get_text()

        if not full_text.strip():
            raise ValueError("No text could be extracted from this Image.")
        
        return client.chat.completions.create(
            model='llama-3.1-8b-instant',
            response_model=BillExtraction,
            messages=[
                {"role": "system", "content": "You are an expert financial auditor. Extract bill details accurately."},
                {"role": "user", "content": f"Extract details from this bill text:\n\n{full_text}"}
            ],
        )

