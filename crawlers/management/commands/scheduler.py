import signal
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from bidding.models import BiddingWebsite
from crawlers.models import CrawlerRun, CrawlerConfig
from crawlers.services import trigger_crawl, check_orphan_runs


class Command(BaseCommand):
    help = 'Background scheduler for periodic crawl triggering'

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=int, default=30,
                            help='Check interval in seconds (default: 30)')

    def handle(self, *args, **options):
        check_interval = options['interval']
        self._running = True

        signal.signal(signal.SIGTERM, self._stop_handler)
        signal.signal(signal.SIGINT, self._stop_handler)

        self.stdout.write(self.style.SUCCESS(
            f'Scheduler started (check interval: {check_interval}s)'
        ))

        while self._running:
            try:
                self._tick()
            except Exception as e:
                self.stderr.write(f'Scheduler error: {e}')

            for _ in range(check_interval):
                if not self._running:
                    break
                time.sleep(1)

        self.stdout.write(self.style.SUCCESS('Scheduler stopped'))

    def _stop_handler(self, signum, frame):
        self._running = False

    def _tick(self):
        now = timezone.now()

        check_orphan_runs()

        for website in BiddingWebsite.objects.filter(is_active=True):
            if website.requires_login:
                continue

            config = CrawlerConfig.objects.filter(website_code=website.code).first()
            if config and not config.is_scheduled_enabled:
                continue

            interval_minutes = website.crawl_interval or 60
            # Use the per-site last run time from its CrawlerRun history.
            # 只看最近一次（含失败）的结束时间：若上次失败，也按间隔退避，
            # 避免从未成功过的站点每 30 秒被重试一次形成失败循环。
            last_run = CrawlerRun.objects.filter(
                website_code=website.code,
                finished_at__isnull=False,
            ).order_by('-finished_at').first()
            if last_run and last_run.finished_at:
                elapsed = now - last_run.finished_at
                if elapsed.total_seconds() < interval_minutes * 60:
                    continue

            running = CrawlerRun.objects.filter(
                website_code=website.code, status='running'
            ).exists()
            if running:
                continue

            run = trigger_crawl(website.code, trigger_type='scheduled')
            if run:
                self.stdout.write(
                    f'Scheduled crawl triggered for {website.code}'
                )
