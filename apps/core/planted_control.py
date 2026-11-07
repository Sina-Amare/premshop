from django.db import connection
from django.http import HttpResponse
from django.urls import path


def planted(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM catalog_product WHERE slug = '" + request.GET["q"] + "'")
    return HttpResponse("ok")


urlpatterns = [path("planted/", planted)]
