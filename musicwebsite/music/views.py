from .models import Blog, Comment
from django.template import loader
from django.http import HttpResponse, HttpResponseRedirect

def show_blog(request, id):
    blog = Blog.objects.get(id=id)
    template = loader.get_template('music/index.html')
    context = {
        'blog_id': id,
        'blog_title': blog.title,
        'blog_content': blog.blog_content,
        'comments': blog.comment_set.all()
    }
    return HttpResponse(template.render(context, request))

def comment(request, id):
    data = request.POST
    user = data['user']
    comment_content = data['content']
    blog = Blog.objects.get(id=id)
    obj = Comment(blog=blog, user=user, comment_content=comment_content)
    obj.full_clean()
    obj.save()
    return HttpResponseRedirect(f'/index/blog/{id}')
