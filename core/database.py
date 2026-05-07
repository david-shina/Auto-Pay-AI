from sqlmodel import create_engine, Session, SQLModel
from dotenv import load_dotenv
import os

load_dotenv(dotenv_path='.env')

DATABASE_URL = os.getenv('DATABASE_URL')

# For SQLite (local testing): "sqlite:///./test.db"
# For Postgres: settings.DATABASE_URL
engine = create_engine(DATABASE_URL, echo=True)

def init_db():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

