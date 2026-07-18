from django.urls import path

from . import views

urlpatterns = [
    path("me/", views.me, name="me"),
    # Read-only Auswahlliste der Nutzer für Empfänger-Dropdowns (STOAA-221).
    path("users/", views.UserListView.as_view(), name="user-list"),
    # Familien-/Haushalts-Freigabe (Beitritt nur mit beidseitiger Zustimmung)
    path("households/", views.households, name="households"),
    # Ziel-initiierter Beitritt per Code (erzeugt nur eine offene Anfrage).
    path("households/join/", views.household_join, name="household-join"),
    # Owner-Verwaltung: Code erzeugen/löschen, Anfragen listen + entscheiden.
    path("households/<int:pk>/join-code/", views.household_join_code, name="household-join-code"),
    path("households/<int:pk>/requests/", views.household_requests, name="household-requests"),
    path(
        "households/<int:pk>/requests/<int:req_id>/decide/",
        views.household_request_decide,
        name="household-request-decide",
    ),
    path("households/<int:pk>/leave/", views.household_leave, name="household-leave"),
]
