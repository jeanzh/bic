"""全国公共资源交易平台 crawler (www.ggzy.gov.cn/deal/dealList.html).

栏目「交易公开」为 Vue SPA，列表走站内 JSON 接口
POST /information/pubTradingInfo/getTradList，匿名可访问、无 WAF。
两个数据来源：SOURCE_TYPE=1 省平台、SOURCE_TYPE=2 央企招投标。
关键字通过 FINDTXT 参数过滤（取自启用 FilterRule 的「包含关键词」并集），
无关键字时爬全栏目。列表固定每页 20 条，最多 50 页（total 上限 1000）。

正文页为服务端渲染：列表 url 形如 /information/deal/html/a/...，正文位于
同路径把 a 换成 b 的页面，可直接 GET 抓取（含标段编号/中标人/中标价等）。

接口对同 IP 有频控（code 800）与图片验证码（code 829），请求间 sleep
request_delay 秒，遇 800 退避 retry_delay 重试，遇 829 无法自动过验证码则停止。
"""
import asyncio
import re
import sys

from bs4 import BeautifulSoup

from bidding.models import BiddingInfo, FilterRule
from crawlers.base import (
    BaseSiteCrawler, parse_amount, parse_date, parse_project_no,
    parse_contact_info,
)


class GgzyCrawler(BaseSiteCrawler):
    code = 'ggzy'
    name = '全国公共资源交易平台'
    url = 'https://www.ggzy.gov.cn/deal/dealList.html'

    BASE = 'https://www.ggzy.gov.cn'
    API_URL = BASE + '/information/pubTradingInfo/getTradList'
    REFERER = url

    # 数据来源：1 省平台 / 2 央企招投标
    SOURCE_TYPES = [('1', '省平台'), ('2', '央企招投标')]

    # 发布时间窗口：01 当天 / 02 近三天 / 03 近十天 / 04 近一月 / 05 近三月 / 06 区间
    DEAL_TIME = '05'

    fetch_detail_enabled = True
    max_detail_fetch = 20

    request_delay = 2.0    # 正常请求间隔（秒），过快会触发频控/验证码
    retry_delay = 30.0     # 频控（code 800）后退避时长
    max_retries = 3

    USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36')

    async def crawl(self, context):
        items = []
        seen = {}
        keywords = self._collect_keywords()
        for source_type, _label in self.SOURCE_TYPES:
            if keywords:
                for kw in keywords:
                    await self._crawl_source(context, source_type, kw, items, seen)
            else:
                await self._crawl_source(context, source_type, '', items, seen)
        await self._enrich_details(context, items)
        return items

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

    async def _crawl_source(self, context, source_type, keyword, items, seen):
        page_no = 1
        cutoff = self._site_cutoff()
        while page_no <= self.max_pages:
            data = await self._post(context, {
                'SOURCE_TYPE': source_type,
                'PAGENUMBER': str(page_no),
                'DEAL_TIME': self.DEAL_TIME,
                'FINDTXT': keyword,
            })
            if data.get('code') != 200:
                break
            payload = data.get('data') or {}
            records = payload.get('records') or []
            if not records:
                break
            total_pages = payload.get('pages') or self.max_pages

            reached_cutoff = False
            for rec in records:
                item = self._parse_record(rec, source_type)
                if not item:
                    continue
                uid = item['source_unique_id']
                if uid in seen:
                    continue
                seen[uid] = True
                # 列表按发布时间倒序，遇早于本站截止日期的条目即停止翻页
                if (cutoff and item.get('publish_date')
                        and item['publish_date'] < cutoff):
                    reached_cutoff = True
                    break
                items.append(item)

            if reached_cutoff or page_no >= total_pages:
                break
            page_no += 1

    async def _post(self, context, params):
        headers = {
            'User-Agent': self.USER_AGENT,
            'Referer': self.REFERER,
            'X-Requested-With': 'XMLHttpRequest',
        }
        for attempt in range(self.max_retries + 1):
            if attempt:
                await asyncio.sleep(self.retry_delay)
            try:
                await asyncio.sleep(self.request_delay)
                resp = await context.request.post(
                    self.API_URL, form=params, headers=headers,
                )
                if resp.status != 200:
                    continue
                data = await resp.json()
                code = data.get('code')
                if code == 200:
                    return data
                if code == 800:  # 频控，退避重试
                    continue
                if code == 829:  # 需要图片验证码，无法自动处理
                    print('[ggzy] captcha required, stopping', file=sys.stderr)
                    return {'code': 829, 'data': {}}
                return {'code': code, 'data': {}}
            except Exception as e:
                print(f'[ggzy] request error: {e}', file=sys.stderr)
                continue
        return {'code': -1, 'data': {}}

    def _parse_record(self, rec, source_type):
        try:
            rid = rec.get('id') or ''
            title = (rec.get('title') or '').strip()
            if not rid or not title:
                return None
            url_path = rec.get('url') or ''
            return {
                'title': title,
                'source_url': (self.BASE + url_path) if url_path else '',
                'source_unique_id': rid,
                'project_no': '',
                'publish_date': parse_date(rec.get('publishTime') or ''),
                'purchaser': '',
                'region': (rec.get('provinceText') or '').strip(),
                'industry': (rec.get('industryTypeText') or '').strip(),
                'budget_amount': None,
                'bid_amount': None,
                'deadline': None,
                'content': '',
                'contact_info': '',
                'attachment_url': '',
                'status': 'open',
                'raw_data': {
                    'source_type': source_type,
                    'business_type': rec.get('businessTypeText') or '',
                    'info_type': rec.get('informationTypeText') or '',
                    'source_platform': rec.get('transactionSourcesPlatformText') or '',
                },
            }
        except Exception as e:
            print(f'[ggzy] item parse error: {e}', file=sys.stderr)
            return None

    async def _enrich_details(self, context, items):
        if not self.fetch_detail_enabled or not items:
            return
        for item in items[:self.max_detail_fetch]:
            await self._enrich_one(context, item)

    async def _enrich_one(self, context, item):
        url = item.get('source_url')
        if not url:
            return
        content_url = url.replace('/html/a/', '/html/b/')
        try:
            await asyncio.sleep(self.request_delay)
            resp = await context.request.get(
                content_url,
                headers={'User-Agent': self.USER_AGENT, 'Referer': self.REFERER},
            )
            if resp.status != 200:
                return
            html = await resp.text()
            if not html:
                return
            soup = BeautifulSoup(html, 'lxml')
            body = soup.get_text(' ', strip=True)
            if not body:
                return
            item['content'] = body
            if not item.get('project_no'):
                item['project_no'] = self._extract_project_no(soup, body)
            if not item.get('purchaser'):
                item['purchaser'] = self._extract_purchaser(soup, body)
            if item.get('budget_amount') is None:
                item['budget_amount'] = self._extract_amount(body, (
                    r'(?:预算金额|采购包预算额|预算额|最高限价)'
                    r'[：:]\s*([^\s，。；;）)]{1,30})',
                ))
            if item.get('bid_amount') is None:
                item['bid_amount'] = self._extract_bid_amount(soup, body)
            if not item.get('contact_info'):
                item['contact_info'] = parse_contact_info(
                    soup.get_text('\n', strip=True))
        except Exception as e:
            print(f'[ggzy] detail {url}: {e}', file=sys.stderr)

    def _extract_project_no(self, soup, text):
        # 中标/结果公告模板：<span id="bdBH"> 为标段编号
        el = soup.select_one('span#bdBH')
        if el:
            v = el.get_text(strip=True)
            if v:
                return v
        # 叙事模板：编号标签后允许中文前缀（如「项目编号：（县区）2026TPDL186 号」）
        m = re.search(
            r'(?:项目编号|标段编号|招标编号|采购编号|交易编号|编号)'
            r'[：:]\s*[^A-Za-z0-9]{0,20}([A-Za-z0-9][A-Za-z0-9_\-/（）()]{2,40})',
            text or '',
        )
        if m:
            return m.group(1)
        return parse_project_no(text)

    def _extract_purchaser(self, soup, text):
        # 中标/结果公告模板：<span id="tbrName"> 为中标人/招标人
        el = soup.select_one('span#tbrName')
        if el:
            v = el.get_text(strip=True)
            if v:
                return v
        for pat in (
            r'(?:招标人信息|采购人信息|中标人信息)'
            r'[^。]{0,60}?名\s*称[：:]\s*([^\s，,。；;）)]{2,40})',
            r'(?:招标人|采购人|中标人)[：:]\s*([^\s，,。；;）)]{2,40})',
        ):
            m = re.search(pat, text or '')
            if m:
                return m.group(1).strip()
        return ''

    def _extract_bid_amount(self, soup, text):
        # 中标/结果公告模板：<span id="zhongBiaoJE"> 为中标价
        el = soup.select_one('span#zhongBiaoJE')
        if el:
            amt = parse_amount(el.get_text(strip=True))
            if amt is not None:
                return amt
        return self._extract_amount(text, (
            r'中标价[（(]?[^：:]{0,10}[)）]?[：:]\s*([^\s，。；;）)]{1,30})',
            r'(?:中标金额|成交金额|成交价)[：:]\s*([^\s，。；;）)]{1,30})',
        ))

    def _extract_amount(self, text, patterns):
        for pat in patterns:
            m = re.search(pat, text or '')
            if m:
                amt = parse_amount(m.group(1))
                if amt is not None:
                    return amt
        return None
