#大作业 史铠瑞 2025010531
import requests
import re         #正则表达式
import os         #创建文件
import json       #保存数据为json格式
import time       #用来让程序暂停一下
import random     #用来生成随机数，让暂停时间不固定
#爬虫伪装
heads = {
    "User-Agent":"Mozilla/5.0(Windows NT 10.0;Win64;x64) AppleWebKit/537.36(KHTML,like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
    print("success")

create_folders()