from dotenv import load_dotenv

load_dotenv()
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    data_dir: str = os.getenv("RESIDENT_DATA_DIR", "data")
    groq_api_key: str | None = os.getenv("GROQ_API_KEY")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    groq_timeout_seconds: float = float(os.getenv("GROQ_TIMEOUT_SECONDS", "30"))
    groq_max_retries: int = int(os.getenv("GROQ_MAX_RETRIES", "2"))
    min_retrieval_score: float = float(os.getenv("MIN_RETRIEVAL_SCORE", "0.20"))


settings = Settings()
