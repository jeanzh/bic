from django.urls import path, re_path
from . import views

urlpatterns = [
    path('bidding/', views.bidding_list, name='bidding_list'),
    path('bidding/<int:pk>/', views.bidding_detail, name='bidding_detail'),
    # project_no 可能含「/」（如 A24H999S26095000/01），需用 re_path 允许斜杠，
    # 否则 {% url 'bidding_project' ... %} 会 NoReverseMatch。
    re_path(r'^bidding/project/(?P<project_no>.+)/$', views.bidding_project, name='bidding_project'),
    path('admin/websites/', views.website_list, name='website_list'),
    path('admin/websites/add/', views.website_create, name='website_create'),
    path('admin/websites/<int:pk>/edit/', views.website_edit, name='website_edit'),
    path('admin/websites/<int:pk>/delete/', views.website_delete, name='website_delete'),
    path('admin/filter-rules/', views.filter_rule_list, name='filter_rule_list'),
    path('admin/filter-rules/add/', views.filter_rule_create, name='filter_rule_create'),
    path('admin/filter-rules/<int:pk>/edit/', views.filter_rule_edit, name='filter_rule_edit'),
    path('admin/filter-rules/<int:pk>/delete/', views.filter_rule_delete, name='filter_rule_delete'),
]
