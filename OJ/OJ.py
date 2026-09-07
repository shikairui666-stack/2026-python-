"""
Online Judge 系统 —— Step 1-6 基础模块（题目/评测/用户/日志/前端）+ Advance AI 智能命题

- 使用 FastAPI 的异步接口（async def）实现，评测通过 asyncio.create_task 异步执行
- 题目配置以 JSON 文件形式存入本地 problems/ 目录，每题一个文件
- 语言配置默认内置 python / cpp，支持动态注册，持久化到 languages.json
- 用户持久化到 users.json，会话基于 Cookie Session（session id 存内存）；启动自动创建管理员 admin / admintestpassword
- 评测日志按 submission_id 记录（内存），支持按提交查询；日志可见性受角色 + 题目公开开关控制
- 日志访问操作写入审计记录，管理员可查询审计日志
- 所有响应统一为 {code, msg, data} 结构，HTTP 状态码与 code 一致
"""

import asyncio
import bcrypt
import collections
import datetime
import hashlib
import httpx
import itertools
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
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
VISIBILITY_FILE = BASE_DIR / "visibility.json"
MODEL_CONFIG_FILE = BASE_DIR / "model_config.json"
SUBMISSIONS_FILE = BASE_DIR / "submissions.json"

app = FastAPI(title="Online Judge", version="0.5.0")


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


def _save_submissions() -> None:
    """提交记录持久化到 submissions.json（重启后端后仍可查看历史提交）"""
    with open(SUBMISSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(SUBMISSIONS, f, ensure_ascii=False, indent=2)


def _load_submissions() -> None:
    """启动时加载历史提交，并恢复提交 id 计数器，避免重启后 id 冲突"""
    global SUBMISSIONS, _submission_counter
    SUBMISSIONS = {}
    if SUBMISSIONS_FILE.exists():
        try:
            SUBMISSIONS = json.loads(SUBMISSIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            SUBMISSIONS = {}
    max_id = 0
    for sid in SUBMISSIONS:
        if sid.isdigit():
            max_id = max(max_id, int(sid))
    _submission_counter = itertools.count(max_id + 1)


# --------------------------------------------------------------------------- #
# 评测日志与审计（Step 5）
# --------------------------------------------------------------------------- #
LOGS: dict[str, list[dict]] = {}   # submission_id -> [日志条目]
AUDIT_LOGS: list[dict] = []        # 日志访问审计记录：{user_id, problem_id, action, time, status}
_LOG_MAX_MSG_LEN = 2000            # 内部日志内容裁剪上限
_visibility: dict[str, bool] = {}  # problem_id -> 日志是否公开（public_cases）


def _append_log(submission_id: str, level: str, stage: str, message: str) -> None:
    """追加一条内部评测日志（消息超长时裁剪，避免日志无限膨胀）"""
    LOGS.setdefault(submission_id, []).append({
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
        "level": level,
        "stage": stage,
        "message": message[:_LOG_MAX_MSG_LEN],
    })


def _load_visibility() -> None:
    global _visibility
    _visibility = {}
    if VISIBILITY_FILE.exists():
        try:
            _visibility = json.loads(VISIBILITY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _visibility = {}


def _save_visibility() -> None:
    with open(VISIBILITY_FILE, "w", encoding="utf-8") as f:
        json.dump(_visibility, f, ensure_ascii=False, indent=2)


def is_log_public(problem_id: str) -> bool:
    """题目是否开启日志公开（public_cases）"""
    return bool(_visibility.get(problem_id, False))


def _append_access_audit(user_id: str, problem_id: str, status: str) -> None:
    """记录一次日志访问，供管理员审计（action 仅 view_logs）"""
    AUDIT_LOGS.append({
        "user_id": user_id,
        "problem_id": problem_id,
        "action": "view_logs",
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
        "status": status,
    })


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


def _peak_memory_mb() -> float:
    """读取已回收子进程的峰值内存（RUSAGE_CHILDREN），返回 MB。仅 POSIX 有效，否则 0。"""
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        # Linux 返回 KB，macOS 返回 bytes
        if sys.platform == "darwin":
            return round(rss / (1024 * 1024), 1)
        return round(rss / 1024, 1)
    except Exception:
        return 0.0


async def _run_process(cmd: list[str], cwd: Path, stdin_data: str,
                       timeout: float, memory_mb: int) -> tuple[int | None, str, str, bool, float]:
    """在子进程中运行命令。返回 (returncode, stdout, stderr, 是否超时, 峰值内存 MB)。
    阻塞调用放到线程池执行，避免卡住事件循环。内存限制通过 RLIMIT_AS 实现，峰值内存用 getrusage 统计。"""

    def _preexec() -> None:
        # 仅 POSIX 可用：限制地址空间以实现内存限制
        try:
            import resource
            limit = int(memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except Exception:
            pass

    def _target() -> tuple[int | None, str, str, bool, float]:
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
                    False,
                    _peak_memory_mb())
        except subprocess.TimeoutExpired:
            return (None, "", "", True, _peak_memory_mb())

    return await asyncio.to_thread(_target)


async def _judge_submission(submission_id: str) -> None:
    """后台评测任务：编译（如需）→ 逐个测试点运行比对 → 更新结果，并记录评测日志。"""
    sub = SUBMISSIONS[submission_id]
    LOGS[submission_id] = []  # 每次评测（含重测）从空日志开始
    _append_log(submission_id, "info", "start", f"评测开始: problem={sub['problem_id']} language={sub['language']}")
    workdir = Path(tempfile.mkdtemp(prefix="oj_", dir=str(WORKSPACE_DIR)))
    try:
        problem = load_problem(sub["problem_id"])
        lang = _languages.get(sub["language"])
        if problem is None or lang is None:
            _append_log(submission_id, "error", "load", "题目或语言不存在")
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
            _append_log(submission_id, "info", "compile", "开始编译")
            cmd = _build_command(lang["compile_cmd"], src, exe)
            rc, out, err, timed, _ = await _run_process(cmd, workdir, "", timeout=30, memory_mb=512)
            if timed or rc != 0:
                msg = _sanitize((err or out), workdir)
                compile_info = {"result": "error", "message": msg}
                _append_log(submission_id, "error", "compile", f"编译失败: {msg}")
                sub.update(status="success", score=0, counts=counts, compile_info=compile_info,
                           run_info={"result": "finished", "message": "0 test cases finished"},
                           error_info="", details=[])
                return
            compile_info = {"result": "success", "message": ""}
            _append_log(submission_id, "info", "compile", "编译成功")

        # 运行阶段
        run_cmd = _build_command(lang["run_cmd"], src, exe)
        details = []
        score = 0
        for idx, tc in enumerate(testcases, start=1):
            start = time.perf_counter()
            rc, out, err, timed, mem = await _run_process(
                run_cmd, workdir, tc.get("input", ""), timeout=time_limit, memory_mb=memory_limit)
            elapsed = time.perf_counter() - start

            if timed:
                result = "TLE"
            elif rc != 0:
                result = "MLE" if _is_mle(rc, err) else "RE"
            else:
                result = "AC" if _normalize_output(out) == _normalize_output(tc.get("output", "")) else "WA"

            details.append({"id": idx, "result": result,
                            "time": round(elapsed, 3), "memory": mem})
            if result == "AC":
                score += 10
            _append_log(submission_id, "info", f"testcase_{idx}",
                        f"测试点 {idx}: {result} (time={round(elapsed, 3)}s)")

        sub.update(status="success", score=score, counts=counts, compile_info=compile_info,
                   run_info={"result": "finished", "message": f"{len(testcases)} test cases finished"},
                   error_info="", details=details)
        _append_log(submission_id, "info", "finish", f"评测完成: score={score}/{counts}")
        # 满分通过 → 通过数 +1（按题目去重）
        if counts > 0 and score == counts:
            _mark_resolved(sub["user_id"], sub["problem_id"])
    except Exception as e:  # 评测过程出现未预期错误
        msg = _sanitize(str(e), workdir)
        _append_log(submission_id, "error", "exception", f"评测异常: {msg}")
        sub.update(status="error", error_info=msg, score=0, counts=0,
                   compile_info=None, run_info=None, details=[])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        _save_submissions()


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


@app.put("/api/users/{user_id}/password")
async def change_password(user_id: str, payload: dict = Body(...), request: Request = None):
    """修改密码（本人或管理员）。本人改自己需验证旧密码；管理员改他人无需旧密码。"""
    user, err = _require_user(request)
    if err is not None:
        return err
    target = _users.get(user_id)
    if target is None:
        return fail(404, "用户不存在")

    new_password = payload.get("new_password")
    if not isinstance(new_password, str) or len(new_password) < 6:
        return fail(400, "新密码长度至少 6 位")

    if user_id == user["user_id"]:
        # 本人修改：需验证旧密码
        old_password = payload.get("old_password")
        if not isinstance(old_password, str) or not _verify_password(old_password, target.get("password", "")):
            return fail(400, "旧密码错误")
    elif user.get("role") != "admin":
        return fail(403, "权限不足")

    target["password"] = _hash_password(new_password)
    _save_users()
    return ok(None, "密码修改成功")


@app.put("/api/users/{user_id}/username")
async def change_username(user_id: str, payload: dict = Body(...), request: Request = None):
    """修改用户名（本人或管理员）。本人改自己需验证密码；管理员改他人无需密码。"""
    user, err = _require_user(request)
    if err is not None:
        return err
    target = _users.get(user_id)
    if target is None:
        return fail(404, "用户不存在")

    new_username = payload.get("new_username")
    if not isinstance(new_username, str) or not (3 <= len(new_username) <= 40):
        return fail(400, "用户名长度需为 3-40 字符")
    if any(u.get("username") == new_username for u in _users.values() if u is not target):
        return fail(400, "用户名已存在")

    if user_id == user["user_id"]:
        password = payload.get("password")
        if not isinstance(password, str) or not _verify_password(password, target.get("password", "")):
            return fail(400, "密码错误")
    elif user.get("role") != "admin":
        return fail(403, "权限不足")

    target["username"] = new_username
    _save_users()
    return ok({"user_id": user_id, "username": new_username}, "用户名修改成功")


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
    _save_submissions()
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
    _save_submissions()
    asyncio.create_task(_judge_submission(submission_id))
    return ok({"submission_id": submission_id, "status": "pending"}, "rejudge started")


# --------------------------------------------------------------------------- #
# 接口：评测日志与权限（Step 5）
# --------------------------------------------------------------------------- #
@app.get("/api/submissions/{submission_id}/log")
async def get_submission_log(submission_id: str, request: Request = None):
    """查询评测日志（测例明细 + 得分）：管理员/本人可见；题目开启 public_cases 后所有登录用户可见。"""
    user, err = _require_user(request)
    if err is not None:
        return err
    sub = SUBMISSIONS.get(submission_id)
    if sub is None:
        return fail(404, "submission not found")

    problem_id = sub.get("problem_id", "")
    allowed = (user.get("role") == "admin"
               or sub.get("user_id") == user.get("user_id")
               or is_log_public(problem_id))
    # 审计：记录本次访问及其状态（未登录 / 提交不存在时已提前返回，不记录）
    _append_access_audit(user["user_id"], problem_id, "200" if allowed else "403")
    if not allowed:
        return fail(403, "权限不足")
    return ok({
        "details": sub.get("details", []),
        "score": sub.get("score"),
        "counts": sub.get("counts"),
    })


@app.put("/api/problems/{problem_id}/log_visibility")
async def set_log_visibility(problem_id: str, payload: dict = Body(...), request: Request = None):
    """配置日志可见性（仅管理员）：public_cases 决定日志是否向所有用户公开。"""
    _, err = _require_admin(request)
    if err is not None:
        return err
    if load_problem(problem_id) is None:
        return fail(404, "problem not found")
    public = payload.get("public_cases", False)
    if not isinstance(public, bool):
        return fail(400, "public_cases 必须是布尔值")
    _visibility[problem_id] = public
    _save_visibility()
    return ok({"problem_id": problem_id, "public_cases": public}, "log visibility updated")


@app.get("/api/logs/access/")
async def list_log_access(user_id: str | None = None, problem_id: str | None = None,
                          page: str | None = None, page_size: str | None = None,
                          request: Request = None):
    """查询日志访问审计记录（仅管理员），支持按用户/题目筛选与分页。"""
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

    items = list(AUDIT_LOGS)
    if user_id:
        items = [a for a in items if a.get("user_id") == user_id]
    if problem_id:
        items = [a for a in items if a.get("problem_id") == problem_id]
    if page_i is not None and size_i is not None:
        start = (page_i - 1) * size_i
        items = items[start:start + size_i]
    return ok(items)


@app.post("/api/reset/")
async def reset_system(request: Request = None):
    """系统重置：清空用户/题目/提交/日志/审计/可见性，退出登录，重建初始管理员（仅管理员）。"""
    _, err = _require_admin(request)
    if err is not None:
        return err

    global _users, _languages, _submission_counter, _ai_task_counter
    if PROBLEMS_DIR.exists():
        for p in PROBLEMS_DIR.glob("*.json"):
            p.unlink()
    _visibility.clear()
    _save_visibility()
    SUBMISSIONS.clear()
    LOGS.clear()
    AUDIT_LOGS.clear()
    AI_TASKS.clear()
    _sessions.clear()
    _submit_times.clear()
    _submission_counter = itertools.count(1)
    _ai_task_counter = itertools.count(1)
    _users = {}
    _save_users()
    _load_users()
    _languages = {k: dict(v) for k, v in DEFAULT_LANGUAGES.items()}
    _save_languages()
    return ok(None, "system reset successfully")


# --------------------------------------------------------------------------- #
# AI 智能命题（Advance）
# --------------------------------------------------------------------------- #
# 模型配置：provider_url / model / api_key / 单价 / 计价单位。api_key 属于敏感信息，
# 只保存在本地 model_config.json，任何查询响应 / 日志 / 错误信息中都不返回明文。
_model_config: dict[str, Any] = {}
AI_TASKS: dict[str, dict] = {}
_ai_task_counter = itertools.count(1)


def _load_model_config() -> None:
    global _model_config
    _model_config = {}
    if MODEL_CONFIG_FILE.exists():
        try:
            _model_config = json.loads(MODEL_CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _model_config = {}


def _save_model_config() -> None:
    with open(MODEL_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(_model_config, f, ensure_ascii=False, indent=2)


def _safe_model_config() -> dict:
    """对外返回的模型配置：api_key 用布尔标记代替，绝不返回明文。"""
    return {
        "provider_url": _model_config.get("provider_url", ""),
        "model": _model_config.get("model", ""),
        "api_key_configured": bool(_model_config.get("api_key")),
        "input_price": _model_config.get("input_price", 0.0),
        "output_price": _model_config.get("output_price", 0.0),
        "price_unit": _model_config.get("price_unit", 1000000),
    }


def _new_ai_task_id() -> str:
    return f"ai-task-{next(_ai_task_counter)}"


def _is_cancelled(task: dict) -> bool:
    """任务是否已被请求中断；是则落盘 cancelled 状态并返回 True。"""
    if task.get("cancel_requested"):
        task["status"] = "cancelled"
        task["progress"] = "任务已取消"
        return True
    return False


def _accumulate_usage(total: dict, usage: dict) -> None:
    for k in ("input_tokens", "output_tokens", "total_tokens"):
        total[k] = int(total.get(k, 0)) + int(usage.get(k, 0) or 0)
    if usage.get("estimated"):
        total["estimated"] = True


def _build_usage(total: dict) -> dict:
    """按配置的单价与计价单位统计费用。模型未返回 usage 时标记 estimated。"""
    inp = int(total.get("input_tokens", 0))
    out = int(total.get("output_tokens", 0))
    unit = int(_model_config.get("price_unit", 1000000) or 1000000)
    iprice = float(_model_config.get("input_price", 0.0) or 0.0)
    oprice = float(_model_config.get("output_price", 0.0) or 0.0)
    cost = inp / unit * iprice + out / unit * oprice
    result = {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
        "cost": round(cost, 6),
        "currency": "USD",
    }
    if total.get("estimated"):
        result["estimated"] = True
    return result


def _extract_json(text: str) -> dict:
    """从模型输出中提取首个完整 JSON 对象（容忍 ```json``` 代码块包裹与前后废话）。"""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("模型输出中未找到 JSON 对象")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("模型输出的 JSON 对象未闭合")


def _build_system_prompt() -> str:
    return (
        "你是一名资深 OJ 命题专家。请根据用户提出的命题需求，设计一道完整、可直接用于在线评测系统的编程题。\n"
        "你必须只输出一个 JSON 对象，不要输出任何解释文字或 markdown 代码块。\n"
        "JSON 字段要求：\n"
        "- 必填字段：id（字符串，仅字母/数字/下划线/连字符）、title、description、input_description、"
        "output_description、samples（数组，元素为 {\"input\", \"output\"}，至少 1 个）、"
        "constraints（数据范围与限制）、testcases（数组，元素为 {\"input\", \"output\"}，至少 3 个，需覆盖边界与不同规模）。\n"
        "- 可选字段：hint、source、tags（字符串数组）、time_limit（浮点，秒）、memory_limit（整数，MB）、author、difficulty。\n"
        "要求：测试点必须包含边界情况（最小值、最大值、空输入、大规模输入）与一般情况；"
        "题面与输入输出格式清晰自洽；难度与考察知识点符合需求。"
    )


def _build_user_prompt(requirement: str, problem_id: str | None) -> str:
    if problem_id:
        existing = load_problem(problem_id)
        if existing:
            return (
                f"命题需求：{requirement}\n\n"
                f"请参考/改编下面这道已有题目（id={problem_id}）的配置，可以修改背景、考察内容或加强测试用例，"
                f"但保留需求中要求的考查目标：\n"
                f"{json.dumps(existing, ensure_ascii=False, indent=2)}"
            )
    return f"命题需求：{requirement}"


async def _call_llm(messages: list[dict], task: dict) -> tuple[str, dict]:
    """调用 OpenAI 兼容 /chat/completions（流式优先，失败回退非流式）。

    返回 (完整文本, usage)。provider 未返回 usage 时按「字符数/4」估算 Token 并标记 estimated。
    流式期间每收到一定量内容即更新 task["progress"]，实现实时进度；检测到中断请求则立即终止请求。
    """
    provider_url = (_model_config.get("provider_url") or "").rstrip("/")
    model = _model_config.get("model") or ""
    api_key = _model_config.get("api_key") or ""
    if not provider_url or not model:
        raise RuntimeError("模型未配置或配置不完整")

    url = provider_url if provider_url.endswith("/chat/completions") else provider_url + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    prompt_text = "\n".join(m.get("content", "") for m in messages)
    payload = {"model": model, "messages": messages, "temperature": 0.7, "stream": True}

    content_parts: list[str] = []
    usage: dict = {}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise RuntimeError(f"模型服务返回 {resp.status_code}: {body[:500]}")
                ctype = resp.headers.get("content-type", "") or ""
                if "text/event-stream" in ctype:
                    async for line in resp.aiter_lines():
                        if task.get("cancel_requested"):
                            break
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(chunk.get("usage"), dict):
                            usage = chunk["usage"]
                        choices = chunk.get("choices") or []
                        if choices and (choices[0].get("delta") or {}).get("content"):
                            content_parts.append(choices[0]["delta"]["content"])
                            if len(content_parts) % 8 == 0:
                                task["progress"] = f"正在生成题目… 已生成 {sum(len(c) for c in content_parts)} 字符"
                else:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    data = json.loads(body)
                    usage = data.get("usage") or {}
                    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                    content_parts.append(content)
    except httpx.HTTPError as e:
        raise RuntimeError(f"模型请求失败：{e}") from e

    text = "".join(content_parts)
    # OpenAI 兼容接口返回 prompt_tokens/completion_tokens，统一映射到 input/output
    if usage:
        if "input_tokens" not in usage and usage.get("prompt_tokens") is not None:
            usage["input_tokens"] = usage["prompt_tokens"]
        if "output_tokens" not in usage and usage.get("completion_tokens") is not None:
            usage["output_tokens"] = usage["completion_tokens"]
    if not usage or not usage.get("total_tokens"):
        usage = {
            "input_tokens": max(1, len(prompt_text) // 4),
            "output_tokens": max(1, len(text) // 4),
            "total_tokens": max(1, (len(prompt_text) + len(text)) // 4),
            "estimated": True,
        }
    usage.setdefault("input_tokens", 0)
    usage.setdefault("output_tokens", 0)
    usage.setdefault("total_tokens", int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)))
    return text, usage


async def _run_ai_task(task_id: str) -> None:
    """后台执行命题任务：解析需求 -> 流式生成 -> 校验整理（失败自动修正一次）。"""
    task = AI_TASKS[task_id]
    if task.get("cancel_requested"):
        task["status"] = "cancelled"
        task["progress"] = "任务已取消"
        return
    task["status"] = "running"

    usage_total: dict = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    try:
        task["progress"] = "解析命题需求…"
        await asyncio.sleep(0.3)
        if _is_cancelled(task):
            return

        messages = [
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": _build_user_prompt(task["requirement"], task.get("problem_id"))},
        ]

        task["progress"] = "正在生成题目…"
        text, usage = await _call_llm(messages, task)
        _accumulate_usage(usage_total, usage)
        if _is_cancelled(task):
            return
        if not text.strip():
            raise RuntimeError("模型返回了空内容")

        task["progress"] = "校验并整理题目配置…"
        await asyncio.sleep(0.2)
        try:
            problem = normalize_problem(_extract_json(text))
        except (ValueError, json.JSONDecodeError) as e:
            task["progress"] = "格式校验未通过，正在修正…"
            fix_messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"你上一轮的输出无法解析为合法题目 JSON，错误信息：{e}。请只输出修正后的完整 JSON 对象。"},
            ]
            text2, usage2 = await _call_llm(fix_messages, task)
            _accumulate_usage(usage_total, usage2)
            if _is_cancelled(task):
                return
            try:
                problem = normalize_problem(_extract_json(text2))
            except Exception as e2:
                raise RuntimeError(f"题目配置校验失败：{e2}") from e2

        if _is_cancelled(task):
            return
        task["result"] = problem
        task["status"] = "success"
        task["progress"] = "生成完成"
        task["usage"] = _build_usage(usage_total)
    except asyncio.CancelledError:
        task["status"] = "cancelled"
        task["progress"] = "任务已取消"
    except Exception as e:
        task["status"] = "failed"
        task["progress"] = "生成失败"
        task["error"] = str(e)


def _public_ai_task(task: dict) -> dict:
    """对外返回的任务信息（不含任何敏感字段）。"""
    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "progress": task.get("progress"),
        "result": task.get("result"),
        "usage": task.get("usage"),
        "error": task.get("error"),
        "problem_id": task.get("problem_id"),
        "requirement": task.get("requirement"),
    }


@app.put("/api/ai/model-config")
async def set_model_config(payload: dict = Body(...), request: Request = None):
    """配置模型（已登录）。api_key 仅保存，不通过任何响应返回。更新时可省略 api_key 以沿用旧密钥。"""
    user, err = _require_user(request)
    if err is not None:
        return err

    provider_url = payload.get("provider_url")
    model = payload.get("model")
    api_key = payload.get("api_key")
    if not isinstance(provider_url, str) or not provider_url.strip():
        return fail(400, "provider_url 必填")
    if not isinstance(model, str) or not model.strip():
        return fail(400, "model 必填")
    if isinstance(api_key, str) and api_key.strip():
        _model_config["api_key"] = api_key.strip()
    elif not _model_config.get("api_key"):
        return fail(400, "api_key 必填")

    input_price = payload.get("input_price", 0.0)
    output_price = payload.get("output_price", 0.0)
    price_unit = payload.get("price_unit", 1000000)
    for name, v in (("input_price", input_price), ("output_price", output_price)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return fail(400, f"{name} 必须是数字")
    if isinstance(price_unit, bool) or not isinstance(price_unit, int) or price_unit <= 0:
        return fail(400, "price_unit 必须是正整数")

    _model_config.update({
        "provider_url": provider_url.strip(),
        "model": model.strip(),
        "input_price": float(input_price),
        "output_price": float(output_price),
        "price_unit": int(price_unit),
    })
    _save_model_config()
    return ok(_safe_model_config(), "model config updated")


@app.get("/api/ai/model-config")
async def get_model_config(request: Request = None):
    """查询模型配置（已登录）。api_key 不返回，仅返回是否已配置。"""
    user, err = _require_user(request)
    if err is not None:
        return err
    return ok(_safe_model_config())


@app.post("/api/ai/problem-tasks/")
async def create_ai_task(payload: dict = Body(...), request: Request = None):
    """创建智能命题任务（已登录）。requirement 必填，problem_id 可选（参考/改编已有题目）。"""
    user, err = _require_user(request)
    if err is not None:
        return err
    if not _model_config.get("provider_url") or not _model_config.get("model") or not _model_config.get("api_key"):
        return fail(400, "请先配置模型")

    requirement = payload.get("requirement")
    if not isinstance(requirement, str) or not requirement.strip():
        return fail(400, "requirement 必填")
    problem_id = payload.get("problem_id")
    if problem_id is not None:
        if not isinstance(problem_id, str) or not problem_id:
            return fail(400, "problem_id 必须是字符串")
        if load_problem(problem_id) is None:
            return fail(404, "指定题目不存在")

    task_id = _new_ai_task_id()
    AI_TASKS[task_id] = {
        "task_id": task_id,
        "user_id": user["user_id"],
        "username": user.get("username"),
        "requirement": requirement.strip(),
        "problem_id": problem_id,
        "status": "pending",
        "progress": "等待开始",
        "result": None,
        "usage": None,
        "error": None,
        "cancel_requested": False,
    }
    asyncio.create_task(_run_ai_task(task_id))
    return ok({"task_id": task_id, "status": "pending"}, "task created")


@app.get("/api/ai/problem-tasks/{task_id}")
async def get_ai_task(task_id: str, request: Request = None):
    """查询任务状态与结果（任务创建者或管理员）。"""
    user, err = _require_user(request)
    if err is not None:
        return err
    task = AI_TASKS.get(task_id)
    if task is None:
        return fail(404, "task not found")
    if user.get("role") != "admin" and task.get("user_id") != user["user_id"]:
        return fail(403, "权限不足")
    return ok(_public_ai_task(task))


@app.put("/api/ai/problem-tasks/{task_id}/cancel")
async def cancel_ai_task(task_id: str, request: Request = None):
    """中断任务（创建者或管理员）。中断后阻止任务继续执行并落盘 cancelled 状态。"""
    user, err = _require_user(request)
    if err is not None:
        return err
    task = AI_TASKS.get(task_id)
    if task is None:
        return fail(404, "task not found")
    if user.get("role") != "admin" and task.get("user_id") != user["user_id"]:
        return fail(403, "权限不足")
    if task.get("status") in ("success", "failed", "cancelled"):
        return fail(409, "任务已经结束")
    task["cancel_requested"] = True
    task["status"] = "cancelled"
    task["progress"] = "任务已取消"
    return ok({"task_id": task_id, "status": "cancelled"}, "task cancelled")


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
_load_languages()
_load_users()
_load_visibility()
_load_model_config()
_load_submissions()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)