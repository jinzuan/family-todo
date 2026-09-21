"""Pydantic 请求/响应模型。"""

import re
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 日期统一 YYYY-MM-DD；非法格式在校验层直接拒绝，避免脏数据入库
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_date(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValueError("日期格式须为 YYYY-MM-DD")
    return value


class JoinRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    family_token: str
    device_name: Optional[str] = None
    role: Optional[str] = None


class JoinResponse(BaseModel):
    device_token: str
    device_id: str
    family_id: str
    role: str


class MeResponse(BaseModel):
    device_id: Optional[str]
    family_id: str
    role: str
    kind: str
    family_name: Optional[str] = None
    device_name: Optional[str] = None


class DeviceRename(BaseModel):
    """修改本设备标识名（家庭成员自定义，如「妈妈的手机」）。"""

    model_config = ConfigDict(extra="ignore")
    device_name: str


class ParseRequest(BaseModel):
    text: str
    # 客户端本地日历日 YYYY-MM-DD；缺省用服务器当天。用于把「今天/明天/X号」解析成具体日期。
    today: Optional[str] = None

    @model_validator(mode="after")
    def _check_today(self):
        _check_date(self.today)
        return self


class ParseItem(BaseModel):
    raw: str
    title: str
    qty: Optional[float] = None
    unit: Optional[str] = None
    price: Optional[float] = None
    category: str
    tag: Optional[str] = None  # 自动标签：动词+对象，如「给姥姥买」；无规则命中为 None
    note: Optional[str] = None  # 分配式附注，如「姥姥4+咱家4」
    is_note: bool = False
    confidence: float = 0.6
    reason: str = ""
    start_date: Optional[str] = None  # 句中日期词解析结果；无日期词=今天
    end_date: Optional[str] = None


class ParseResponse(BaseModel):
    items: List[ParseItem]
    notes: List[str] = Field(default_factory=list)


class TodoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    qty: Optional[float] = None
    unit: Optional[str] = None
    price: Optional[float] = None
    category: str = "other"
    tag: Optional[str] = None  # 自动标签：动词+对象，如「给姥姥买」
    note: Optional[str] = None
    due_date: Optional[str] = None  # 旧字段；给了就映射为单日窗口
    start_date: Optional[str] = None  # 有效期起点；与 end_date 双空=持久
    end_date: Optional[str] = None
    persistent: bool = False  # true=无日期持久待办
    important: bool = False  # true=重要（未完成时置顶；完成后落末尾）

    @model_validator(mode="after")
    def _normalize_dates(self):
        _check_date(self.due_date)
        _check_date(self.start_date)
        _check_date(self.end_date)
        if self.persistent:
            # 持久：忽略任何日期，强制双空
            self.start_date = None
            self.end_date = None
            return self
        if self.start_date is not None or self.end_date is not None:
            # 只给一个则自动补齐为单日；两个都给则校验顺序
            start = self.start_date or self.end_date
            end = self.end_date or self.start_date
            if start > end:
                raise ValueError("start_date 不能晚于 end_date")
            self.start_date = start
            self.end_date = end
        return self


class TodoUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: Optional[str] = None
    qty: Optional[float] = None
    unit: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None
    tag: Optional[str] = None
    note: Optional[str] = None
    due_date: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    persistent: Optional[bool] = None
    sort_order: Optional[int] = None
    important: Optional[bool] = None

    @model_validator(mode="after")
    def _normalize_dates(self):
        _check_date(self.due_date)
        _check_date(self.start_date)
        _check_date(self.end_date)
        sent = self.model_fields_set
        if self.persistent:
            # 显式转持久：双空
            self.start_date = None
            self.end_date = None
            sent.add("start_date")
            sent.add("end_date")
            return self
        start_sent = "start_date" in sent
        end_sent = "end_date" in sent
        if start_sent and end_sent:
            if self.start_date and self.end_date and self.start_date > self.end_date:
                raise ValueError("start_date 不能晚于 end_date")
        elif start_sent and self.start_date:
            # 只给起点 → 补齐终点为单日
            self.end_date = self.start_date
            sent.add("end_date")
        elif end_sent and self.end_date:
            self.start_date = self.end_date
            sent.add("start_date")
        return self


class BulkCreateRequest(BaseModel):
    source: str = "oneline"
    items: List[TodoIn]


class BulkUpdateRequest(BaseModel):
    """发布端批量修改自己发布的未完成待办：一组 id + 同一份补丁。"""

    model_config = ConfigDict(extra="ignore")
    ids: List[str]
    patch: TodoUpdate


class BulkAckRequest(BaseModel):
    """执行端多选标记知悉：一组待办 id。"""

    model_config = ConfigDict(extra="ignore")
    ids: List[str]


class CompleteRequest(BaseModel):
    """勾父级遇到待办子项时的三选一决定。"""

    model_config = ConfigDict(extra="ignore")
    decision: Optional[str] = None


class ClearRequest(BaseModel):
    """清空某日：服务端软删该日已了结的一级待办，保留未来/持久。"""

    model_config = ConfigDict(extra="ignore")
    date: str

    @model_validator(mode="after")
    def _check(self):
        _check_date(self.date)
        return self


class StatsEventRequest(BaseModel):
    metric: str
    value: int = 1
