from django.urls import path

from . import views

urlpatterns = [
    path("me/", views.me, name="me"),
    # Read-only Auswahlliste der Nutzer für Empfänger-Dropdowns (STOAA-221).
    path("users/", views.UserListView.as_view(), name="user-list"),
    # Familien-/Haushalts-Freigabe
    path("households/", views.households, name="households"),
    path("households/<int:pk>/members/", views.household_add_member, name="household-add-member"),
    path("households/<int:pk>/leave/", views.household_leave, name="household-leave"),
]
