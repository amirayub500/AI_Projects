"""
schemas.py  -  Day 21 Final: request/response shapes for POST /chat
====================================================================
Pydantic models. FastAPI uses them to:
  - validate incoming JSON (missing/wrong "message" -> automatic 422 error)
  - shape the outgoing JSON
  - document the endpoint at http://localhost:8000/docs
(Same idea as Day 20 Deployment.)
"""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    # Body of POST /chat:  {"message": "Research the history of solar power"}
    message: str


class ChatResponse(BaseModel):
    # Reply of POST /chat:  {"response": "<final report text>"}
    response: str
