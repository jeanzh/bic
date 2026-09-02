"""邮银易采 crawler (cg.psbc.com) — 中国邮政储蓄银行邮银易采平台.

站点为传统 CMS（易招标），匿名可访问、无需登录，普通 HTTP 即可（无 legacy
SSL 问题），故用 context.request 直接取数：
- 列表：POST /cms/api/dynamicData/queryContentPage
  请求体 {"pageSize","pageNo","dto":{siteId:"725",categoryId, city, county,
          purchaseMode, secondCompanyId}}
  返回 res.{total, rows[{title, url, publishDate, quoteBeginTime,
          quoteEndTime, purchaseMode, agentCompanyName, categoryName}]}
- 正文：GET /cms/api/dynamicData/queryContentInfo?contentId={uid}
  返回 res.content.text（HTML 全文）。静态详情页正文被登录墙遮挡（仅返回
  「预览结束…」占位），真正全文由此接口返回。

「招标信息」栏目（前缀 1ywgg）下子栏目：
  1ywgg1=资格预审公告(222) / 1ywgg2=招标公告(223) / 1ywgg3=中标候选人公示(224)
  / 1ywgg4=变更公告(225) / 1ywgg5=其他公告(226) / 1ywgg7=中标公告(235)。
本项目按需采集「招标公告」（categoryId=223）。

列表直接给出报价截止时间 quoteEndTime（即投标截止时间），无需从正文解析。
列表无地区字段（provinceName/cityName/countyName 均为空），地区须从标题或
agentCompanyName（分支行名）推断，故列表级用 prefilter_item 预过滤，入库时
apply_filters 再用正文精确地区兜底。
"""
import asyncio
import json
import re
import sys
from datetime import datetime

from bs4 import BeautifulSoup

from bidding.models import BiddingInfo, FilterRule
from bidding.services import prefilter_item
from crawlers.base import (
    BaseSiteCrawler, parse_amount, parse_date, parse_project_no,
)


class YouyinCrawler(BaseSiteCrawler):
    code = 'youyin'
    name = '邮银易采'
    url = 'https://cg.psbc.com/cms/default/webfile/1ywgg2/index.html'

    BASE = 'https://cg.psbc.com'
    WEBFILE = BASE + '/cms/default/webfile'
    LIST_API = BASE + '/cms/api/dynamicData/queryContentPage'
    DETAIL_API = BASE + '/cms/api/dynamicData/queryContentInfo'

    SITE_ID = '725'

    # 招标信息子栏目：categoryId -> (url段, 名称)。仅采集「招标公告」。
    COLUMNS = [
        ('223', '1ywgg2', '招标公告'),
    ]

    fetch_detail_enabled = True
    max_detail_fetch = 10000  # 正文为一次 GET，成本低，覆盖全部列表条目以匹配正文关键词
    detail_concurrency = 8

    request_delay = 0.5
    retry_delay = 5.0
    max_retries = 3
    PAGE_SIZE = 200         # 服务器可放大 pageSize，减少翻页
    max_pages = 100         # 招标公告 9480 条 / 200 ≈ 48 页

    USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36')

    # 省级行政区全称（含「市/省/自治区」），用于从标题/分支名精确取地区。
    CHINA_REGIONS = (
        '北京市', '天津市', '上海市', '重庆市',
        '河北省', '山西省', '辽宁省', '吉林省', '黑龙江省',
        '江苏省', '浙江省', '安徽省', '福建省', '江西省', '山东省',
        '河南省', '湖北省', '湖南省', '广东省', '海南省',
        '四川省', '贵州省', '云南省', '陕西省', '甘肃省', '青海省', '台湾省',
        '内蒙古自治区', '广西壮族自治区', '西藏自治区',
        '宁夏回族自治区', '新疆维吾尔自治区',
        '香港特别行政区', '澳门特别行政区',
    )
    MUNICIPALITIES = {'北京': '北京市', '天津': '天津市', '上海': '上海市', '重庆': '重庆市'}
    REGION_ALIASES = {
        '新疆': '新疆维吾尔自治区', '广西': '广西壮族自治区',
        '西藏': '西藏自治区', '宁夏': '宁夏回族自治区', '内蒙古': '内蒙古自治区',
    }

    async def crawl(self, context):
        items = []
        seen = {}
        for cat_id, url_seg, label in self.COLUMNS:
            await self._crawl_column(context, cat_id, url_seg, label, items, seen)
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

    async def _fetch_list(self, context, cat_id, page_no):
        payload = {
            'pageSize': str(self.PAGE_SIZE),
            'pageNo': page_no,
            'dto': {
                'siteId': self.SITE_ID, 'categoryId': cat_id,
                'city': '', 'county': '', 'purchaseMode': '', 'secondCompanyId': '',
            },
        }
        for attempt in range(self.max_retries + 1):
            if attempt:
                await asyncio.sleep(self.retry_delay)
            try:
                await asyncio.sleep(self.request_delay)
                resp = await context.request.post(
                    self.LIST_API,
                    headers={'Content-Type': 'application/json'},
                    data=json.dumps(payload),
                )
                if resp.status != 200:
                    continue
                data = await resp.json()
                return (data.get('res') or {}).get('rows') or []
            except Exception as e:
                print(f'[youyin] list error page {page_no}: {e}', file=sys.stderr)
                continue
        return []

    async def _crawl_column(self, context, cat_id, url_seg, label, items, seen):
        page_no = 1
        has_rules = FilterRule.objects.filter(is_active=True).exists()
        cutoff = None if has_rules else self._site_cutoff()
        while page_no <= self.max_pages:
            rows = await self._fetch_list(context, cat_id, page_no)
            if not rows:
                break
            reached_cutoff = False
            for row in rows:
                item = self._parse_row(row, cat_id, url_seg, label)
                if not item:
                    continue
                uid = item['source_unique_id']
                if uid in seen:
                    continue
                seen[uid] = True
                # 列表无地区字段，用标题/分支名推断地区做预过滤（正文精确地区留待入库）
                if not prefilter_item(item.get('title'), item.get('region')):
                    continue
                if (cutoff and item.get('publish_date')
                        and item['publish_date'] < cutoff):
                    reached_cutoff = True
                    break
                items.append(item)
            if reached_cutoff or len(rows) < self.PAGE_SIZE:
                break
            page_no += 1

    def _parse_row(self, row, cat_id, url_seg, label):
        try:
            title = (row.get('title') or '').strip()
            rel = (row.get('url') or '').strip()
            if not title or not rel:
                return None
            uid = rel.rstrip('/').split('/')[-1].replace('.html', '')
            if not uid:
                uid = rel
            agent = (row.get('agentCompanyName') or '').strip()
            return {
                'title': title,
                'source_url': self.WEBFILE + rel,
                'source_unique_id': uid,
                'project_no': '',
                'publish_date': parse_date(row.get('publishDate') or ''),
                'purchaser': '',
                'region': self._region(agent, title),
                'industry': '',
                'budget_amount': None,
                'bid_amount': None,
                'deadline': self._parse_iso_dt(row.get('quoteEndTime') or ''),
                'content': '',
                'contact_info': '',
                'attachment_url': '',
                'status': 'open',
                'raw_data': {
                    'category_id': cat_id,
                    'column': label,
                    'url_seg': url_seg,
                    'purchase_mode': row.get('purchaseMode') or '',
                    'agent_company': agent,
                    'publish_time': row.get('publishDate') or '',
                    'quote_begin': row.get('quoteBeginTime') or '',
                    'quote_end': row.get('quoteEndTime') or '',
                },
            }
        except Exception as e:
            print(f'[youyin] item parse error: {e}', file=sys.stderr)
            return None

    def _parse_iso_dt(self, s):
        s = (s or '').strip()
        if not s:
            return None
        # 规范化时区偏移 +0800 -> +08:00 后交给 fromisoformat
        s = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', s)
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    def _region(self, agent, title):
        text = f'{agent or ""} {title or ""}'
        for r in self.CHINA_REGIONS:
            if r in text:
                return r
        # 分支名优先（更干净），标题兜底
        for src in (agent, title):
            if not src:
                continue
            # 先去掉机构名前缀噪声（如「邮储银行」的「行」），再取分支名
            clean = re.sub(
                r'中国邮政储蓄银行股份有限公司|中国邮政储蓄银行|邮储银行|股份有限公司|有限公司',
                '', src)
            m = re.search(r'([一-龥]{2,6}?)(?:分行|支行)', clean)
            if m and m.group(1):
                return self._normalize_region(m.group(1))
        return ''

    def _normalize_region(self, name):
        for k, v in self.MUNICIPALITIES.items():
            if name.startswith(k):
                return v
        for k, v in self.REGION_ALIASES.items():
            if name.startswith(k):
                return v
        return name

    async def _enrich_details(self, context, items):
        await self.enrich_http(context, items)

    async def _enrich_one(self, context, item):
        uid = item.get('source_unique_id')
        if not uid:
            return
        try:
            await asyncio.sleep(self.request_delay)
            resp = await context.request.get(
                f'{self.DETAIL_API}?contentId={uid}',
                headers={'User-Agent': self.USER_AGENT},
            )
            if resp.status != 200:
                return
            data = await resp.json()
            content = ((data.get('res') or {}).get('content')) or {}
            html = content.get('text') or ''
            if not html:
                return
            soup = BeautifulSoup(html, 'lxml')
            body = soup.get_text(' ', strip=True).replace('\xa0', ' ')
            if not body:
                return
            item['content'] = body
            if not item.get('project_no'):
                item['project_no'] = parse_project_no(body)
            if not item.get('purchaser'):
                item['purchaser'] = (self._extract_purchaser(body)
                                     or self._purchaser_from_title(item['title']))
            if item.get('budget_amount') is None:
                item['budget_amount'] = self._extract_amount(body)
            if not item.get('contact_info'):
                item['contact_info'] = self._extract_contact(body)
        except Exception as e:
            print(f'[youyin] detail {uid}: {e}', file=sys.stderr)

    def _extract_purchaser(self, text):
        for pat in (
            r'(?:招标人名称|招标人|采购人)\s*[：:为]\s*([^\s，,。；;）)]{2,60})',
            r'([^\s，,。；;）)]{2,60}?)\s*[（(]\s*(?:采购人|招标人)\s*[）)]',
        ):
            m = re.search(pat, text or '')
            if m:
                return m.group(1).strip()
        return ''

    def _purchaser_from_title(self, title):
        """标题以「中国邮政储蓄银行[股份有限公司]XX分行/支行」开头。"""
        t = title or ''
        m = re.search(r'(中国邮政储蓄银行(?:股份有限公司)?[一-龥]{1,8}?)(分行|支行)', t)
        if m:
            return m.group(1) + m.group(2)
        m2 = re.search(r'中国邮政储蓄银行(?:股份有限公司)?', t)
        if m2:
            return m2.group(0)
        return ''

    def _extract_amount(self, text):
        pat = (
            r'(?:最高含税投标限价|最高投标限价|最高限价|采购预算|预算金额|预算额|估算|预算)'
            r'\s*(?:（[^）]*）)?\s*(?:为|[：:])?\s*'
            r'[^。；;）)]{0,20}?([\d,，.]+(?:\s*(?:万元|亿元|万|亿))?)'
        )
        m = re.search(pat, text or '')
        if m:
            amt = parse_amount(m.group(1))
            if amt is not None:
                return amt
        return None

    def _extract_contact(self, text):
        # 「联系方式」小节可能不带冒号（get_text 以空格拼接），取其后整段。
        m = re.search(r'联系方式\s*[：:]?\s*(.+)', text or '')
        if m:
            return m.group(1).strip()
        m2 = re.search(r'联系信息\s*[：:]?\s*(.+)', text or '')
        if m2:
            return m2.group(1).strip()
        return ''
