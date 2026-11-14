from fastapi import APIRouter
from pydantic import BaseModel

from . import telegram

router = APIRouter(prefix="/api/auth")


class Start(BaseModel):
    api_id: int
    api_hash: str
    phone: str


class Code(BaseModel):
    code: str


class Password(BaseModel):
    password: str


@router.get("/status")
async def status():
    return await telegram.status()


@router.post("/start")
async def start(body: Start):
    await telegram.start_login(body.api_id, body.api_hash, body.phone)
    return {"code_sent": True}


@router.post("/code")
async def code(body: Code):
    return await telegram.submit_code(body.code)


@router.post("/password")
async def password(body: Password):
    return await telegram.submit_password(body.password)


@router.post("/logout")
async def logout():
    await telegram.logout()
    return {"ok": True}
