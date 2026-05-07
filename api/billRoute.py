from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from core.database import get_session
from sqlmodel import Session
import shutil
from services.loader import PDFLoader
from services.imageloader import ImageLoader
from models.bill import Bill
from datetime import datetime
import os
from dateutil import parser
from agents.graphs import build_graph

router = APIRouter(prefix='/api/v1/bill', tags=['bill'])
ALLOWED_TYPES = ["image/jpeg", "image/png", "application/pdf", 'image/jpg']

agent_executor = build_graph()


def get_loader(file_path: str):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.pdf':
        return PDFLoader(file_path)
    elif ext in ['.png', '.jpg', '.jpeg']:
        return ImageLoader(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

@router.post('/upload')
async def upload_files(file: UploadFile = File(...),  session: Session = Depends(get_session)):

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400, 
            detail=f"File type {file.content_type} is not supported."
        )
    
    temp_path = f'temp_{file.filename}'
    with open(temp_path, 'wb') as buffer:
        shutil.copyfileobj(file.file, buffer)


    try:
        loader = get_loader(temp_path)

        extracted_data = await loader.extract_bill_info()

        clean_date = parser.parse(extracted_data.due_date)

        db_bill = Bill(
            vendor_name=extracted_data.vendor_name,
            amount=extracted_data.amount,
            currency=extracted_data.currency,
            due_date= clean_date, #datetime.strptime(extracted_data.due_date, "%Y-%m-%d"),
            account_number=extracted_data.account_number,
            bank_code=extracted_data.bank_code,
            status="pending"
        )

        session.add(db_bill)
        session.commit()
        session.refresh(db_bill)

        initial_state = {
            'bill_id':db_bill.id,
            'currency': db_bill.currency,
            'user_balance':500000.00,
            'bill_amount': db_bill.amount,
            'due_date': db_bill.due_date.isoformat(),
            'is_approved': False
        }

        result = await agent_executor.ainvoke(initial_state)

        return {
        "status": "success",
        "bill": db_bill,
        "agent_decision": result["decision"],
        "agent_reasoning": result["reasoning"]
    }

    finally:
        # Clean up temp file
        if os.path.exists(temp_path):
            os.remove(temp_path)
