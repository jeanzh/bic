from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib import messages
from django.db.models import Q
from .models import User


def login_view(request):
    # 临时：已由 AutoLoginAdminMiddleware 自动以管理员身份登录，无需登录框
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        if username:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={'display_name': username},
            )
            auth_login(request, user)
            return redirect('dashboard')
        messages.error(request, '请输入用户名')
    return render(request, 'accounts/login.html')


def logout_view(request):
    auth_logout(request)
    return redirect('login')


def user_list(request):
    users = User.objects.all().order_by('-is_admin', 'username')
    return render(request, 'accounts/user_list.html', {'users': users})


def promote_admin(request, pk):
    if request.method == 'POST':
        user = get_object_or_404(User, pk=pk)
        user.is_admin = True
        user.save()
    return redirect('user_list')


def demote_admin(request, pk):
    if request.method == 'POST':
        user = get_object_or_404(User, pk=pk)
        if user != request.user:
            user.is_admin = False
            user.save()
    return redirect('user_list')
