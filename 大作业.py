#大作业 史铠瑞 2025010531
import requests
import re#正则表达式
import os#文件
import json#保存数据
import time
import random#用来生成随机数
#爬虫伪装部分
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
#歌词部分
def safe_filename(name):
    #2026.8.24爬取马思维歌曲出错，文件名不满足规范要求
    return re.sub(r'[\\/:*?"<>|]', '_', name)

def clean_lrc(raw):
    raw=re.sub(r'\[.*?\]', '',raw) #正则去掉歌词里带的[]时间部分，不采用贪婪策略，保证全部过滤掉
    lines=[line.strip() for line in raw.splitlines() if line.strip()] #过滤空行，保持美观
    return "\n".join(lines)

def get_lyrics(song_id):
    url=api_base+"/song/lyric"  #这里为了获取该歌曲的url
    params={"id":song_id,"lv":1,"tv":-1} #关闭翻译
    try:
        r=requests.get(url,headers=headers,params=params,timeout=15)  #延长秒数
        r.raise_for_status()  #4xx/5xx
        data=r.json()
        lyric=data.get("lrc",{}).get("lyric") if isinstance(data,dict) else None
        if lyric:
            return clean_lrc(lyric)
        return "暂无歌词"
    except Exception as e:
        print(f"[歌词] id={song_id} 获取失败: {e}")
        return "歌词获取失败"

    
#歌手部分，获取名字，简介，图片，url
def get_artist_info(artist_id): #这里同理
    url="https://music.163.com/api/v1/artist/"+str(artist_id)
    try:
        r=requests.get(url,headers=headers,timeout=15) #111
        r.raise_for_status()
        data=r.json()
        artist=data["artist"]
        return{
            "id":artist_id,
            "name":artist.get("name",""),
            "picurl":artist.get("picUrl",""),
            "jianjie":artist.get("briefDesc","暂无简介"),
            "url":"https://music.163.com/#/artist?id="+str(artist_id)
        }
    except Exception as e:
        print(f"[歌手] id={artist_id} 获取失败: {e}")
        return None
#一个歌手25首歌曲
def get_artist_songs(artist_id,limit=25):
    url=api_base+"/artist/top/song"
    params={"id":artist_id,"limit":limit}
    try:
        r=requests.get(url,headers=headers,params=params,timeout=15)
        r.raise_for_status()
        data=r.json()
        songs=[]#放空
        for item in data.get("songs",[])[:limit]:
            try:
                ar=item.get("ar") or []
                al=item.get("al") or {}
                songs.append({
                    "id":item["id"],
                    "name":item.get("name",""),
                    "artist_id":artist_id,
                    "artist_name":ar[0].get("name","") if ar else "",
                    "album":al.get("name",""),
                    "cover":al.get("picUrl",""),
                    "url":"https://music.163.com/#/song?id="+str(item["id"])
                })
            except Exception as e:
                print(f"[歌曲] id={item.get('id')} 跳过: {e}")
                continue
        return songs
    except Exception as e:
        print(f"[歌曲列表] artist_id={artist_id} 获取失败: {e}")
        return []#防止崩溃
#图片下载和存储
def download_image(img_url,save_path):
    if not img_url or not isinstance(img_url,str):
        return
    if os.path.exists(save_path):
        return
    #防重
    try:
        r=requests.get(img_url,headers=headers,timeout=15)
        r.raise_for_status()
        with open(save_path,"wb") as f:
            f.write(r.content)
    except Exception as e:
        print(f"[图片] {save_path} 下载失败: {e}")

#main函数
create_folders()
singer_ids=[
    6452,#周杰伦
    3684,#林俊杰
    2116,#陈奕迅
    6460,#张学友
    5781,#薛之谦
    29051613,#郑润泽
    5538,#汪苏泷
    31376161,#颜人中
    12631485,#h3r3
    4292,#李荣浩
    5771,#许嵩
    2738,#方大同
    5196,#陶喆
    1143033,#队长
    6472,#张杰
    12138269,#毛不易
    5929,#徐良
    6731,#赵雷
    1132392,#马思唯
    1030001,#周深
    5346,
    3066,
    861777,
    2843,
    1038093,
    12932368,
    3695,
    9272,
    7763,
    8926,
    7214,
    8325,
    1007170,
    9621,
    9269,
    29802127,
    10562,
    14312549,
    7219,
    9606,
    9945,
    10561,
    59655434,
    906118,
    10559,
    8234,
    30471229,
    9178,
    7891,
    9489,
    12172529,
    9061,
    7570,
    10558,
    35531,
    185858,
    90331,
    178059,
    12107961,
    33184,
    38853,
    1045123,
    780003,
    301757,
    45236,
    44266,
    64147,
    11972054,
    74625,
    1043338,
    46487,
    14486166,
    14621097,
    159300,
    159692,
    16456,
    13193,
    51265187,
    11127,
    12676697,
    12707,
    11564,
    222871,
    189873,
    12977,
    11265,
    1047337,
    33806754,
    12712,
    11015,
    12081,
    847346,
    96266,
    94779,
    98105,
    747030,
    12068017,
    759509,
    126339,
    37351063
]
total_songs=[]
json_save_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),"music_data","songs_full_data.json")
def save_data():
    #为了防止失败不保存
    try:
        with open(json_save_path,"w",encoding="utf-8") as f:
            json.dump(total_songs,f,ensure_ascii=False,indent=2)
    except Exception as e:
        print(f"保存但是失败: {e}")

for singer_id in singer_ids:
    singer=get_artist_info(singer_id)
    if singer==None:
        continue
    else:
        touxiang_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),"music_data","singer_head",str(singer_id)+".jpg")
        download_image(singer["picurl"], touxiang_path)
        singer["local_avatar"]=touxiang_path
        gequ_list=get_artist_songs(singer_id,limit=25)
        for gequ in gequ_list:
            lrc=get_lyrics(gequ["id"])
            gequ["lyric"]=lrc
            lrc_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),"music_data","lyrics",str(gequ["id"])+"_"+safe_filename(gequ["name"])+".txt")
            with open(lrc_path,"w",encoding="utf-8") as f:
                f.write(lrc)
            fengmian_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),"music_data","images",str(gequ["id"])+".jpg")
            download_image(gequ["cover"],fengmian_path)
            gequ["local_cover"]=fengmian_path
            gequ["artist_detail"]=singer
            total_songs.append(gequ)
            time.sleep(random.uniform(3,6))
        time.sleep(random.uniform(10,15))
        save_data()