from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from accounts.decorators import admin_required
from .models import SystemConfig


@login_required
def dashboard(request):
    return redirect('bidding_list')


@admin_required
def system_config(request):
    if request.method == 'POST':
        for key in ['crawl_max_concurrent', 'bidding_retention_days']:
            value = request.POST.get(key, '')
            description = request.POST.get(f'{key}_desc', '')
            SystemConfig.set(key, value, description)
        messages.success(request, '系统配置已更新')
        return redirect('system_config')

    configs = SystemConfig.objects.all()
    return render(request, 'core/system_config.html', {'configs': configs})
