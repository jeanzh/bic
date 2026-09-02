from django.shortcuts import render, redirect
from django.contrib import messages
from accounts.decorators import admin_required

from bidding.models import BiddingWebsite
from .models import CrawlerRun, CrawlerConfig
from .services import trigger_crawl, stop_crawl, get_status, check_orphan_runs


@admin_required
def crawler_dashboard(request):
    check_orphan_runs()

    websites = BiddingWebsite.objects.all()
    website_statuses = []
    for site in websites:
        status = get_status(site.code)
        status['website'] = site
        website_statuses.append(status)

    recent_runs = CrawlerRun.objects.all().order_by('-created_at')[:20]
    return render(request, 'crawlers/dashboard.html', {
        'website_statuses': website_statuses,
        'recent_runs': recent_runs,
    })


@admin_required
def trigger_manual(request, website_code):
    if request.method != 'POST':
        return redirect('crawler_dashboard')

    run = trigger_crawl(website_code, trigger_type='manual')
    if run is None:
        messages.warning(request, f'{website_code} 已有爬虫在运行，请等待完成后再触发')
    else:
        messages.success(request, f'{website_code} 爬虫已启动')
    return redirect('crawler_dashboard')


@admin_required
def stop(request, website_code):
    if request.method != 'POST':
        return redirect('crawler_dashboard')

    success, msg = stop_crawl(website_code)
    if success:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect('crawler_dashboard')


@admin_required
def crawler_config(request):
    websites = BiddingWebsite.objects.all()
    configs = []
    for site in websites:
        cfg, _ = CrawlerConfig.objects.get_or_create(website_code=site.code)
        configs.append((site, cfg))

    if request.method == 'POST':
        for site in websites:
            site.crawl_interval = int(request.POST.get(f'interval_{site.code}', '60') or 60)
            site.is_active = request.POST.get(f'active_{site.code}') == 'on'
            site.save(update_fields=['crawl_interval', 'is_active'])

            cfg = CrawlerConfig.objects.get_or_create(website_code=site.code)[0]
            cfg.is_scheduled_enabled = request.POST.get(f'enabled_{site.code}') == 'on'
            cfg.save(update_fields=['is_scheduled_enabled'])

        messages.success(request, '爬虫配置已更新')
        return redirect('crawler_config')

    return render(request, 'crawlers/config.html', {'configs': configs})
