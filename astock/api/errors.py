"""统一错误处理

让所有 API 错误响应有统一结构：
    {
        "error":   "STOCK_NOT_FOUND",        # 机器可读错误码
        "message": "股票 002028 不存在",      # 人类可读消息（中文）
        "detail":  "ts_code=002028.SZ ...",   # 可选详情（debug 用）
    }

HTTPException 触发的 4xx 也走这个格式，保证前端只需要处理一种 schema。
"""
import logging
import traceback
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


logger = logging.getLogger("api.errors")


# -------------------- 自定义业务异常 --------------------
class APIError(Exception):
    """业务异常基类。

    用法：
        raise APIError("STOCK_NOT_FOUND", "股票 002028 不存在", status=404)
    """
    def __init__(self, code: str, message: str, status: int = 400,
                 detail: str = None):
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail
        super().__init__(message)


class NotFound(APIError):
    def __init__(self, message: str, detail: str = None, code: str = "NOT_FOUND"):
        super().__init__(code, message, status=404, detail=detail)


class BadRequest(APIError):
    def __init__(self, message: str, detail: str = None, code: str = "BAD_REQUEST"):
        super().__init__(code, message, status=400, detail=detail)


# -------------------- 响应工具 --------------------
def _err_response(status: int, code: str, message: str, detail: str = None) -> JSONResponse:
    body = {"error": code, "message": message}
    if detail:
        body["detail"] = detail
    return JSONResponse(status_code=status, content=body)


# -------------------- handlers --------------------
async def api_error_handler(request: Request, exc: APIError):
    return _err_response(exc.status, exc.code, exc.message, exc.detail)


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """把 HTTPException 也归一化成 {error, message, detail}"""
    # 状态码 → 默认错误码
    default_codes = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "UNPROCESSABLE",
    }
    code = default_codes.get(exc.status_code, f"HTTP_{exc.status_code}")
    # detail 可以是字符串或 dict
    if isinstance(exc.detail, dict):
        message = exc.detail.get("message") or exc.detail.get("msg") or "请求处理失败"
        detail = exc.detail.get("detail")
    else:
        message = str(exc.detail) if exc.detail else "请求处理失败"
        detail = None
    return _err_response(exc.status_code, code, message, detail)


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Pydantic 参数校验失败 -> 422"""
    errs = exc.errors()
    first = errs[0] if errs else {}
    loc = ".".join(str(x) for x in first.get("loc", [])) or "?"
    msg = first.get("msg", "参数校验失败")
    return _err_response(
        422, "VALIDATION_ERROR",
        f"参数错误：{loc} - {msg}",
        detail=str(errs) if len(errs) > 1 else None,
    )


async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底：未捕获的异常一律转 500，避免泄露 Python 堆栈给前端"""
    tb = traceback.format_exc()
    logger.error(
        "[UNHANDLED] %s %s -> %s: %s\n%s",
        request.method, request.url.path, type(exc).__name__, exc, tb,
    )
    return _err_response(
        500, "INTERNAL_ERROR",
        f"服务端错误：{type(exc).__name__}",
        detail=str(exc)[:200],
    )


# -------------------- 注册器 --------------------
def install(app):
    """在 FastAPI app 上注册所有 handler。在 main.py 里调用一次。"""
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
