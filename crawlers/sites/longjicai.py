"""龙集采 crawler (ibuy.ccb.com) — 中国建设银行集中采购平台.

站点为 Vue SPA，但全部数据以静态 JSON 形式提供、匿名可访问（无需登录）：
- 栏目树：/json/contentFile/channel.json
- 列表：/json/contentFile/{menuId}/{page}.json（每页固定 1000 条，按发布时间倒序）
- 正文：/json/contentFile/{menuId}/{year}/{id}.json（content 为 HTML）

「招标专区」（#/ccbbidzbzq，菜单 354）下含 6 个子栏目，本爬虫逐一采集：
招标公告(355) / 资格预审公告(356) / 更正公告(357) / 中标候选人公示(358) /
中标结果公示(359) / 其他公告(379)。

站内搜索框为前端标题包含匹配，无服务端关键字接口，故关键字过滤（取自启用
FilterRule 的「包含关键词」并集）在本爬虫内按标题包含实现。
"""
import asyncio
import re
import sys
from datetime import datetime

from bs4 import BeautifulSoup
from django.utils import timezone as tz

from bidding.models import BiddingInfo, FilterRule
from bidding.services import prefilter_item
from crawlers.base import (
    BaseSiteCrawler, parse_amount, parse_date, parse_project_no,
)


class LongjicaiCrawler(BaseSiteCrawler):
    code = 'longjicai'
    name = '龙集采'
    url = 'https://ibuy.ccb.com/cms/index.html#/ccbbidzbzq'

    BASE = 'https://ibuy.ccb.com'
    JSON_BASE = BASE + '/json/contentFile'

    # 招标专区（菜单 354）下的子栏目：menuId -> 名称
    # 仅采集「招标公告」栏目。
    COLUMNS = [
        ('355', '招标公告'),
    ]

    # 中标类栏目：结果/候选人公示已产生中标结果（当前未采集）
    AWARDED_MENUS = {'358', '359'}

    fetch_detail_enabled = True
    max_detail_fetch = 5000  # 正文为静态 JSON，抓取成本低，覆盖全部列表条目以匹配正文关键词
    detail_concurrency = 8

    request_delay = 0.5    # 静态 JSON，无频控，适度间隔
    retry_delay = 5.0
    max_retries = 3
    PAGE_SIZE = 1000       # 站内每页固定条数

    USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36')

    async def crawl(self, context):
        items = []
        seen = {}
        for menu_id, label in self.COLUMNS:
            await self._crawl_column(context, menu_id, label, items, seen)
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

    async def _crawl_column(self, context, menu_id, label, items, seen):
        page_no = 1
        # 无启用规则时按日期截止位停止翻页（全量）；有规则时需扫完全部页
        # 才能找齐命中地区/关键词的条目。
        has_rules = FilterRule.objects.filter(is_active=True).exists()
        cutoff = None if has_rules else self._site_cutoff()
        while page_no <= self.max_pages:
            rows = await self._fetch_json(context, f'/{menu_id}/{page_no}.json')
            if not rows:
                break
            reached_cutoff = False
            for row in rows:
                item = self._parse_row(row, menu_id, label)
                if not item:
                    continue
                uid = item['source_unique_id']
                if uid in seen:
                    continue
                seen[uid] = True
                # 站内搜索框为标题包含匹配（无服务端关键字接口）。这里用
                # 列表级预过滤（标题包含/排除关键词 + 地区）与 apply_filters
                # 保持一致，使详情抓取预算花在真正会入库的条目上；正文中的
                # 关键词未知，留待入库时 apply_filters 兜底。
                if not prefilter_item(item.get('title'), item.get('region')):
                    continue
                # 列表按发布时间倒序，遇早于本站截止日期的条目即停止翻页
                if (cutoff and item.get('publish_date')
                        and item['publish_date'] < cutoff):
                    reached_cutoff = True
                    break
                items.append(item)
            if reached_cutoff or len(rows) < self.PAGE_SIZE:
                break
            page_no += 1

    async def _fetch_json(self, context, path):
        url = self.JSON_BASE + path
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
                return await resp.json()
            except Exception as e:
                print(f'[longjicai] fetch error {url}: {e}', file=sys.stderr)
                continue
        return []

    def _parse_row(self, row, menu_id, label):
        try:
            rid = row.get('id') or ''
            title = (row.get('title') or '').strip()
            if not rid or not title:
                return None
            release = row.get('releaseDate') or ''
            return {
                'title': title,
                'source_url': (f'{self.BASE}/cms/index.html#/content'
                               f'?pId={menu_id}&id={rid}'),
                'source_unique_id': rid,
                'project_no': '',
                'publish_date': parse_date(release),
                'purchaser': '',
                'region': (row.get('area') or '').strip(),
                'industry': '',
                'budget_amount': None,
                'bid_amount': None,
                'deadline': None,
                'content': '',
                'contact_info': '',
                'attachment_url': '',
                'status': 'awarded' if menu_id in self.AWARDED_MENUS else 'open',
                'raw_data': {
                    'menu_id': menu_id,
                    'column': label,
                    'release_date': release,
                    'release_inst': row.get('releaseInst') or '',
                    'platform_inst': row.get('ptInst') or '',
                },
            }
        except Exception as e:
            print(f'[longjicai] item parse error: {e}', file=sys.stderr)
            return None

    async def _enrich_details(self, context, items):
        await self.enrich_http(context, items)

    async def _enrich_one(self, context, item):
        raw = item.get('raw_data') or {}
        menu_id = raw.get('menu_id')
        rid = item.get('source_unique_id')
        if not menu_id or not rid:
            return
        year = (raw.get('release_date') or '')[:4] or 'null'
        try:
            data = await self._fetch_json(context, f'/{menu_id}/{year}/{rid}.json')
            if not isinstance(data, dict) or not data.get('content'):
                return
            soup = BeautifulSoup(data['content'], 'lxml')
            body = soup.get_text(' ', strip=True)
            if not body:
                return
            item['content'] = body
            if not item.get('project_no'):
                item['project_no'] = parse_project_no(body)
            if not item.get('purchaser'):
                item['purchaser'] = self._extract_purchaser(body)
            if item.get('budget_amount') is None:
                item['budget_amount'] = self._extract_amount(body)
            if item.get('deadline') is None:
                item['deadline'] = self._extract_deadline(body)
            if not item.get('contact_info'):
                item['contact_info'] = self._extract_contact(body)
        except Exception as e:
            print(f'[longjicai] detail {rid}: {e}', file=sys.stderr)

    def _extract_purchaser(self, text):
        for pat in (
            r'招\s*标\s*人\s*[：:为]\s*([^\s，,。；;）)]{2,60})',
            r'采\s*购\s*人\s*[：:为]\s*([^\s，,。；;）)]{2,60})',
        ):
            m = re.search(pat, text or '')
            if m:
                return m.group(1).strip()
        return ''

    def _extract_amount(self, text):
        # 标签形如「最高限价」「采购预算」「预算金额」「预算」，其后可能带
        # （说明）再跟冒号；金额可能写作「人民币 ¥ 1438.19 万元」含空格/¥。
        pat = (
            r'(?:最高限价|采购预算|预算金额|预算额|预算)'
            r'\s*(?:（[^）]*）)?\s*[：:]\s*'
            r'[^。；;）)]{0,30}?([\d,，.]+(?:\s*(?:万元|亿元|万|亿))?)'
        )
        m = re.search(pat, text or '')
        if m:
            amt = parse_amount(m.group(1))
            if amt is not None:
                return amt
        return None

    def _extract_deadline(self, text):
        m = re.search(
            r'(?:投标截止时间|报名截止时间|递交截止时间)[：:]\s*'
            r'(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})时(\d{1,2})分',
            text or '',
        )
        if not m:
            return None
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          int(m.group(4)), int(m.group(5)))
            return tz.make_aware(dt)
        except ValueError:
            return None

    def _extract_contact(self, text):
        """公告末尾「联系方式」段落。"""
        m = re.search(r'(?:联系方式|联系信息)[：:]\s*(.+)', text or '')
        if m:
            return m.group(1).strip()
        return ''
