"""中国采购与招标网 crawler (www.chinabidding.cn)."""
import sys

from crawlers.base import BaseSiteCrawler, parse_amount, parse_date


class ChinaBiddingCrawler(BaseSiteCrawler):
    code = 'chinabidding'
    name = '中国采购与招标网'
    url = 'https://www.chinabidding.cn/search/search?keyword='

    async def crawl(self, context):
        page = await context.new_page()
        items = []
        try:
            await page.goto(self.url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(3000)

            # Generic result extraction: anchors inside list containers.
            rows = await page.query_selector_all(
                'div.search-result li, ul.search-list li, div.list li, li[class*="result"]'
            )
            if not rows:
                rows = await page.query_selector_all('a[href*="detail"], a[href*="content"]')

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
                        href = 'https://www.chinabidding.cn' + href

                    spans = await row.query_selector_all('span')
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
                    print(f'[chinabidding] item parse error: {e}', file=sys.stderr)
        finally:
            await page.close()
        return await self.enrich_items(context, items)
