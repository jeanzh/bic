"""中国金融集中采购网 crawler (www.cfcpn.com / 金采网).

栏目「采购公告」(column=cggg) 走站内 JSON 接口
POST /jcw/noticeinfo/noticeInfo/dataNoticeList，匿名可访问。
列表接口即返回标题/地区/采购人/品类/采购方式；正文与项目编号需再以
{id, isDetail:1} 调同一接口获取（noticeContent 为完整正文，briefContent 为摘要）。

接口对同一 IP 有较严频控（约每分钟 5 次），超出返回 403
「访问过于频繁，请稍后再试」。且该接口只认浏览器网络栈——Playwright 的
context.request（独立 HTTP 栈）会被 WAF 拦截（同样返回 403），故必须先在
页面内 goto 建立浏览器会话，再用 page.evaluate(fetch) 取数（同源 Cookie +
浏览器指纹），与中银智采/农银e采一致。故请求间 sleep request_delay 秒，
遇 403 退避 retry_delay 秒重试。
"""
import asyncio
import html as _html
import json
import sys
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from bidding.models import BiddingInfo
from crawlers.base import BaseSiteCrawler, parse_date, parse_project_no, parse_contact_info


class CfinCrawler(BaseSiteCrawler):
    code = 'cfin'
    name = '中国金融集中采购网'
    url = 'http://www.cfcpn.com/jcw/sys/index/goUrl?url=modules/sys/login/list&column=cggg'

    BASE = 'http://www.cfcpn.com'
    API_URL = BASE + '/jcw/noticeinfo/noticeInfo/dataNoticeList'
    REFERER = url
    PAGE_SIZE = 100
    max_detail_fetch = 300  # 频控严格，仅对最新 300 条抓正文兜底关键词匹配
    request_delay = 15.0   # 站点约每分钟 5 次，放慢到 4 次/分左右留余量
    retry_delay = 120.0    # 403 频控后冷却时长（秒）
    max_retries = 2

    # 栏目 code -> 接口 noticeType 值
    # （其余栏目：zjgg=2 征集公告 / gzgg=3 更正公告 / jggg=4 结果公告）
    COLUMNS = [
        ('cggg', '1', '采购公告'),
    ]

    async def crawl(self, context):
        # 接口只认浏览器网络栈，故先开页 goto 建立浏览器会话，再在页面内 fetch 取数。
        page = await context.new_page()
        try:
            await page.goto(self.url, wait_until='domcontentloaded', timeout=45000)
            items = []
            cutoff = self._site_cutoff()
            for _col, notice_type, _label in self.COLUMNS:
                await self._crawl_column(page, notice_type, cutoff, items)
            await self._enrich_details(page, items)
            return items
        finally:
            await page.close()

    def _site_cutoff(self):
        latest = (BiddingInfo.objects
                  .filter(website__code=self.code, publish_date__isnull=False)
                  .order_by('-publish_date')
                  .values_list('publish_date', flat=True)
                  .first())
        return latest.isoformat() if latest else None

    async def _crawl_column(self, page, notice_type, cutoff, items):
        page_no = 1
        while page_no <= self.max_pages:
            rows = await self._list_page(page, notice_type, page_no)
            if not rows:
                break
            reached_cutoff = False
            for row in rows:
                item = self._parse_row(row, notice_type)
                if not item:
                    continue
                # 列表按发布时间倒序，遇到早于本站截止日期的条目即停止翻页
                if (cutoff and item.get('publish_date')
                        and item['publish_date'] < cutoff):
                    reached_cutoff = True
                    break
                items.append(item)
            if reached_cutoff or len(rows) < self.PAGE_SIZE:
                break
            page_no += 1

    async def _list_page(self, page, notice_type, page_no):
        data = await self._post(page, {
            'noticeType': notice_type,
            'pageSize': str(self.PAGE_SIZE),
            'pageNo': str(page_no),
            'noticeState': '1',
            'isValid': '1',
            'orderBy': 'publish_time desc',
        })
        return data.get('rows') or []

    async def _post(self, page, params):
        """在页面内 fetch POST 取数（浏览器网络栈，绕开 WAF 对独立 HTTP 栈的拦截）。

        站点对同一 IP 有严格频控（约每分钟 5 次），超出返回 403
        「访问过于频繁，请稍后再试」。以 request_delay 控速，遇 403/网络错误
        退避重试；连续失败抛 RuntimeError 让本轮运行以明确原因结束，而非空转。
        """
        js = """async (a) => {
            const r = await fetch(a.url, {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded',
                          'X-Requested-With': 'XMLHttpRequest'},
                body: a.body,
            });
            return {status: r.status, text: await r.text()};
        }"""
        body = urlencode(params)
        for attempt in range(self.max_retries + 1):
            if attempt:
                await asyncio.sleep(self.retry_delay)
            try:
                await asyncio.sleep(self.request_delay)
                res = await page.evaluate(js, {'url': self.API_URL, 'body': body})
                if res and res.get('status') == 200:
                    return json.loads(res['text'])
            except Exception as e:
                print(f'[cfin] request error: {e}', file=sys.stderr)
        raise RuntimeError(
            '金采网接口持续请求失败（频控 403「访问过于频繁」），请稍后（数分钟）再试'
        )

    def _parse_row(self, row, notice_type):
        try:
            rid = row.get('id') or ''
            title = (row.get('noticeTitle') or '').strip()
            if not rid or not title:
                return None

            is_org = row.get('isOrg')
            purchaser = (row.get('userName') or '').strip()
            if not purchaser:
                purchaser = '代理机构发布' if is_org == '1' else '金融机构发布'

            detail_url = (f'{self.BASE}/jcw/sys/index/goUrl'
                          f'?url=modules/sys/login/detail'
                          f'&column={notice_type}&searchVal={rid}')

            return {
                'title': title,
                'source_url': detail_url,
                'source_unique_id': rid,
                'project_no': '',
                'publish_date': parse_date(row.get('publishTime') or ''),
                'purchaser': purchaser,
                'region': (row.get('area') or '').strip(),
                'industry': (row.get('labelAllId') or '').strip(),
                'budget_amount': None,
                'bid_amount': None,
                'deadline': None,
                'content': '',
                'contact_info': '',
                'attachment_url': '',
                'status': 'open',
                'raw_data': {
                    'purchase_type': row.get('purchaseTypeName') or '',
                    'tags': row.get('yxCategoryNames') or '',
                    'notice_source': row.get('noticeSource') or '',
                    'notice_type': notice_type,
                },
            }
        except Exception as e:
            print(f'[cfin] item parse error: {e}', file=sys.stderr)
            return None

    async def _enrich_details(self, page, items):
        if not self.fetch_detail_enabled or not items:
            return
        for item in items[:self.max_detail_fetch]:
            try:
                await self._enrich_one(page, item)
            except RuntimeError as e:
                # 频控导致接口持续失败：中止本轮正文抓取，避免长时间空转
                print(f'[cfin] enrich aborted: {e}', file=sys.stderr)
                break

    async def _enrich_one(self, page, item):
        rid = item.get('source_unique_id')
        if not rid:
            return
        # 频控/网络异常从 _post 向上传播，交给 _enrich_details 中止整轮
        data = await self._post(page, {'id': rid, 'isDetail': '1'})
        try:
            rows = data.get('rows') or []
            if not rows:
                return
            row = rows[0]
            raw = row.get('noticeContent') or row.get('briefContent') or ''
            text = _html_to_text(_html.unescape(raw))
            if text:
                item['content'] = text
                if not item.get('project_no'):
                    item['project_no'] = parse_project_no(text)
                if not item.get('contact_info'):
                    item['contact_info'] = parse_contact_info(text)
            if not item.get('attachment_url'):
                item['attachment_url'] = self._extract_attachment(row)
        except Exception as e:
            print(f'[cfin] detail {rid}: {e}', file=sys.stderr)

    def _extract_attachment(self, row):
        file_raw = row.get('file')
        if not file_raw:
            return ''
        try:
            files = json.loads(file_raw)
            if isinstance(files, list) and files:
                fp = files[0].get('filePath') or files[0].get('url') or ''
                return fp if isinstance(fp, str) else ''
        except Exception:
            return ''
        return ''


def _html_to_text(html_str):
    try:
        soup = BeautifulSoup(html_str, 'lxml')
        return soup.get_text('\n').strip()
    except Exception:
        return html_str
