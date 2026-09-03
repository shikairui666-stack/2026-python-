"""
Online Judge 系统 —— Step 1：题目管理 + Step 2：题目评测 + Step 3：评测列表/重测 + Step 4：用户管理

- 使用 FastAPI 的异步接口（async def）实现，评测通过 asyncio.create_task 异步执行
- 题目配置以 JSON 文件形式存入本地 problems/ 目录，每题一个文件
- 语言配置默认内置 python / cpp，支持动态注册，持久化到 languages.json
- 用户持久化到 users.json，会话基于 Cookie Session（session id 存内存）；启动自动创建管理员 admin / admintestpassword
- 所有响应统一为 {code, msg, data} 结构，HTTP 状态码与 code 一致
"""

import asyncio
import bcrypt
import collections
import datetime
import hashlib
import itertools
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

# --------------------------------------------------------------------------- #
# 基础配置
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
PROBLEMS_DIR = BASE_DIR / "problems"
WORKSPACE_DIR = BASE_DIR / "workspace"
LANGUAGES_FILE = BASE_DIR / "languages.json"
USERS_FILE = BASE_DIR / "users.json"

app = FastAPI(title="Online Judge", version="0.3.0")


# --------------------------------------------------------------------------- #
# 统一响应工具
# --------------------------------------------------------------------------- #
def ok(data: Any = None, msg: str = "success") -> JSONResponse:
    """成功响应：HTTP 200 + {code:200, msg, data}"""
    return JSONResponse(status_code=200, content={"code": 200, "msg": msg, "data": data})


def fail(code: int, msg: str) -> JSONResponse:
    """失败响应：HTTP code + {code, msg, data:null}"""
    return JSONResponse(status_code=code, content={"code": code, "msg": msg, "data": None})


# --------------------------------------------------------------------------- #
# 题目字段与校验
# --------------------------------------------------------------------------- #
# 必选字段
REQUIRED_FIELDS = [
    "id", "title", "description", "input_description",
    "output_description", "samples", "constraints", "testcases",
]
# 可选字段及默认值（查询时未设置的字段需返回对应类型的默认值）
OPTIONAL_DEFAULTS = {
    "hint": "",
    "source": "",
    "tags": [],
    "time_limit": 3.0,
    "memory_limit": 128,
    "author": "",
    "difficulty": "",
}
# 这些可选字段在“未提供”时不强制写入默认值，评测时回退到语言配置
NO_DEFAULT_STORED = {"time_limit", "memory_limit"}
# id 只允许字母、数字、下划线、连字符，避免文件名注入
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_cases(value: Any, field: str) -> list[dict]:
    """校验样例/测试点：必须是 list，元素为含 input/output 字符串的对象"""
    if not isinstance(value, list):
        raise ValueError(f"{field} 必须是列表")
    result = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{field}[{i}] 必须是对象")
        if not isinstance(item.get("input"), str):
            raise ValueError(f"{field}[{i}].input 必须是字符串")
        if not isinstance(item.get("output"), str):
            raise ValueError(f"{field}[{i}].output 必须是字符串")
        result.append({"input": item["input"], "output": item["output"]})
    return result


def normalize_problem(payload: Any) -> dict:
    """校验题目配置并补全可选字段默认值，返回规范化后的题目字典。校验失败抛 ValueError。"""
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")

    # 必选字段缺失校验
    for field in REQUIRED_FIELDS:
        if field not in payload:
            raise ValueError(f"缺少必填字段: {field}")

    # id 校验
    pid = payload["id"]
    if not isinstance(pid, str) or not pid:
        raise ValueError("id 必须是非空字符串")
    if not ID_PATTERN.match(pid):
        raise ValueError("id 只能包含字母、数字、下划线和连字符")

    # 字符串字段校验
    for field in ["title", "description", "input_description", "output_description", "constraints"]:
        if not isinstance(payload[field], str):
            raise ValueError(f"{field} 必须是字符串")

    problem = {
        "id": pid,
        "title": payload["title"],
        "description": payload["description"],
        "input_description": payload["input_description"],
        "output_description": payload["output_description"],
        "samples": _validate_cases(payload["samples"], "samples"),
        "constraints": payload["constraints"],
        "testcases": _validate_cases(payload["testcases"], "testcases"),
    }

    # 可选字段：提供则校验类型；未提供时除 time_limit/memory_limit 外使用默认值
    for field, default in OPTIONAL_DEFAULTS.items():
        value = payload.get(field)
        if value is None:
            if field in NO_DEFAULT_STORED:
                continue  # 保持可选，评测时回退到语言配置
            problem[field] = list(default) if isinstance(default, list) else default
            continue
        if field == "tags":
            if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
                raise ValueError("tags 必须是字符串列表")
        elif field == "time_limit":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("time_limit 必须是数字")
            value = float(value)
        elif field == "memory_limit":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("memory_limit 必须是整数")
        elif field in ("hint", "source", "author", "difficulty"):
            if not isinstance(value, str):
                raise ValueError(f"{field} 必须是字符串")
        problem[field] = value

    return problem


# --------------------------------------------------------------------------- #
# 题目存储读写
# --------------------------------------------------------------------------- #
def _problem_path(problem_id: str) -> Path:
    return PROBLEMS_DIR / f"{problem_id}.json"


def load_problem(problem_id: str) -> dict | None:
    path = _problem_path(problem_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_problem(problem: dict) -> None:
    PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_problem_path(problem["id"]), "w", encoding="utf-8") as f:
        json.dump(problem, f, ensure_ascii=False, indent=2)


def _with_defaults(problem: dict) -> dict:
    """读取到的题目补全可选字段默认值，保证返回值字段完整"""
    for field, default in OPTIONAL_DEFAULTS.items():
        if field not in problem:
            problem[field] = list(default) if isinstance(default, list) else default
    return problem


# --------------------------------------------------------------------------- #
# 语言配置
# --------------------------------------------------------------------------- #
DEFAULT_LANGUAGES = {
    "python": {
        "name": "python",
        "file_ext": ".py",
        "compile_cmd": None,
        "run_cmd": "python3 {src}",
        "time_limit": 1.0,
        "memory_limit": 128,
    },
    "cpp": {
        "name": "cpp",
        "file_ext": ".cpp",
        "compile_cmd": "g++ {src} -o {exe} -std=c++14 -O2",
        "run_cmd": "{exe}",
        "time_limit": 1.0,
        "memory_limit": 128,
    },
}

_languages: dict[str, dict] = {}


def _load_languages() -> None:
    global _languages
    _languages = {k: dict(v) for k, v in DEFAULT_LANGUAGES.items()}
    if LANGUAGES_FILE.exists():
        try:
            saved = json.loads(LANGUAGES_FILE.read_text(encoding="utf-8"))
            for name, cfg in saved.items():
                if isinstance(cfg, dict):
                    _languages[name] = cfg
        except (json.JSONDecodeError, OSError):
            pass


def _save_languages() -> None:
    with open(LANGUAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(_languages, f, ensure_ascii=False, indent=2)


def normalize_language(payload: Any) -> dict:
    """校验语言配置，返回规范化字典。校验失败抛 ValueError。"""
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")

    name = payload.get("name")
    if not isinstance(name, str) or not name or not ID_PATTERN.match(name):
        raise ValueError("name 必须是非空字符串（字母/数字/下划线/连字符）")

    file_ext = payload.get("file_ext")
    if not isinstance(file_ext, str) or not file_ext:
        raise ValueError("file_ext 必填，如 .py / .cpp")
    if not file_ext.startswith("."):
        file_ext = "." + file_ext

    run_cmd = payload.get("run_cmd")
    if not isinstance(run_cmd, str) or not run_cmd:
        raise ValueError("run_cmd 必填")

    compile_cmd = payload.get("compile_cmd")
    if compile_cmd is not None and not isinstance(compile_cmd, str):
        raise ValueError("compile_cmd 必须是字符串或 null")

    time_limit = payload.get("time_limit", 1.0)
    if isinstance(time_limit, bool) or not isinstance(time_limit, (int, float)):
        raise ValueError("time_limit 必须是数字")
    memory_limit = payload.get("memory_limit", 128)
    if isinstance(memory_limit, bool) or not isinstance(memory_limit, int):
        raise ValueError("memory_limit 必须是整数")

    return {
        "name": name,
        "file_ext": file_ext,
        "compile_cmd": compile_cmd or None,
        "run_cmd": run_cmd,
        "time_limit": float(time_limit),
        "memory_limit": int(memory_limit),
    }


# --------------------------------------------------------------------------- #
# 用户与鉴权（Step 4：Cookie Session + 用户管理）
# --------------------------------------------------------------------------- #
SESSION_COOKIE = "session"

_users: dict[str, dict] = {}
_sessions: dict[str, str] = {}  # session_id -> user_id


def _hash_password(password: str) -> str:
    """使用 bcrypt 加密密码（带随机盐）。bcrypt 只取前 72 字节，超出部分截断。"""
    data = password.encode("utf-8")[:72]
    return bcrypt.hashpw(data, bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, stored_hash: str) -> bool:
    """校验密码。兼容旧版无盐 SHA-256 数据，命中后调用方负责升级为 bcrypt。"""
    if stored_hash.startswith("$2"):
        try:
            return bcrypt.checkpw(password.encode("utf-8")[:72], stored_hash.encode("utf-8"))
        except ValueError:
            return False
    # 旧数据：无盐 SHA-256（hexdigest）
    return stored_hash == hashlib.sha256(password.encode("utf-8")).hexdigest()


def _next_user_id() -> str:
    max_id = 0
    for uid in _users:
        if uid.isdigit():
            max_id = max(max_id, int(uid))
    return str(max_id + 1)


def _save_users() -> None:
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(_users, f, ensure_ascii=False, indent=2)


def _load_users() -> None:
    global _users
    _users = {}
    if USERS_FILE.exists():
        try:
            _users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _users = {}
    # 兼容旧数据：补齐字段
    for u in _users.values():
        u.setdefault("submit_count", 0)
        u.setdefault("resolve_count", 0)
        u.setdefault("resolved", [])
        u.setdefault("role", "user")
    # 确保初始管理员存在（admin / admintestpassword）
    if not any(u.get("username") == "admin" for u in _users.values()):
        uid = _next_user_id()
        _users[uid] = {
            "user_id": uid,
            "username": "admin",
            "password": _hash_password("admintestpassword"),
            "role": "admin",
            "join_time": datetime.date.today().isoformat(),
            "submit_count": 0,
            "resolve_count": 0,
            "resolved": [],
        }
        _save_users()


def _public_user(u: dict) -> dict:
    """用户对外信息（不含密码、resolved 等内部字段）"""
    return {
        "user_id": u.get("user_id"),
        "username": u.get("username"),
        "join_time": u.get("join_time"),
        "role": u.get("role", "user"),
        "submit_count": u.get("submit_count", 0),
        "resolve_count": u.get("resolve_count", 0),
    }


def _mark_resolved(user_id: str, problem_id: str) -> None:
    """某用户首次完全通过某题时，通过数 +1（按题目去重）"""
    u = _users.get(user_id)
    if u is None:
        return
    resolved = u.setdefault("resolved", [])
    if problem_id not in resolved:
        resolved.append(problem_id)
        u["resolve_count"] = u.get("resolve_count", 0) + 1
        _save_users()


def get_current_user(request: Request) -> dict | None:
    """从 Cookie 中的 session id 解析当前登录用户"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    uid = _sessions.get(token)
    return _users.get(uid) if uid else None


def _require_user(request: Request) -> tuple[dict | None, JSONResponse | None]:
    """要求登录。返回 (user, None) 或 (None, 错误响应)。"""
    user = get_current_user(request)
    if user is None:
        return None, fail(401, "未登录")
    if user.get("role") == "banned":
        return None, fail(403, "用户被禁用")
    return user, None


def _require_admin(request: Request) -> tuple[dict | None, JSONResponse | None]:
    """要求管理员。返回 (user, None) 或 (None, 错误响应)。"""
    user, err = _require_user(request)
    if err is not None:
        return None, err
    if user.get("role") != "admin":
        return None, fail(403, "权限不足")
    return user, None


# --------------------------------------------------------------------------- #
# 提交与评测
# --------------------------------------------------------------------------- #
SUBMISSIONS: dict[str, dict] = {}
_submission_counter = itertools.count(1)
_submit_times: dict[str, collections.deque] = {}  # user_id -> 提交时间队列，用于 1min 内最多 3 次的频率限制


def _new_submission_id() -> str:
    return str(next(_submission_counter))


def _build_command(template: str, src: Path, exe: Path) -> list[str]:
    """将命令模板中的 {src}/{exe} 替换为实际路径并拆分。统一用正斜杠避免 Windows 转义问题。"""
    def _p(p: Path) -> str:
        return str(p).replace("\\", "/")
    return shlex.split(template.replace("{src}", _p(src)).replace("{exe}", _p(exe)))


def _sanitize(text: str, workdir: Path) -> str:
    """从对外返回的错误信息中移除工作目录绝对路径，避免泄露服务器目录"""
    for p in (str(workdir), str(workdir).replace("\\", "/")):
        text = text.replace(p + "/", "").replace(p + "\\", "").replace(p, "")
    return text.strip()


def _normalize_output(s: str) -> list[str]:
    """忽略行末空格与最后一行多余换行后，拆成行列表用于比对"""
    lines = s.replace("\r\n", "\n").split("\n")
    lines = [line.rstrip() for line in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _is_mle(returncode: int | None, stderr: str) -> bool:
    low = (stderr or "").lower()
    for kw in ("memoryerror", "bad_alloc", "out of memory", "cannot allocate", "memory limit"):
        if kw in low:
            return True
    return returncode == -9  # SIGKILL（Linux OOM 杀手）


async def _run_process(cmd: list[str], cwd: Path, stdin_data: str,
                       timeout: float, memory_mb: int) -> tuple[int | None, str, str, bool]:
    """在子进程中运行命令。返回 (returncode, stdout, stderr, 是否超时)。
    阻塞调用放到线程池执行，避免卡住事件循环。"""

    def _preexec() -> None:
        # 仅 POSIX 可用：限制地址空间以实现内存限制
        try:
            import resource
            limit = int(memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except Exception:
            pass

    def _target() -> tuple[int | None, str, str, bool]:
        kwargs: dict = {
            "cwd": str(cwd),
            "input": stdin_data.encode("utf-8"),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": timeout,
        }
        if os.name == "posix":
            kwargs["preexec_fn"] = _preexec
        try:
            proc = subprocess.run(cmd, **kwargs)
            return (proc.returncode,
                    proc.stdout.decode("utf-8", errors="replace"),
                    proc.stderr.decode("utf-8", errors="replace"),
                    False)
        except subprocess.TimeoutExpired:
            return (None, "", "", True)

    return await asyncio.to_thread(_target)


async def _judge_submission(submission_id: str) -> None:
    """后台评测任务：编译（如需）→ 逐个测试点运行比对 → 更新结果。"""
    sub = SUBMISSIONS[submission_id]
    workdir = Path(tempfile.mkdtemp(prefix="oj_", dir=str(WORKSPACE_DIR)))
    try:
        problem = load_problem(sub["problem_id"])
        lang = _languages.get(sub["language"])
        if problem is None or lang is None:
            sub.update(status="error", error_info="problem or language not found",
                       score=0, counts=0, compile_info=None, run_info=None, details=[])
            return

        testcases = problem.get("testcases") or []
        counts = len(testcases) * 10
        # 限制优先级：题目 > 语言 > 默认
        time_limit = problem.get("time_limit") or lang.get("time_limit") or 3.0
        memory_limit = problem.get("memory_limit") or lang.get("memory_limit") or 128

        src = workdir / f"Main{lang['file_ext']}"
        exe = workdir / ("Main.exe" if os.name == "nt" else "Main")
        src.write_text(sub["code"], encoding="utf-8")

        # 编译阶段
        compile_info = None
        if lang.get("compile_cmd"):
            cmd = _build_command(lang["compile_cmd"], src, exe)
            rc, out, err, timed = await _run_process(cmd, workdir, "", timeout=30, memory_mb=512)
            if timed or rc != 0:
                compile_info = {"result": "error", "message": _sanitize((err or out), workdir)}
                sub.update(status="success", score=0, counts=counts, compile_info=compile_info,
                           run_info={"result": "finished", "message": "0 test cases finished"},
                           error_info="", details=[])
                return
            compile_info = {"result": "success", "message": ""}

        # 运行阶段
        run_cmd = _build_command(lang["run_cmd"], src, exe)
        details = []
        score = 0
        for idx, tc in enumerate(testcases, start=1):
            start = time.perf_counter()
            rc, out, err, timed = await _run_process(
                run_cmd, workdir, tc.get("input", ""), timeout=time_limit, memory_mb=memory_limit)
            elapsed = time.perf_counter() - start

            if timed:
                result = "TLE"
            elif rc != 0:
                result = "MLE" if _is_mle(rc, err) else "RE"
            else:
                result = "AC" if _normalize_output(out) == _normalize_output(tc.get("output", "")) else "WA"

            details.append({"id": idx, "result": result,
                            "time": round(elapsed, 3), "memory": 0})
            if result == "AC":
                score += 10

        sub.update(status="success", score=score, counts=counts, compile_info=compile_info,
                   run_info={"result": "finished", "message": f"{len(testcases)} test cases finished"},
                   error_info="", details=details)
        # 满分通过 → 通过数 +1（按题目去重）
        if counts > 0 and score == counts:
            _mark_resolved(sub["user_id"], sub["problem_id"])
    except Exception as e:  # 评测过程出现未预期错误
        sub.update(status="error", error_info=_sanitize(str(e), workdir), score=0, counts=0,
                   compile_info=None, run_info=None, details=[])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 接口：题目管理
# --------------------------------------------------------------------------- #
@app.get("/api/problems/")
async def list_problems(request: Request = None):
    """查看题目列表：返回所有题目的简要信息（需登录）"""
    user, err = _require_user(request)
    if err is not None:
        return err
    problems = []
    if PROBLEMS_DIR.exists():
        for path in sorted(PROBLEMS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            problems.append({"id": data.get("id", ""), "title": data.get("title", "")})
    return ok(problems)


@app.post("/api/problems/")
async def add_problem(payload: dict = Body(...), request: Request = None):
    """添加题目：校验字段完整性，保存到存储目录（需登录）"""
    user, err = _require_user(request)
    if err is not None:
        return err
    try:
        problem = normalize_problem(payload)
    except ValueError as e:
        return fail(400, str(e))
    if _problem_path(problem["id"]).exists():
        return fail(409, f"problem {problem['id']} already exists")
    save_problem(problem)
    return ok({"id": problem["id"]}, "add success")


@app.get("/api/problems/{problem_id}")
async def get_problem(problem_id: str, request: Request = None):
    """查看具体题目信息：返回详细配置（含可选字段默认值，需登录）"""
    user, err = _require_user(request)
    if err is not None:
        return err
    problem = load_problem(problem_id)
    if problem is None:
        return fail(404, "problem not found")
    return ok(_with_defaults(problem))


@app.put("/api/problems/{problem_id}")
async def edit_problem(problem_id: str, payload: dict = Body(...), request: Request = None):
    """编辑题目：校验完整配置并覆盖原题目内容（需登录）"""
    user, err = _require_user(request)
    if err is not None:
        return err
    try:
        problem = normalize_problem(payload)
    except ValueError as e:
        return fail(400, str(e))
    if problem["id"] != problem_id:
        return fail(400, "path id 与请求体 id 不一致")
    if not _problem_path(problem_id).exists():
        return fail(404, "problem not found")
    save_problem(problem)
    return ok({"id": problem_id}, "update success")


@app.delete("/api/problems/{problem_id}")
async def delete_problem(problem_id: str, request: Request = None):
    """删除题目：根据 id 删除配置文件（仅管理员）"""
    user, err = _require_admin(request)
    if err is not None:
        return err
    path = _problem_path(problem_id)
    if not path.exists():
        return fail(404, "problem not found")
    path.unlink()
    return ok({"id": problem_id}, "delete success")


# --------------------------------------------------------------------------- #
# 接口：语言管理（Step 2）
# --------------------------------------------------------------------------- #
@app.get("/api/languages/")
async def list_languages(request: Request = None):
    """查询当前支持的所有编程语言（需登录）"""
    user, err = _require_user(request)
    if err is not None:
        return err
    return ok({"name": sorted(_languages.keys())})


@app.post("/api/languages/")
async def register_language(payload: dict = Body(...), request: Request = None):
    """动态注册新语言（需登录）"""
    user, err = _require_user(request)
    if err is not None:
        return err
    try:
        lang = normalize_language(payload)
    except ValueError as e:
        return fail(400, str(e))
    _languages[lang["name"]] = lang
    _save_languages()
    return ok({"name": lang["name"]}, "language registered")


# --------------------------------------------------------------------------- #
# 接口：用户与鉴权（Step 4）
# --------------------------------------------------------------------------- #
def _validate_new_user(username: Any, password: Any) -> JSONResponse | None:
    """注册/建号公共校验。返回 None 表示通过，否则返回错误响应。"""
    if not isinstance(username, str) or not username:
        return fail(400, "username 必填")
    if not isinstance(password, str) or not password:
        return fail(400, "password 必填")
    if not (3 <= len(username) <= 40):
        return fail(400, "用户名长度需为 3-40 字符")
    if len(password) < 6:
        return fail(400, "密码长度至少 6 位")
    if any(u.get("username") == username for u in _users.values()):
        return fail(400, "用户名已存在")
    return None


@app.post("/api/auth/login")
async def login(payload: dict = Body(...)):
    """登录：校验用户名密码，写入 Cookie Session"""
    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(username, str) or not username:
        return fail(400, "username 必填")
    if not isinstance(password, str) or not password:
        return fail(400, "password 必填")
    for u in _users.values():
        if u.get("username") == username:
            if not _verify_password(password, u.get("password", "")):
                return fail(401, "用户名或密码错误")
            # 旧版无盐 SHA-256 数据命中后透明升级为 bcrypt
            if not u.get("password", "").startswith("$2"):
                u["password"] = _hash_password(password)
                _save_users()
            if u.get("role") == "banned":
                return fail(403, "用户被禁用")
            token = secrets.token_hex(16)
            _sessions[token] = u["user_id"]
            resp = ok({
                "user_id": u["user_id"],
                "username": u["username"],
                "role": u["role"],
            }, "login success")
            resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
            return resp
    return fail(401, "用户名或密码错误")


@app.post("/api/auth/logout")
async def logout(request: Request):
    """登出：清除服务器端 session 与 Cookie"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or token not in _sessions:
        return fail(401, "未登录")
    _sessions.pop(token, None)
    resp = ok(None, "logout success")
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.post("/api/users/")
async def register(payload: dict = Body(...)):
    """注册普通用户（role 固定为 user）"""
    username = payload.get("username")
    password = payload.get("password")
    err = _validate_new_user(username, password)
    if err is not None:
        return err
    uid = _next_user_id()
    _users[uid] = {
        "user_id": uid,
        "username": username,
        "password": _hash_password(password),
        "role": "user",
        "join_time": datetime.date.today().isoformat(),
        "submit_count": 0,
        "resolve_count": 0,
        "resolved": [],
    }
    _save_users()
    return ok(_public_user(_users[uid]), "register success")


@app.post("/api/users/admin")
async def create_admin(payload: dict = Body(...), request: Request = None):
    """创建管理员账户（仅管理员）"""
    _, err = _require_admin(request)
    if err is not None:
        return err
    username = payload.get("username")
    password = payload.get("password")
    err = _validate_new_user(username, password)
    if err is not None:
        return err
    uid = _next_user_id()
    _users[uid] = {
        "user_id": uid,
        "username": username,
        "password": _hash_password(password),
        "role": "admin",
        "join_time": datetime.date.today().isoformat(),
        "submit_count": 0,
        "resolve_count": 0,
        "resolved": [],
    }
    _save_users()
    return ok({"user_id": uid, "username": username}, "success")


@app.get("/api/users/{user_id}")
async def get_user(user_id: str, request: Request = None):
    """查询用户信息（仅本人或管理员）"""
    user, err = _require_user(request)
    if err is not None:
        return err
    target = _users.get(user_id)
    if target is None:
        return fail(404, "user not found")
    if user.get("role") != "admin" and user["user_id"] != user_id:
        return fail(403, "权限不足")
    return ok(_public_user(target))


@app.put("/api/users/{user_id}/role")
async def change_role(user_id: str, payload: dict = Body(...), request: Request = None):
    """变更用户权限（仅管理员），role ∈ {user, admin, banned}"""
    _, err = _require_admin(request)
    if err is not None:
        return err
    target = _users.get(user_id)
    if target is None:
        return fail(404, "user not found")
    role = payload.get("role")
    if role not in ("user", "admin", "banned"):
        return fail(400, "role 必须是 user/admin/banned 之一")
    target["role"] = role
    _save_users()
    return ok({"user_id": user_id, "role": role}, "role updated")


@app.get("/api/users/")
async def list_users(page: str | None = None, page_size: str | None = None,
                     request: Request = None):
    """用户列表（仅管理员），支持分页"""
    _, err = _require_admin(request)
    if err is not None:
        return err

    # 分页参数校验（语义与 GET /api/submissions/ 一致）
    page_i: int | None = None
    size_i: int | None = None
    if page is not None and page_size is None:
        return fail(400, "page 非空但 page_size 为空")
    if page is None and page_size is not None:
        try:
            size_i = int(page_size)
        except ValueError:
            return fail(400, "page_size 必须是整数")
        page_i = 1
    elif page is not None and page_size is not None:
        try:
            page_i, size_i = int(page), int(page_size)
        except ValueError:
            return fail(400, "page/page_size 必须是整数")
    if size_i is not None and size_i < 1:
        return fail(400, "page_size 必须 >= 1")
    if page_i is not None and page_i < 1:
        return fail(400, "page 必须 >= 1")

    users = sorted(_users.values(), key=lambda u: int(u.get("user_id", "0") or 0))
    total = len(users)
    if page_i is not None and size_i is not None:
        start = (page_i - 1) * size_i
        users = users[start:start + size_i]
    return ok({"total": total, "users": [_public_user(u) for u in users]})


# --------------------------------------------------------------------------- #
# 接口：提交评测（Step 2）
# --------------------------------------------------------------------------- #
@app.post("/api/submissions/")
async def submit(payload: dict = Body(...), request: Request = None):
    """提交评测：需登录，校验后创建 pending 评测，后台异步执行"""
    user, err = _require_user(request)
    if err is not None:
        return err

    problem_id = payload.get("problem_id")
    language = payload.get("language")
    code = payload.get("code")
    if not isinstance(problem_id, str) or not problem_id:
        return fail(400, "problem_id 必填")
    if not isinstance(language, str) or not language:
        return fail(400, "language 必填")
    if not isinstance(code, str) or not code:
        return fail(400, "code 必填")

    if load_problem(problem_id) is None:
        return fail(404, "problem not found")
    if language not in _languages:
        return fail(404, "language not found")

    # 频率限制：1min 内每用户最多 3 次提交
    now = time.time()
    times = _submit_times.setdefault(user["user_id"], collections.deque())
    while times and now - times[0] > 60:
        times.popleft()
    if len(times) >= 3:
        return fail(429, "提交频率超限，1min 内最多 3 次")
    times.append(now)

    submission_id = _new_submission_id()
    SUBMISSIONS[submission_id] = {
        "submission_id": submission_id,
        "user_id": user["user_id"],
        "problem_id": problem_id,
        "language": language,
        "code": code,
        "status": "pending",
        "score": None,
        "counts": None,
        "compile_info": None,
        "run_info": None,
        "error_info": None,
        "details": [],
    }
    # 统计：提交数 +1
    user["submit_count"] = user.get("submit_count", 0) + 1
    _save_users()
    asyncio.create_task(_judge_submission(submission_id))
    return ok({"submission_id": submission_id, "status": "pending"})


@app.get("/api/submissions/{submission_id}")
async def get_submission(submission_id: str, request: Request = None):
    """查询评测结果：需登录，仅本人或管理员可查"""
    user, err = _require_user(request)
    if err is not None:
        return err
    sub = SUBMISSIONS.get(submission_id)
    if sub is None:
        return fail(404, "submission not found")
    if user.get("role") != "admin" and sub.get("user_id") != user["user_id"]:
        return fail(403, "权限不足")
    return ok({
        "submission_id": sub["submission_id"],
        "status": sub["status"],
        "score": sub.get("score"),
        "counts": sub.get("counts"),
        "compile_info": sub.get("compile_info"),
        "run_info": sub.get("run_info"),
        "error_info": sub.get("error_info"),
    })


# --------------------------------------------------------------------------- #
# 接口：评测列表 / 重新评测（Step 3）
# --------------------------------------------------------------------------- #
@app.get("/api/submissions/")
async def list_submissions(
    user_id: str | None = None,
    problem_id: str | None = None,
    status: str | None = None,
    page: str | None = None,
    page_size: str | None = None,
    request: Request = None,
):
    """评测列表：分页 + 多条件筛选 + 权限（普通用户仅见本人，管理员可见全部）"""
    user, err = _require_user(request)
    if err is not None:
        return err

    # 一级条件不可全空
    if not user_id and not problem_id:
        return fail(400, "user_id 与 problem_id 至少提供一个")

    # 分页参数校验
    page_i: int | None = None
    size_i: int | None = None
    if page is not None and page_size is None:
        return fail(400, "page 非空但 page_size 为空")
    if page is None and page_size is not None:
        page_i, size_i = 1, None
        try:
            size_i = int(page_size)
        except ValueError:
            return fail(400, "page_size 必须是整数")
    elif page is not None and page_size is not None:
        try:
            page_i, size_i = int(page), int(page_size)
        except ValueError:
            return fail(400, "page/page_size 必须是整数")
    if size_i is not None and size_i < 1:
        return fail(400, "page_size 必须 >= 1")
    if page_i is not None and page_i < 1:
        return fail(400, "page 必须 >= 1")

    # 权限：普通用户未提供 user_id 时只能看自己的记录
    if user.get("role") != "admin":
        if user_id and user_id != user["user_id"]:
            return fail(403, "权限不足")
        user_id = user["user_id"]

    # 筛选
    subs = list(SUBMISSIONS.values())
    if user_id:
        subs = [s for s in subs if s.get("user_id") == user_id]
    if problem_id:
        subs = [s for s in subs if s.get("problem_id") == problem_id]
    if status:
        subs = [s for s in subs if s.get("status") == status]

    # 按提交 id 升序
    subs.sort(key=lambda s: int(s["submission_id"]))

    total = len(subs)
    # 分页切片
    if page_i is not None and size_i is not None:
        start = (page_i - 1) * size_i
        subs = subs[start:start + size_i]

    # 构造返回：pending/error 只返回 submission_id + status
    items = []
    for s in subs:
        if s.get("status") in ("pending", "error"):
            items.append({"submission_id": s["submission_id"], "status": s["status"]})
        else:
            items.append({
                "submission_id": s["submission_id"],
                "status": s["status"],
                "score": s.get("score"),
                "counts": s.get("counts"),
            })
    return ok({"total": total, "submissions": items})


@app.put("/api/submissions/{submission_id}/rejudge")
async def rejudge(submission_id: str, request: Request = None):
    """重新评测：仅管理员，重置为 pending 后重新执行"""
    user, err = _require_admin(request)
    if err is not None:
        return err
    sub = SUBMISSIONS.get(submission_id)
    if sub is None:
        return fail(404, "submission not found")
    sub.update(status="pending", score=None, counts=None, compile_info=None,
               run_info=None, error_info=None, details=[])
    asyncio.create_task(_judge_submission(submission_id))
    return ok({"submission_id": submission_id, "status": "pending"}, "rejudge started")


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
_load_languages()
_load_users()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)