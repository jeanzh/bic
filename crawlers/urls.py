from django.urls import path
from . import views

urlpatterns = [
    path('crawlers/', views.crawler_dashboard, name='crawler_dashboard'),
    path('crawlers/config/', views.crawler_config, name='crawler_config'),
    path('crawlers/trigger/<str:website_code>/', views.trigger_manual, name='trigger_manual'),
    path('crawlers/stop/<str:website_code>/', views.stop, name='stop_crawler'),
]
