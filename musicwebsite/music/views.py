import time
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404,redirect,render

from .models import Artist,Comment,Song


def song_list(request):
    """主页"""
    songs=Song.objects.select_related('artist').order_by('id')
    paginator=Paginator(songs,10)#查歌曲
    page_number=request.GET.get('page',1)
    page_obj=paginator.get_page(page_number)
    return render(request,'music/song_list.html',{'page_obj':page_obj})

def song_detail(request,song_id):
    """歌曲页"""
    song=get_object_or_404(Song.objects.select_related('artist'),pk=song_id)
    comments=song.comments.order_by('-created_at')#倒序
    return render(request,'music/song_detail.html',{'song':song,'comments':comments})


def artist_list(request):
    """歌手页"""
    artists=Artist.objects.order_by('id')
    paginator=Paginator(artists,10)#同理
    page_number=request.GET.get('page',1)
    page_obj=paginator.get_page(page_number)
    return render(request,'music/artist_list.html',{'page_obj':page_obj})


def artist_detail(request,artist_id):
    """歌手信息"""
    artist=get_object_or_404(Artist,pk=artist_id)
    songs=artist.songs.order_by('id')  #查到这个歌手的所有歌
    return render(request,'music/artist_detail.html',{'artist':artist,'songs':songs})


def add_comment(request,song_id):
    """提交评论"""
    song=get_object_or_404(Song,pk=song_id)
    content=request.POST.get('content','').strip()  # 去掉首尾空格，防止存空白评论
    if content:
        Comment.objects.create(song=song,content=content)
    return redirect('song_detail',song_id=song.id)


def delete_comment(request,comment_id):
    """删除评论"""
    comment=get_object_or_404(Comment,pk=comment_id)
    song_id=comment.song_id  # 先记下所属歌曲，删完跳回详情页
    comment.delete()
    return redirect('song_detail',song_id=song_id)


def search(request):
    """搜索"""
    keyword=request.GET.get('q','').strip()
    category=request.GET.get('type','song')
    start=time.time()
    if keyword:
        if category == 'artist':
            results=list(Artist.objects.filter(Q(name__icontains=keyword) | Q(brief__icontains=keyword)).order_by('id'))
        else:
            results=list(Song.objects.filter(Q(name__icontains=keyword) | Q(artist__name__icontains=keyword) | Q(lyric__icontains=keyword)).select_related('artist').order_by('id'))
    else:
        results=[]
    cost=time.time() - start
    page_obj=Paginator(results,10).get_page(request.GET.get('page',1))
    return render(request,'music/search.html',{'keyword':keyword,'category':category,'page_obj':page_obj,'count':len(results),'cost':cost})
