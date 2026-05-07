import os
from abc import abstractmethod, ABC
from langchain_core.documents import Document
import requests
from urllib.parse import urlparse
import httpx
import tempfile



class BaseLoader(ABC):
    """
    Abstract base class for all document loaders (PDF, DOCX, PPTX, etc.).
    Each loader must implement `detect_type` and `load`.
    """

    def __init__(self, file_path:str):
        self.file_path = file_path
        self._validate_file()
        
    
    def _validate_file(self):
        parsed = urlparse(self.file_path)
        is_url = all(['parsed.scheme, parsed.netloc'])


        if is_url:
            try:
                response = requests.head(self.file_path, allow_redirects=True, timeout=10)
                if response.status_code >= 400:
                    raise ValueError(f'URL is unreachable. Status {response.status_code}')
                print('Valid File Link')
            except requests.RequestException as e:
                raise ValueError(f"Could not connect to URL: {e}")
        else:
            if not os.path.exists(self.file_path):
                raise FileNotFoundError(f"Local file does not exist: {self.file_path}")
            print("✅ Local file found.")

    @abstractmethod
    def detect_type(self)->str:
        """Detect document type (text-based, image-based, etc.)."""
        pass

    @abstractmethod
    async def get_text(self):
        """Extract and return documents as a list of LangChain Document objects."""
        pass