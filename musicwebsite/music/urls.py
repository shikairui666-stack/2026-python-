from django.urls import path

from . import views

urlpatterns = [
    path('', views.song_list, name='song_list'),                       # 主页 = 歌曲列表
    path('song/<int:song_id>/comment/', views.add_comment, name='add_comment'),
    path('song/<int:song_id>/', views.song_detail, name='song_detail'),
    path('comment/<int:comment_id>/delete/', views.delete_comment, name='delete_comment'),
    path('artist/', views.artist_list, name='artist_list'),
    path('artist/<int:artist_id>/', views.artist_detail, name='artist_detail'),
]
