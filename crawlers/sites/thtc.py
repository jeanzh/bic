"""天恒招标 crawler (www.thtc.com.cn).

栏目「招标信息」(/news/1/) 为门户 SaaS（中企动力）服务端渲染，匿名可访问、
无 WAF，普通 HTTP GET 即可。列表按发布时间倒序，每页 10 条，分页 URL 为
/bidding/{栏目ID}-{offset}-10.html（offset = 0,10,20,...）。列表已含标题与
发布日期；正文需进详情页。

详情页 /News_Details/{id}.html 为静态 HTML，正文在 div[class*="e_richText"]
（含招标编号/招标人/联系方式），可直接 GET 解析，无需浏览器。
"""
import asyncio
import re
import sys
from datetime import datetime

from bs4 import BeautifulSoup
from django.utils import timezone as tz

from bidding.models import BiddingInfo
from crawlers.base import BaseSiteCrawler, parse_date, parse_project_no


class ThtcCrawler(BaseSiteCrawler):
    code = 'thtc'
    name = '天恒招标'
    url = 'http://www.thtc.com.cn/news/1/'

    BASE = 'http://www.thtc.com.cn'
    COLUMN_ID = '1986725342372503552'   # 招标信息栏目 detailId（news/1/）
    PAGE_SIZE = 10

    fetch_detail_enabled = True
    max_detail_fetch = 500              # 详情为一次静态 GET，覆盖 max_pages 全量列表
    detail_concurrency = 8

    request_delay = 0.5
    retry_delay = 5.0
    max_retries = 3
    max_pages = 50                      # 共 405 页；增量靠 cutoff 提前停页

    USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36')

    async def crawl(self, context):
        items = []
        seen = {}
        cutoff = self._site_cutoff()
        await self._crawl_list(context, items, seen, cutoff=cutoff)
        await self._enrich_details(context, items)
        return items

    def _site_cutoff(self):
        """本站已入库数据的最新发布日期。"""
        latest = (BiddingInfo.objects
                  .filter(website__code=self.code, publish_date__isnull=False)
                  .order_by('-publish_date')
                  .values_list('publish_date', flat=True)
                  .first())
        return latest.isoformat() if latest else None

    def _list_url(self, page_no):
        offset = (page_no - 1) * self.PAGE_SIZE
        return f'{self.BASE}/bidding/{self.COLUMN_ID}-{offset}-{self.PAGE_SIZE}.html'

    async def _crawl_list(self, context, items, seen, cutoff=None):
        page_no = 1
        while page_no <= self.max_pages:
            url = self._list_url(page_no)
            html = await self._fetch(context, url)
            if not html:
                break
            page_items = self._parse_page(html)
            if not page_items:
                break

            reached_cutoff = False
            for item in page_items:
                uid = item['source_unique_id']
                if uid in seen:
                    continue
                seen[uid] = True
                if (cutoff and item.get('publish_date')
                        and item['publish_date'] < cutoff):
                    # 列表按发布时间倒序，遇 cutoff 即停页。
                    reached_cutoff = True
                    break
                items.append(item)

            if reached_cutoff or len(page_items) < self.PAGE_SIZE:
                break
            page_no += 1

    async def _fetch(self, context, url):
        for attempt in range(self.max_retries + 1):
            if attempt:
                await asyncio.sleep(self.retry_delay)
            try:
                await asyncio.sleep(self.request_delay)
                resp = await context.request.get(
                    url, headers={'User-Agent': self.USER_AGENT},
                )
                if resp.status != 200:
                    continue
                return await resp.text()
            except Exception as e:
                print(f'[thtc] fetch error {url}: {e}', file=sys.stderr)
                continue
        return ''

    def _parse_page(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items = []
        for node in soup.select('div.cbox-1.p_loopitem'):
            item = self._parse_item(node)
            if item:
                items.append(item)
        return items

    def _parse_item(self, node):
        try:
            a = node.select_one('a[href*="News_Details"]')
            if not a:
                return None
            title = a.get_text(strip=True)
            href = a.get('href') or ''
            if not title or not href:
                return None
            m = re.search(r'News_Details/(\d+)\.html', href)
            if not m:
                return None
            uid = m.group(1)
            date_el = node.select_one('.e_timeFormat-3')
            publish_date = parse_date(date_el.get_text(strip=True)) if date_el else None
            return {
                'title': title,
                'source_url': self.BASE + href,
                'source_unique_id': uid,
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
                'raw_data': {},
            }
        except Exception as e:
            print(f'[thtc] item parse error: {e}', file=sys.stderr)
            return None

    async def _enrich_details(self, context, items):
        await self.enrich_http(context, items)

    async def _enrich_one(self, context, item):
        uid = item.get('source_unique_id')
        if not uid:
            return
        try:
            await asyncio.sleep(self.request_delay)
            resp = await context.request.get(
                f'{self.BASE}/News_Details/{uid}.html',
                headers={'User-Agent': self.USER_AGENT},
            )
            if resp.status != 200:
                return
            html = await resp.text()
            soup = BeautifulSoup(html, 'lxml')
            body_el = soup.select_one('div[class*="e_richText"]')
            if not body_el:
                return
            body = body_el.get_text(' ', strip=True).replace('\xa0', ' ')
            if not body:
                return
            item['content'] = body
            if not item.get('project_no'):
                item['project_no'] = parse_project_no(body)
            if not item.get('purchaser'):
                item['purchaser'] = self._extract_purchaser(body)
            if item.get('deadline') is None:
                item['deadline'] = self._parse_deadline(body)
            if not item.get('contact_info'):
                item['contact_info'] = self._extract_contact(body)
        except Exception as e:
            print(f'[thtc] detail {uid}: {e}', file=sys.stderr)

    def _extract_purchaser(self, text):
        """从「天恒招标有限公司受 XX 的委托」或「招标人：XX」取招标人。"""
        m = re.search(r'受\s*([^\s，,。；;）)]{2,60}?)\s*的委托', text or '')
        if m:
            return m.group(1).strip()
        m = re.search(r'(?:招\s*标\s*人|采\s*购\s*人)\s*[：:]\s*([^\s，,。；;）)]{2,60})', text or '')
        if m:
            return m.group(1).strip()
        return ''

    def _parse_deadline(self, text):
        """从「投标截止时间：YYYY年M月D日H时M分」取截止时间（北京时间）。"""
        m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})时(\d{1,2})分', text or '')
        if not m:
            return None
        y, mo, d, h, mi = (int(x) for x in m.groups())
        try:
            return tz.make_aware(datetime(y, mo, d, h, mi))
        except ValueError:
            return None

    def _extract_contact(self, text):
        """取正文末尾「联系方式」小节（正文另有一处「…+联系方式」报名说明需避开）。"""
        m = re.search(
            r'联系方式\s*(?=招\s*标\s*人|招标代理机构|联\s*系\s*人|地\s*址)',
            text or '',
        )
        if not m:
            return ''
        return text[m.start():].strip()
