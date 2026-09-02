from django.db import models


class CrawlerRun(models.Model):
    TRIGGER_CHOICES = [('scheduled', '定时'), ('manual', '手动')]
    STATUS_CHOICES = [
        ('pending', '等待中'), ('running', '运行中'),
        ('completed', '已完成'), ('failed', '失败'), ('stopped', '已停止'),
    ]

    website_code = models.CharField(max_length=50, verbose_name='网站代码')
    trigger_type = models.CharField(max_length=10, choices=TRIGGER_CHOICES, verbose_name='触发方式')
    pid = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=15, default='pending', choices=STATUS_CHOICES, verbose_name='状态')
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, verbose_name='错误信息')
    items_found = models.PositiveIntegerField(default=0, verbose_name='采集条数')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Run #{self.id} - {self.website_code} - {self.get_status_display()}'


class CrawlerConfig(models.Model):
    website_code = models.CharField(max_length=50, unique=True, verbose_name='网站代码')
    interval_minutes = models.PositiveIntegerField(default=60, verbose_name='爬取间隔(分钟)')
    is_scheduled_enabled = models.BooleanField(default=True, verbose_name='启用定时')
    last_scheduled_run_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.website_code} Config'
