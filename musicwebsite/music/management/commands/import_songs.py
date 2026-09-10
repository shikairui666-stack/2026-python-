import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from music.models import Artist, Song


class Command(BaseCommand):
    help = '从 songs_full_data.json 导入歌手和歌曲'

    def add_arguments(self, parser):
        parser.add_argument(
            '--path',
            default='',
            help='songs_full_data.json 的路径；默认自动在仓库根下查找 crawler/music_data 或 music_data',
        )

    def _find_json(self, explicit: str) -> Path:
        """确定 json 路径：优先 --path，否则在仓库根下按候选位置自动查找。"""
        if explicit:
            p = Path(explicit)
            if p.exists():
                return p
            raise CommandError(f'找不到指定文件：{p}')

        # settings.BASE_DIR = musicwebsite/，其上一级即仓库根
        repo_root = settings.BASE_DIR.parent
        candidates = [
            repo_root / 'crawler' / 'music_data' / 'songs_full_data.json',
            repo_root / 'music_data' / 'songs_full_data.json',
        ]
        for p in candidates:
            if p.exists():
                return p
        raise CommandError(
            '未找到 songs_full_data.json。请先运行爬虫脚本生成数据，或用 --path 指定路径。\n'
            '已查找：' + '、'.join(str(c) for c in candidates)
        )

    def handle(self, *args, **options):
        json_path = self._find_json(options.get('path', ''))

        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)

        # 导入前先清空
        Song.objects.all().delete()
        Artist.objects.all().delete()

        artists = {}  # 歌手名 -> Artist 对象，用来去重

        for item in data:
            ad = item['artist_detail']

            # 同一个歌手只建一次（250 首歌只有 10 个歌手）
            artist = artists.get(ad['name'])
            if artist is None:
                artist, _ = Artist.objects.get_or_create(
                    name=ad['name'],
                    defaults={
                        'image_url': ad.get('picurl', ''),
                        'brief': ad.get('jianjie', ''),
                        'url': ad.get('url', ''),
                    },
                )
                artists[ad['name']] = artist

            Song.objects.create(
                name=item['name'],
                artist=artist,
                image_url=item.get('cover', ''),
                lyric=item.get('lyric', ''),
                url=item.get('url', ''),
            )

        self.stdout.write(self.style.SUCCESS(
            f'导入完成：{Artist.objects.count()} 位歌手，{Song.objects.count()} 首歌'
        ))
