from django.urls import path, include
import music.views as views

urlpatterns = [
    path('blog/<int:id>', views.show_blog),
    path('comment/<int:id>', views.comment)
]
