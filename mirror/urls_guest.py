from django.urls import path

from . import views_guest

app_name = "guest"

urlpatterns = [
    path("", views_guest.welcome, name="welcome"),
    path("kiosk/", views_guest.welcome, name="kiosk"),
    path("consent/", views_guest.consent, name="consent"),
    path("camera/", views_guest.camera, name="camera"),
    path("camera/heartbeat/", views_guest.camera_heartbeat, name="camera-heartbeat"),
    path("camera/photo/<uuid:photo_id>/", views_guest.capture_preview, name="capture-preview"),
    path("collection/", views_guest.gowns, name="gowns"),
    path("fitting/", views_guest.fitting, name="fitting"),
    path("portrait/generate/", views_guest.generate_portrait, name="generate"),
    path("portrait/<uuid:portrait_id>/", views_guest.portrait_status, name="portrait-status"),
    path("portrait/<uuid:portrait_id>/result/", views_guest.portrait_result, name="portrait-result"),
    path("portrait/<uuid:portrait_id>/delete/", views_guest.portrait_delete, name="portrait-delete"),
    path("portrait/<uuid:portrait_id>/file/", views_guest.portrait_file, name="portrait-file"),
    path("session/end/", views_guest.end_session, name="end-session"),
    path("complete/", views_guest.complete, name="complete"),
    path("gowns/<uuid:gown_id>/image/", views_guest.gown_image, name="gown-image"),
    path("gowns/<uuid:gown_id>/cap/", views_guest.gown_cap_image, name="gown-cap-image"),
    path("event/assets/<str:kind>/", views_guest.event_asset, name="event-asset"),
]
