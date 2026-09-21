"""智能拆分路由：/api/parse（仅预览，不落库）。"""

from fastapi import APIRouter, Depends

from ..auth import AuthContext, require_auth
from ..schemas import ParseRequest, ParseResponse
from ..services import parser

router = APIRouter(tags=["parse"])


@router.post("/parse", response_model=ParseResponse)
def parse(payload: ParseRequest, ctx: AuthContext = Depends(require_auth)):
    result = parser.parse_text(payload.text, today=payload.today)
    return ParseResponse(items=result["items"], notes=result["notes"])
