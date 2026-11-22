from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path

from mirror import views_public

urlpatterns = [
    path("", include("mirror.urls_guest")),
    path("admin/", include(("mirror.urls_admin", "console"), namespace="console")),
    path("sw.js", views_public.service_worker, name="service-worker"),
    path("manifest.webmanifest", views_public.web_manifest, name="web-manifest"),
]

urlpatterns += staticfiles_urlpatterns()
