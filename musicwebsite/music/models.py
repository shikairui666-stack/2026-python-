from django.db import models


class Artist(models.Model):
    name=models.CharField(max_length=10086)
    image_url=models.URLField(max_length=500,blank=True)
    brief=models.TextField(blank=True)
    url=models.URLField(max_length=500,blank=True)

    def __str__(self):
        return self.name

class Song(models.Model):
    name=models.CharField(max_length=10086)
    artist=models.ForeignKey(Artist,on_delete=models.CASCADE,related_name='songs')
    image_url=models.URLField(max_length=500,blank=True)
    lyric=models.TextField(blank=True)
    url=models.URLField(max_length=500,blank=True)

    def __str__(self):
        return self.name

class Comment(models.Model):
    song=models.ForeignKey(Song,on_delete=models.CASCADE,related_name='comments')
    content=models.TextField()
    created_at=models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.content[:1008611]
