(function () {
  "use strict";

  const KEYS = {
    api: "ft_api",
    token: "ft_token",
    device: "ft_device",
    role: "ft_role",
    family: "ft_family",
    deviceName: "ft_device_name",
    defaultView: "ft_default_view",
  };

  const CATEGORY_LABEL = { shopping: "购物", errand: "事务", other: "其他" };
  const ROLE_LABEL = { publisher: "发布端", executor: "执行端" };
  const STATUS_LABEL = {
    pending: "待办",
    completed: "已完成",
    ignored: "已忽略",
    cancelled: "已取消",
    unfinished: "未完成",
  };

  const state = {
    api: "",
    token: "",
    me: null,
    todos: [],
    parsed: [],
    notes: [],
    batch: [],
    viewMode: "today", // today | date | future | overdue | done | all
    viewDate: todayIso(),
    todoDates: new Set(), // 今天/未来有未完成待办的日期，迷你月历红点数据
    persistentOpen: false,
    persistent: [],
    published: [],
    publishedOpen: true,
    editingPublished: new Set(), // 发布端「编辑我发布的」中展开编辑器的待办 id
    editSelected: new Set(), // 发布端批量修改已勾选的待办 id
    ackSelecting: false, // 执行端多选知悉模式
    ackSelected: new Set(), // 执行端多选知悉勾选的待办 id
    doneOpen: true,
    settledKey: "",
    expanded: new Set(),
    addingSub: null,
    decisionId: null,
    ws: null,
    wsTimer: null,
    retry: 0,
    refreshTimer: null,
  };

  // ---- 日期辅助（本地日历日，YYYY-MM-DD） ----

  function pad2(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function ymd(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function todayIso() {
    return ymd(new Date());
  }

  function shiftIso(iso, days) {
    const parts = iso.split("-").map(Number);
    const d = new Date(parts[0], parts[1] - 1, parts[2]);
    d.setDate(d.getDate() + days);
    return ymd(d);
  }

  function dateLabel(iso) {
    const today = todayIso();
    if (iso === today) return "今天";
    if (iso === shiftIso(today, 1)) return "明天";
    if (iso === shiftIso(today, -1)) return "昨天";
    const parts = iso.split("-").map(Number);
    return parts[1] + "月" + parts[2] + "日";
  }

  // ---- 自绘迷你月历（替代原生 date picker；今天/未来有未完成待办的日子画红点） ----

  // 每个日历面板当前显示的月份（YYYY-MM），互不影响
  const CAL_MONTHS = {};

  // 面板配置：如何取当前选中日、点选后做什么
  const CALENDARS = {
    execCalendar: {
      selected: function () {
        return state.viewMode === "date" ? state.viewDate : "";
      },
      pick: function (iso) {
        state.viewMode = "date";
        state.viewDate = iso;
        closeCalendar("execCalendar");
        refresh();
      },
    },
    pubCalendar: {
      selected: function () {
        return $("pubDateCustom").value || todayIso();
      },
      pick: function (iso) {
        $("pubDateCustom").value = iso;
        syncPublishDateUI();
        closeCalendar("pubCalendar");
      },
    },
  };

  function monthKey(iso) {
    return (iso || todayIso()).slice(0, 7);
  }

  function shiftMonthKey(ym, delta) {
    const p = ym.split("-").map(Number);
    const d = new Date(p[0], p[1] - 1 + delta, 1);
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1);
  }

  function calendarMonth(id) {
    if (!CAL_MONTHS[id]) CAL_MONTHS[id] = monthKey(todayIso());
    return CAL_MONTHS[id];
  }

  function openCalendar(id) {
    const cfg = CALENDARS[id];
    if (!cfg) return;
    CAL_MONTHS[id] = monthKey(cfg.selected() || todayIso());
    $(id).classList.remove("hidden");
    renderCalendar(id);
    loadTodoDates(); // 打开时拉最新红点，失败静默
  }

  function closeCalendar(id) {
    const panel = $(id);
    if (panel) panel.classList.add("hidden");
  }

  function closeAllCalendars() {
    Object.keys(CALENDARS).forEach(closeCalendar);
  }

  function isCalendarOpen(id) {
    return !$(id).classList.contains("hidden");
  }

  function shiftCalendarMonth(id, delta) {
    CAL_MONTHS[id] = shiftMonthKey(calendarMonth(id), delta);
    renderCalendar(id);
  }

  function renderCalendar(id) {
    const panel = $(id);
    if (!panel || panel.classList.contains("hidden")) return;
    const ym = calendarMonth(id);
    const parts = ym.split("-").map(Number);
    const year = parts[0];
    const month = parts[1];
    const selected = CALENDARS[id].selected();
    const today = todayIso();
    const firstWeekday = new Date(year, month - 1, 1).getDay(); // 0=周日
    const total = new Date(year, month, 0).getDate();

    panel.innerHTML = "";
    panel.appendChild(
      el("div", { className: "cal-head" }, [
        el("button", {
          className: "cal-nav",
          text: "‹",
          attrs: { type: "button", "data-cal-nav": "-1", "data-cal": id, "aria-label": "上个月" },
        }),
        el("span", { className: "cal-title", text: year + "年" + month + "月" }),
        el("button", {
          className: "cal-nav",
          text: "›",
          attrs: { type: "button", "data-cal-nav": "1", "data-cal": id, "aria-label": "下个月" },
        }),
      ])
    );

    const week = el("div", { className: "cal-week" });
    ["日", "一", "二", "三", "四", "五", "六"].forEach(function (w) {
      week.appendChild(el("span", { text: w }));
    });
    panel.appendChild(week);

    const grid = el("div", { className: "cal-grid" });
    for (let i = 0; i < firstWeekday; i++) {
      grid.appendChild(el("span", { className: "cal-blank" }));
    }
    for (let day = 1; day <= total; day++) {
      const iso = year + "-" + pad2(month) + "-" + pad2(day);
      const cls = ["cal-day"];
      if (iso === today) cls.push("today");
      if (iso === selected) cls.push("selected");
      const btn = el("button", {
        className: cls.join(" "),
        text: String(day),
        attrs: { type: "button", "data-date": iso, "data-cal": id, "aria-label": iso },
      });
      if (state.todoDates.has(iso)) {
        btn.appendChild(el("span", { className: "cal-dot" }));
      }
      grid.appendChild(btn);
    }
    panel.appendChild(grid);
    panel.appendChild(
      el("div", { className: "cal-foot" }, [
        el("button", {
          className: "cal-today",
          text: "回到今天",
          attrs: { type: "button", "data-cal-today": id },
        }),
      ])
    );
  }

  function onCalendarClick(e) {
    if (!e.target.closest) return;
    const nav = e.target.closest("[data-cal-nav]");
    if (nav) {
      shiftCalendarMonth(nav.getAttribute("data-cal"), Number(nav.getAttribute("data-cal-nav")));
      return;
    }
    const todayBtn = e.target.closest("[data-cal-today]");
    if (todayBtn) {
      CALENDARS[todayBtn.getAttribute("data-cal-today")].pick(todayIso());
      return;
    }
    const day = e.target.closest("[data-date]");
    if (day) CALENDARS[day.getAttribute("data-cal")].pick(day.getAttribute("data-date"));
  }

  async function loadTodoDates() {
    try {
      const data = await api("/api/todo-dates");
      state.todoDates = new Set((data && data.dates) || []);
    } catch (e) {
      // 红点非关键路径：静默失败，不打断待办列表
    }
    renderCalendar("execCalendar");
    renderCalendar("pubCalendar");
  }

  function isPublisher() {
    return !!(state.me && state.me.role === "publisher");
  }

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, opts, children) {
    const node = document.createElement(tag);
    opts = opts || {};
    if (opts.className) node.className = opts.className;
    if (opts.text != null) node.textContent = opts.text;
    if (opts.attrs) {
      Object.keys(opts.attrs).forEach(function (k) {
        node.setAttribute(k, opts.attrs[k]);
      });
    }
    if (opts.on) {
      Object.keys(opts.on).forEach(function (k) {
        node.addEventListener(k, opts.on[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c == null || c === false) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function toast(msg, isError) {
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast" + (isError ? " error" : "");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function () {
      t.className = "toast hidden";
    }, 2600);
  }

  function setConn(ok, text) {
    const c = $("conn");
    c.className = "conn " + (ok ? "on" : "off");
    c.textContent = text || (ok ? "实时已连接" : "实时未连接");
  }

  function defaultApi() {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("api");
    if (q) return q.replace(/\/+$/, "");
    const saved = localStorage.getItem(KEYS.api);
    if (saved) return saved.replace(/\/+$/, "");
    if (window.location.protocol === "http:" || window.location.protocol === "https:") {
      const port = window.location.port;
      if (!port || port === "80" || port === "443" || port === "8000") {
        // 同源：若入口路径在 /todo 下，API 指向 origin + /todo（nginx 反代路径）
        const m = window.location.pathname.match(/^\/(todo)\/?/);
        return m ? (window.location.origin + "/" + m[1]) : window.location.origin;
      }
      return window.location.protocol + "//" + window.location.hostname + ":8000";
    }
    return "http://localhost:8000";
  }

  async function api(path, opts) {
    opts = opts || {};
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    if (state.token) headers["Authorization"] = "Bearer " + state.token;
    const res = await fetch(state.api + path, Object.assign({}, opts, { headers: headers }));
    if (res.status === 401) {
      clearAuth();
      showJoin("登录已失效，请重新加入");
      throw new Error("未授权");
    }
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (e) {
      data = text;
    }
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : "请求失败 " + res.status;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function saveAuth(payload, deviceName) {
    state.token = payload.device_token;
    localStorage.setItem(KEYS.token, payload.device_token);
    localStorage.setItem(KEYS.device, payload.device_id);
    localStorage.setItem(KEYS.role, payload.role);
    localStorage.setItem(KEYS.family, payload.family_id);
    if (deviceName) localStorage.setItem(KEYS.deviceName, deviceName);
  }

  function clearAuth() {
    state.token = "";
    state.me = null;
    state.todos = [];
    [KEYS.token, KEYS.device, KEYS.role, KEYS.family, KEYS.deviceName].forEach(function (k) {
      localStorage.removeItem(k);
    });
    if (state.ws) {
      try {
        state.ws.close();
      } catch (e) {}
      state.ws = null;
    }
    clearTimeout(state.wsTimer);
    $("logoutBtn").classList.add("hidden");
  }

  function showJoin(msg) {
    $("joinView").classList.remove("hidden");
    $("appViews").classList.add("hidden");
    $("logoutBtn").classList.add("hidden");
    $("joinApi").value = state.api;
    $("joinError").textContent = msg || "";
    updateMeta();
  }

  function defaultTab() {
    const saved = localStorage.getItem(KEYS.defaultView);
    if (saved === "publish" || saved === "execute") return saved;
    return state.me && state.me.role === "publisher" ? "publish" : "execute";
  }

  function enterApp() {
    $("joinView").classList.add("hidden");
    $("appViews").classList.remove("hidden");
    $("logoutBtn").classList.remove("hidden");
    updateMeta();
    switchTab(defaultTab());
    connectWS();
    refresh();
    maybePromptName();
  }

  async function loadMe() {
    state.me = await api("/api/me");
  }

  function updateMeta() {
    const m = state.me;
    if (!m) {
      $("meta").textContent = "";
      return;
    }
    const role = ROLE_LABEL[m.role] || m.role || "成员";
    $("meta").textContent =
      (m.family_name || "我的家") + " · " + (m.device_name || "本机") + " · " + role;
    const nameInput = $("settingsDeviceName");
    if (nameInput && document.activeElement !== nameInput) {
      nameInput.value = m.device_name || "";
    }
  }

  // ---- 本设备标识名：首次进入提示 + 设置里可改 ----

  function namePromptKey() {
    return "ft_name_ok_" + ((state.me && state.me.device_id) || "");
  }

  function maybePromptName() {
    if (!state.me || state.me.device_name) return;
    if (localStorage.getItem(namePromptKey())) return;
    $("nameModalInput").value = "";
    $("nameModal").classList.remove("hidden");
  }

  function closeNameModal() {
    $("nameModal").classList.add("hidden");
  }

  async function renameDevice(name) {
    name = (name || "").trim();
    if (!name) {
      toast("请填写设备名", true);
      return false;
    }
    try {
      const me = await api("/api/me", {
        method: "PATCH",
        body: JSON.stringify({ device_name: name }),
      });
      state.me = me;
      localStorage.setItem(KEYS.deviceName, name);
      updateMeta();
      toast("设备名已保存");
      return true;
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
      return false;
    }
  }

  async function doJoin(familyToken, deviceName, role) {
    $("joinError").textContent = "";
    $("joinBtn").disabled = true;
    try {
      const data = await api("/api/auth/join", {
        method: "POST",
        body: JSON.stringify({
          family_token: familyToken,
          device_name: deviceName || null,
          role: role || null,
        }),
      });
      saveAuth(data, deviceName);
      const url = new URL(window.location.href);
      url.searchParams.delete("t");
      window.history.replaceState(null, "", url.pathname + url.search + url.hash);
      await loadMe();
      enterApp();
      toast("已加入家庭");
    } catch (e) {
      if (e.message !== "未授权") showJoin(e.message || "加入失败");
    } finally {
      $("joinBtn").disabled = false;
    }
  }

  async function bootstrap() {
    state.api = defaultApi();
    state.token = localStorage.getItem(KEYS.token) || "";
    const params = new URLSearchParams(window.location.search);
    const t = params.get("t");
    if (t) {
      $("joinApi").value = state.api;
      $("joinToken").value = t;
      $("joinDevice").value = localStorage.getItem(KEYS.deviceName) || "";
      $("joinRole").value = localStorage.getItem(KEYS.role) || "executor";
      await doJoin(t, $("joinDevice").value, $("joinRole").value);
      return;
    }
    if (!state.token) {
      showJoin("");
      return;
    }
    try {
      await loadMe();
      enterApp();
    } catch (e) {
      if (state.token) showJoin("无法连接服务器，请检查服务器地址");
    }
  }

  function switchTab(which) {
    const pub = which === "publish";
    $("tabPublish").className = "tab" + (pub ? " active" : "");
    $("tabExecute").className = "tab" + (pub ? "" : " active");
    $("brand").textContent = "待会儿办";
    $("publishView").classList.toggle("hidden", !pub);
    $("executeView").classList.toggle("hidden", pub);
    closeQuickMenu();
    closeAllCalendars();
  }

  // 执行端底部 [+]->二级菜单 / 收起的面板（只做收纳，不改业务）
  const EXEC_PANELS = ["quickAddPanel", "datePanel"];

  function toggleSettings(force) {
    const panel = $("settingsPanel");
    const show = force === undefined ? panel.classList.contains("hidden") : force;
    panel.classList.toggle("hidden", !show);
  }

  function closeQuickMenu() {
    $("quickMenu").classList.add("hidden");
    $("fabBtn").classList.remove("open");
  }

  function toggleExecPanel(id) {
    const target = $(id);
    const willShow = target.classList.contains("hidden");
    EXEC_PANELS.forEach(function (p) {
      $(p).classList.add("hidden");
    });
    if (willShow) target.classList.remove("hidden");
  }

  // ---- 发布端：智能拆分 ----

  async function doParse() {
    const text = $("parseText").value.trim();
    if (!text) {
      toast("请输入内容", true);
      return;
    }
    $("parseBtn").disabled = true;
    $("parseHint").textContent = "拆分中…";
    try {
      const data = await api("/api/parse", {
        method: "POST",
        body: JSON.stringify({ text: text, today: todayIso() }),
      });
      state.parsed = (data.items || []).map(function (it, i) {
        return {
          _key: "p" + i + "_" + Date.now(),
          raw: it.raw,
          title: it.title || "",
          qty: it.qty,
          unit: it.unit,
          price: it.price,
          category: it.category || "other",
          tag: it.tag || "",
          note: it.note || "",
          start_date: it.start_date || todayIso(),
          end_date: it.end_date || it.start_date || todayIso(),
          reason: it.reason || "",
          important: false,
        };
      });
      state.notes = data.notes || [];
      $("parseHint").textContent = "拆出 " + state.parsed.length + " 条，未成条 " + state.notes.length + " 条";
      renderPreview();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
      $("parseHint").textContent = "";
    } finally {
      $("parseBtn").disabled = false;
    }
  }

  // 标签 chip：有标签才显示（标题旁常驻，详情默认收起）
  function setTagChip(chip, text) {
    const t = (text || "").trim();
    chip.textContent = t;
    chip.classList.toggle("hidden", !t);
  }

  function renderPreview() {
    const list = $("previewList");
    list.textContent = "";
    $("previewCard").classList.toggle("hidden", state.parsed.length === 0);

    const notesList = $("notesList");
    notesList.textContent = "";
    state.notes.forEach(function (n) {
      notesList.appendChild(el("li", { text: n }));
    });
    $("notesCard").classList.toggle("hidden", state.notes.length === 0);

    state.parsed.forEach(function (item) {
      // 标题旁的标签 chip：有标签才显示；不展开详情时只留标题+标签，紧凑不占竖向空间
      const tagChip = el("span", {
        className: "tag family-tag title-tag" + (item.tag ? "" : " hidden"),
        text: item.tag || "",
      });
      const title = el("input", {
        className: "preview-title",
        attrs: { placeholder: "标题", value: item.title },
        on: {
          input: function (e) {
            item.title = e.target.value;
          },
        },
      });
      const qty = el("input", {
        attrs: { type: "number", step: "any", placeholder: "数量", value: item.qty == null ? "" : item.qty },
        on: {
          input: function (e) {
            item.qty = e.target.value === "" ? null : Number(e.target.value);
          },
        },
      });
      const unit = el("input", {
        attrs: { placeholder: "单位", value: item.unit || "" },
        on: {
          input: function (e) {
            item.unit = e.target.value || null;
          },
        },
      });
      const price = el("input", {
        attrs: { type: "number", step: "any", placeholder: "金额", value: item.price == null ? "" : item.price },
        on: {
          input: function (e) {
            item.price = e.target.value === "" ? null : Number(e.target.value);
          },
        },
      });
      const cat = el("select", {
        on: {
          change: function (e) {
            item.category = e.target.value;
          },
        },
      });
      Object.keys(CATEGORY_LABEL).forEach(function (key) {
        const opt = el("option", { text: CATEGORY_LABEL[key], attrs: { value: key } });
        if (item.category === key) opt.selected = true;
        cat.appendChild(opt);
      });
      const tag = el("input", {
        attrs: { placeholder: "标签", value: item.tag || "" },
        on: {
          input: function (e) {
            item.tag = e.target.value;
            setTagChip(tagChip, item.tag);
          },
        },
      });
      const star = el("button", {
        className: "star-btn" + (item.important ? " on" : ""),
        text: "★",
        attrs: { type: "button", title: item.important ? "取消重要" : "标为重要" },
        on: {
          click: function (e) {
            e.preventDefault();
            item.important = !item.important;
            renderPreview();
          },
        },
      });
      const del = el("button", {
        className: "del",
        text: "×",
        attrs: { title: "删除该条" },
        on: {
          click: function () {
            state.parsed = state.parsed.filter(function (x) {
              return x !== item;
            });
            renderPreview();
          },
        },
      });
      const dateHint = el("span", {
        className: "date-hint",
        text: "📅 " + dateLabel(item.start_date),
      });
      const datePick = el("input", {
        attrs: {
          type: "date",
          value: item.start_date || todayIso(),
          title: "这条待办的日期（默认按句识别）",
        },
        on: {
          change: function (e) {
            item.start_date = e.target.value || todayIso();
            item.end_date = item.start_date;
            dateHint.textContent = "📅 " + dateLabel(item.start_date);
          },
        },
      });
      const grid = el("div", { className: "grid" }, [qty, unit, price, cat, tag]);
      const meta = el("div", { className: "reason" }, [
        dateHint,
        datePick,
        el("span", {
          text:
            " " +
            (item.note ? "分配：" + item.note + "　·　" : "") +
            item.raw +
            "　·　" +
            item.reason,
        }),
      ]);
      const head = el("div", { className: "preview-head" }, [title, tagChip, star, del]);
      const details = el("details", { className: "item-details" }, [
        el("summary", { className: "details-summary", text: "详细设置" }),
        grid,
        meta,
      ]);
      list.appendChild(el("div", { className: "preview-row" }, [head, details]));
    });
  }

  function publishMode() {
    const active = document.querySelector("#pubDateSeg .chip.active");
    return active ? active.getAttribute("data-mode") : "today";
  }

  // 发布端日期分段 → 统一映射为 start/end/persistent（2.5 最简方案）
  function currentPublishWindow() {
    const mode = publishMode();
    if (mode === "persistent") return { persistent: true };
    if (mode === "tomorrow") {
      const d = shiftIso(todayIso(), 1);
      return { start_date: d, end_date: d };
    }
    if (mode === "range") {
      let s = $("pubStart").value || todayIso();
      let e = $("pubEnd").value || s;
      if (s > e) {
        const t = s;
        s = e;
        e = t;
      }
      return { start_date: s, end_date: e };
    }
    if (mode === "custom") {
      const d = $("pubDateCustom").value || todayIso();
      return { start_date: d, end_date: d };
    }
    const t = todayIso();
    return { start_date: t, end_date: t };
  }

  function syncPublishDateUI() {
    const mode = publishMode();
    const custom = $("pubDateCustom").value || todayIso();
    $("pubDateBtn").textContent = "📅 " + dateLabel(custom);
    $("pubDateBtn").classList.toggle("hidden", mode !== "custom");
    $("pubRangeWrap").classList.toggle("hidden", mode !== "range");
    $("pubPersistHint").classList.toggle("hidden", mode !== "persistent");
    if (mode !== "custom") closeCalendar("pubCalendar");
  }

  // 把一条解析结果拼成发布用的 payload：overrideAll 时整体用 win，
  // 否则用该条按句识别的日期（没有则今天）。
  function itemWithWindow(it, win, overrideAll) {
    const base = {
      title: (it.title || "").trim(),
      qty: it.qty,
      unit: it.unit,
      price: it.price,
      category: it.category,
      tag: (it.tag || "").trim() || null,
      note: (it.note || "").trim() || null,
      important: !!it.important,
    };
    if (overrideAll) return Object.assign(base, win);
    base.start_date = it.start_date || todayIso();
    base.end_date = it.end_date || base.start_date;
    return base;
  }

  async function doPublish() {
    const win = currentPublishWindow();
    // 默认「今天」：句中有日期词的按句归日，没日期的落到今天；
    // 主动选了明天/某天/区间/持久 → 整体覆盖为所选日期。
    const overrideAll = publishMode() !== "today";
    const items = state.parsed
      .filter(function (it) {
        return (it.title || "").trim();
      })
      .map(function (it) {
        return itemWithWindow(it, win, overrideAll);
      });
    if (!items.length) {
      toast("没有可发布的条目", true);
      return;
    }
    $("publishBtn").disabled = true;
    try {
      await api("/api/todos/bulk", {
        method: "POST",
        body: JSON.stringify({ source: "oneline", items: items }),
      });
      state.parsed = [];
      state.notes = [];
      $("parseText").value = "";
      $("parseHint").textContent = "";
      renderPreview();
      toast("已发布 " + items.length + " 条");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    } finally {
      $("publishBtn").disabled = false;
    }
  }

  async function addManual() {
    const title = $("mTitle").value.trim();
    if (!title) {
      toast("请填写标题", true);
      return;
    }
    const payload = Object.assign(
      {
        title: title,
        qty: $("mQty").value === "" ? null : Number($("mQty").value),
        unit: $("mUnit").value || null,
        price: $("mPrice").value === "" ? null : Number($("mPrice").value),
        category: $("mCategory").value,
        important: $("mImportant").checked,
      },
      currentPublishWindow()
    );
    try {
      await api("/api/todos", { method: "POST", body: JSON.stringify(payload) });
      $("mTitle").value = "";
      $("mQty").value = "";
      $("mUnit").value = "";
      $("mPrice").value = "";
      $("mCategory").value = "other";
      $("mImportant").checked = false;
      toast("已添加");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  // ---- 发布端：列表式批量添加 ----

  function renderBatch() {
    const list = $("batchList");
    list.textContent = "";
    state.batch.forEach(function (item) {
      const cb = el("input", { attrs: { type: "checkbox" } });
      cb.checked = !!item.checked;
      cb.addEventListener("change", function () {
        item.checked = cb.checked;
        updateBatchCount();
      });
      // 标题旁标签 chip（有才显示）；备注/数量等详情默认收起
      const tagChip = el("span", {
        className: "tag family-tag title-tag" + (item.tag ? "" : " hidden"),
        text: item.tag || "",
      });
      const title = el("input", {
        attrs: { placeholder: "标题", value: item.title },
        on: {
          input: function (e) {
            item.title = e.target.value;
            updateBatchCount();
          },
        },
      });
      const metaText =
        (item.note ? "分配：" + item.note + "  " : "") + qtyPriceText(item);
      const meta = el("span", { className: "hint", text: metaText });
      const star = el("button", {
        className: "star-btn" + (item.important ? " on" : ""),
        text: "★",
        attrs: { type: "button", title: item.important ? "取消重要" : "标为重要" },
        on: {
          click: function (e) {
            e.preventDefault();
            item.important = !item.important;
            renderBatch();
          },
        },
      });
      const head = el("div", { className: "batch-head" }, [title, tagChip, star]);
      const details = metaText.trim()
        ? el("details", { className: "item-details" }, [
            el("summary", { className: "details-summary", text: "详细设置" }),
            meta,
          ])
        : null;
      const body = el("div", { className: "batch-body" }, details ? [head, details] : [head]);
      list.appendChild(el("div", { className: "batch-row" }, [cb, body]));
    });
    updateBatchCount();
  }

  function batchChecked() {
    return state.batch.filter(function (it) {
      return it.checked && (it.title || "").trim();
    });
  }

  function updateBatchCount() {
    const n = batchChecked().length;
    $("batchAddBtn").textContent = n ? "添加勾选 (" + n + ")" : "添加勾选";
  }

  async function generateBatch() {
    const text = $("batchText").value.trim();
    if (!text) {
      toast("请输入内容", true);
      return;
    }
    $("batchGenBtn").disabled = true;
    try {
      const data = await api("/api/parse", {
        method: "POST",
        body: JSON.stringify({ text: text, today: todayIso() }),
      });
      state.batch = (data.items || []).map(function (it, i) {
        return {
          _key: "b" + i + "_" + Date.now(),
          title: it.title || "",
          qty: it.qty,
          unit: it.unit,
          price: it.price,
          category: it.category || "other",
          tag: it.tag || "",
          note: it.note || "",
          start_date: it.start_date || todayIso(),
          end_date: it.end_date || it.start_date || todayIso(),
          checked: true,
          important: false,
        };
      });
      renderBatch();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    } finally {
      $("batchGenBtn").disabled = false;
    }
  }

  function toggleBatchAll() {
    const allOn = state.batch.length > 0 && state.batch.every(function (i) {
      return i.checked;
    });
    state.batch.forEach(function (i) {
      i.checked = !allOn;
    });
    renderBatch();
  }

  async function addBatch() {
    const win = currentPublishWindow();
    const overrideAll = publishMode() !== "today";
    const items = batchChecked().map(function (it) {
      return itemWithWindow(it, win, overrideAll);
    });
    if (!items.length) {
      toast("没有勾选的条目", true);
      return;
    }
    try {
      await api("/api/todos/bulk", {
        method: "POST",
        body: JSON.stringify({ source: "oneline", items: items }),
      });
      state.batch = [];
      $("batchText").value = "";
      renderBatch();
      toast("已添加 " + items.length + " 条");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  // ---- 执行端：列表 / 勾选 / 子待办 ----

  async function addExec() {
    const title = $("execTitle").value.trim();
    if (!title) {
      toast("请填写标题", true);
      return;
    }
    const payload = {
      title: title,
      qty: $("execQty").value === "" ? null : Number($("execQty").value),
      unit: $("execUnit").value || null,
      price: $("execPrice").value === "" ? null : Number($("execPrice").value),
      category: "other",
      start_date: state.viewDate,
      end_date: state.viewDate,
    };
    try {
      await api("/api/todos", { method: "POST", body: JSON.stringify(payload) });
      $("execTitle").value = "";
      $("execQty").value = "";
      $("execUnit").value = "";
      $("execPrice").value = "";
      toast("已添加");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  function renderDateNav() {
    const label = $("dateLabel");
    if (state.viewMode === "today") label.textContent = dateLabel(todayIso());
    else if (state.viewMode === "date") label.textContent = dateLabel(state.viewDate);
    else if (state.viewMode === "future") label.textContent = "未来";
    else if (state.viewMode === "overdue") label.textContent = "已过期";
    else if (state.viewMode === "done") label.textContent = "已完成";
    else label.textContent = "全部";
    $("execDatePicker").value =
      state.viewMode === "date" ? state.viewDate : todayIso();
    Array.prototype.forEach.call(
      document.querySelectorAll("#viewModeChips .chip"),
      function (c) {
        c.classList.toggle("active", c.getAttribute("data-mode") === state.viewMode);
      }
    );
  }

  async function refresh() {
    renderDateNav();
    // 今天/具体日固定带已完成（结算判定需要）；未来/过期/全部只取未完成；已完成只取已处理
    let dateToken;
    let includeDone;
    if (state.viewMode === "today") {
      dateToken = "today";
      includeDone = 1;
    } else if (state.viewMode === "date") {
      dateToken = state.viewDate;
      includeDone = 1;
    } else if (state.viewMode === "future") {
      dateToken = "future";
      includeDone = 0;
    } else if (state.viewMode === "overdue") {
      dateToken = "overdue";
      includeDone = 0;
    } else if (state.viewMode === "done") {
      // 已完成：只取已处理项（后端 status=1），强制带已完成
      dateToken = "done";
      includeDone = 1;
    } else {
      dateToken = "all";
      includeDone = 0;
    }
    // 接收端只取一级（不展示子待办）；发布端平铺以便管理子项
    const flat = isPublisher() ? "&flat=1" : "";
    try {
      const data = await api(
        "/api/todos?date=" + encodeURIComponent(dateToken) +
          "&include_done=" + includeDone + flat
      );
      state.todos = data.items || [];
      // 持久区块独立拉取，固定含已完成（完成的持久待办只留在持久区块）
      const pdata = await api(
        "/api/todos?date=persistent&include_done=1" + flat
      );
      state.persistent = pdata.items || [];
      // 已发布待办（含已完成，只看一级，可撤回自己的）：不按设备角色区分。
      // 发布管理跟随「发布」tab，后端 mine=1 已按当前设备 created_by 返回，
      // 任何角色都拉取并填充，切到发布 tab 才能看到/管理自己发布的。
      const mdata = await api("/api/todos?date=all&include_done=1&mine=1");
      state.published = mdata.items || [];
      await loadTodoDates();
      renderPublished();
      renderTodos();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    } finally {
      // 渲染中途出错也要做一次结算判定（只读 state.todos，不依赖 DOM）
      checkSettle();
    }
  }

  function scheduleRefresh() {
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(refresh, 250);
  }

  // 安卓壳专用：网页 WebSocket 收到 todo.* 时，通知原生刷新桌面小组件。
  // 浏览器里 window.FTAndroid 不存在，静默跳过。
  function notifyNativeTodosChanged() {
    try {
      if (window.FTAndroid && typeof window.FTAndroid.onTodosChanged === "function") {
        window.FTAndroid.onTodosChanged();
      }
    } catch (e) {}
  }

  function isPersistent(node) {
    return !node.start_date && !node.end_date && !node.due_date;
  }

  function buildChildrenMap(items) {
    const map = new Map();
    (items || state.todos).forEach(function (t) {
      const key = t.parent_id || "__root__";
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(t);
    });
    return map;
  }

  function anyPending(node, childrenMap) {
    if (node.status_detail === "pending") return true;
    return (childrenMap.get(node.id) || []).some(function (c) {
      return anyPending(c, childrenMap);
    });
  }

  // FLIP：先记旧位置，重排后让卡片从旧位置滑到新位置（完成 → 划到末尾）
  function capturePositions() {
    const map = new Map();
    document
      .querySelectorAll("#todoList [data-id], #persistentList [data-id]")
      .forEach(function (n) {
        map.set(n.getAttribute("data-id"), n.getBoundingClientRect());
      });
    return map;
  }

  function playFlip(first) {
    document
      .querySelectorAll("#todoList [data-id], #persistentList [data-id]")
      .forEach(function (n) {
        const id = n.getAttribute("data-id");
        const f = first.get(id);
        if (!f) return;
        const l = n.getBoundingClientRect();
        const dx = f.left - l.left;
        const dy = f.top - l.top;
        if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
        n.style.transition = "none";
        n.style.transform = "translate(" + dx + "px," + dy + "px)";
        requestAnimationFrame(function () {
          n.style.transition = "transform .38s cubic-bezier(.22,.61,.36,1)";
          n.style.transform = "";
        });
      });
  }

  function renderDoneHeader(count) {
    return el(
      "button",
      {
        className: "done-header",
        attrs: { type: "button" },
        on: {
          click: function () {
            state.doneOpen = !state.doneOpen;
            renderTodos();
          },
        },
      },
      [
        el("span", { text: "已完成" }),
        el("span", { className: "done-count", text: String(count) }),
        el("span", { className: "caret", text: state.doneOpen ? "▴" : "▾" }),
      ]
    );
  }

  function renderTodos() {
    const mainList = $("todoList");
    const persistList = $("persistentList");
    const first = capturePositions();
    mainList.textContent = "";
    persistList.textContent = "";

    const mainMap = buildChildrenMap(state.todos);
    const persistMap = buildChildrenMap(state.persistent);

    // 主列表：持久待办单独成块；其余同日待办未完成在前、已完成沉底到「已完成」区
    const mainRoots = (mainMap.get("__root__") || []).filter(function (t) {
      return !isPersistent(t);
    });
    const pendingRoots = [];
    const doneRoots = [];
    mainRoots.forEach(function (node) {
      if (node.status_detail === "pending" || anyPending(node, mainMap)) {
        pendingRoots.push(node);
      } else {
        doneRoots.push(node);
      }
    });
    // 重要未完成置顶（稳定排序，其余保持后端顺序）
    pendingRoots.sort(function (a, b) {
      return (a.important ? 0 : 1) - (b.important ? 0 : 1);
    });

    state.todos.forEach(function (t) {
      if ((mainMap.get(t.id) || []).some(function (c) {
        return c.status_detail === "pending";
      })) {
        state.expanded.add(t.id);
      }
    });

    pendingRoots.forEach(function (node) {
      mainList.appendChild(renderNode(node, mainMap, "root"));
    });
    if (doneRoots.length) {
      mainList.appendChild(renderDoneHeader(doneRoots.length));
      if (state.doneOpen) {
        doneRoots.forEach(function (node) {
          mainList.appendChild(renderNode(node, mainMap, "done"));
        });
      }
    }
    $("emptyHint").classList.toggle("hidden", mainRoots.length !== 0);

    const persistRoots = persistMap.get("__root__") || [];
    persistRoots.forEach(function (node) {
      persistList.appendChild(renderNode(node, persistMap, "root"));
    });
    $("persistentBlock").classList.toggle("hidden", persistRoots.length === 0);
    $("persistentCount").textContent = String(persistRoots.length);
    $("persistentList").classList.toggle("hidden", !state.persistentOpen);
    $("persistentCaret").textContent = state.persistentOpen ? "▴" : "▾";

    playFlip(first);
  }

  // ---- 发布端：已发布待办统一管理（按日期分组）----

  // 归组：今天（0）→ 未来按日期递增（1）→ 持久（2）→ 已过期（3）。
  // 覆盖今天的区间待办也归「今天」，与后端窗口语义一致。
  function pubDateLabel(iso) {
    const today = todayIso();
    if (iso.slice(0, 4) === today.slice(0, 4)) return dateLabel(iso);
    return iso;
  }

  function pubGroup(item) {
    if (isPersistent(item)) {
      return { key: "persistent", label: "📌 持久", rank: 2, sort: "" };
    }
    const start = item.start_date || item.due_date || "";
    const end = item.end_date || item.due_date || start;
    const today = todayIso();
    if (start && end && start <= today && today <= end) {
      return { key: "today", label: "今天", rank: 0, sort: today };
    }
    if (start && start > today) {
      return { key: start, label: pubDateLabel(start), rank: 1, sort: start };
    }
    if (end && end < today) {
      return { key: "overdue", label: "已过期", rank: 3, sort: "" };
    }
    return { key: "persistent", label: "📌 持久", rank: 2, sort: "" };
  }

  function renderPublished() {
    const card = $("publishedCard");
    const list = $("publishedList");
    const empty = $("publishedEmpty");
    if (!card || !list) return;
    // 发布管理跟随「发布」tab（#publishView），不再按设备角色强制隐藏。
    // 卡片本身就在 publishView 内，tab 切到发布时由 publishView 的显隐决定可见性；
    // 这里只去掉角色前提，显隐交给 tab。
    card.classList.remove("hidden");

    const items = (state.published || []).slice();
    // 清掉已不在列表里的选择 / 展开，避免脏状态
    const ids = new Set(items.map(function (t) { return t.id; }));
    state.editingPublished.forEach(function (id) {
      if (!ids.has(id)) state.editingPublished.delete(id);
    });
    state.editSelected.forEach(function (id) {
      if (!ids.has(id)) state.editSelected.delete(id);
    });

    list.textContent = "";
    empty.classList.toggle("hidden", items.length !== 0);
    if (!items.length) {
      updateEditBatchBar();
      return;
    }

    const groups = new Map();
    items.forEach(function (item) {
      const g = pubGroup(item);
      if (!groups.has(g.key)) groups.set(g.key, { meta: g, items: [] });
      groups.get(g.key).items.push(item);
    });
    const ordered = Array.from(groups.values()).sort(function (a, b) {
      if (a.meta.rank !== b.meta.rank) return a.meta.rank - b.meta.rank;
      return a.meta.sort < b.meta.sort ? -1 : a.meta.sort > b.meta.sort ? 1 : 0;
    });

    ordered.forEach(function (group) {
      // 组内：未完成在前，重要置顶，再按发布顺序
      group.items.sort(function (a, b) {
        const sa = a.status_detail === "pending" ? 0 : 1;
        const sb = b.status_detail === "pending" ? 0 : 1;
        if (sa !== sb) return sa - sb;
        if (!!a.important !== !!b.important) return a.important ? -1 : 1;
        return (a.sort_order || 0) - (b.sort_order || 0);
      });
      const section = el("div", { className: "pub-group" }, [
        el("div", { className: "pub-group-head" }, [
          el("span", { className: "pub-group-title", text: group.meta.label }),
          el("span", { className: "pub-group-count", text: String(group.items.length) }),
        ]),
      ]);
      group.items.forEach(function (item) {
        section.appendChild(buildPubRow(item));
      });
      list.appendChild(section);
    });

    updateEditBatchBar();
  }

  async function withdrawTodo(id) {
    try {
      await api("/api/todos/" + id + "/withdraw", { method: "POST" });
      toast("已撤回");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  // ---- 发布端：统一管理已发布待办（按日期分组；单个编辑 / 勾选批量）----

  function buildPubRow(item) {
    const wrap = el("div", { className: "edit-row-wrap" });
    const pending = item.status_detail === "pending";
    const row = el("div", {
      className: "published-row edit-row pub-manage status-" + item.status_detail,
      attrs: { "data-id": item.id },
    });

    // 批量修改勾选：只对未完成开放（后端 bulk-update 只允许改未完成）
    if (pending) {
      const check = el("input", {
        className: "edit-check",
        attrs: { type: "checkbox", "aria-label": "勾选批量修改" },
      });
      check.checked = state.editSelected.has(item.id);
      check.addEventListener("change", function () {
        if (check.checked) state.editSelected.add(item.id);
        else state.editSelected.delete(item.id);
        updateEditBatchBar();
      });
      row.appendChild(check);
    }

    // 状态勾选框：待办 → 完成；已处理 → 撤销回待办
    const box = el("div", {
      className: "check-box",
      text: pending ? "" : "✓",
      attrs: { title: pending ? "点选完成" : "点选回到待办" },
    });
    box.addEventListener("click", function () {
      if (pending) onComplete(item.id);
      else onUncomplete(item.id);
    });
    row.appendChild(box);

    const title = el("span", { className: "pub-title" });
    title.appendChild(document.createTextNode(item.title));
    if (item.important) {
      title.appendChild(el("span", { className: "star-badge", text: "★" }));
    }
    row.appendChild(el("div", { className: "pub-main" }, [title, buildPubBadges(item)]));
    row.appendChild(buildPubActions(item, pending));

    wrap.appendChild(row);
    if (state.editingPublished.has(item.id)) wrap.appendChild(buildEditForm(item));
    return wrap;
  }

  // 每条显示：类别/标签 + 状态 + 区间 + 知悉人 / 完成人
  function buildPubBadges(item) {
    const parts = [];
    if (item.category && CATEGORY_LABEL[item.category]) {
      parts.push(
        el("span", { className: "tag " + item.category, text: CATEGORY_LABEL[item.category] })
      );
    }
    if (item.tag) parts.push(el("span", { className: "tag family-tag", text: item.tag }));
    parts.push(
      el("span", {
        className: "badge status-badge status-" + item.status_detail,
        text: STATUS_LABEL[item.status_detail] || "待办",
      })
    );
    if (item.start_date && item.end_date && item.start_date !== item.end_date) {
      const wb = windowBadge(item);
      if (wb) parts.push(wb);
    }
    if (item.acked_by) {
      parts.push(
        el("span", {
          className: "badge ack-badge",
          text: (item.acker_name || "已知悉") + " 知悉",
        })
      );
    }
    if (item.completed_by && item.status_detail === "completed") {
      parts.push(
        el("span", {
          className: "badge done-badge",
          text: (item.completer_name || "已有人") + " 完成",
        })
      );
    }
    return el("div", { className: "pub-badges" }, parts);
  }

  function buildPubActions(item, pending) {
    const actions = el("div", { className: "pub-actions" });
    if (pending) {
      actions.appendChild(
        el("button", {
          className: "ghost small",
          attrs: { type: "button", title: "改标题 / 标签 / 日期" },
          text: "编辑",
          on: {
            click: function () {
              togglePubEdit(item.id);
            },
          },
        })
      );
      if (!item.acked_by) {
        actions.appendChild(
          el("button", {
            className: "ghost small",
            attrs: { type: "button", title: "标记为已知悉" },
            text: "知悉",
            on: {
              click: function () {
                ackOne(item.id);
              },
            },
          })
        );
      }
    } else {
      actions.appendChild(
        el("button", {
          className: "ghost small",
          attrs: { type: "button", title: "撤销完成，回到待办" },
          text: "撤销",
          on: {
            click: function () {
              onUncomplete(item.id);
            },
          },
        })
      );
    }
    actions.appendChild(
      el("button", {
        className: "ghost small",
        attrs: { type: "button", title: "撤回该待办" },
        text: "撤回",
        on: {
          click: function () {
            withdrawTodo(item.id);
          },
        },
      })
    );
    return actions;
  }

  function togglePubEdit(id) {
    if (state.editingPublished.has(id)) state.editingPublished.delete(id);
    else state.editingPublished.add(id);
    renderPublished();
  }

  function buildEditForm(item) {
    const title = el("input", { attrs: { placeholder: "标题", value: item.title } });
    const tag = el("input", {
      attrs: { placeholder: "标签（留空清除）", value: item.tag || "" },
    });
    const start = el("input", { attrs: { type: "date", value: item.start_date || "" } });
    const end = el("input", { attrs: { type: "date", value: item.end_date || "" } });
    const todayBtn = el("button", {
      className: "ghost small",
      attrs: { type: "button" },
      text: "设为今天",
      on: {
        click: function () {
          const t = todayIso();
          start.value = t;
          end.value = t;
        },
      },
    });
    const save = el("button", {
      className: "primary small",
      attrs: { type: "button" },
      text: "保存",
      on: {
        click: function () {
          saveEditTodo(item.id, title.value, tag.value, start.value, end.value);
        },
      },
    });
    const cancel = el("button", {
      className: "ghost small",
      attrs: { type: "button" },
      text: "收起",
      on: {
        click: function () {
          state.editingPublished.delete(item.id);
          renderPublished();
        },
      },
    });
    return el("div", { className: "edit-form" }, [
      title,
      tag,
      el("div", { className: "edit-dates" }, [
        start,
        el("span", { className: "hint", text: "至" }),
        end,
        todayBtn,
      ]),
      el("div", { className: "edit-form-actions" }, [save, cancel]),
    ]);
  }

  async function saveEditTodo(id, titleValue, tagValue, startValue, endValue) {
    const title = (titleValue || "").trim();
    if (!title) {
      toast("标题不能为空", true);
      return;
    }
    const patch = { title: title, tag: (tagValue || "").trim() || null };
    // 至少填了一边日期才改窗口；两边都空 → 保持原日期不动
    if (startValue || endValue) {
      patch.start_date = startValue || endValue;
      patch.end_date = endValue || startValue;
    }
    try {
      await api("/api/todos/" + id + "?mine=1", {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      state.editingPublished.delete(id);
      toast("已保存");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  function updateEditBatchBar() {
    const bar = $("editBatchBar");
    if (!bar) return;
    const count = $("editBatchCount");
    if (count) count.textContent = "已选 " + state.editSelected.size + " 条";
    bar.classList.toggle("hidden", state.editSelected.size === 0);
  }

  async function bulkEditPublished(patch, okMsg) {
    const ids = Array.from(state.editSelected);
    if (!ids.length) {
      toast("先勾选待办", true);
      return;
    }
    try {
      await api("/api/todos/bulk-update", {
        method: "POST",
        body: JSON.stringify({ ids: ids, patch: patch }),
      });
      state.editSelected.clear();
      toast(okMsg || "已批量修改");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  function qtyPriceText(node) {
    // 数量与金额合并到标题一行：「青椒 ×3个」「葱花饼 15元」
    const parts = [];
    if (node.qty != null) parts.push("×" + node.qty + (node.unit || ""));
    if (node.price != null) parts.push(node.price + "元");
    return parts.join(" ");
  }

  function renderNode(node, childrenMap, depth) {
    const pub = isPublisher();
    const children = childrenMap.get(node.id) || [];
    const container = el("div", {
      className:
        "todo-node status-" + node.status_detail +
        (node.status === 1 ? " status-done" : "") +
        (depth === "done" ? " inline-done" : "") +
        (node.important && node.status_detail === "pending" ? " is-important" : ""),
      attrs: { "data-id": node.id },
    });

    const checkbox = el("div", {
      className: "check-box",
      text: node.status_detail === "pending" ? "" : "✓",
      attrs: { title: node.status_detail === "pending" ? "点选完成" : "点选回到待办" },
    });

    const title = el("div", { className: "title" });
    title.appendChild(document.createTextNode(node.title));
    const qp = qtyPriceText(node);
    if (qp) title.appendChild(el("span", { className: "qty", text: "  " + qp }));

    const body = el("div", { className: "body" }, [
      title,
      el("div", { className: "sub" }, buildSubInfo(node, children, pub)),
    ]);

    if (pub) {
      // 发布端：保留子待办展开与 +子 管理
      const actions = el("div", { className: "actions" }, buildActions(node, children.length > 0));
      const line = el("div", { className: "line" }, [checkbox, body, actions]);
      checkbox.addEventListener("click", function () {
        if (node.status_detail === "pending") onComplete(node.id);
        else onUncomplete(node.id);
      });
      container.appendChild(line);

      if (children.length && state.expanded.has(node.id)) {
        const subList = el("div", { className: "sub-list" });
        children.forEach(function (child) {
          subList.appendChild(renderNode(child, childrenMap, depth + 1));
        });
        container.appendChild(subList);
      }
      if (state.addingSub === node.id) {
        container.appendChild(buildSubAdd(node.id));
      }
      return container;
    }

    // 接收端：经典布局——左侧勾选框 + 标题
    const line = el("div", { className: "line" }, [checkbox, body]);
    checkbox.addEventListener("click", function (e) {
      e.stopPropagation();
      if (node.status_detail === "pending") onComplete(node.id);
      else onUncomplete(node.id);
    });
    // 关键：line 必须先挂回带状态类的 .todo-node，再整体交给左滑层。
    // 之前把 line 直接塞进 .swipe-wrap，导致 .todo-node/.status-* 的后代选择器
    // 全部失配：已完成不划线不变灰（!important 也救不了），.line 无 padding 而贴边。
    container.appendChild(line);
    const wrap = buildSwipe(container, node);
    line.addEventListener("click", function () {
      if (wrap._justSwiped) return;
      if (state.ackSelecting && !isPublisher() && node.status_detail === "pending") {
        // 多选知悉：点选/取消选择
        if (state.ackSelected.has(node.id)) {
          state.ackSelected.delete(node.id);
          container.classList.remove("ack-picked");
        } else {
          state.ackSelected.add(node.id);
          container.classList.add("ack-picked");
        }
        syncAckSelBtn();
        return;
      }
      if (wrap.classList.contains("swiped")) closeSwipe(wrap);
    });
    return el("div", { className: "todo-wrap" }, [wrap]);
  }

  function buildSubAdd(nodeId) {
    const input = el("input", { attrs: { placeholder: "子待办标题" } });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") submitSubtask(nodeId, input.value);
    });
    const ok = el("button", {
      className: "primary",
      text: "挂上",
      on: {
        click: function () {
          submitSubtask(nodeId, input.value);
        },
      },
    });
    const cancel = el("button", {
      className: "ghost",
      text: "收起",
      on: {
        click: function () {
          state.addingSub = null;
          renderTodos();
        },
      },
    });
    return el("div", { className: "sub-add" }, [input, ok, cancel]);
  }

  function swipeBtn(text, extra, fn) {
    return el("button", {
      className: "swipe-btn " + (extra || ""),
      attrs: { type: "button" },
      text: text,
      on: {
        click: function (e) {
          e.stopPropagation();
          fn();
        },
      },
    });
  }

  function buildSwipe(inner, node) {
    const buttons = [];
    if (state.viewMode === "overdue") {
      // 过期待办：重新唤起（PATCH 日期为今天）
      buttons.push(swipeBtn("重新唤起", "", function () { resurrect(node.id); }));
    }
    if (node.status_detail === "pending") {
      // 执行端：可标记知悉（反馈闭环）；发布端用「一键知悉/多选知悉」
      if (!isPublisher()) {
        buttons.push(swipeBtn("知悉", "", function () { ackOne(node.id); }));
      }
      buttons.push(swipeBtn("忽略", "", function () { mark(node.id, "ignore"); }));
    } else {
      buttons.push(swipeBtn("撤销", "", function () { onUncomplete(node.id); }));
    }
    buttons.push(swipeBtn("未完成", "danger", function () { onRemove(node.id); }));
    const actions = el("div", { className: "swipe-actions" }, buttons);
    const wrap = el("div", { className: "swipe-wrap" }, [actions, inner]);
    attachSwipe(wrap, inner, node);
    return wrap;
  }

  function attachSwipe(wrap, inner, node) {
    const actions = wrap.querySelector(".swipe-actions");
    const OPEN_X = -150;
    let startX = 0;
    let startY = 0;
    let baseX = 0;
    let dx = 0;
    let tracking = false;
    let axis = "";

    // 跟手平移 + 操作按钮随位移淡入
    function apply(x, animate) {
      inner.style.transition = animate
        ? "transform .24s cubic-bezier(.22,.61,.36,1)"
        : "none";
      inner.style.transform = x ? "translateX(" + x + "px)" : "";
      if (actions) {
        const p = Math.min(1, Math.abs(Math.min(x, 0)) / Math.abs(OPEN_X));
        actions.style.opacity = String(Math.max(0, p));
      }
    }

    inner.addEventListener(
      "touchstart",
      function (e) {
        const t = e.touches[0];
        startX = t.clientX;
        startY = t.clientY;
        baseX = wrap.classList.contains("swiped") ? OPEN_X : 0;
        dx = baseX;
        tracking = true;
        axis = "";
        inner.classList.add("dragging");
        wrap.classList.add("swiping");
      },
      { passive: true }
    );
    inner.addEventListener(
      "touchmove",
      function (e) {
        if (!tracking) return;
        const t = e.touches[0];
        const raw = t.clientX - startX;
        const dy = t.clientY - startY;
        if (!axis && (Math.abs(raw) > 8 || Math.abs(dy) > 8)) {
          axis = Math.abs(raw) > Math.abs(dy) ? "x" : "y";
        }
        if (axis !== "x") return;
        // 左滑最多 -150（露出操作），右滑最多 90（提示完成）
        let x = baseX + raw;
        x = x > 0 ? Math.min(x, 90) : Math.max(x, OPEN_X);
        dx = x;
        apply(x, false);
      },
      { passive: true }
    );
    inner.addEventListener("touchend", function () {
      if (!tracking) return;
      tracking = false;
      inner.classList.remove("dragging");
      wrap.classList.remove("swiping");
      if (
        dx > 60 &&
        node &&
        node.status_detail === "pending" &&
        !wrap.classList.contains("swiped")
      ) {
        // 右滑到位 → 完成
        apply(0, true);
        onComplete(node.id);
      } else if (dx <= OPEN_X * 0.5) {
        closeSwipes(wrap);
        wrap.classList.add("swiped");
        apply(OPEN_X, true);
      } else {
        wrap.classList.remove("swiped");
        apply(0, true);
      }
      wrap._justSwiped = true;
      setTimeout(function () {
        wrap._justSwiped = false;
      }, 350);
    });
  }

  function closeSwipe(wrap) {
    wrap.classList.remove("swiped", "swiping");
    const inner = wrap.querySelector(".todo-node");
    const actions = wrap.querySelector(".swipe-actions");
    if (inner) {
      inner.style.transition = "transform .24s cubic-bezier(.22,.61,.36,1)";
      inner.style.transform = "";
    }
    if (actions) actions.style.opacity = "0";
  }

  function closeSwipes(except) {
    Array.prototype.forEach.call(
      document.querySelectorAll(".swipe-wrap"),
      function (w) {
        if (w !== except) closeSwipe(w);
      }
    );
  }

  function shortDate(iso) {
    if (!iso) return "";
    const p = iso.split("-");
    return p[1] + "-" + p[2];
  }

  // 起止区间徽标：区间显示 start~end；持久显示 📌；单日不额外占位
  function windowBadge(node) {
    if (isPersistent(node)) {
      return el("span", { className: "badge window-badge persistent", text: "📌持久" });
    }
    if (node.start_date && node.end_date && node.start_date !== node.end_date) {
      return el("span", {
        className: "badge window-badge",
        text: shortDate(node.start_date) + "~" + shortDate(node.end_date),
      });
    }
    return null;
  }

  function buildSubInfo(node, children, pub) {
    const parts = [];
    if (node.important && node.status_detail === "pending") {
      parts.push(el("span", { className: "badge important-badge", text: "★ 重要" }));
    }
    if (node.category && CATEGORY_LABEL[node.category]) {
      parts.push(el("span", { className: "tag " + node.category, text: CATEGORY_LABEL[node.category] }));
    }
    if (node.tag) {
      parts.push(el("span", { className: "tag family-tag", text: node.tag }));
    }
    if (node.creator_name) {
      parts.push(el("span", { className: "badge creator-badge", text: node.creator_name + " 发布" }));
    }
    const wb = windowBadge(node);
    if (wb) parts.push(wb);
    if (node.status_detail !== "pending") {
      parts.push(el("span", { className: "badge", text: STATUS_LABEL[node.status_detail] }));
    }
    // 反馈闭环：知悉人 / 完成人（发布端能看到被谁知悉、被谁完成）
    if (node.acked_by && (!node.acked_at || true)) {
      parts.push(el("span", { className: "badge ack-badge", text: (node.acker_name || "已知悉") + " 知悉" }));
    }
    if (node.completed_by && node.status_detail === "completed") {
      parts.push(el("span", { className: "badge done-badge", text: (node.completer_name || "已有人") + " 完成" }));
    }
    if (pub && children.length) {
      const done = children.filter(function (c) {
        return c.status_detail !== "pending";
      }).length;
      parts.push(el("span", { className: "badge", text: "子项 " + done + "/" + children.length }));
    }
    return parts;
  }

  function buildActions(node, hasChildren) {
    const actions = [];
    if (hasChildren) {
      actions.push(
        el("button", {
          className: "ghost small",
          attrs: { type: "button" },
          text: state.expanded.has(node.id) ? "收起" : "展开",
          on: {
            click: function () {
              if (state.expanded.has(node.id)) state.expanded.delete(node.id);
              else state.expanded.add(node.id);
              renderTodos();
            },
          },
        })
      );
    }
    if (node.status_detail === "pending") {
      actions.push(
        el("button", {
          className: "ghost small",
          attrs: { type: "button" },
          text: "忽略",
          on: {
            click: function () {
              mark(node.id, "ignore");
            },
          },
        }),
        el("button", {
          className: "ghost small",
          attrs: { type: "button" },
          text: "取消",
          on: {
            click: function () {
              mark(node.id, "cancel");
            },
          },
        })
      );
    } else {
      actions.push(
        el("button", {
          className: "ghost small",
          attrs: { type: "button" },
          text: "撤销",
          on: {
            click: function () {
              onUncomplete(node.id);
            },
          },
        })
      );
    }
    if (node.status_detail === "pending") {
      actions.push(
        el("button", {
          className: "ghost small",
          attrs: { type: "button" },
          text: "+子",
          on: {
            click: function () {
              state.addingSub = state.addingSub === node.id ? null : node.id;
              renderTodos();
            },
          },
        })
      );
    }
    actions.push(
      el("button", {
        className: "ghost small",
        attrs: { type: "button", title: "删除" },
        text: "删",
        on: {
          click: function () {
            onDelete(node.id);
          },
        },
      })
    );
    return actions;
  }

  async function onComplete(id) {
    try {
      const res = await api("/api/todos/" + id + "/complete", { method: "POST" });
      if (res && res.needs_decision) {
        if (isPublisher()) {
          // 发布端：弹出子项三选一
          openDecision(id, res);
        } else {
          // 接收端：不看子待办，直接按完成处理
          await api("/api/todos/" + id + "/complete", {
            method: "POST",
            body: JSON.stringify({ decision: "complete" }),
          });
          toast("已完成");
          await refresh();
        }
      } else {
        toast("已完成");
        await refresh();
      }
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  async function onUncomplete(id) {
    try {
      await api("/api/todos/" + id + "/uncomplete", { method: "POST" });
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  // ---- 反馈闭环（执行端）：一键知悉全部 / 单条知悉 / 多选知悉 ----
  async function ackOne(id) {
    try {
      await api("/api/todos/" + id + "/ack", { method: "POST" });
      toast("已标记知悉");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  async function ackAll() {
    try {
      const res = await api("/api/todos/ack-all", { method: "POST" });
      toast("已全部标记知悉（" + (res.count || 0) + " 条）");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  async function ackSelected(ids) {
    if (!ids || !ids.length) return;
    try {
      const res = await api("/api/todos/ack-bulk", {
        method: "POST",
        body: JSON.stringify({ ids: ids }),
      });
      toast("已标记知悉（" + (res.count || 0) + " 条）");
      state.editSelected.clear();
      state.selectMode = false;
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  async function mark(id, kind) {
    const path = kind === "ignore" ? "/ignore" : "/cancel";
    try {
      await api("/api/todos/" + id + path, { method: "POST" });
      toast(kind === "ignore" ? "已忽略" : "已取消");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  async function onDelete(id) {
    const node = document.querySelector('[data-id="' + id + '"]');
    if (node) node.classList.add("deleting");
    try {
      await api("/api/todos/" + id, { method: "DELETE" });
      toast("已删除");
      setTimeout(refresh, 400);
    } catch (e) {
      if (node) node.classList.remove("deleting");
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  async function onRemove(id) {
    // 软删：变红 + 标「未完成」，不真删、不弹确认
    try {
      await api("/api/todos/" + id + "/remove", { method: "POST" });
      toast("已标为未完成");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  async function resurrect(id) {
    // 过期待办重新唤起：PATCH 日期为今天，回到今日待办
    const t = todayIso();
    try {
      await api("/api/todos/" + id, {
        method: "PATCH",
        body: JSON.stringify({ start_date: t, end_date: t }),
      });
      toast("已重新唤起");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  async function submitSubtask(parentId, title) {
    title = (title || "").trim();
    if (!title) return;
    try {
      await api("/api/todos/" + parentId + "/subtasks", {
        method: "POST",
        body: JSON.stringify({ title: title, category: "other" }),
      });
      state.addingSub = null;
      state.expanded.add(parentId);
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  function openDecision(id, info) {
    state.decisionId = id;
    $("decisionText").textContent = "还有 " + info.pending_count + " 条未处理子项，选择处理方式：";
    $("decisionModal").classList.remove("hidden");
  }

  function closeDecision() {
    state.decisionId = null;
    $("decisionModal").classList.add("hidden");
  }

  async function applyDecision(decision) {
    const id = state.decisionId;
    closeDecision();
    if (!id) return;
    try {
      await api("/api/todos/" + id + "/complete", {
        method: "POST",
        body: JSON.stringify({ decision: decision }),
      });
      if (decision === "cancel") toast("已取消，父项未完成");
      else toast("已处理");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  // ---- 今日结算：全部了结 → 弹窗 → 服务端软删清空今日 ----

  function settleKeyFor(day) {
    return "ft_settled_" + day;
  }

  function checkSettle() {
    // 只在「今天」视图且执行端可见时判定；集合=覆盖今天的一级待办（排除持久）。
    // 注意：点日历选「今天」或前后翻页回到今天时 viewMode 是 date，也应算今天视图，
    // 否则勾完最后一件事不会弹结算（用户反馈的 bug）。
    const today = todayIso();
    const isTodayView =
      state.viewMode === "today" ||
      (state.viewMode === "date" && state.viewDate === today);
    if (!isTodayView) return;
    if ($("executeView").classList.contains("hidden")) return;
    const key = settleKeyFor(today);
    state.settledKey = key;
    const map = buildChildrenMap(state.todos);
    const roots = (map.get("__root__") || []).filter(function (t) {
      return !isPersistent(t);
    });
    if (!roots.length) {
      localStorage.removeItem(key);
      return;
    }
    // 结束 = status_detail != pending（completed/ignored/cancelled/unfinished 都算）
    const ended = roots.filter(function (t) {
      return t.status_detail !== "pending";
    });
    if (ended.length !== roots.length) {
      localStorage.removeItem(key);
      return;
    }
    // 同一轮不重复弹；条件一旦变为未满足（上面已清标记）可再次触发
    if (localStorage.getItem(key)) return;
    localStorage.setItem(key, "1");
    // 稍等完成卡片划到末尾的动画，再浮出结算
    const snapshot = ended.slice();
    setTimeout(function () {
      openSettle(snapshot);
    }, 360);
  }

  function openSettle(roots) {
    const list = $("settleList");
    list.textContent = "";
    roots.forEach(function (t) {
      const label = STATUS_LABEL[t.status_detail] || "";
      list.appendChild(el("li", { text: t.title + "（" + label + "）" }));
    });
    const now = new Date();
    $("settleTime").textContent =
      "现在时间：" + pad2(now.getHours()) + ":" + pad2(now.getMinutes());
    $("settleModal").classList.remove("hidden");
  }

  function closeSettle() {
    $("settleModal").classList.add("hidden");
  }

  async function clearToday() {
    const today = todayIso();
    try {
      await api("/api/todos/clear", {
        method: "POST",
        body: JSON.stringify({ date: today }),
      });
      closeSettle();
      state.doneOpen = true;
      localStorage.setItem(settleKeyFor(today), "1");
      toast("本轮已结算，回到无任务");
      await refresh();
    } catch (e) {
      if (e.message !== "未授权") toast(e.message, true);
    }
  }

  // ---- WebSocket ----

  function wsUrl() {
    try {
      const u = new URL(state.api);
      u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
      // 保留 API 的路径前缀（如 /todo），把路径指向 前缀 + /ws
      const base = u.pathname.replace(/\/(api\/?)?$/, ""); // api 尾缀去掉(若有)
      u.pathname = (base ? base + "/" : "") + "ws";
      u.search = "";
      u.hash = "";
      return u.toString();
    } catch (e) {
      return "ws://localhost:8000/ws";
    }
  }

  function connectWS() {
    if (!state.token) return;
    if (state.ws) {
      try {
        state.ws.onclose = null;
        state.ws.close();
      } catch (e) {}
      state.ws = null;
    }
    let ws;
    try {
      ws = new WebSocket(wsUrl());
    } catch (e) {
      scheduleReconnect();
      return;
    }
    state.ws = ws;
    setConn(false, "实时连接中…");

    ws.onopen = function () {
      ws.send(JSON.stringify({ type: "auth", token: state.token }));
    };
    ws.onmessage = function (ev) {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      if (msg.type === "auth") {
        if (msg.ok) {
          state.retry = 0;
          setConn(true, "实时已连接");
          refresh();
        } else {
          setConn(false, "实时鉴权失败");
        }
      } else if (msg.type === "ping") {
        ws.send(JSON.stringify({ type: "pong" }));
      } else if (typeof msg.type === "string" && msg.type.indexOf("todo.") === 0) {
        scheduleRefresh();
        notifyNativeTodosChanged();
      }
    };
    ws.onclose = function () {
      setConn(false, "实时未连接");
      scheduleReconnect();
    };
    ws.onerror = function () {};
  }

  function scheduleReconnect() {
    if (!state.token) return;
    clearTimeout(state.wsTimer);
    state.retry = Math.min(state.retry + 1, 6);
    const delay = Math.min(1000 * Math.pow(2, state.retry - 1), 15000);
    state.wsTimer = setTimeout(connectWS, delay);
  }

  // ---- 事件绑定 ----

  $("joinBtn").addEventListener("click", function () {
    const token = $("joinToken").value.trim();
    if (!token) {
      $("joinError").textContent = "请输入家庭令牌";
      return;
    }
    state.api = ($("joinApi").value || "").trim().replace(/\/+$/, "") || defaultApi();
    localStorage.setItem(KEYS.api, state.api);
    doJoin(token, $("joinDevice").value.trim(), $("joinRole").value);
  });

  $("logoutBtn").addEventListener("click", function () {
    clearAuth();
    showJoin("已退出本机设备");
  });

  $("nameModalSave").addEventListener("click", async function () {
    const ok = await renameDevice($("nameModalInput").value);
    if (ok) {
      localStorage.setItem(namePromptKey(), "1");
      closeNameModal();
    }
  });
  $("nameModalSkip").addEventListener("click", function () {
    localStorage.setItem(namePromptKey(), "1");
    closeNameModal();
  });
  $("nameModalInput").addEventListener("keydown", function (e) {
    if (e.key === "Enter") $("nameModalSave").click();
  });
  $("settingsNameSave").addEventListener("click", function () {
    renameDevice($("settingsDeviceName").value);
  });

  $("tabPublish").addEventListener("click", function () {
    switchTab("publish");
  });
  $("tabExecute").addEventListener("click", function () {
    switchTab("execute");
    refresh();
  });

  Array.prototype.forEach.call(
    document.querySelectorAll("#pubDateSeg .chip"),
    function (chip) {
      chip.addEventListener("click", function () {
        Array.prototype.forEach.call(
          document.querySelectorAll("#pubDateSeg .chip"),
          function (c) {
            c.classList.remove("active");
          }
        );
        chip.classList.add("active");
        syncPublishDateUI();
      });
    }
  );
  $("pubDateCustom").value = todayIso();
  $("pubStart").value = todayIso();
  $("pubEnd").value = todayIso();
  syncPublishDateUI();

  // 发布端「选某天」→ 自绘迷你月历（原生 pubDateCustom 仅作隐藏兜底）
  $("pubDateBtn").addEventListener("click", function (e) {
    e.stopPropagation();
    if (isCalendarOpen("pubCalendar")) closeCalendar("pubCalendar");
    else openCalendar("pubCalendar");
  });

  // 两个迷你月历共用事件委托：翻月 / 回到今天 / 点选日期
  Array.prototype.forEach.call(document.querySelectorAll(".mini-cal"), function (p) {
    p.addEventListener("click", onCalendarClick);
  });

  $("parseBtn").addEventListener("click", doParse);
  $("clearParseBtn").addEventListener("click", function () {
    $("parseText").value = "";
    state.parsed = [];
    state.notes = [];
    $("parseHint").textContent = "";
    renderPreview();
  });
  $("publishBtn").addEventListener("click", doPublish);
  $("manualAddBtn").addEventListener("click", addManual);

  $("batchGenBtn").addEventListener("click", generateBatch);
  $("batchAllBtn").addEventListener("click", toggleBatchAll);
  $("batchAddBtn").addEventListener("click", addBatch);

  // 发布端：已发布待办批量编辑
  $("editBatchDateBtn").addEventListener("click", function () {
    const day = $("editBatchDate").value;
    if (!day) {
      toast("请先选择日期", true);
      return;
    }
    bulkEditPublished({ start_date: day, end_date: day }, "已统一日期");
  });
  $("editBatchTagBtn").addEventListener("click", function () {
    const tag = $("editBatchTag").value.trim();
    bulkEditPublished({ tag: tag || null }, tag ? "已统一标签" : "已清除标签");
  });
  $("editBatchClearBtn").addEventListener("click", function () {
    state.editSelected.clear();
    $("editBatchDate").value = "";
    $("editBatchTag").value = "";
    renderPublished();
  });

  $("prevDay").addEventListener("click", function () {
    state.viewMode = "date";
    state.viewDate = shiftIso(state.viewDate, -1);
    refresh();
  });
  $("nextDay").addEventListener("click", function () {
    state.viewMode = "date";
    state.viewDate = shiftIso(state.viewDate, 1);
    refresh();
  });
  // 日期条点开自绘迷你月历（原生 execDatePicker 保留为隐藏兜底）
  $("dateLabel").addEventListener("click", function () {
    if (isCalendarOpen("execCalendar")) closeCalendar("execCalendar");
    else openCalendar("execCalendar");
  });
  $("execDatePicker").addEventListener("change", function (e) {
    if (!e.target.value) return;
    state.viewDate = e.target.value;
    state.viewMode = "date";
    refresh();
  });

  // 执行端筛选档：今天 / 未来 / 已过期 / 全部
  $("viewModeChips").addEventListener("click", function (e) {
    const btn = e.target.closest("[data-mode]");
    if (!btn) return;
    const mode = btn.getAttribute("data-mode");
    state.viewMode = mode;
    if (mode === "today") state.viewDate = todayIso();
    closeCalendar("execCalendar");
    refresh();
  });

  // 反馈闭环：执行端 一键知悉 / 多选知悉
  if ($("ackAllBtn")) {
    $("ackAllBtn").addEventListener("click", async function () {
      await ackAll();
    });
  }
  if ($("ackSelBtn")) {
    $("ackSelBtn").addEventListener("click", function () {
      if (!state.ackSelecting) {
        // 进入多选知悉模式
        state.ackSelecting = true;
        state.ackSelected.clear();
        syncAckSelBtn();
        toast("点选要标记知悉的待办 · 再点按钮确认");
      } else {
        ackSelected(Array.from(state.ackSelected));
      }
    });
  }
  function syncAckSelBtn() {
    const btn = $("ackSelBtn");
    if (!btn) return;
    btn.hidden = !state.ackSelecting;
    btn.textContent = state.ackSelecting
      ? "确认知悉(" + state.ackSelected.size + ")"
      : "多选知悉";
    const bar = $("execAckBar");
    if (bar) bar.hidden = isPublisher(); // 执行端常显，发布端隐藏
  }
  function exitAckSelect() {
    state.ackSelecting = false;
    state.ackSelected.clear();
    syncAckSelBtn();
    renderTodos();
  }
  syncAckSelBtn();

  $("refreshBtn").addEventListener("click", refresh);

  // 顶栏齿轮：设置独立入口（与添加分开）
  $("settingsBtn").addEventListener("click", function (e) {
    e.stopPropagation();
    toggleSettings();
  });
  $("settingsCloseBtn").addEventListener("click", function () {
    toggleSettings(false);
  });
  $("settingsPanel").addEventListener("click", function (e) {
    if (e.target === $("settingsPanel")) toggleSettings(false);
  });

  // 关于 / 开源入口（点「关于」→ 介绍 + 开源链接）
  $("aboutBtn").addEventListener("click", function () {
    const dn = localStorage.getItem(KEYS.deviceName);
    if ($("aboutDevice")) $("aboutDevice").textContent = dn || "未设置";
    if ($("aboutVersion")) $("aboutVersion").textContent = "—";
    toggleSettings(false);
    $("aboutPanel").classList.remove("hidden");
    // 顺手取一下版本号（取不到就保持 —）
    try {
      const base = (localStorage.getItem(KEYS.api) || "").replace(/\/+$/, "");
      fetch(base + "/api/app/latest.json", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (j && $("aboutVersion")) {
            $("aboutVersion").textContent =
              (j.version_name || j.versionName || "—") + (j.version_code ? " (" + (j.version_code || j.versionCode) + ")" : "");
          }
        })
        .catch(function () {});
    } catch (e) {}
  });
  $("aboutCloseBtn").addEventListener("click", function () {
    $("aboutPanel").classList.add("hidden");
  });
  $("aboutPanel").addEventListener("click", function (e) {
    if (e.target === $("aboutPanel")) $("aboutPanel").classList.add("hidden");
  });

  // 默认视图设置：打开 App 时进入发布端 / 接收端（跟随角色为空）
  $("defaultView").value = localStorage.getItem(KEYS.defaultView) || "";
  $("defaultView").addEventListener("change", function (e) {
    const v = e.target.value;
    if (v === "publish" || v === "execute") localStorage.setItem(KEYS.defaultView, v);
    else localStorage.removeItem(KEYS.defaultView);
    toast(v === "publish" ? "默认进入发布端" : v === "execute" ? "默认进入接收端" : "默认跟随角色");
  });

  // 持久待办：收起为一行，点开才列
  $("persistentToggle").addEventListener("click", function () {
    state.persistentOpen = !state.persistentOpen;
    renderTodos();
  });

  // 日期/筛选面板
  $("dateFilterBtn").addEventListener("click", function () {
    toggleExecPanel("datePanel");
  });

  // 发布端「＋ 高级」折叠
  $("advancedToggle").addEventListener("click", function () {
    const block = $("advancedBlock");
    const open = block.classList.toggle("hidden") === false;
    $("advancedToggle").classList.toggle("open", open);
  });

  // 底部 [+]->二级快速菜单
  $("fabBtn").addEventListener("click", function (e) {
    e.stopPropagation();
    const open = $("quickMenu").classList.toggle("hidden") === false;
    $("fabBtn").classList.toggle("open", open);
  });
  Array.prototype.forEach.call(
    document.querySelectorAll("#quickMenu [data-action]"),
    function (btn) {
      btn.addEventListener("click", function () {
        const action = btn.getAttribute("data-action");
        closeQuickMenu();
        if (action === "add") {
          toggleExecPanel("quickAddPanel");
          const t = $("execTitle");
          if (t) t.focus();
        }
      });
    }
  );

  $("settleClearBtn").addEventListener("click", clearToday);
  $("settleCloseBtn").addEventListener("click", closeSettle);
  $("settleModal").addEventListener("click", function (e) {
    if (e.target === $("settleModal")) closeSettle();
  });
  $("execAddBtn").addEventListener("click", addExec);
  $("execTitle").addEventListener("keydown", function (e) {
    if (e.key === "Enter") addExec();
  });

  $("decisionModal").addEventListener("click", function (e) {
    if (e.target === $("decisionModal")) closeDecision();
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-decision]"), function (btn) {
    btn.addEventListener("click", function () {
      applyDecision(btn.getAttribute("data-decision"));
    });
  });

  document.addEventListener("click", function (e) {
    closeSwipes();
    if (!e.target.closest || (!e.target.closest("#fabBtn") && !e.target.closest("#quickMenu"))) {
      closeQuickMenu();
    }
    // 点日历外部收起迷你月历（触发器自身/日历内部不关）
    if (
      e.target.closest &&
      !e.target.closest(".mini-cal") &&
      !e.target.closest("#dateLabel") &&
      !e.target.closest("#pubDateBtn")
    ) {
      closeAllCalendars();
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && state.token) refresh();
  });

  bootstrap();
})();
