"""智能拆分规则引擎。

流水线：规范化 → 分句 → 逐句解析（备注/前缀/分类/数量/价格/标题）→ 结构化输出。
原则：只做"预览 + 可编辑"，绝不自动入库；不引入第三方分词库。
"""

import datetime
import re
from datetime import date as _date
from datetime import timedelta as _timedelta

# ---- 词表 / 正则 ----

# 数量单位白名单：刻意不含 元/块/毛/角，防止价格被当数量
UNIT = r"个|只|斤|两|袋|包|盒|瓶|把|串|张|条|颗|棵|份|箱|桶|件|本|支|提"

# 中文数字映射（含「两」），支持 十/百/千 组合，如 十二、二十、二十三、一百零五
_CN_DIGIT = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}

# 数字：阿拉伯（含小数）或中文数字
_NUM = r"(?:\d+(?:\.\d+)?|[零一二两三四五六七八九十百千]+)"

# 数量：标题 + 数字 + 单位 + 剩余
QTY_RE = re.compile(
    r"^(?P<title>.*?)\s*(?P<qty>" + _NUM + r")\s*(?P<unit>" + UNIT + r")(?P<rest>.*)$"
)

# 价格：标题 + 数字 + 货币单位 + 剩余
PRICE_RE = re.compile(
    r"^(?P<title>.*?)\s*(?P<price>" + _NUM + r")\s*(?P<punit>元|块钱|块|毛|角)(?P<rest>.*)$"
)

# ---- 日期词识别（一句话发布：把句子按日期切成「内容 + 日期」）----

# 相对日期词：词 -> 相对今天的天数（「大后天」必须排在「后天」前，靠长度排序保证）
_REL_DAYS = {
    "大后天": 3, "大前天": -3,
    "后天": 2, "前天": -2,
    "明天": 1, "明日": 1, "明儿": 1, "明晚": 1, "明早": 1, "明晨": 1,
    "今天": 0, "今日": 0, "今儿": 0, "今晚": 0, "今早": 0, "今晨": 0,
    "昨天": -1, "昨日": -1, "昨晚": -1,
}
_REL_ALT = "|".join(sorted(_REL_DAYS, key=len, reverse=True))

# 日期词总表：相对词 / 年月日 / 月日 / 阿拉伯 X号X日 / 中文 X号X日
DATE_RE = re.compile(
    r"(?P<rel>" + _REL_ALT + r")"
    r"|(?P<ymd>(?P<y>\d{4})年(?P<ym>\d{1,2})月(?P<yd>\d{1,2})[日号]?)"
    r"|(?P<md>(?P<m>\d{1,2})月(?P<d>\d{1,2})[日号]?)"
    r"|(?P<dom>(?P<dd>\d{1,2})[日号])"
    r"|(?P<cndom>(?P<cd>[零一二两三四五六七八九十]+)[日号])"
)


def chinese_to_int(text: str):
    """中文数字转整数；无法识别返回 None。

    「三个」→3、「两斤」→2、「十二」→12、「二十」→20、「二十三」→23。
    """
    if not text:
        return None
    total = 0
    section = 0
    number = 0
    for ch in text:
        if ch in _CN_DIGIT:
            number = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
        else:
            return None
    return total + section + number


def to_number(text: str):
    """把阿拉伯数字串或中文数字串转成 int/float；失败返回 None。"""
    if text and re.fullmatch(r"\d+(?:\.\d+)?", text):
        value = float(text)
        return int(value) if value.is_integer() else value
    return chinese_to_int(text)


def _coerce_today(today) -> _date:
    """把 YYYY-MM-DD 字符串 / date / None 统一成 date（None=服务器当天）。"""
    if today is None:
        return datetime.date.today()
    if isinstance(today, _date):
        return today
    return _date.fromisoformat(str(today))


def _roll_day_of_month(today: _date, day: int):
    """把「X号」落到最近的未来（含今天）某月 X 日；31 号遇小月顺延。"""
    if not 1 <= day <= 31:
        return None
    for offset in range(0, 13):
        year = today.year + (today.month - 1 + offset) // 12
        month = (today.month - 1 + offset) % 12 + 1
        try:
            candidate = _date(year, month, day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


def _resolve_date(match, today: _date):
    """把一次日期词匹配解析成具体日期；无法解析返回 None（原样保留为内容）。"""
    if match.group("rel"):
        return today + _timedelta(days=_REL_DAYS[match.group("rel")])
    if match.group("ymd"):
        try:
            return _date(
                int(match.group("y")), int(match.group("ym")), int(match.group("yd"))
            )
        except ValueError:
            return None
    if match.group("md"):
        month, day = int(match.group("m")), int(match.group("d"))
        for year in (today.year, today.year + 1):
            try:
                candidate = _date(year, month, day)
            except ValueError:
                return None
            if candidate >= today:
                return candidate
        return None
    if match.group("dom"):
        return _roll_day_of_month(today, int(match.group("dd")))
    if match.group("cndom"):
        day = chinese_to_int(match.group("cd"))
        return _roll_day_of_month(today, day) if day is not None else None
    return None


def split_date_events(seg: str, today: _date) -> list[tuple[str, object]]:
    """把一句按日期词切成事件序列。

    返回 [("text", 内容), ("date", date), ...]，顺序即原文顺序；日期词本身被剥离。
    「明天去给咱家买点花卷」→ [("date", 明天), ("text", "去给咱家买点花卷")]；
    「买花卷」→ [("text", "买花卷")]。
    """
    seg = (seg or "").strip()
    if not seg:
        return []
    events: list[tuple[str, object]] = []
    last = 0
    for match in DATE_RE.finditer(seg):
        resolved = _resolve_date(match, today)
        if resolved is None:
            continue
        before = seg[last:match.start()].strip(" 　，,。.；;！!？?、:：")
        if before:
            events.append(("text", before))
        events.append(("date", resolved))
        last = match.end()
    tail = seg[last:].strip(" 　，,。.；;！!？?、:：")
    if tail:
        events.append(("text", tail))
    if not events:
        events.append(("text", seg))
    return events

# 备注显式前缀
NOTE_PREFIX_RE = re.compile(r"^(备注|注|说明|提醒)\s*[:：]")

# 模糊词：命中且无动作/无数量才判为备注
FUZZY_WORDS = (
    "可能", "也许", "大概", "后面", "之后", "回头", "再说", "看情况",
    "待定", "不一定", "暂时", "先不", "补上", "要是", "如果",
)

# 口语前缀：剥离但不改分类
PREFIX_RE = re.compile(r"^(回来|顺便|待会(儿)?|回头|记得|别忘了|麻烦|请|去|先|帮我?)\s*")

# 分类关键词
SHOPPING_RE = re.compile(r"买|购|拿|捎|带|要|来点|来|采")
ERRAND_RE = re.compile(r"取|扔|丢|寄|还|送|交|办|修|换|找|预约|回来")

# ---- 自动标签：识别「动词 + 对象（给谁）」----
# 规则（简单可解释）：
#   ① 在标题里找第一个动作词（长的优先，带回来 > 带）；
#   ② 找「给 + 对象」：对象取自家人词表/咱家/三称，或 2-3 字名字（后面紧跟动作词）；
#   ③ 命中对象 → tag="给{对象}{动作}"；只有动作 → tag=动作；都没有 → None（保持分类）。
# 同对象不同动作自然得到不同标签：给姥姥买 / 给姥姥拿。

# 动作词：长的放前面，保证「带回来/带回」优先于「带」
TAG_VERBS = ("带回来", "带回", "买", "拿", "取", "交", "送", "带", "还", "收", "捎")
TAG_VERB_RE = re.compile("|".join(TAG_VERBS))

# 对象词：家人 + 咱家/家里 + 三称；长的放前面，避免「姥爷」被「姥」抢先
TAG_OBJECTS = (
    "姥姥", "姥爷", "奶奶", "爷爷",
    "妈妈", "爸爸", "姐姐", "哥哥", "弟弟", "妹妹",
    "咱家", "家里",
    "我", "你", "您", "他", "她", "俺",
    "姥", "奶", "爷", "妈", "爸", "姐", "哥", "弟", "妹",
)
# 名字分支要求 2-3 字且紧跟动作词，避免「给钱买」把「钱」当对象
TAG_OBJECT_RE = re.compile(
    r"给\s*(?:(?P<pron>[我你他她俺您])?(?P<known>" + "|".join(TAG_OBJECTS) + r")"
    r"|(?P<name>[\u4e00-\u9fa5]{2,3}?)(?=(?:" + "|".join(TAG_VERBS) + r")))"
)

# 对象规范化：口语单字/近义 → 统一标签，便于同义聚合
TAG_OBJECT_CANON = {"姥": "姥姥", "奶": "奶奶", "爷": "爷爷", "家里": "咱家"}

# ---- 分配式识别（用户反馈：'买N个X，[给]A个/咱家B个' 被拆乱）----
# 分配式 = 一个「总数句」（买N个X）+ 若干「分份子句」（[给]对象M个）。
# 与普通列举的区分：分份子句里只有「对象 + 数量 + 单位」，没有独立物品名；
# 普通列举每句都带自己的物品名（买青椒3个，土豆2个）。
ALLOC_OBJECTS = (
    "咱家", "家里", "姥姥", "姥爷", "奶奶", "爷爷",
    "妈妈", "爸爸", "姐姐", "哥哥", "弟弟", "妹妹",
    "老婆", "老公", "儿子", "女儿", "孩子",
    "我", "你", "您", "他", "她", "俺", "咱",
    "姥", "奶", "爷", "妈", "爸", "姐", "哥", "弟", "妹",
)
_ALLOC_OBJ_ALT = "|".join(sorted(ALLOC_OBJECTS, key=len, reverse=True))

# 分配份额：可带「给」/「分给」，对象可带一个旁称修饰（你姥姥 → 姥姥）
ALLOC_SHARE_RE = re.compile(
    r"(?P<give>分给|均给|各给|给)?\s*"
    r"(?P<obj>(?:[我你他她咱俺您](?=(?:" + _ALLOC_OBJ_ALT + r")))?"
    r"(?:" + _ALLOC_OBJ_ALT + r"))\s*"
    r"(?P<qty>" + _NUM + r")\s*(?P<unit>" + UNIT + r")"
)

# 分配式对象归一化：单字/口语 → 统一称呼
ALLOC_OBJECT_CANON = {
    "姥": "姥姥", "奶": "奶奶", "爷": "爷爷",
    "妈": "妈妈", "爸": "爸爸", "姐": "姐姐",
    "哥": "哥哥", "弟": "弟弟", "妹": "妹妹", "家里": "咱家",
}


def _fmt_num(value):
    """整数去 .0，便于拼「姥姥4+咱家4」。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _allocation_shares(seg: str):
    """若整句只由「[给]对象+数量+单位」的分份构成，返回份列表；否则 None。

    - 「你姥姥四个咱家四个」→ [姥姥×4, 咱家×4]
    - 「给你三个给我两个」→ [给你×3, 给我×2]
    - 「土豆2个」→ None（有独立物品名，属普通列举）
    """
    seg = (seg or "").strip()
    if not seg:
        return None
    matches = list(ALLOC_SHARE_RE.finditer(seg))
    if not matches:
        return None
    shares = []
    remainder = seg
    for m in reversed(matches):
        obj = m.group("obj")
        # 去掉旁称「你/我/他…」，如「你姥姥」→「姥姥」
        obj = re.sub(r"^[我你他她咱俺您](?=(?:" + _ALLOC_OBJ_ALT + r"))", "", obj)
        shares.append(
            {
                "object": ALLOC_OBJECT_CANON.get(obj, obj),
                "give": m.group("give"),
                "qty": to_number(m.group("qty")),
                "unit": m.group("unit"),
            }
        )
        remainder = remainder[: m.start()] + remainder[m.end():]
    # 去掉分隔符/「给」等连接词后仍有剩余 → 不是纯分份，不合并
    leftover = re.sub(r"[，,。.；;！!？?、:：\s]+", "", remainder)
    leftover = re.sub(r"^(?:分给|均给|各给|给|都|各|均)+", "", leftover)
    if leftover:
        return None
    shares.reverse()
    return shares


def _allocation_head_item(parsed: dict):
    """分配式「总数句」的物品名；不满足（无购物动词/无物品）返回 None。"""
    if parsed.get("is_note") or parsed.get("category") != "shopping":
        return None
    title = parsed.get("title") or ""
    m = SHOPPING_RE.search(title)
    if not m:
        return None
    item = title[m.end():].strip(" 　，。；;！!？?、:：")
    item = re.sub(r"^(?:点|些|个)", "", item)
    return item or None


def _share_label(share: dict) -> str:
    """分份展示：姥姥4 / 给你3 / 咱家4。"""
    return "{}{}{}".format(
        share.get("give") or "", share["object"], _fmt_num(share["qty"])
    )


def _merge_allocation(head: dict, flat: list, i: int):
    """把「总数句 + 连续分份子句」合并成一条；不构成分配式返回 None。

    返回 (merged_item, 消费的子句数)。约束：分份单位一致、总数不小于份数之和
    （总数句可无数量，用份数之和当总数）。
    """
    item = _allocation_head_item(head)
    if item is None:
        return None
    day = flat[i][1]
    shares = []
    consumed = 1
    j = i + 1
    while j < len(flat):
        text, jday = flat[j]
        if jday != day:  # 跨天的分份不合并，避免张冠李戴
            break
        part = _allocation_shares(text)
        if not part:
            break
        shares.extend(part)
        consumed += 1
        j += 1
    if not shares:
        return None
    units = {s["unit"] for s in shares}
    if len(units) != 1:
        return None
    unit = units.pop()
    head_qty = head.get("qty")
    if head.get("unit") is not None and head.get("unit") != unit:
        return None
    share_total = sum(s["qty"] for s in shares)
    if head_qty is not None and head_qty < share_total:
        return None  # 总数比分出去的还少，判为误识别，保持原样
    total = head_qty if head_qty is not None else share_total
    note = "+".join(_share_label(s) for s in shares)
    merged = dict(head)
    merged["note"] = note
    merged["raw"] = "，".join(flat[k][0] for k in range(i, i + consumed))
    merged["qty"] = total
    merged["unit"] = unit
    merged["reason"] = (
        (head.get("reason") or "")
        + "；识别为分配式：总数{}，分给{}，合并为一条".format(_fmt_num(total), note)
    ).strip("；")
    merged["confidence"] = max(head.get("confidence") or 0.0, 0.85)
    return merged, consumed

# 全角 → 半角（数字/字母/全角空格），标点保留原样参与分句
_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)


def normalize(text: str) -> str:
    """规范化：全角数字/字母转半角，制表/全角空格压成半角空格，去首尾空白。

    注意：不压缩换行，分句仍需要它。
    """
    text = (text or "").translate(_FULLWIDTH)
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def split_sentences(text: str) -> list[str]:
    """中文分句：先保护小数/千分位，再按标点/换行拆分，最后还原。

    - 「3.5个」「1,000元」不会被拆坏；
    - 顿号「、」也拆（"葱、姜、蒜"成三条）。
    """
    text = normalize(text)
    # 1) 保护数字中间的点/逗号（小数、千分位），分别用不同占位符以原样还原
    text = re.sub(r"(?<=\d)\.(?=\d)", "\x00", text)
    text = re.sub(r"(?<=\d),(?=\d)", "\x01", text)
    # 2) 拆分
    segs = re.split(r"[，,。.；;！!？?\n\r、]+", text)
    # 3) 还原
    return [
        s.replace("\x00", ".").replace("\x01", ",").strip()
        for s in segs
        if s.strip()
    ]


def _is_note(seg: str) -> bool:
    """备注判定：显式前缀，或「含模糊词 + 无动作 + 无数量」。"""
    if NOTE_PREFIX_RE.match(seg):
        return True
    if not any(w in seg for w in FUZZY_WORDS):
        return False
    has_action = bool(SHOPPING_RE.search(seg) or ERRAND_RE.search(seg))
    has_qty = QTY_RE.match(seg) is not None
    return not has_action and not has_qty


def _strip_prefix(title: str) -> tuple[str, bool]:
    """循环剥离口语前缀；若剥完为空则保留原句。"""
    stripped = False
    while True:
        m = PREFIX_RE.match(title)
        if not m:
            break
        candidate = title[m.end():].strip()
        if not candidate:
            break
        title = candidate
        stripped = True
    return title, stripped


def extract_tag(title: str):
    """按「动作 + 对象」生成标签；无对象退化为动作；无动作返回 None。

    - 给姥姥买 → 给姥姥买；给姥姥拿 → 给姥姥拿（同对象不同动作分开）；
    - 给咱家买 → 给咱家买；给你姥买 → 给姥姥买；给小明买 → 给小明买；
    - 回来买花卷 → 买；取快递 → 取；娃娃菜 → None（保持分类）。
    """
    text = (title or "").strip()
    if not text:
        return None
    m = TAG_VERB_RE.search(text)
    if not m:
        return None
    verb = m.group(0)
    obj = None
    om = TAG_OBJECT_RE.search(text)
    if om:
        obj = om.group("known") or om.group("name")
        if obj:
            obj = TAG_OBJECT_CANON.get(obj, obj)
    if obj:
        return "给" + obj + verb
    return verb


def parse_sentence(seg: str) -> dict:
    """解析单句，返回结构化结果（不落库）。"""
    raw = (seg or "").strip()

    # (a) 备注识别（优先，先于前缀清理/数量提取）
    if _is_note(raw):
        return {
            "raw": raw,
            "title": raw,
            "qty": None,
            "unit": None,
            "price": None,
            "category": "note",
            "is_note": True,
            "tag": None,
            "note": None,
            "confidence": 0.85,
            "reason": "命中备注模糊词且无明确动作/数量",
        }

    reasons: list[str] = []
    title = raw

    # (b) 前缀清理
    title, stripped = _strip_prefix(title)
    if stripped:
        reasons.append("剥离口语前缀")

    # (d) 数量提取（在价格之前，避免 15元 被误当数量）
    qty = None
    unit = None
    m = QTY_RE.match(title)
    if m:
        qty = to_number(m.group("qty"))
        unit = m.group("unit")
        title = (m.group("title") + m.group("rest")).strip()
        reasons.append(f"命中数量单位「{unit}」")

    # (e) 价格提取（价格不写 qty；单位明确为钱）
    price = None
    m = PRICE_RE.match(title)
    if m:
        price = to_number(m.group("price"))
        title = (m.group("title") + m.group("rest")).strip()
        reasons.append("命中价格")

    # (f) 清理标题
    title = title.strip(" 　，。；;！!？?、:：")
    if not title:
        title = raw.strip()  # 标题为空但原文有内容 → 退回原文，避免空任务

    # (c) 分类
    if SHOPPING_RE.search(title):
        category = "shopping"
        reasons.append("命中购物倾向")
    elif ERRAND_RE.search(title):
        category = "errand"
        reasons.append("命中事务倾向")
    elif qty is not None:
        category = "shopping"
        reasons.append("含数量单位，按购物倾向")
    else:
        category = "other"

    # (h) 自动标签（动词 + 对象）
    tag = extract_tag(title)
    if tag:
        reasons.append(f"标签「{tag}」")

    # (g) 置信度
    if qty is not None or price is not None:
        confidence = 0.9
    elif category != "other":
        confidence = 0.8
    else:
        confidence = 0.6

    return {
        "raw": raw,
        "title": title,
        "qty": qty,
        "unit": unit,
        "price": price,
        "category": category,
        "is_note": False,
        "tag": tag,
        "note": None,
        "confidence": confidence,
        "reason": "；".join(reasons) if reasons else "未命中规则，按原文保留",
    }


def parse_text(text: str, today=None) -> dict:
    """拆分整段文本，返回 {"items": [...], "notes": [...]}。

    日期识别：句中的日期词（今天/明天/后天/X号/X月X号…）会成为切分点，
    其后的内容归到该日期；没有日期词的内容沿用当前日期，初始为今天。
    每条 item 带 start_date/end_date（YYYY-MM-DD），供发布端直接落库。

    分配式合并：先按日期把子句摊平，再把「买N个X，[给]A个/咱家B个」这一类
    总数句 + 连续分份子句合成一条（qty=总数，note=分配），避免把总数重复计成多条。
    """
    base_day = _coerce_today(today)
    current = base_day
    # 先摊平成 (子句原文, 归属日期)，便于跨子句识别分配式
    flat: list[tuple[str, _date]] = []
    for seg in split_sentences(text):
        for kind, value in split_date_events(seg, base_day):
            if kind == "date":
                current = value
                continue
            flat.append((value, current))

    items: list[dict] = []
    notes: list[str] = []
    i = 0
    while i < len(flat):
        value, day = flat[i]
        parsed = parse_sentence(value)
        if parsed["is_note"]:
            notes.append(parsed["raw"])
            i += 1
            continue
        merged = _merge_allocation(parsed, flat, i)
        if merged is not None:
            item, consumed = merged
            item["start_date"] = day.isoformat()
            item["end_date"] = day.isoformat()
            items.append(item)
            i += consumed
            continue
        parsed["start_date"] = day.isoformat()
        parsed["end_date"] = day.isoformat()
        items.append(parsed)
        i += 1
    return {"items": items, "notes": notes}
