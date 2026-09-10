from typing import Optional, List
from pydantic import BaseModel


class ChatRequest(BaseModel):
    ticket_id: Optional[int] = None
    message: Optional[str] = None
    answer: Optional[str] = None


class ChatResponse(BaseModel):
    ticket_id: int
    type: str
    category: Optional[str] = None
    confidence: Optional[float] = None
    message: str
    options: List[str] = []
    steps: List[str] = []
    success_question: Optional[str] = None
    solution_id: Optional[str] = None
    status: Optional[str] = None


class FeedbackRequest(BaseModel):
    ticket_id: int
    rating: int
    comment: Optional[str] = None