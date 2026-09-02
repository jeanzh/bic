"""中银智采 crawler (ctpch.fmscop.bankofchina.com) — 中国银行集中采购平台.

站点为 Vue SPA，数据走统一网关 JSON 接口，匿名可访问、无需登录：
- 列表：POST /pcm/c-pcm-web/C08411SUP000/v1/SupplierEnroll/client/getNoticeByType
  请求体 {orgType, pageSize, pageNo, ancmAncDt, ancmHdlnCntnt, noticeType,
          areaCode, ts, purchaseType}
  返回 respBody.{totalRecord, totalPage, data[{pkNotice, ancmHdlnCntnt,
          ancmAncDt, noticeType}]}
- 正文：POST .../getNoticeDetail
  请求体 {noticeType, pkNotice, purchaseType, page}
  返回 respBody.{ancmHdlnCntnt, ancmCntnt(HTML), ancmAncDt, areaName,
          ancmAttached}

两个栏目（purchaseType）：1 集中采购专区 / 2 分散采购专区。
每个栏目下按机构层级（orgType）：1 总行 / 2 分行。
公告类型（noticeType）：1 采购公告 / 2 结果公示 / 3 结果公告；本项目采集
「采购公告」（对应招标公告/采购邀请公告）。

网关请求体需带 reqHeader（globalSerNo/requestTime/apiCode 等）；服务器仅用
URL 路由，reqHeader 中 globalSerNo/requestTime 按时间戳生成即可。

注意：网关服务器使用 legacy SSL renegotiation，Node 的 fetch（context.request）
会被 OpenSSL 拒绝（unsafe legacy renegotiation disabled），必须走浏览器网络栈，
故用 page.evaluate 在页面内 fetch 取数。

列表不返回地区，地区须从标题的分支机构名（如「…河南省分行…」）或正文
「地址：」/areaName 推断，故列表级用 prefilter_item 预过滤，入库时
apply_filters 再用正文精确地区兜底。
"""
import asyncio
import random
import re
import sys
import time
from datetime import datetime

from bs4 import BeautifulSoup
from django.utils import timezone as tz

from bidding.models import BiddingInfo, FilterRule
from bidding.services import prefilter_item
from crawlers.base import (
    BaseSiteCrawler, parse_amount, parse_date, parse_project_no,
)


class ZhongyinCrawler(BaseSiteCrawler):
    code = 'zhongyin'
    name = '中银智采'
    url = 'https://ctpch.fmscop.bankofchina.com/pcm/#/first-page/purchase1'

    BASE = 'https://ctpch.fmscop.bankofchina.com'
    LIST_API = (BASE + '/pcm/c-pcm-web/C08411SUP000/v1/SupplierEnroll/client/'
                'getNoticeByType')
    DETAIL_API = (BASE + '/pcm/c-pcm-web/C08411SUP000/v1/SupplierEnroll/client/'
                  'getNoticeDetail')
    LIST_API_CODE = '/SupplierEnroll/client/getNoticeByType'
    DETAIL_API_CODE = '/SupplierEnroll/client/getNoticeDetail'

    # 栏目：purchaseType -> 名称
    COLUMNS = [
        ('1', '集中采购专区'),
        ('2', '分散采购专区'),
    ]
    # 机构层级：orgType -> 名称
    ORG_TYPES = [
        ('1', '总行'),
        ('2', '分行'),
    ]
    NOTICE_TYPE = '1'   # 采购公告（招标公告/采购邀请公告）

    fetch_detail_enabled = True
    max_detail_fetch = 3000  # 正文为一次 JSON POST；page.evaluate 无法并发，取最新 3000 条兜底

    request_delay = 0.5
    retry_delay = 5.0
    max_retries = 3
    PAGE_SIZE = 100         # 列表每页条数（站点下拉上限 100）
    max_pages = 200         # 分行公告量大，需翻页找全命中地区条目

    USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36')

    async def crawl(self, context):
        # 网关需浏览器网络栈（legacy renegotiation），不能用 context.request。
        page = await context.new_page()
        try:
            await page.goto(self.url, wait_until='domcontentloaded', timeout=45000)
            items = []
            seen = {}
            for pt, pt_label in self.COLUMNS:
                for ot, ot_label in self.ORG_TYPES:
                    await self._crawl_combo(page, pt, pt_label, ot, ot_label,
                                            items, seen)
            await self._enrich_details(page, items)
            return items
        finally:
            await page.close()

    def _header(self, api_code):
        """构造网关 reqHeader（globalSerNo/requestTime 按时间戳生成即可）。"""
        now = datetime.now()
        global_ser_no = ('C084110647U5A' + now.strftime('%Y%m%d%H%M%S')
                         + f'{random.randint(0, 9999999):07d}')
        request_time = now.strftime('%Y%m%d %H:%M:%S.') + f'{now.microsecond // 1000:03d}'
        return {
            'formatVer': '01',
            'globalSerNo': global_ser_no,
            'txnSerNo': '10000000000000000000',
            'requestTime': request_time,
            'callCode': 'A084110647U5A',
            'channelCode': '000100',
            'entityCode': '003',
            'targetSerCode': 'C08411GWG100',
            'apiCode': api_code,
        }

    def _site_cutoff(self):
        """本站已入库数据的最新发布日期。"""
        latest = (BiddingInfo.objects
                  .filter(website__code=self.code, publish_date__isnull=False)
                  .order_by('-publish_date')
                  .values_list('publish_date', flat=True)
                  .first())
        return latest.isoformat() if latest else None

    async def _post(self, page, url, payload):
        """在页面上下文内 POST（走浏览器网络栈，绕开 legacy renegotiation）。"""
        js = """async (a) => {
            const r = await fetch(a.url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(a.payload),
            });
            if (!r.ok) return null;
            return await r.json();
        }"""
        for attempt in range(self.max_retries + 1):
            if attempt:
                await asyncio.sleep(self.retry_delay)
            try:
                await asyncio.sleep(self.request_delay)
                return await page.evaluate(js, {'url': url, 'payload': payload})
            except Exception as e:
                print(f'[zhongyin] post error {url}: {e}', file=sys.stderr)
        return None

    async def _crawl_combo(self, page, pt, pt_label, ot, ot_label, items, seen):
        page_no = 1
        has_rules = FilterRule.objects.filter(is_active=True).exists()
        cutoff = None if has_rules else self._site_cutoff()
        while page_no <= self.max_pages:
            data = await self._post(page, self.LIST_API, {
                'reqBody': {
                    'orgType': ot,
                    'pageSize': self.PAGE_SIZE,
                    'pageNo': page_no,
                    'ancmAncDt': '',
                    'ancmHdlnCntnt': '',
                    'noticeType': self.NOTICE_TYPE,
                    'areaCode': '',
                    'ts': '',
                    'purchaseType': pt,
                },
                'reqHeader': self._header(self.LIST_API_CODE),
            })
            resp = (data or {}).get('respBody') or {}
            rows = resp.get('data') or []
            if not rows:
                break
            reached_cutoff = False
            for row in rows:
                item = self._parse_row(row, pt, pt_label, ot, ot_label)
                if not item:
                    continue
                uid = item['source_unique_id']
                if uid in seen:
                    continue
                seen[uid] = True
                # 列表无地区字段，用标题推断地区做预过滤（正文精确地区留待入库）
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

    def _parse_row(self, row, pt, pt_label, ot, ot_label):
        try:
            pk = row.get('pkNotice') or ''
            title = (row.get('ancmHdlnCntnt') or '').strip()
            if not pk or not title:
                return None
            return {
                'title': title,
                'source_url': (f'{self.BASE}/pcm/#/first-page/detail'
                               f'?noticeType={self.NOTICE_TYPE}&pkNotice={pk}'
                               f'&purchaseType={pt}&page=1'),
                'source_unique_id': pk,
                'project_no': '',
                'publish_date': parse_date(row.get('ancmAncDt') or ''),
                'purchaser': '',
                'region': self._region_from_title(title),
                'industry': '',
                'budget_amount': None,
                'bid_amount': None,
                'deadline': None,
                'content': '',
                'contact_info': '',
                'attachment_url': '',
                'status': 'open',
                'raw_data': {
                    'notice_type': self.NOTICE_TYPE,
                    'purchase_type': pt,
                    'column': pt_label,
                    'org_type': ot,
                    'org_label': ot_label,
                    'publish_time': row.get('ancmAncDt') or '',
                },
            }
        except Exception as e:
            print(f'[zhongyin] item parse error: {e}', file=sys.stderr)
            return None

    # 省级行政区全称（含「市/省/自治区」），用于从标题精确取地区，
    # 避免贪婪匹配把「股份有限公司」中的「公司」误并入地区名。
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

    def _region_from_title(self, title):
        """从标题分支机构名推断省/市，如「…河南省分行…」→ 河南省。"""
        t = title or ''
        for r in self.CHINA_REGIONS:
            if r in t:
                return r
        # 分支名可能省略「省/市」，如「中国银行…北京分行」→ 北京
        m = re.search(r'中国银行(?:股份有限公司)?([一-龥]{1,6}?)(?:分行|支行)', t)
        if m:
            return m.group(1)
        return ''

    async def _enrich_details(self, page, items):
        if not self.fetch_detail_enabled or not items:
            return
        for item in items[:self.max_detail_fetch]:
            await self._enrich_one(page, item)

    async def _enrich_one(self, page, item):
        pk = item.get('source_unique_id')
        pt = (item.get('raw_data') or {}).get('purchase_type') or '1'
        if not pk:
            return
        try:
            data = await self._post(page, self.DETAIL_API, {
                'reqBody': {
                    'noticeType': self.NOTICE_TYPE,
                    'pkNotice': pk,
                    'purchaseType': pt,
                    'page': '1',
                },
                'reqHeader': self._header(self.DETAIL_API_CODE),
            })
            resp = (data or {}).get('respBody') or {}
            html = resp.get('ancmCntnt') or ''
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
            if item.get('deadline') is None:
                item['deadline'] = self._extract_deadline(body)
            if not item.get('contact_info'):
                item['contact_info'] = self._extract_contact(body)
            # 正文 areaName /「地址：」比标题推断更精确，覆盖地区
            region = (resp.get('areaName') or '').strip()
            if not region:
                region = self._region_from_content(body)
            if region:
                item['region'] = region
        except Exception as e:
            print(f'[zhongyin] detail {pk}: {e}', file=sys.stderr)

    def _region_from_content(self, text):
        # 正文可能出现「网站地址：http://…」等 URL，跳过，取首个真实地址。
        for m in re.finditer(r'地\s*址\s*[：:]\s*([^\s，,。；;）)]{2,40})', text or ''):
            addr = m.group(1).strip()
            if addr.startswith(('http', 'www', 'HTTP', 'WWW')):
                continue
            m2 = re.match(r'([一-龥]{1,4}(?:省|自治区|特别行政区|市))', addr)
            if m2:
                return m2.group(1)
            m3 = re.match(r'[一-龥]{1,6}', addr)
            if m3:
                return m3.group(0)
        return ''

    def _extract_purchaser(self, text):
        # 常见形式：「招标人：XX」「采购人：XX」或「XX（采购人）」
        for pat in (
            r'(?:招标人名称|招标人|采购人)\s*[：:]\s*([^\s，,。；;）)]{2,60})',
            r'([^\s，,。；;）)]{2,60}?)\s*[（(]\s*(?:采购人|招标人)\s*[）)]',
        ):
            m = re.search(pat, text or '')
            if m:
                return m.group(1).strip()
        return ''

    def _purchaser_from_title(self, title):
        """本站采购人即中国银行及其分支行，标题即含机构名。"""
        t = title or ''
        m = re.search(r'(中国银行(?:股份有限公司)?[一-龥]{1,8}?)(分行|支行)', t)
        if m:
            return m.group(1) + m.group(2)
        m2 = re.search(r'中国银行(?:股份有限公司)?', t)
        if m2:
            return m2.group(0)
        return ''

    def _extract_amount(self, text):
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
            r'(?:投标截止时间|投标文件递交截止时间|报名截止时间|递交截止时间)'
            r'[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})时(\d{1,2})分',
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
        m = re.search(r'(?:联系方式|联系信息)[：:]\s*(.+)', text or '')
        if m:
            return m.group(1).strip()
        return ''
