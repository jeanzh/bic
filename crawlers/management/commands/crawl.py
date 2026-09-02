import os
import sys
import traceback
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from bidding.models import BiddingWebsite, BiddingInfo
from bidding.services import apply_filters
from crawlers.models import CrawlerRun
from crawlers.sites import get_crawler

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/120.0.0.0 Safari/537.36')


class Command(BaseCommand):
    help = 'Run a crawler for a given website'

    def add_arguments(self, parser):
        parser.add_argument('website_code')
        parser.add_argument('run_id', type=int)
        parser.add_argument('--headed', action='store_true', help='Run in headed mode')

    def handle(self, *args, **options):
        website_code = options['website_code']
        run_id = options['run_id']
        headless = not options['headed']

        # Allow sync ORM calls from async context in this standalone process
        os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

        CrawlerRun.objects.filter(pk=run_id).update(
            pid=os.getpid(),
            status='running',
            started_at=timezone.now(),
        )

        exit_code = 0
        try:
            import asyncio
            asyncio.run(self._run_crawl(website_code, run_id, headless))
            CrawlerRun.objects.filter(pk=run_id).update(
                status='completed',
                finished_at=timezone.now(),
            )
            self.stdout.write(self.style.SUCCESS(f'{website_code} crawl completed'))
        except Exception as e:
            CrawlerRun.objects.filter(pk=run_id).update(
                status='failed',
                finished_at=timezone.now(),
                error_message=str(e)[:500],
            )
            self.stderr.write(f'Crawl failed: {e}')
            traceback.print_exc()
            exit_code = 1

        sys.exit(exit_code)

    async def _run_crawl(self, website_code: str, run_id: int, headless: bool):
        from playwright.async_api import async_playwright

        website = BiddingWebsite.objects.filter(code=website_code).first()

        if website and website.requires_login:
            raise RuntimeError(
                f'{website_code} requires login; login crawler not implemented yet'
            )

        crawler = get_crawler(website_code)
        if crawler is None:
            raise RuntimeError(f'No crawler registered for code: {website_code}')

        # Allow a per-website URL (e.g. a specific province platform) to
        # override the crawler's default entry point.
        if website and website.url:
            crawler.url = website.url

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            context = await browser.new_context(
                accept_downloads=True,
                user_agent=USER_AGENT,
                locale='zh-CN',
            )
            try:
                items = await crawler.crawl(context)
                saved = 0
                for item in items:
                    if not apply_filters(item):
                        continue
                    if self._save_item(website, item):
                        saved += 1
                CrawlerRun.objects.filter(pk=run_id).update(items_found=saved)
                self.stdout.write(f'{website_code}: {len(items)} fetched, {saved} saved')
            finally:
                await browser.close()

    def _save_item(self, website: BiddingWebsite, item: dict):
        if not website:
            return None

        source_unique_id = item.get('source_unique_id') or item.get('source_url') or ''
        if not source_unique_id:
            return None

        defaults = {
            'title': (item.get('title') or '')[:500],
            'source_url': item.get('source_url') or '',
            'project_no': item.get('project_no') or '',
            'publish_date': item.get('publish_date'),
            'purchaser': item.get('purchaser') or '',
            'region': item.get('region') or '',
            'industry': item.get('industry') or '',
            'budget_amount': _to_decimal(item.get('budget_amount')),
            'bid_amount': _to_decimal(item.get('bid_amount')),
            'deadline': item.get('deadline'),
            'content': item.get('content') or '',
            'contact_info': item.get('contact_info') or '',
            'attachment_url': item.get('attachment_url') or '',
            'status': item.get('status') or 'open',
            'raw_data': item.get('raw_data') or {},
        }

        obj, created = BiddingInfo.objects.get_or_create(
            website=website,
            source_unique_id=source_unique_id,
            defaults=defaults,
        )
        if not created:
            for field, value in defaults.items():
                setattr(obj, field, value)
            obj.save()
        return obj


def _to_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None
