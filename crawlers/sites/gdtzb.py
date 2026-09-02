"""国电投电子商务平台 crawler (www.gdtzb.com/zb/).

招标信息栏目，支持通过站内搜索 search.php?kw= 做关键字过滤。
关键字取自启用 FilterRule 的「包含关键词」并集；无关键字时爬全栏目。

站点带阿里云 WAF（acw_sc__v2 挑战），列表页与详情页首次访问都会重定向到
挑战页；Playwright 执行 JS 后自动写入 cookie 并刷新，真实内容随后出现。
"""
import sys
from urllib.parse import quote

from bidding.models import BiddingInfo, FilterRule
from crawlers.base import parse_date
from crawlers.sites.gdtzb_base import GdtzbBase


class GdtzbCrawler(GdtzbBase):
    code = 'gdtzb'
    name = '国电投电子商务平台'
    url = 'http://www.gdtzb.com/zb/'

    LIST_ITEM_SELECTOR = 'li.tender-list'
    SEARCH_URL = 'http://www.gdtzb.com/zb/search.php'

    async def crawl(self, context):
        page = await context.new_page()
        items = []
        seen = {}
        cutoff = self._site_cutoff()
        try:
            keywords = self._collect_keywords()
            if keywords:
                for kw in keywords:
                    await self._crawl_search(page, kw, cutoff, items, seen)
            else:
                await self._crawl_list(page, cutoff, items, seen)
        finally:
            await page.close()
        return await self.enrich_items(context, items)

    def _collect_keywords(self):
        """启用规则「包含关键词」的并集（去重、排序）。"""
        kws = set()
        for rule in FilterRule.objects.filter(is_active=True):
            for kw in rule.keyword_list():
                kws.add(kw)
        return sorted(kws)

    def _site_cutoff(self):
        """本站已入库数据的最新发布日期。"""
        latest = (BiddingInfo.objects
                  .filter(website__code=self.code, publish_date__isnull=False)
                  .order_by('-publish_date')
                  .values_list('publish_date', flat=True)
                  .first())
        return latest.isoformat() if latest else None

    async def _crawl_search(self, page, kw, cutoff, items, seen):
        await self._paginate(
            page,
            lambda n: f'{self.SEARCH_URL}?kw={quote(kw)}&esfd=title&page={n}',
            cutoff, items, seen, tag=f'kw={kw}',
        )

    async def _crawl_list(self, page, cutoff, items, seen):
        await self._paginate(
            page,
            lambda n: f'{self.url}?page={n}',
            cutoff, items, seen, tag='full',
        )

    async def _paginate(self, page, url_fn, cutoff, items, seen, tag=''):
        page_no = 1
        while page_no <= self.max_pages:
            url = url_fn(page_no)
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            if not await self._wait_content(page, self.LIST_ITEM_SELECTOR):
                print(f'[gdtzb] list not loaded ({tag}): {url}', file=sys.stderr)
                break

            lis = await page.query_selector_all(self.LIST_ITEM_SELECTOR)
            if not lis:
                break

            reached_cutoff = False
            for li in lis:
                item = await self._parse_li(li)
                if not item:
                    continue
                if item['source_unique_id'] in seen:
                    continue
                seen[item['source_unique_id']] = True
                # 列表按发布时间倒序，遇到早于本站截止日期的条目即停止翻页
                if (cutoff and item.get('publish_date')
                        and item['publish_date'] < cutoff):
                    reached_cutoff = True
                    break
                items.append(item)

            if reached_cutoff or not await self._has_next_page(page):
                break
            page_no += 1

    async def _has_next_page(self, page):
        try:
            nxt = await page.query_selector('input#destoon_next')
            if not nxt:
                return False
            return bool((await nxt.get_attribute('value') or '').strip())
        except Exception:
            return False

    async def _parse_li(self, li):
        try:
            a = await li.query_selector('div.tender-title a')
            title = (await a.inner_text()).strip() if a else ''
            href = (await a.get_attribute('href')) if a else ''
            if not title or not href:
                return None

            date_el = await li.query_selector('p.date')
            publish_date = parse_date(await date_el.inner_text()) if date_el else None

            tags = []
            for t in await li.query_selector_all('div.tender-tags p a'):
                txt = (await t.inner_text()).strip()
                if txt:
                    tags.append(txt)

            return {
                'title': title,
                'source_url': href,
                'source_unique_id': href.rsplit('/', 1)[-1].replace('.html', '') or href,
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
                'raw_data': {'tags': tags},
            }
        except Exception as e:
            print(f'[gdtzb] item parse error: {e}', file=sys.stderr)
            return None
