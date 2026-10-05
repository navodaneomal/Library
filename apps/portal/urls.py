from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("books/", views.my_books, name="my_books"),
    path("books/<int:pk>/renew/", views.renew_loan, name="renew"),
    path("reservations/", views.reservations, name="reservations"),
    path("reservations/<int:pk>/", views.reservation_detail, name="reservation"),
    path("reservations/<int:pk>/cancel/", views.cancel_reservation, name="cancel_reservation"),
    path("fees/", views.fees, name="fees"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/read-all/", views.mark_notifications_read, name="notifications_read"),
    path("notifications/<int:pk>/", views.open_notification, name="open_notification"),
    path("profile/", views.profile, name="profile"),
    path("card/", views.card, name="card"),
]
