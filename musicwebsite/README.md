# 音乐信息网站

基于 **Django** 的音乐信息网站，展示网易云爬取的歌手与歌曲数据，支持搜索、歌手列表、歌曲详情与评论。

## 功能

- 歌曲列表 / 详情（分页）
- 歌手列表 / 详情
- 关键词搜索
- 评论（新增 / 删除）

## 启动

```bash
pip install -r requirements.txt
python manage.py migrate          # 建表
python manage.py runserver        # http://127.0.0.1:8000
```

> 默认数据库为 `musicwebsite/db.sqlite3`（已加入 `.gitignore`，不提交）。首次运行 `migrate` 会自动创建。

## 数据导入

站点数据来自 [../crawler/](../crawler/) 爬虫输出的 `songs_full_data.json`：

```bash
# 1. 先运行爬虫（见 ../crawler/README.md）
# 2. 再导入到数据库
python manage.py import_songs
```

`import_songs` 会自动在仓库根下查找 `crawler/music_data/songs_full_data.json`，也可手动指定：

```bash
python manage.py import_songs --path /path/to/songs_full_data.json
```

## 目录结构

```
musicwebsite/
├── manage.py                 # Django 入口
├── musicwebsite/             # 项目配置（settings / urls / wsgi / asgi）
├── music/                    # 应用（models / views / urls / templates / static）
│   ├── management/commands/import_songs.py   # 从 JSON 导入数据
│   ├── templates/music/      # 页面模板
│   └── static/music/         # 静态资源
└── requirements.txt
```
