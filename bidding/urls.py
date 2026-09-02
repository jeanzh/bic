from django.urls import path
from . import views

urlpatterns = [
    path('bidding/', views.bidding_list, name='bidding_list'),
    path('bidding/<int:pk>/', views.bidding_detail, name='bidding_detail'),
    path('bidding/project/<str:project_no>/', views.bidding_project, name='bidding_project'),
    path('admin/websites/', views.website_list, name='website_list'),
    path('admin/websites/add/', views.website_create, name='website_create'),
    path('admin/websites/<int:pk>/edit/', views.website_edit, name='website_edit'),
    path('admin/websites/<int:pk>/delete/', views.website_delete, name='website_delete'),
    path('admin/filter-rules/', views.filter_rule_list, name='filter_rule_list'),
    path('admin/filter-rules/add/', views.filter_rule_create, name='filter_rule_create'),
    path('admin/filter-rules/<int:pk>/edit/', views.filter_rule_edit, name='filter_rule_edit'),
    path('admin/filter-rules/<int:pk>/delete/', views.filter_rule_delete, name='filter_rule_delete'),
]
