"""中信金控采购共享平台 crawler (ebid.cfhc.citic) — 中信银行/中信金控集采平台.

站点同为易招标 CMS（与邮银易采同款），匿名可访问、无需登录，普通 HTTP 即可：
- 列表：POST /cms/api/dynamicData/queryContentPage
  请求体 {"pageSize","pageNo","dto":{siteId:"725",categoryId, city, county,
          purchaseMode, secondCompanyId}}
  返回 res.{total, rows[{title, url, publishDate, quoteBeginTime, quoteEndTime,
          agentCompanyName, provinceName, cityName, categoryName}]}
- 正文：GET /cms/default/webfile/{url}（静态 HTML，正文在 .text-part-text）。
  本站静态详情页直接含全文、无需登录（queryContentInfo 接口反而 404）。

「采购信息」栏目（前缀 ywgg）下子栏目：
  ywgg1=采购公告(211) / ywgg2=中标候选人公示(212) / ywgg3=采购结果公告(213)
  / ywgg4=变更公告(214)。本项目按需采集「采购公告」（categoryId=211）。

列表直接给出地区 provinceName（如「北京市」），故地区直接取自 provinceName，
无需从标题/分支名推断。列表也给出报价截止时间 quoteEndTime（即投标截止时间，
带 +00:00 时区，fromisoformat 直接解析为 aware 时间）。
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


class ZhongxinjinkongCrawler(BaseSiteCrawler):
    code = 'zhongxinjinkong'
    name = '中信金控采购共享平台'
    url = 'https://ebid.cfhc.citic/cms/default/webfile/ywgg1/index.html'

    BASE = 'https://ebid.cfhc.citic'
    WEBFILE = BASE + '/cms/default/webfile'
    LIST_API = BASE + '/cms/api/dynamicData/queryContentPage'

    SITE_ID = '725'

    # 采购信息子栏目：categoryId -> (url段, 名称)。仅采集「采购公告」。
    COLUMNS = [
        ('211', 'ywgg1', '采购公告'),
    ]

    fetch_detail_enabled = True
    max_detail_fetch = 6000  # 正文为一次 GET，成本低，覆盖全部列表条目以匹配正文关键词
    detail_concurrency = 8

    request_delay = 0.5
    retry_delay = 5.0
    max_retries = 3
    PAGE_SIZE = 10          # 服务器 pageSize 上限约 10（超过返回「系统繁忙」）
    max_pages = 520         # 无地区过滤时全量 5102 条 / 10 ≈ 511 页

    USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36')

    # 省级行政区代码（GB/T 2260），用于列表级按地区过滤（规则 region 映射）。
    PROVINCE_CODES = {
        '北京': '110000', '北京市': '110000',
        '天津': '120000', '天津市': '120000',
        '河北': '130000', '河北省': '130000',
        '山西': '140000', '山西省': '140000',
        '内蒙古': '150000', '内蒙古自治区': '150000',
        '辽宁': '210000', '辽宁省': '210000',
        '吉林': '220000', '吉林省': '220000',
        '黑龙江': '230000', '黑龙江省': '230000',
        '上海': '310000', '上海市': '310000',
        '江苏': '320000', '江苏省': '320000',
        '浙江': '330000', '浙江省': '330000',
        '安徽': '340000', '安徽省': '340000',
        '福建': '350000', '福建省': '350000',
        '江西': '360000', '江西省': '360000',
        '山东': '370000', '山东省': '370000',
        '河南': '410000', '河南省': '410000',
        '湖北': '420000', '湖北省': '420000',
        '湖南': '430000', '湖南省': '430000',
        '广东': '440000', '广东省': '440000',
        '广西': '450000', '广西壮族自治区': '450000',
        '海南': '460000', '海南省': '460000',
        '重庆': '500000', '重庆市': '500000',
        '四川': '510000', '四川省': '510000',
        '贵州': '520000', '贵州省': '520000',
        '云南': '530000', '云南省': '530000',
        '西藏': '540000', '西藏自治区': '540000',
        '陕西': '610000', '陕西省': '610000',
        '甘肃': '620000', '甘肃省': '620000',
        '青海': '630000', '青海省': '630000',
        '宁夏': '640000', '宁夏回族自治区': '640000',
        '新疆': '650000', '新疆维吾尔自治区': '650000',
        '台湾': '710000', '台湾省': '710000',
        '香港': '810000', '香港特别行政区': '810000',
        '澳门': '820000', '澳门特别行政区': '820000',
    }

    def _target_provinces(self):
        """从生效规则 region 映射到省级行政区代码列表。

        用于列表级按地区过滤，减少翻页；无规则或无地区时返回 ['']（不过滤）。
        """
        codes = []
        for rule in FilterRule.objects.filter(is_active=True):
            for r in rule.region_list():
                code = self.PROVINCE_CODES.get(r)
                if code and code not in codes:
                    codes.append(code)
        return codes or ['']

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

    async def _fetch_list(self, context, cat_id, page_no, province=''):
        payload = {
            'pageSize': str(self.PAGE_SIZE),
            'pageNo': page_no,
            'dto': {
                'siteId': self.SITE_ID, 'categoryId': cat_id,
                'province': province, 'city': '', 'county': '',
                'purchaseMode': '', 'secondCompanyId': '',
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
                print(f'[zhongxinjinkong] list error page {page_no}: {e}', file=sys.stderr)
                continue
        return []

    async def _crawl_column(self, context, cat_id, url_seg, label, items, seen):
        for province in self._target_provinces():
            await self._crawl_province(context, cat_id, url_seg, label, province,
                                       items, seen)

    async def _crawl_province(self, context, cat_id, url_seg, label, province,
                              items, seen):
        page_no = 1
        has_rules = FilterRule.objects.filter(is_active=True).exists()
        cutoff = None if has_rules else self._site_cutoff()
        while page_no <= self.max_pages:
            rows = await self._fetch_list(context, cat_id, page_no, province)
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
                # 列表直接给出地区，用标题+地区做预过滤（正文精确地区留待入库）
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
            region = (row.get('provinceName') or '').strip() or self._region(agent, title)
            return {
                'title': title,
                'source_url': self.WEBFILE + rel,
                'source_unique_id': uid,
                'project_no': '',
                'publish_date': parse_date(row.get('publishDate') or ''),
                'purchaser': '',
                'region': region,
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
                    'province': row.get('province') or '',
                    'city_name': row.get('cityName') or '',
                    'publish_time': row.get('publishDate') or '',
                    'quote_begin': row.get('quoteBeginTime') or '',
                    'quote_end': row.get('quoteEndTime') or '',
                },
            }
        except Exception as e:
            print(f'[zhongxinjinkong] item parse error: {e}', file=sys.stderr)
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

    # 省级行政区全称（含「市/省/自治区」），用于列表 provinceName 缺失时兜底。
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

    def _region(self, agent, title):
        text = f'{agent or ""} {title or ""}'
        for r in self.CHINA_REGIONS:
            if r in text:
                return r
        # 分支名优先（更干净），标题兜底
        for src in (agent, title):
            if not src:
                continue
            clean = re.sub(r'中信银行股份有限公司|中信银行|股份有限公司|有限公司', '', src)
            m = re.search(r'([一-龥]{2,6}?)(?:分行|支行)', clean)
            if m and m.group(1):
                return m.group(1)
        return ''

    async def _enrich_details(self, context, items):
        await self.enrich_http(context, items)

    async def _enrich_one(self, context, item):
        url = item.get('source_url')
        if not url:
            return
        try:
            await asyncio.sleep(self.request_delay)
            resp = await context.request.get(url, headers={'User-Agent': self.USER_AGENT})
            if resp.status != 200:
                return
            html = await resp.text()
            soup = BeautifulSoup(html, 'lxml')
            el = (soup.select_one('.text-part-text')
                  or soup.select_one('.cfcpn-news-content')
                  or soup.body)
            body = el.get_text(' ', strip=True).replace('\xa0', ' ')
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
            print(f'[zhongxinjinkong] detail {url}: {e}', file=sys.stderr)

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
        """标题常以「中信银行[股份有限公司]XX分行/支行」开头。"""
        t = title or ''
        m = re.search(r'(中信银行(?:股份有限公司)?[一-龥]{1,8}?)(分行|支行)', t)
        if m:
            return m.group(1) + m.group(2)
        m2 = re.search(r'中信银行(?:股份有限公司)?', t)
        if m2:
            return m2.group(0)
        return ''

    def _extract_amount(self, text):
        pat = (
            r'(?:最高含税投标限价|最高投标限价|最高限价|招标控制价|项目预算|合同估算价|'
            r'采购预算|预算金额|预算额|估算|预算)'
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
        text = text or ''
        # 联系信息小节标题可能为「联系方式」或「招标人及招标代理机构相关情况」，
        # 且可能不带冒号（get_text 以空格拼接），取其后整段，截断到后续小节。
        for kw in ('联系方式', '联系信息', '招标人及招标代理机构相关情况',
                   '招标代理机构相关情况'):
            m = re.search(kw + r'\s*[：:]?\s*(.+?)(?:公告发布媒介|发布媒介|招标公告附件|我要参与|$)',
                          text)
            if m:
                return m.group(1).strip()
        # 兜底：部分公告无「联系方式」标题，联系信息以「招标人：…联系人…联系电话…」
        # 平铺在文末，取最后一个「招标人」之后到「我要参与/招标公告附件」为止的整段。
        idx = text.rfind('招标人')
        if idx >= 0:
            m = re.match(r'招标人.*?(?:我要参与|招标公告附件|$)', text[idx:])
            if m and re.search(r'联系人|联系电话|电子邮箱|邮箱', m.group(0)):
                return m.group(0).strip()
        return ''
