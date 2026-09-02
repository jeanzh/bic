"""中国金融采购平台 crawler (zgjr.gdtzb.com).

国电投电子商务平台聚合的中国金融采购平台，分 5 个栏目（v1..v5）。
站点带阿里云 WAF（acw_sc__v2 挑战），列表页与详情页（跨域 www.gdtzb.com）
首次访问都会被重定向到挑战页；Playwright 执行 JS 后会自动写入 cookie 并刷新。

匿名可拿到：标题、发布日期、所属地区、部分正文（敏感字段被 *** 掩码）、
部分项目编号。预算金额 / 截止时间 / 联系方式需付费会员，无法匿名抓取。
"""
import re
import sys
from urllib.parse import urljoin

from bidding.models import BiddingInfo
from crawlers.base import parse_date
from crawlers.sites.gdtzb_base import GdtzbBase


class ZgjrCrawler(GdtzbBase):
    code = 'zgjr'
    name = '中国金融采购平台'
    url = 'http://zgjr.gdtzb.com/'

    # 五个栏目：列表入口 URL -> 行业标签（用于 industry 字段与去重/截止判定）
    SECTIONS = [
        ('http://zgjr.gdtzb.com/v1/', '人寿保险'),
        ('http://zgjr.gdtzb.com/v2/', '农业发展银行'),
        ('http://zgjr.gdtzb.com/v3/', '建设银行'),
        ('http://zgjr.gdtzb.com/v4/', '中国邮政'),
        ('http://zgjr.gdtzb.com/v5/', '中华联合'),
    ]

    LIST_ITEM_SELECTOR = 'div.pdbox ul li'

    async def crawl(self, context):
        page = await context.new_page()
        items = []
        try:
            for section_url, industry in self.SECTIONS:
                cutoff = self._section_cutoff(industry)
                try:
                    page_no = 1
                    section_max = self.max_pages
                    while True:
                        url = section_url if page_no == 1 else f'{section_url}{page_no}/'
                        await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                        if not await self._wait_content(page, self.LIST_ITEM_SELECTOR):
                            print(f'[zgjr] list not loaded: {url}', file=sys.stderr)
                            break

                        if page_no == 1:
                            total = await self._total_pages(page)
                            if total:
                                section_max = min(self.max_pages, total)

                        lis = await page.query_selector_all(self.LIST_ITEM_SELECTOR)
                        if not lis:
                            break

                        reached_cutoff = False
                        for li in lis:
                            item = await self._parse_li(li, section_url, industry)
                            if not item:
                                continue
                            # 列表按发布时间倒序，遇到早于本栏目截止日期的条目即停止翻页
                            if (cutoff and item.get('publish_date')
                                    and item['publish_date'] < cutoff):
                                reached_cutoff = True
                                break
                            # 注意：本栏目地区仅在详情页可得，列表阶段不做 region 预筛，
                            # 完整过滤（含 region）由 crawl 命令入库时的 apply_filters 完成。
                            items.append(item)

                        if reached_cutoff:
                            break
                        page_no += 1
                        if page_no > section_max:
                            break
                except Exception as e:
                    print(f'[zgjr] list error {section_url}: {e}', file=sys.stderr)
        finally:
            await page.close()
        return await self.enrich_items(context, items)

    def _section_cutoff(self, industry):
        """本栏目已入库数据的最新发布日期。"""
        latest = (BiddingInfo.objects
                  .filter(website__code=self.code,
                          industry=industry,
                          publish_date__isnull=False)
                  .order_by('-publish_date')
                  .values_list('publish_date', flat=True)
                  .first())
        return latest.isoformat() if latest else None

    async def _total_pages(self, page):
        try:
            pages = await page.query_selector('div.pages')
            if not pages:
                return 0
            text = await pages.inner_text()
            m = re.search(r'共\s*(\d+)\s*页', text)
            return int(m.group(1)) if m else 0
        except Exception:
            return 0

    async def _parse_li(self, li, section_url, industry):
        try:
            a = await li.query_selector('a')
            title = (await a.inner_text()).strip() if a else ''
            href = (await a.get_attribute('href')) if a else ''
            if not title or not href:
                return None

            source_url = href if href.startswith('http') else urljoin(section_url, href)

            publish_date = None
            date_span = await li.query_selector('span.fr')
            if date_span:
                publish_date = parse_date(await date_span.inner_text())

            return {
                'title': title,
                'source_url': source_url,
                'source_unique_id': href.rsplit('/', 1)[-1] or href,
                'project_no': '',
                'publish_date': publish_date,
                'purchaser': '',
                'region': '',
                'industry': industry,
                'budget_amount': None,
                'bid_amount': None,
                'deadline': None,
                'content': '',
                'contact_info': '',
                'attachment_url': '',
                'status': 'open',
                'raw_data': {'section': section_url},
            }
        except Exception as e:
            print(f'[zgjr] item parse error: {e}', file=sys.stderr)
            return None
