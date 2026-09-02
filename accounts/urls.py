from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('admin/users/', views.user_list, name='user_list'),
    path('admin/users/<int:pk>/promote/', views.promote_admin, name='promote_admin'),
    path('admin/users/<int:pk>/demote/', views.demote_admin, name='demote_admin'),
]
