import re

from django.db import models


def _split_list(text):
    """按中英文逗号、顿号、分号分隔，去空白、去空项。"""
    return [p.strip() for p in re.split(r'[,，、;；]+', text or '') if p.strip()]


class BiddingWebsite(models.Model):
    CATEGORY_CHOICES = [
        ('government', '政府采购'),
        ('finance', '金融集采'),
        ('province', '省公共资源'),
        ('other', '其他'),
    ]

    name = models.CharField(max_length=100, verbose_name='网站名称')
    code = models.CharField(max_length=50, unique=True, verbose_name='唯一代码')
    url = models.URLField(max_length=500, verbose_name='主页地址')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other', verbose_name='分类')
    is_active = models.BooleanField(default=True, verbose_name='启用')
    requires_login = models.BooleanField(default=False, verbose_name='需登录')
    crawl_interval = models.PositiveIntegerField(default=60, verbose_name='爬取间隔(分钟)')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'code']

    def __str__(self):
        return f'{self.name} ({self.code})'


class BiddingInfo(models.Model):
    STATUS_CHOICES = [
        ('open', '招标中'),
        ('closed', '已截止'),
        ('awarded', '已中标'),
    ]

    website = models.ForeignKey(BiddingWebsite, on_delete=models.CASCADE, related_name='infos', verbose_name='来源网站')
    title = models.CharField(max_length=500, verbose_name='标题')
    source_url = models.URLField(max_length=1000, verbose_name='原始链接')
    source_unique_id = models.CharField(max_length=200, verbose_name='去重键')
    project_no = models.CharField(max_length=200, blank=True, default='', db_index=True, verbose_name='项目编号')
    publish_date = models.DateField(null=True, blank=True, verbose_name='发布日期')
    purchaser = models.CharField(max_length=300, blank=True, verbose_name='招标人/采购人')
    region = models.CharField(max_length=100, blank=True, verbose_name='地区')
    industry = models.CharField(max_length=100, blank=True, verbose_name='行业分类')
    budget_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, verbose_name='预算金额(元)')
    bid_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, verbose_name='中标金额(元)')
    deadline = models.DateTimeField(null=True, blank=True, verbose_name='截止时间')
    content = models.TextField(blank=True, verbose_name='公告正文')
    contact_info = models.TextField(blank=True, verbose_name='联系方式')
    attachment_url = models.URLField(max_length=1000, blank=True, verbose_name='附件地址')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open', verbose_name='状态')
    raw_data = models.JSONField(default=dict, blank=True, verbose_name='原始数据')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('website', 'source_unique_id')]
        indexes = [
            models.Index(fields=['publish_date']),
            models.Index(fields=['region']),
            models.Index(fields=['industry']),
        ]
        ordering = ['-publish_date', '-created_at']

    def __str__(self):
        return self.title


class FilterRule(models.Model):
    name = models.CharField(max_length=100, verbose_name='规则名称')
    keywords = models.TextField(blank=True, verbose_name='包含关键词(逗号分隔)')
    exclude_keywords = models.TextField(blank=True, verbose_name='排除关键词(逗号分隔)')
    regions = models.TextField(blank=True, verbose_name='地区(逗号分隔)')
    industries = models.TextField(blank=True, verbose_name='行业(逗号分隔)')
    is_active = models.BooleanField(default=True, verbose_name='启用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_active', 'name']

    def __str__(self):
        return self.name

    def keyword_list(self):
        return _split_list(self.keywords)

    def exclude_keyword_list(self):
        return _split_list(self.exclude_keywords)

    def region_list(self):
        return _split_list(self.regions)

    def industry_list(self):
        return _split_list(self.industries)
