# 网易云爬虫

基于 `requests` 的网易云音乐爬虫，爬取歌手的歌曲信息、歌词、封面与歌手头像，输出 JSON 供 [../musicwebsite/](../musicwebsite/) 导入。

## 运行

```bash
pip install -r requirements.txt
python 网易云爬虫.py
```

## 输出

数据写入脚本同级的 `music_data/` 目录（已加入 `.gitignore`）：

```
music_data/
├── songs_full_data.json   # 歌曲 + 歌手 + 歌词汇总 JSON（供网站导入）
├── images/                # 歌曲封面
├── singer_head/           # 歌手头像
└── lyrics/                # 歌词文本
```

默认只爬取 `test_singer_ids` 里的 10 位歌手（每位 25 首歌）；如需全部歌手，把脚本 `main()` 里的 `test_singer_ids` 换成 `singer_ids` 即可。

## 说明

- 爬取过程在每次请求间加了 `time.sleep`（3~6s / 10~15s）降低被封风险，完整跑一遍约 10~15 分钟
- 歌词与歌曲详情走网易云公开 API，歌曲名/专辑/封面走详情页 HTML 解析（API 缺失时回退）
- 数据仅用于学习交流，请勿用于商业用途
