"""诚E招 crawler (www.chengezhao.com).

栏目「业务公告 > 项目公告」为服务端渲染，匿名可访问、无 WAF，且列表页
的 <p> 里已包含完整公告正文（含招标编号/预算/联系方式），故无需再进详情页。
列表每页 8 条，分页 /page/N/，共数百页，按发布时间倒序。
"""
import re
import sys
from urllib.parse import quote

from bs4 import BeautifulSoup

from bidding.models import BiddingInfo
from crawlers.base import BaseSiteCrawler, parse_project_no


class ChengezhaoCrawler(BaseSiteCrawler):
    code = 'chengezhao'
    name = '诚E招'
    url = ('https://www.chengezhao.com/cms/categories/'
           '%E4%B8%9A%E5%8A%A1%E5%85%AC%E5%91%8A/%E9%A1%B9%E7%9B%AE%E5%85%AC%E5%91%8A/')

    fetch_detail_enabled = False  # 列表页已含完整正文

    BASE = 'https://www.chengezhao.com'
    CATEGORY = quote('业务公告/项目公告', safe='/')
    USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36')

    async def crawl(self, context):
        items = []
        cutoff = self._site_cutoff()
        page_no = 1
        while page_no <= self.max_pages:
            html = await self._fetch_page(context, page_no)
            if not html:
                break
            page_items, total_pages = self._parse_page(html)
            if not page_items:
                break

            reached_cutoff = False
            for item in page_items:
                # 列表按发布时间倒序，遇到早于截止日期的条目即停止翻页
                if (cutoff and item.get('publish_date')
                        and item['publish_date'] < cutoff):
                    reached_cutoff = True
                    break
                items.append(item)

            if reached_cutoff or page_no >= total_pages:
                break
            page_no += 1
        return items

    def _site_cutoff(self):
        latest = (BiddingInfo.objects
                  .filter(website__code=self.code, publish_date__isnull=False)
                  .order_by('-publish_date')
                  .values_list('publish_date', flat=True)
                  .first())
        return latest.isoformat() if latest else None

    def _list_url(self, page_no):
        if page_no == 1:
            return f'{self.BASE}/cms/categories/{self.CATEGORY}/'
        return f'{self.BASE}/cms/categories/{self.CATEGORY}/page/{page_no}/'

    async def _fetch_page(self, context, page_no):
        url = self._list_url(page_no)
        try:
            resp = await context.request.get(
                url, headers={'User-Agent': self.USER_AGENT},
            )
            if resp.status != 200:
                return ''
            return await resp.text()
        except Exception as e:
            print(f'[chengezhao] fetch error {url}: {e}', file=sys.stderr)
            return ''

    def _parse_page(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items = []
        for node in soup.select('div.cez-business-main__news-item'):
            item = self._parse_item(node)
            if item:
                items.append(item)
        return items, self._parse_total_pages(soup)

    def _parse_total_pages(self, soup):
        cur = soup.select_one('span.pagination__item--current')
        if cur:
            text = cur.get_text(strip=True)
            if '/' in text:
                try:
                    return int(text.split('/')[-1])
                except ValueError:
                    pass
        return self.max_pages

    def _parse_item(self, node):
        try:
            a_title = node.select_one('div.cez-business-main__news-item-content h3 a')
            if not a_title:
                return None
            title = a_title.get_text(strip=True)
            href = a_title.get('href') or ''
            if not title or not href:
                return None

            publish_date = None
            date_a = node.select_one('a.cez-business-main__news-item-date')
            if date_a:
                spans = date_a.select('span')
                if len(spans) >= 2:
                    mmdd = spans[0].get_text(strip=True)
                    yyyy = spans[1].get_text(strip=True)
                    if yyyy and mmdd:
                        publish_date = f'{yyyy}-{mmdd}'

            content = ''
            p = node.select_one('div.cez-business-main__news-item-content p')
            if p:
                content = p.get_text('\n', strip=True).replace('\xa0', ' ')

            return {
                'title': title,
                'source_url': self.BASE + href,
                'source_unique_id': href.rstrip('/').rsplit('/', 1)[-1],
                'project_no': parse_project_no(content),
                'publish_date': publish_date,
                'purchaser': '',
                'region': self._extract_region(content),
                'industry': '',
                'budget_amount': None,
                'bid_amount': None,
                'deadline': None,
                'content': content,
                'contact_info': '',
                'attachment_url': '',
                'status': 'open',
                'raw_data': {},
            }
        except Exception as e:
            print(f'[chengezhao] item parse error: {e}', file=sys.stderr)
            return None

    def _extract_region(self, content):
        """从正文「项目所在地区：」提取地区，如 '广东省，深圳市'。"""
        m = re.search(r'(?:项目)?所在地区[：:]\s*([^\n。；;）)]+)', content or '')
        if m:
            return m.group(1).strip().rstrip('，,）)】]')
        return ''
