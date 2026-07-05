from django.urls import path

from . import views

urlpatterns = [
    path("me/", views.me, name="me"),
    # Read-only Auswahlliste der Nutzer für Empfänger-Dropdowns (STOAA-221).
    path("users/", views.UserListView.as_view(), name="user-list"),
]
