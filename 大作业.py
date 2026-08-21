#大作业 史铠瑞 2025010531
import requests
import re         #正则表达式
import os         #创建文件
import json       #保存数据为json格式
import time       #用来让程序暂停一下
import random     #用来生成随机数，让暂停时间不固定
#爬虫伪装
headers = {
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0;Win64;x64) AppleWebKit/537.36 (KHTML,like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer":"https://music.163.com/"
}
#网易云音乐API
api_base = "https://music.163.com/api"
#函数1：创建文件夹用于后续保存
def create_folders():
    base_dir=os.path.dirname(os.path.abspath(__file__))
    path1=os.path.join(base_dir,"music_data","images")
    path2=os.path.join(base_dir,"music_data","singer_head")
    path3=os.path.join(base_dir,"music_data","lyrics")
    os.makedirs(path1,exist_ok=True)
    os.makedirs(path2,exist_ok=True)
    os.makedirs(path3,exist_ok=True)

def clean_lrc(raw):
    raw=re.sub(r'\[.*?\]', '',raw) #正则去掉歌词里带的[]时间部分，不采用贪婪策略，保证全部过滤掉
    lines=[line.strip() for line in raw.splitlines() if line.strip()] #过滤空行，保持美观
    return "\n".join(lines)

def get_lyrics(song_id):
    url=api_base+"/song/lyric"  #这里为了获取该歌曲的url地址
    params={"id":song_id,"lv":1,"tv":-1} #关闭翻译
    try:
        r=requests.get(url,headers=headers,params=params,timeout=10)  #超过十秒即判定为请求失败
        data=r.json()
        if "lrc" in data and "lyric" in data["lrc"]:
            return clean_lrc(data["lrc"]["lyric"])
        return "暂无歌词"
    except:
        return "歌词获取失败"

def get_artist_info(artist_id): #这里同理
    url=api_base+"/artist/desc"
    params={"id":artist_id}
    try:
        r=requests.get(url,headers=headers,params=params,timeout=10) #111
        data=r.json()
        return {
            "id":artist_id,
            "name":data["artist"]["name"],
            "picurl":data["artist"]["picUrl"],
            "jianjie":data.get("briefDesc","暂无简介"),
            "url":"https://music.163.com/#/artist?id="+str(artist_id)
        }
    except:
        return None