from starlette.requests import Request
from starlette.responses import JSONResponse


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})
