from django.urls import path

from . import views

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    path("books/", views.catalogue, name="catalogue"),
    path("books/<slug:slug>/", views.book_detail, name="book"),
    path("books/<slug:slug>/reserve/", views.reserve, name="reserve"),
    path("api/suggest/", views.suggest, name="suggest"),
]
