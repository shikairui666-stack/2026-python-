# OJ 在线评测系统

基于 **FastAPI + Streamlit** 的在线评测系统（Online Judge），覆盖题目管理、代码评测、用户与鉴权、评测日志、以及 AI 智能命题。

## 功能

- **题库管理**：内置 22 道题（LeetCode / 洛谷经典题），支持增删改查、公开开关
- **评测引擎**：编译 / 运行 / 输出比对 / 时间与内存统计，支持 Python 与 C++（语言可动态注册）
- **用户与鉴权**：注册 / 登录（Cookie Session）、密码 bcrypt 哈希、角色权限（用户 / 管理员）
- **评测日志**：按提交记录日志、按角色控制可见性、审计日志
- **AI 智能命题**：对接 OpenAI 兼容接口，流式生成题目 + Token 计价 + 中断

## 启动

```bash
pip install -r requirements.txt

# 后端（FastAPI）
python OJ.py
# 监听 http://127.0.0.1:8000

# 前端（Streamlit，另开一个终端）
streamlit run app.py
```

初始管理员账号：`admin` / `admintestpassword`（首次启动自动创建）。

> 前端通过 `BASE_URL = "http://127.0.0.1:8000"` 连接后端（见 `app.py` 顶部），如后端改端口需同步修改。

## 目录结构

```
OJ/
├── OJ.py               # FastAPI 后端（题目/评测/用户/日志/AI 命题）
├── app.py              # Streamlit 前端
├── problems/           # 题库（每题一个 JSON）
├── .streamlit/config.toml
└── requirements.txt
```

运行时数据（`users.json`、`languages.json`、`submissions.json`、`model_config.json`、`workspace/`）由程序自动初始化，已加入 `.gitignore` 不提交。

## AI 智能命题

前端「AI 智能命题」页里配置模型 `provider_url` / `model` / `api_key`（任意 OpenAI 兼容接口均可）。`api_key` 仅保存在本地 `model_config.json`，不对外返回。
