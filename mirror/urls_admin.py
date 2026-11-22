from django.urls import path

from . import views_admin

urlpatterns = [
    path("login/", views_admin.ConsoleLoginView.as_view(), name="login"),
    path("logout/", views_admin.console_logout, name="logout"),
    path("", views_admin.dashboard, name="dashboard"),
    path("gowns/", views_admin.gowns, name="gowns"),
    path("gowns/add/", views_admin.gown_create, name="gown-create"),
    path("gowns/<uuid:gown_id>/edit/", views_admin.gown_edit, name="gown-edit"),
    path("gowns/<uuid:gown_id>/archive/", views_admin.gown_archive, name="gown-archive"),
    path("portraits/", views_admin.portraits, name="portraits"),
    path("portraits/<uuid:portrait_id>/delete/", views_admin.portrait_delete, name="portrait-delete"),
    path("settings/", views_admin.event_settings, name="settings"),
    path("monitoring/", views_admin.monitoring, name="monitoring"),
]
