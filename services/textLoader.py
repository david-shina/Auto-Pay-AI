import instructor
from groq import Groq
from .models.bill import BillExtraction
from datetime import datetime

client = instructor.from_groq(Groq())

class TextLoader:
    def __init__(self, user_text: str):
        self.user_text = user_text
        self.current_date = datetime.now().strftime("%Y-%m-%d")

    async def extract_bill_info(self) -> BillExtraction:
        return client.chat.completions.create(
            model='llama-3.1-8b-instant',
            response_model=BillExtraction,
            messages=[
                {"role": "system", "content": f"You are a financial assistant. Extract bill or payment details from the user's message. If a piece of info is missing, guess logically or leave it null. Today's date is {self.current_date}. Use this to resolve relative dates"},
                {"role": "user", "content": f"Extract details from this message: {self.user_text}"}
            ],
        )
