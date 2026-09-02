from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q

from accounts.decorators import admin_required
from .models import BiddingWebsite, BiddingInfo, FilterRule
from .services import group_by_project


@login_required
def bidding_list(request):
    qs = BiddingInfo.objects.select_related('website').all()

    q = request.GET.get('q', '').strip()
    website_id = request.GET.get('website', '').strip()
    region = request.GET.get('region', '').strip()
    status = request.GET.get('status', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(content__icontains=q) | Q(purchaser__icontains=q))
    if website_id:
        qs = qs.filter(website_id=website_id)
    if region:
        qs = qs.filter(region__icontains=region)
    if status:
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(publish_date__gte=date_from)
    if date_to:
        qs = qs.filter(publish_date__lte=date_to)

    groups = group_by_project(qs)
    paginator = Paginator(groups, 12)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'bidding/list.html', {
        'page_obj': page_obj,
        'websites': BiddingWebsite.objects.all(),
        'status_choices': BiddingInfo.STATUS_CHOICES,
        'filters': {
            'q': q, 'website': website_id, 'region': region,
            'status': status, 'date_from': date_from, 'date_to': date_to,
        },
    })


@login_required
def bidding_detail(request, pk):
    info = get_object_or_404(BiddingInfo.objects.select_related('website'), pk=pk)
    related = []
    if info.project_no:
        related = (BiddingInfo.objects.select_related('website')
                   .filter(project_no=info.project_no)
                   .exclude(pk=info.pk)
                   .order_by('-publish_date', '-created_at'))
    return render(request, 'bidding/detail.html', {'info': info, 'related': related})


@login_required
def bidding_project(request, project_no):
    infos = (BiddingInfo.objects.select_related('website')
             .filter(project_no=project_no)
             .order_by('-publish_date', '-created_at'))
    if not infos.exists():
        raise Http404
    return render(request, 'bidding/project_detail.html', {
        'project_no': project_no,
        'infos': infos,
    })


@admin_required
def website_list(request):
    websites = BiddingWebsite.objects.all()
    return render(request, 'bidding/website_list.html', {'websites': websites})


@admin_required
def website_create(request):
    if request.method == 'POST':
        website = BiddingWebsite(
            name=request.POST.get('name', '').strip(),
            code=request.POST.get('code', '').strip(),
            url=request.POST.get('url', '').strip(),
            category=request.POST.get('category', 'other'),
            is_active=request.POST.get('is_active') == 'on',
            requires_login=request.POST.get('requires_login') == 'on',
            crawl_interval=int(request.POST.get('crawl_interval', '60') or 60),
        )
        if not website.name or not website.code:
            messages.error(request, '名称和代码为必填项')
        else:
            website.save()
            messages.success(request, '网站已添加')
            return redirect('website_list')
    return render(request, 'bidding/website_form.html', {
        'categories': BiddingWebsite.CATEGORY_CHOICES,
        'website': None,
    })


@admin_required
def website_edit(request, pk):
    website = get_object_or_404(BiddingWebsite, pk=pk)
    if request.method == 'POST':
        website.name = request.POST.get('name', '').strip()
        website.code = request.POST.get('code', '').strip()
        website.url = request.POST.get('url', '').strip()
        website.category = request.POST.get('category', 'other')
        website.is_active = request.POST.get('is_active') == 'on'
        website.requires_login = request.POST.get('requires_login') == 'on'
        website.crawl_interval = int(request.POST.get('crawl_interval', '60') or 60)
        website.save()
        messages.success(request, '网站已更新')
        return redirect('website_list')
    return render(request, 'bidding/website_form.html', {
        'categories': BiddingWebsite.CATEGORY_CHOICES,
        'website': website,
    })


@admin_required
def website_delete(request, pk):
    website = get_object_or_404(BiddingWebsite, pk=pk)
    if request.method == 'POST':
        website.delete()
        messages.success(request, '网站已删除')
    return redirect('website_list')


@admin_required
def filter_rule_list(request):
    rules = FilterRule.objects.all()
    return render(request, 'bidding/filter_rule_list.html', {'rules': rules})


@admin_required
def filter_rule_create(request):
    if request.method == 'POST':
        rule = FilterRule(
            name=request.POST.get('name', '').strip(),
            keywords=request.POST.get('keywords', '').strip(),
            exclude_keywords=request.POST.get('exclude_keywords', '').strip(),
            regions=request.POST.get('regions', '').strip(),
            industries=request.POST.get('industries', '').strip(),
            is_active=request.POST.get('is_active') == 'on',
        )
        if not rule.name:
            messages.error(request, '规则名称为必填项')
        else:
            rule.save()
            messages.success(request, '过滤规则已添加')
            return redirect('filter_rule_list')
    return render(request, 'bidding/filter_rule_form.html', {'rule': None})


@admin_required
def filter_rule_edit(request, pk):
    rule = get_object_or_404(FilterRule, pk=pk)
    if request.method == 'POST':
        rule.name = request.POST.get('name', '').strip()
        rule.keywords = request.POST.get('keywords', '').strip()
        rule.exclude_keywords = request.POST.get('exclude_keywords', '').strip()
        rule.regions = request.POST.get('regions', '').strip()
        rule.industries = request.POST.get('industries', '').strip()
        rule.is_active = request.POST.get('is_active') == 'on'
        rule.save()
        messages.success(request, '过滤规则已更新')
        return redirect('filter_rule_list')
    return render(request, 'bidding/filter_rule_form.html', {'rule': rule})


@admin_required
def filter_rule_delete(request, pk):
    rule = get_object_or_404(FilterRule, pk=pk)
    if request.method == 'POST':
        rule.delete()
        messages.success(request, '过滤规则已删除')
    return redirect('filter_rule_list')
