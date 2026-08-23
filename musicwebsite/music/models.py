from django.db import models
class Blog(models.Model):
    title = models.CharField(max_length=30)
    blog_content = models.CharField(max_length=2000)

class Comment(models.Model):
    blog = models.ForeignKey(Blog, on_delete=models.CASCADE)
    user = models.CharField(max_length=10)
    comment_content = models.CharField(max_length=1000)
