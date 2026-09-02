"""省级公共资源交易中心 generic crawler.

Each province hosts its own public resource trading platform; most render
tender announcements as a paged list of titled links. The exact entry URL is
configured per BiddingWebsite record, and this crawler overrides `url` from
that record before crawling.
"""
import sys

from crawlers.base import BaseSiteCrawler, parse_amount, parse_date


class ProvinceCrawler(BaseSiteCrawler):
    code = 'province'
    name = '省级公共资源交易中心'
    url = ''

    async def crawl(self, context):
        page = await context.new_page()
        items = []
        try:
            await page.goto(self.url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(3000)

            # Common listing patterns across provincial trading platforms.
            rows = await page.query_selector_all(
                'ul.list li, ul.ewb-list li, ul.trade-list li, '
                'table tbody tr, li[class*="list"], li[class*="item"]'
            )
            if not rows:
                rows = await page.query_selector_all('a[href*="detail"], a[href*="content"], a[target="_blank"]')

            for row in rows:
                try:
                    a = await row.query_selector('a') if row else None
                    if a is None:
                        a = row
                    title = (await a.inner_text()).strip() if a else ''
                    href = (await a.get_attribute('href')) if a else ''
                    if not title or not href:
                        continue

                    if href.startswith('/'):
                        # Relative to the configured site host.
                        base = self.url.split('//', 1)[0] + '//' + self.url.split('//', 1)[1].split('/', 1)[0]
                        href = base + href

                    spans = await row.query_selector_all('span, td')
                    span_texts = []
                    for s in spans:
                        t = (await s.inner_text()).strip()
                        if t:
                            span_texts.append(t)

                    publish_date = None
                    for t in span_texts:
                        publish_date = parse_date(t)
                        if publish_date:
                            break

                    item = {
                        'title': title,
                        'source_url': href,
                        'source_unique_id': href.rsplit('/', 1)[-1] or href,
                        'project_no': '',
                        'publish_date': publish_date,
                        'purchaser': '',
                        'region': '',
                        'industry': '',
                        'budget_amount': None,
                        'bid_amount': None,
                        'deadline': None,
                        'content': '',
                        'contact_info': '',
                        'attachment_url': '',
                        'status': 'open',
                        'raw_data': {'spans': span_texts},
                    }
                    items.append(item)
                except Exception as e:
                    print(f'[province] item parse error: {e}', file=sys.stderr)
        finally:
            await page.close()
        return await self.enrich_items(context, items)
