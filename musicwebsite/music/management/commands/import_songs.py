import json
from pathlib import Path

from django.core.management.base import BaseCommand

from music.models import Artist,Song


class Command(BaseCommand):
    help='从 songs_full_data.json 导入歌手和歌曲'

    def handle(self,*args,**options):
        json_path=Path(r"C:/Users/瑞/learngit/music_data/songs_full_data.json")

        with open(json_path,encoding="utf-8") as f:
            data=json.load(f)

        #导入前先清空
        Song.objects.all().delete()
        Artist.objects.all().delete()

        artists={}  # 歌手名 -> Artist 对象，用来去重

        for item in data:
            ad=item["artist_detail"]

            # 同一个歌手只建一次（250 首歌只有 10 个歌手）
            artist=artists.get(ad["name"])
            if artist is None:
                artist,_=Artist.objects.get_or_create(
                    name=ad["name"],
                    defaults={
                        "image_url":ad.get("picurl",""),
                        "brief":ad.get("jianjie",""),
                        "url":ad.get("url",""),
                    },
                )
                artists[ad["name"]]=artist

            Song.objects.create(
                name=item["name"],
                artist=artist,
                image_url=item.get("cover",""),
                lyric=item.get("lyric",""),
                url=item.get("url",""),
            )

        self.stdout.write(self.style.SUCCESS(
            f"导入完成：{Artist.objects.count()} 位歌手，{Song.objects.count()} 首歌"
        ))
