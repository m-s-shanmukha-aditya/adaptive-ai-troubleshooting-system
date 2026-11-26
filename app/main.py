
from fastapi import FastAPI, HTTPException
import logging
from pydantic import BaseModel, Field

from app.services.troubleshooter import Troubleshooter

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Adaptive Troubleshooter",
    description="An AI-powered technical troubleshooting system.",
    version="0.1.0",
)

troubleshooter = Troubleshooter()


class TroubleshootRequest(BaseModel):
    issue: str = Field(
        min_length=5,
        description="The technical issue reported by the user.",
    )


class TroubleshootResponse(BaseModel):
    issue: str
    solution: str


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.post("/troubleshoot", response_model=TroubleshootResponse)
def troubleshoot(request: TroubleshootRequest):
    try:
        solution = troubleshooter.troubleshoot(request.issue)

        return TroubleshootResponse(
            issue=request.issue,
            solution=solution,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Troubleshooting failed: {str(exc)}",
        )
