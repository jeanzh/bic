"""农银e采 crawler (jc.abchina.com.cn) — 中国农业银行集中采购平台.

站点为 Vue SPA（Element UI），数据走统一网关 JSON 接口，匿名可访问、无需登录：
- 列表：POST /gateway/puc/portalMessage/queryInviteMessageForEie
  请求体 {pageInfo:{current,size}, beginTime, endTime, cOidOrgunit, messageType, publishColumn}
  返回 value.records[{messageId, messageTitle, startTime, placeTop, messageType, projectType}]
- 正文：POST /gateway/puc/portalMessage/queryPortalMessageDetailForEie
  请求体 {messageId}，返回 value.{messageTitle, startTime, content(HTML), attachFileList, contentAttachList}
- 详情页：#/noticeDetail?messageId={messageId}

招标专区（publishColumn=2）含子栏目（messageType）：3 招标(资审)公告 /
4 变更公告 / 5 中标结果公示；本项目按需采集「招标(资审)公告」。
projectType：1 服务 / 2 货物 / 3 工程。

注意：网关服务器使用 legacy SSL renegotiation，Node 的 fetch（context.request）
会被 OpenSSL 拒绝（unsafe legacy renegotiation disabled），必须走浏览器网络栈，
故用 page.evaluate 在页面内 fetch 取数。

站内搜索框为「公告名称」包含匹配，无服务端关键字参数；列表也不返回地区，
地区须从正文「地址：」或标题的分支机构名推断，故列表级用 prefilter_item
（标题关键词 + 标题推断地区）预过滤，入库时 apply_filters 再用正文精确地区兜底。
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


class NongyinecaiCrawler(BaseSiteCrawler):
    code = 'nongyinecai'
    name = '农银e采'
    url = 'https://jc.abchina.com.cn/puc/#/biddingPage'

    BASE = 'https://jc.abchina.com.cn'
    LIST_API = BASE + '/gateway/puc/portalMessage/queryInviteMessageForEie'
    DETAIL_API = BASE + '/gateway/puc/portalMessage/queryPortalMessageDetailForEie'

    # 招标专区(publishColumn=2)子栏目：messageType -> 名称
    MESSAGE_TYPES = [('3', '招标(资审)公告')]
    PUBLISH_COLUMN = 2

    PROJECT_TYPES = {'1': '服务', '2': '货物', '3': '工程'}

    fetch_detail_enabled = True
    max_detail_fetch = 2500  # 正文为一次 JSON POST；page.evaluate 无法并发，覆盖全量列表条目

    request_delay = 0.5
    retry_delay = 5.0
    max_retries = 3
    PAGE_SIZE = 50          # 列表每页条数（站点默认 10，接口可放大）

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
            for mt, label in self.MESSAGE_TYPES:
                await self._crawl_type(page, mt, label, items, seen)
            await self._enrich_details(page, items)
            return items
        finally:
            await page.close()

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
                print(f'[nongyinecai] post error {url}: {e}', file=sys.stderr)
        return None

    async def _crawl_type(self, page, mt, label, items, seen):
        page_no = 1
        has_rules = FilterRule.objects.filter(is_active=True).exists()
        cutoff = None if has_rules else self._site_cutoff()
        while page_no <= self.max_pages:
            data = await self._post(page, self.LIST_API, {
                'pageInfo': {'current': page_no, 'size': self.PAGE_SIZE},
                'beginTime': None, 'endTime': None, 'cOidOrgunit': None,
                'messageType': mt, 'publishColumn': self.PUBLISH_COLUMN,
            })
            if not data:
                break
            rows = (data.get('value') or {}).get('records') or []
            if not rows:
                break
            reached_cutoff = False
            for row in rows:
                item = self._parse_row(row, mt, label)
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

    def _parse_row(self, row, mt, label):
        try:
            rid = row.get('messageId') or ''
            title = (row.get('messageTitle') or '').strip()
            if not rid or not title:
                return None
            return {
                'title': title,
                'source_url': f'{self.BASE}/puc/#/noticeDetail?messageId={rid}',
                'source_unique_id': rid,
                'project_no': '',
                'publish_date': parse_date(row.get('startTime') or ''),
                'purchaser': '',
                'region': self._region_from_title(title),
                'industry': self.PROJECT_TYPES.get(str(row.get('projectType')), ''),
                'budget_amount': None,
                'bid_amount': None,
                'deadline': None,
                'content': '',
                'contact_info': '',
                'attachment_url': '',
                'status': 'open',
                'raw_data': {
                    'message_type': mt,
                    'column': label,
                    'project_type': row.get('projectType'),
                    'place_top': row.get('placeTop'),
                    'start_time': row.get('startTime') or '',
                },
            }
        except Exception as e:
            print(f'[nongyinecai] item parse error: {e}', file=sys.stderr)
            return None

    def _region_from_title(self, title):
        """从标题分支机构名推断省/市，如「…北京市分行…」→ 北京市。"""
        for pat in (
            r'([一-龥]{1,4}(?:省|自治区|特别行政区|市|州|盟|区))(?:分行|支行)',
            r'([一-龥]{2,4})(?:分行|支行)',
        ):
            m = re.search(pat, title or '')
            if m:
                return m.group(1)
        return ''

    async def _enrich_details(self, page, items):
        if not self.fetch_detail_enabled or not items:
            return
        for item in items[:self.max_detail_fetch]:
            await self._enrich_one(page, item)

    async def _enrich_one(self, page, item):
        rid = item.get('source_unique_id')
        if not rid:
            return
        try:
            data = await self._post(page, self.DETAIL_API, {'messageId': rid})
            value = (data or {}).get('value') or {}
            html = value.get('content') or ''
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
                item['purchaser'] = self._extract_purchaser(body)
            if item.get('budget_amount') is None:
                item['budget_amount'] = self._extract_amount(body)
            if item.get('deadline') is None:
                item['deadline'] = self._extract_deadline(body)
            if not item.get('contact_info'):
                item['contact_info'] = self._extract_contact(body)
            # 正文「地址：」比标题推断更精确，覆盖地区
            region = self._region_from_content(body)
            if region:
                item['region'] = region
        except Exception as e:
            print(f'[nongyinecai] detail {rid}: {e}', file=sys.stderr)

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
        for pat in (
            r'(?:招标人名称|招标人|采购人)\s*[：:]\s*([^\s，,。；;）)]{2,60})',
        ):
            m = re.search(pat, text or '')
            if m:
                return m.group(1).strip()
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
