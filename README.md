# 大作业合集

本仓库包含三个独立项目：

| 目录 | 项目 | 技术栈 | 说明 |
|------|------|--------|------|
| [OJ/](OJ/) | 在线评测系统 | FastAPI + Streamlit | 题库 / 评测 / 用户 / 日志 + AI 智能命题 |
| [musicwebsite/](musicwebsite/) | 音乐信息网站 | Django | 歌曲 / 歌手展示、搜索、评论 |
| [crawler/](crawler/) | 网易云爬虫 | requests | 爬取网易云歌曲、歌词、歌手数据，供网站导入 |

## 环境要求

- Python 3.12+（本项目在 3.14 上开发）
- 推荐使用 [uv](https://github.com/astral-sh/uv) 或 `venv` 为每个项目创建独立虚拟环境

## 快速开始

### 1. OJ 在线评测系统

```bash
cd OJ
pip install -r requirements.txt
python OJ.py              # 后端，默认 http://127.0.0.1:8000
streamlit run app.py      # 前端（另开一个终端）
```

初始管理员账号：`admin` / `admintestpassword`（详见 [OJ/README.md](OJ/README.md)）。

### 2. 音乐信息网站

```bash
cd musicwebsite
pip install -r requirements.txt
python manage.py migrate              # 建表
python manage.py runserver            # http://127.0.0.1:8000
```

> 站点需要数据才能看到内容：先运行爬虫生成数据，再执行 `python manage.py import_songs` 导入（见下）。

### 3. 网易云爬虫

```bash
cd crawler
pip install -r requirements.txt
python 网易云爬虫.py
```

输出到 `crawler/music_data/`（约 2.6G，已加入 `.gitignore`，不提交）。

## 数据链路

```
crawler（网易云爬虫）
   └─> crawler/music_data/songs_full_data.json
          └─> musicwebsite: python manage.py import_songs
                 └─> 网站数据库 db.sqlite3
```

## 目录结构

```
learngit/
├── OJ/              # 在线评测系统（FastAPI 后端 + Streamlit 前端）
├── musicwebsite/    # 音乐信息网站（Django）
├── crawler/         # 网易云爬虫（requests）
└── README.md
```
