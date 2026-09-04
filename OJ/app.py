"""
Online Judge 前端（Step 6：前端交互）

- 使用 Streamlit 实现图形界面，通过 REST API 与 FastAPI 后端（OJ.py）交互，不直接读写后端数据。
- 覆盖三组页面：用户页面组、题目页面组、评测与提交页面组。
- 会话基于后端下发的 Cookie（session id），前端保存 token 并在每次请求中携带，不硬编码身份。
- 统一封装 API 调用函数（api()），集中处理请求、身份信息与异常。
- 所有响应遵循后端 {code, msg, data} 结构，HTTP 状态码与 code 一致。

启动方式：
    1. 先启动后端：  python OJ.py            （默认监听 http://127.0.0.1:8000）
    2. 再启动前端：  streamlit run app.py

依赖：  pip install streamlit requests
"""

import re
import time

import pandas as pd
import requests
import streamlit as st

# --------------------------------------------------------------------------- #
# 基础配置
# --------------------------------------------------------------------------- #
BASE_URL = "http://127.0.0.1:8000"   # 后端服务地址
SESSION_COOKIE = "session"            # 后端会话 Cookie 名

st.set_page_config(page_title="Online Judge", page_icon="⚖️", layout="wide")


# --------------------------------------------------------------------------- #
# 统一 API 调用（任务 4：接口对接）
# --------------------------------------------------------------------------- #
def is_logged_in() -> bool:
    """是否已登录（本地保存了会话 token）"""
    return bool(st.session_state.get("session_token"))


def current_user() -> dict:
    """当前登录用户简要信息 {user_id, username, role}"""
    return st.session_state.get("user") or {}


def _cookies() -> dict:
    """构造请求 Cookie，携带登录会话 token"""
    token = st.session_state.get("session_token")
    return {SESSION_COOKIE: token} if token else {}


def api(method: str, path: str, json: dict | None = None, params: dict | None = None):
    """统一 API 调用：发送请求、同步 Cookie、返回 (response, body)。

    - 后端不可达时返回 {code:-1, msg:...}，前端据此给出友好提示。
    - 登录响应中的 Set-Cookie 会写入 session_state；登出时清除。
    - 受保护接口返回 401 时视为会话过期，自动清除本地登录态。
    """
    url = BASE_URL + path
    cookies = _cookies()
    try:
        resp = requests.request(method, url, json=json, params=params,
                                cookies=cookies, timeout=15)
    except requests.exceptions.RequestException as e:
        return None, {"code": -1, "msg": f"无法连接后端服务：{e}", "data": None}

    try:
        body = resp.json()
    except ValueError:
        body = {"code": resp.status_code, "msg": resp.text or "未知响应", "data": None}

    # 同步会话 Cookie：登录时写入；登出（后端删除 Cookie）时清除
    token = resp.cookies.get(SESSION_COOKIE)
    if token:
        st.session_state["session_token"] = token
    elif token is not None and SESSION_COOKIE in resp.cookies:
        st.session_state.pop("session_token", None)
        st.session_state.pop("user", None)

    # 会话过期：非登录接口返回 401 时清除本地登录态
    if body.get("code") == 401 and cookies and path != "/api/auth/login":
        st.session_state.pop("session_token", None)
        st.session_state.pop("user", None)

    return resp, body


def _msg(body: dict) -> str:
    """从响应体提取错误信息"""
    if not body:
        return "未知错误"
    return body.get("msg", "请求失败")


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #
def _get_problems() -> list[dict]:
    """拉取题目列表（简要信息 {id, title}），失败返回空列表"""
    _, body = api("GET", "/api/problems/")
    if body.get("code") != 200:
        return []
    return body.get("data", [])


def _split_tags(s: str) -> list[str]:
    return [t.strip() for t in (s or "").split(",") if t.strip()]


def _clean_cases(rows) -> list[dict]:
    """清洗 data_editor 返回的样例/测试点：去掉空行，统一为 {input, output} 字符串"""
    result = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        inp, out = r.get("input"), r.get("output")
        if inp is None:
            inp = ""
        if out is None:
            out = ""
        inp, out = str(inp), str(out)
        if inp == "" and out == "":
            continue
        result.append({"input": inp, "output": out})
    return result


# --------------------------------------------------------------------------- #
# 任务 1：用户页面组
# --------------------------------------------------------------------------- #
def page_user() -> None:
    st.header("用户中心")

    tab_login, tab_profile, tab_admin = st.tabs(["登录 / 注册", "我的信息", "用户管理"])
    with tab_login:
        _login_register()
    with tab_profile:
        _profile()
    with tab_admin:
        _admin_users()


def _login_register() -> None:
    if is_logged_in():
        st.info("您已登录，如需切换账号请先在侧边栏退出。")
        return

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("登录")
        with st.form("login_form"):
            username = st.text_input("用户名", key="login_u")
            password = st.text_input("密码", type="password", key="login_p")
            if st.form_submit_button("登录"):
                if not username or not password:
                    st.error("用户名和密码不能为空")
                else:
                    _, body = api("POST", "/api/auth/login",
                                  json={"username": username, "password": password})
                    if body.get("code") == 200:
                        st.session_state["user"] = body["data"]
                        st.success(f"登录成功，欢迎 {body['data']['username']}")
                        st.rerun()
                    else:
                        st.error(_msg(body))
    with c2:
        st.subheader("注册")
        with st.form("register_form"):
            username = st.text_input("用户名", key="reg_u")
            password = st.text_input("密码", type="password", key="reg_p")
            confirm = st.text_input("确认密码", type="password", key="reg_c")
            if st.form_submit_button("注册"):
                if not username or not password:
                    st.error("用户名和密码不能为空")
                elif password != confirm:
                    st.error("两次输入的密码不一致")
                else:
                    _, body = api("POST", "/api/users/",
                                  json={"username": username, "password": password})
                    if body.get("code") == 200:
                        st.success("注册成功，请登录")
                    else:
                        st.error(_msg(body))


def _profile() -> None:
    if not is_logged_in():
        st.warning("请先登录。")
        return
    uid = current_user().get("user_id")
    _, body = api("GET", f"/api/users/{uid}")
    if body.get("code") != 200:
        st.error(_msg(body))
        return
    d = body["data"]
    st.subheader("我的信息")
    c1, c2, c3 = st.columns(3)
    c1.metric("用户名", d.get("username"))
    c2.metric("角色", d.get("role"))
    c3.metric("通过题数", d.get("resolve_count"))
    c1.metric("提交次数", d.get("submit_count"))
    c2.metric("注册时间", d.get("join_time"))
    c3.write(f"用户 ID：{d.get('user_id')}")


def _admin_users() -> None:
    if not is_logged_in():
        st.warning("请先登录。")
        return
    if current_user().get("role") != "admin":
        st.warning("用户管理仅对管理员开放。")
        return

    _, body = api("GET", "/api/users/")
    if body.get("code") != 200:
        st.error(_msg(body))
        return
    users = body["data"].get("users", [])

    st.subheader("用户列表")
    if users:
        st.dataframe(pd.DataFrame(users))
    else:
        st.write("暂无用户")

    if users:
        st.subheader("修改用户角色")
        labels = [f"{u['user_id']} - {u['username']}（{u['role']}）" for u in users]
        sel = st.selectbox("选择用户", labels, key="role_sel")
        uid = sel.split(" - ")[0].strip()
        new_role = st.selectbox("新角色", ["user", "admin", "banned"], key="role_new")
        if st.button("确认修改角色"):
            _, b = api("PUT", f"/api/users/{uid}/role", json={"role": new_role})
            if b.get("code") == 200:
                st.success("角色已更新")
                st.rerun()
            else:
                st.error(_msg(b))

    st.subheader("创建管理员账户")
    with st.form("create_admin"):
        au = st.text_input("用户名", key="admin_u")
        ap = st.text_input("密码", type="password", key="admin_p")
        if st.form_submit_button("创建管理员"):
            if not au or not ap:
                st.error("用户名和密码不能为空")
            else:
                _, b = api("POST", "/api/users/admin", json={"username": au, "password": ap})
                if b.get("code") == 200:
                    st.success("管理员创建成功")
                    st.rerun()
                else:
                    st.error(_msg(b))


# --------------------------------------------------------------------------- #
# 任务 2：题目页面组
# --------------------------------------------------------------------------- #
def page_problems() -> None:
    st.header("题目管理")
    if not is_logged_in():
        st.warning("请先登录后再访问题目页面。")
        return

    tab_list, tab_add, tab_edit, tab_del = st.tabs(["题目列表", "新增题目", "编辑题目", "删除题目"])
    with tab_list:
        _problem_list()
    with tab_add:
        _problem_add()
    with tab_edit:
        _problem_edit()
    with tab_del:
        _problem_delete()


def _problem_list() -> None:
    st.subheader("题目列表")
    problems = _get_problems()
    if not problems:
        st.write("暂无题目")
        return
    st.write(f"共 {len(problems)} 题")
    st.dataframe(pd.DataFrame(problems))

    st.subheader("查看题目详情")
    sel = st.selectbox("选择题目", [p["id"] for p in problems], key="detail_sel")
    _, b = api("GET", f"/api/problems/{sel}")
    if b.get("code") != 200:
        st.error(_msg(b))
        return
    p = b["data"]
    st.markdown(f"### {p['title']}")
    st.write(f"**ID**：{p['id']} ｜ **难度**：{p.get('difficulty') or '未设置'} ｜ "
             f"**来源**：{p.get('source') or '未设置'} ｜ **作者**：{p.get('author') or '未设置'}")
    st.write(f"**标签**：{', '.join(p.get('tags', [])) or '无'}")
    st.write(f"**时间限制**：{p.get('time_limit')}s ｜ **内存限制**：{p.get('memory_limit')}MB")
    st.markdown("**题目描述**")
    st.write(p["description"])
    st.markdown("**输入描述**")
    st.write(p["input_description"])
    st.markdown("**输出描述**")
    st.write(p["output_description"])
    st.markdown("**约束**")
    st.write(p["constraints"])
    if p.get("hint"):
        st.markdown("**提示**")
        st.write(p["hint"])
    st.markdown("**样例**")
    for i, s in enumerate(p["samples"], start=1):
        st.markdown(f"样例 {i}：输入 `{s['input']}` ｜ 输出 `{s['output']}`")


def render_problem_form(prefix: str, defaults: dict | None = None) -> dict:
    """渲染题目表单，prefix 作为控件 key 前缀（编辑时随题目切换重置），返回表单数据"""
    d = defaults or {}

    pid = st.text_input("题目 ID", value=d.get("id", ""), key=f"{prefix}_id")
    title = st.text_input("标题", value=d.get("title", ""), key=f"{prefix}_title")
    description = st.text_area("题目描述", value=d.get("description", ""),
                               height=120, key=f"{prefix}_desc")
    c1, c2 = st.columns(2)
    input_desc = c1.text_area("输入描述", value=d.get("input_description", ""),
                              height=100, key=f"{prefix}_in")
    output_desc = c2.text_area("输出描述", value=d.get("output_description", ""),
                               height=100, key=f"{prefix}_out")
    constraints = st.text_area("约束条件", value=d.get("constraints", ""), key=f"{prefix}_cons")
    hint = st.text_area("提示（可选）", value=d.get("hint", ""), key=f"{prefix}_hint")

    c3, c4, c5 = st.columns(3)
    source = c3.text_input("来源（可选）", value=d.get("source", ""), key=f"{prefix}_src")
    author = c4.text_input("作者（可选）", value=d.get("author", ""), key=f"{prefix}_author")
    difficulty = c5.text_input("难度（可选）", value=d.get("difficulty", ""), key=f"{prefix}_diff")
    tags = st.text_input("标签（可选，逗号分隔）",
                         value=", ".join(d.get("tags", [])), key=f"{prefix}_tags")

    c6, c7 = st.columns(2)
    time_limit = c6.number_input("时间限制（秒）", min_value=0.0, max_value=60.0,
                                 value=float(d.get("time_limit", 3.0)), key=f"{prefix}_tl")
    memory_limit = c7.number_input("内存限制（MB）", min_value=1, max_value=2048,
                                   value=int(d.get("memory_limit", 128)), key=f"{prefix}_ml")

    st.markdown("**样例（samples）**")
    samples = st.data_editor(d.get("samples") or [{"input": "", "output": ""}],
                             num_rows="dynamic", key=f"{prefix}_samples")
    st.markdown("**测试点（testcases）**")
    testcases = st.data_editor(d.get("testcases") or [{"input": "", "output": ""}],
                               num_rows="dynamic", key=f"{prefix}_testcases")

    return {
        "id": pid,
        "title": title,
        "description": description,
        "input_description": input_desc,
        "output_description": output_desc,
        "samples": _clean_cases(samples),
        "constraints": constraints,
        "testcases": _clean_cases(testcases),
        "hint": hint,
        "source": source,
        "tags": _split_tags(tags),
        "time_limit": time_limit,
        "memory_limit": int(memory_limit),
        "author": author,
        "difficulty": difficulty,
    }


def _validate_problem_form(p: dict) -> list[str]:
    """提交前格式检查，返回错误信息列表（空表示通过）"""
    errors = []
    if not p["id"]:
        errors.append("题目 ID 不能为空")
    elif not re.fullmatch(r"[A-Za-z0-9_-]+", p["id"]):
        errors.append("题目 ID 只能包含字母、数字、下划线、连字符")
    if not p["title"]:
        errors.append("标题不能为空")
    if not p["description"]:
        errors.append("题目描述不能为空")
    if not p["input_description"]:
        errors.append("输入描述不能为空")
    if not p["output_description"]:
        errors.append("输出描述不能为空")
    if not p["constraints"]:
        errors.append("约束条件不能为空")
    if not p["samples"]:
        errors.append("至少需要一个样例")
    if not p["testcases"]:
        errors.append("至少需要一个测试点")
    return errors


def _problem_add() -> None:
    st.subheader("新增题目")
    draft = st.session_state.get("ai_draft")
    if draft:
        st.success(f"已载入 AI 生成草稿（id: `{draft.get('id')}`），可编辑后提交。")
        prefix = f"add_{draft.get('id')}"
    else:
        prefix = "add"
    p = render_problem_form(prefix, draft)
    if draft and st.button("清空草稿"):
        st.session_state.pop("ai_draft", None)
        st.rerun()
    if st.button("提交新增题目", key=f"submit_{prefix}"):
        errors = _validate_problem_form(p)
        if errors:
            for e in errors:
                st.error(e)
        else:
            _, body = api("POST", "/api/problems/", json=p)
            if body.get("code") == 200:
                st.session_state.pop("ai_draft", None)
                st.success(f"题目 {p['id']} 添加成功")
            else:
                st.error(_msg(body))


def _problem_edit() -> None:
    st.subheader("编辑题目")
    problems = _get_problems()
    if not problems:
        st.write("暂无题目")
        return
    ids = [p["id"] for p in problems]
    sel = st.selectbox("选择要编辑的题目", ids, key="edit_sel")
    _, b = api("GET", f"/api/problems/{sel}")
    if b.get("code") != 200:
        st.error(_msg(b))
        return
    p = render_problem_form(f"edit_{sel}", b["data"])
    if st.button("保存修改"):
        errors = _validate_problem_form(p)
        if errors:
            for e in errors:
                st.error(e)
        else:
            _, b2 = api("PUT", f"/api/problems/{sel}", json=p)
            if b2.get("code") == 200:
                st.success("修改成功")
            else:
                st.error(_msg(b2))


def _problem_delete() -> None:
    st.subheader("删除题目")
    if current_user().get("role") != "admin":
        st.warning("删除题目仅对管理员开放。")
        return
    problems = _get_problems()
    if not problems:
        st.write("暂无题目")
        return
    sel = st.selectbox("选择要删除的题目", [p["id"] for p in problems], key="del_sel")
    st.warning(f"即将删除题目：{sel}（此操作不可恢复）")
    confirm = st.checkbox("我确认删除该题目", key="del_confirm")
    if st.button("删除", disabled=not confirm):
        _, b = api("DELETE", f"/api/problems/{sel}")
        if b.get("code") == 200:
            st.success("删除成功")
            st.rerun()
        else:
            st.error(_msg(b))


# --------------------------------------------------------------------------- #
# 任务 3：评测与提交页面组
# --------------------------------------------------------------------------- #
def page_submissions() -> None:
    st.header("评测与提交")
    if not is_logged_in():
        st.warning("请先登录后再提交代码。")
        return

    tab_submit, tab_list, tab_detail = st.tabs(["代码提交", "提交记录", "提交详情"])
    with tab_submit:
        _submit_code()
    with tab_list:
        _submission_list()
    with tab_detail:
        _submission_detail()


def _submit_code() -> None:
    st.subheader("提交代码")
    problems = _get_problems()
    if not problems:
        st.warning("暂无题目，请先添加题目。")
        return
    _, lb = api("GET", "/api/languages/")
    if lb.get("code") != 200:
        st.error(_msg(lb))
        return
    languages = lb["data"].get("name", [])

    pid = st.selectbox("题目", [p["id"] for p in problems], key="sub_pid")
    lang = st.selectbox("语言", languages, key="sub_lang")
    code = st.text_area("代码", height=300, key="sub_code")

    if st.button("提交评测"):
        if not code.strip():
            st.error("代码不能为空")
        else:
            _, body = api("POST", "/api/submissions/",
                          json={"problem_id": pid, "language": lang, "code": code})
            if body.get("code") != 200:
                st.error(_msg(body))
            else:
                sid = body["data"]["submission_id"]
                st.session_state["last_submission"] = sid
                st.session_state["detail_sid"] = sid
                st.success(f"提交成功，submission_id = {sid}")
                _poll_and_show(sid)


def _poll_and_show(sid: str, max_seconds: int = 30) -> None:
    """提交后轮询评测状态，完成后展示结果（最多轮询 max_seconds 秒）"""
    is_admin = current_user().get("role") == "admin"
    ph = st.empty()
    for _ in range(max_seconds):
        _, b = api("GET", f"/api/submissions/{sid}")
        if b.get("code") != 200:
            ph.error(_msg(b))
            return
        d = b["data"]
        if d["status"] == "pending":
            ph.info(f"评测中……（submission_id={sid}）")
            time.sleep(1)
        else:
            ph.empty()
            render_result(sid, d, is_admin)
            return
    ph.warning("评测时间较长，请稍后到“提交详情”页查看结果。")


def _submission_list() -> None:
    st.subheader("提交记录")
    u = current_user()
    is_admin = u.get("role") == "admin"
    problems = _get_problems()
    pid_options = ["（全部）"] + [p["id"] for p in problems]

    c1, c2 = st.columns(2)
    sel_problem = c1.selectbox("题目", pid_options, key="list_pid")
    sel_status = c2.selectbox("状态", ["（全部）", "pending", "success", "error"], key="list_status")

    params: dict = {}
    if sel_problem != "（全部）":
        params["problem_id"] = sel_problem
    if is_admin:
        uid_filter = st.text_input("用户 ID（管理员可筛选，留空则不限）", key="list_uid").strip()
        if uid_filter:
            params["user_id"] = uid_filter
    else:
        params["user_id"] = u["user_id"]

    # 后端要求 user_id 与 problem_id 至少提供一个
    if "user_id" not in params and "problem_id" not in params:
        st.info("请选择题目或填写用户 ID（后端要求至少一个筛选条件）。")
        return

    if sel_status != "（全部）":
        params["status"] = sel_status

    c3, c4 = st.columns(2)
    page = c3.number_input("页码", min_value=1, value=1, key="list_page")
    page_size = c4.number_input("每页条数", min_value=1, max_value=200, value=50, key="list_psize")
    params["page"] = int(page)
    params["page_size"] = int(page_size)

    _, body = api("GET", "/api/submissions/", params=params)
    if body.get("code") != 200:
        st.error(_msg(body))
        return
    data = body["data"]
    subs = data.get("submissions", [])
    st.write(f"共 {data.get('total', 0)} 条记录，当前页 {len(subs)} 条")
    if subs:
        st.dataframe(pd.DataFrame(subs))
    else:
        st.write("暂无记录")


def _submission_detail() -> None:
    st.subheader("提交详情")
    is_admin = current_user().get("role") == "admin"

    sid = st.text_input("submission_id（提交后自动填入）", key="detail_sid").strip()
    if not sid:
        st.info("请输入 submission_id，或先在“代码提交”页提交一次。")
        return

    if st.button("刷新状态"):
        pass  # 触发一次脚本重跑，重新拉取最新状态

    _, body = api("GET", f"/api/submissions/{sid}")
    if body.get("code") != 200:
        st.error(_msg(body))
        return
    render_result(sid, body["data"], is_admin)


def render_result(sid: str, d: dict, is_admin: bool) -> None:
    """展示一次评测的结果：状态、得分、编译/运行/错误信息、测例详情与日志"""
    st.markdown(f"**submission_id**：`{sid}` ｜ **状态**：`{d['status']}`")

    if d["status"] == "pending":
        st.warning("评测中……（点击“刷新状态”查看最新结果）")
        return
    if d["status"] == "error":
        st.error(f"评测出错：{d.get('error_info') or '未知错误'}")
        return

    score, counts = d.get("score"), d.get("counts")
    st.metric("得分", f"{score} / {counts}")

    ci = d.get("compile_info") or {}
    if ci.get("result") == "error":
        st.error("编译失败：")
        st.code(ci.get("message", ""), language="text")
        return
    if ci.get("result") == "success":
        st.success("编译成功")

    ri = d.get("run_info") or {}
    st.write(f"**运行信息**：{ri.get('result')} — {ri.get('message', '')}")

    # 评测日志（测例明细，受 public_cases / 本人 / 管理员权限控制）
    _, lb = api("GET", f"/api/submissions/{sid}/log")
    if lb.get("code") == 200:
        details = lb["data"].get("details", [])
        if details:
            st.markdown("**测例详情**")
            st.dataframe(pd.DataFrame(details))
    else:
        st.caption(f"评测日志不可见：{_msg(lb)}")

    if is_admin:
        if st.button("重新评测（管理员）", key=f"rejudge_{sid}"):
            _, rb = api("PUT", f"/api/submissions/{sid}/rejudge")
            if rb.get("code") == 200:
                st.success("已重新评测")
                st.rerun()
            else:
                st.error(_msg(rb))


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# AI 智能命题（Advance）
# --------------------------------------------------------------------------- #
def page_ai() -> None:
    st.header("AI 智能命题")
    if not is_logged_in():
        st.warning("请先登录后再使用 AI 命题功能。")
        return

    tab_config, tab_generate = st.tabs(["模型配置", "智能命题"])
    with tab_config:
        _ai_config()
    with tab_generate:
        _ai_generate()


def _ai_config() -> None:
    st.subheader("模型配置")
    _, body = api("GET", "/api/ai/model-config")
    current = body.get("data") if body.get("code") == 200 else {}

    st.info("支持 OpenAI 兼容接口（OpenAI / DeepSeek / Moonshot / Ollama 等），"
            "填写 API 基地址（含版本路径，如 https://api.deepseek.com/v1）。")
    with st.form("ai_config_form"):
        provider_url = st.text_input("提供商 URL（API 基地址）",
                                     value=current.get("provider_url", ""),
                                     placeholder="如 https://api.deepseek.com/v1")
        model = st.text_input("模型名称", value=current.get("model", ""),
                              placeholder="如 deepseek-chat")
        api_key = st.text_input("模型密钥（留空则沿用已保存的密钥）", type="password")
        c1, c2, c3 = st.columns(3)
        input_price = c1.number_input("输入单价", min_value=0.0,
                                      value=float(current.get("input_price", 0.0)), format="%.6f")
        output_price = c2.number_input("输出单价", min_value=0.0,
                                       value=float(current.get("output_price", 0.0)), format="%.6f")
        price_unit = c3.number_input("计价单位（Token）", min_value=1,
                                     value=int(current.get("price_unit", 1000000)))
        if st.form_submit_button("保存配置"):
            if not provider_url.strip() or not model.strip():
                st.error("提供商 URL 与模型名称不能为空")
            else:
                payload = {
                    "provider_url": provider_url.strip(),
                    "model": model.strip(),
                    "input_price": float(input_price),
                    "output_price": float(output_price),
                    "price_unit": int(price_unit),
                }
                if api_key.strip():
                    payload["api_key"] = api_key.strip()
                _, b = api("PUT", "/api/ai/model-config", json=payload)
                if b.get("code") == 200:
                    st.success("模型配置已保存")
                    st.rerun()
                else:
                    st.error(_msg(b))

    if current.get("api_key_configured"):
        st.caption(f"当前：{current.get('model')} @ {current.get('provider_url')}（密钥已配置，不显示明文）")


def _ai_generate() -> None:
    st.subheader("智能命题")
    _, cb = api("GET", "/api/ai/model-config")
    cfg = cb.get("data") if cb.get("code") == 200 else {}
    if not cfg.get("api_key_configured"):
        st.warning("请先在“模型配置”页完成模型配置。")
        return

    problems = _get_problems()
    pid_options = ["（不参考已有题目）"] + [p["id"] for p in problems]
    col1, col2 = st.columns([3, 1])
    requirement = col1.text_area(
        "命题需求（知识点、难度、题型等）", height=120,
        placeholder="如：考察一维前缀和，难度入门，包含边界与大数情况")
    ref = col2.selectbox("参考 / 改编题目", pid_options)

    if st.button("生成题目", type="primary"):
        if not requirement.strip():
            st.error("命题需求不能为空")
        else:
            payload = {"requirement": requirement.strip()}
            if ref != "（不参考已有题目）":
                payload["problem_id"] = ref
            _, b = api("POST", "/api/ai/problem-tasks/", json=payload)
            if b.get("code") != 200:
                st.error(_msg(b))
            else:
                st.session_state["ai_task_id"] = b["data"]["task_id"]
                st.success(f"任务已创建：{b['data']['task_id']}")
                # 短轮询展示实时进度（上限约 8 秒，避免长时间阻塞界面）
                ph = st.empty()
                for _ in range(8):
                    _, tb = api("GET", f"/api/ai/problem-tasks/{b['data']['task_id']}")
                    if tb.get("code") != 200:
                        break
                    t = tb["data"]
                    if t["status"] in ("pending", "running"):
                        ph.info(f"🔄 {t.get('progress') or '处理中…'}")
                        time.sleep(1)
                    else:
                        break
                ph.empty()
                st.rerun()

    _render_ai_task()


def _render_ai_task() -> None:
    task_id = st.session_state.get("ai_task_id")
    if not task_id:
        return
    st.divider()
    st.subheader("任务状态")

    col_r, col_c = st.columns(2)
    col_r.button("刷新进度")
    if col_c.button("取消任务"):
        _, b = api("PUT", f"/api/ai/problem-tasks/{task_id}/cancel")
        if b.get("code") == 200:
            st.warning("任务已取消")
        else:
            st.error(_msg(b))

    _, b = api("GET", f"/api/ai/problem-tasks/{task_id}")
    if b.get("code") != 200:
        st.error(_msg(b))
        return
    task = b["data"]
    _render_ai_status(task)


def _render_ai_status(task: dict) -> None:
    status = task.get("status")
    emoji = {"pending": "⏳", "running": "🔄", "success": "✅", "cancelled": "⛔", "failed": "❌"}
    st.markdown(f"**task_id**：`{task.get('task_id')}` ｜ 状态：{emoji.get(status, '')} **{status}**")

    if status in ("pending", "running"):
        st.info(task.get("progress") or "处理中…")
        st.caption("点击“刷新进度”查看最新进展，或点击“取消任务”中断。")
        return
    if status == "cancelled":
        st.warning("任务已被取消。")
        return
    if status == "failed":
        st.error(f"生成失败：{task.get('error')}")
        return

    usage = task.get("usage") or {}
    if usage:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("输入 Token", usage.get("input_tokens"))
        c2.metric("输出 Token", usage.get("output_tokens"))
        c3.metric("总 Token", usage.get("total_tokens"))
        c4.metric("费用", f"{usage.get('cost')} {usage.get('currency')}")
        if usage.get("estimated"):
            st.caption("模型未返回 usage，Token 按「字符数 / 4」估算；"
                       "费用 = 输入 Token × 输入单价 + 输出 Token × 输出单价（按计价单位折算）。")

    result = task.get("result") or {}
    st.markdown("### 生成结果")
    st.markdown(f"**{result.get('title')}**（id: `{result.get('id')}`，难度：{result.get('difficulty') or '未设置'}）")
    st.write(f"标签：{'、'.join(result.get('tags') or []) or '无'}")
    st.markdown("**题目描述**")
    st.write(result.get("description"))
    st.markdown("**输入描述**")
    st.write(result.get("input_description"))
    st.markdown("**输出描述**")
    st.write(result.get("output_description"))
    st.markdown("**约束条件**")
    st.write(result.get("constraints"))
    st.markdown("**样例**")
    for s in result.get("samples") or []:
        st.markdown(f"- 输入 `{s.get('input')}` ｜ 输出 `{s.get('output')}`")
    st.markdown(f"**测试点**：{len(result.get('testcases') or [])} 个")
    with st.expander("查看完整 JSON"):
        st.json(result)

    col_add, col_edit = st.columns(2)
    if col_add.button("直接新增到题库"):
        _, b = api("POST", "/api/problems/", json=result)
        if b.get("code") == 200:
            st.success(f"题目 `{result.get('id')}` 已新增到题库")
        else:
            st.error(_msg(b))
    if col_edit.button("带入题目新增表单编辑"):
        st.session_state["ai_draft"] = result
        st.session_state["nav"] = "题目管理"
        st.rerun()


def main() -> None:
    st.sidebar.title("⚖️ Online Judge")

    if is_logged_in():
        u = current_user()
        st.sidebar.success(f"已登录：{u.get('username')}（{u.get('role')}）")
        if st.sidebar.button("退出登录"):
            api("POST", "/api/auth/logout")
            st.session_state.clear()
            st.rerun()
    else:
        st.sidebar.warning("未登录")

    page = st.sidebar.radio("导航", ["用户中心", "题目管理", "评测与提交", "AI 智能命题"], key="nav")
    if page == "用户中心":
        page_user()
    elif page == "题目管理":
        page_problems()
    elif page == "评测与提交":
        page_submissions()
    else:
        page_ai()


if __name__ == "__main__":
    main()
