from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import errors
from app.utils.logging import get_logger

log = get_logger(__name__)

_STATUS = {
    errors.NotFound: 404,
    errors.Conflict: 409,
    errors.PaymentDeclined: 402,
    errors.RefundExceedsCapture: 422,
    errors.InvalidCurrency: 422,
    errors.ProviderUnavailable: 503,
}


def register(app: FastAPI) -> None:
    @app.exception_handler(errors.DomainError)
    async def _domain_error(request: Request, exc: errors.DomainError) -> JSONResponse:
        status = _STATUS.get(type(exc), 400)
        log.warning("domain_error", code=exc.code, path=request.url.path, status=status)
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=status)
