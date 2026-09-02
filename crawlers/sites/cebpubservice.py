"""中国招标投标公共服务平台 crawler (bulletin.cebpubservice.com).

栏目「招标公告」列表为服务端渲染，匿名可访问、无 WAF，直接 GET
/xxfbcmses/search/bulletin.html 即可。列表按发布时间倒序，每页 20 条，
共约 500 页；每条已含标题/行业/地区/来源渠道/发布时间/开标时间。

支持关键字检索：`word` 参数（取自启用 FilterRule 的「包含关键词」并集，URL
单次编码即可），无关键字时爬全栏目。行业/地区筛选（industryName/area）保留给
保存阶段的 apply_filters 处理——列表页已带行业与地区，故无需进详情页即可过滤。

详情页位于 ctbpsp.com 的 Vue SPA（#/bulletinDetail），其数据接口
/cutominfoapi/bulletinuuid/{uuid} 受阿里云 WAF 拦截，详情页面还叠加 VAPTCHA
人机验证，无法自动化抓取正文，故 fetch_detail_enabled=False。
"""
import asyncio
import re
import sys
from datetime import datetime, date
from urllib.parse import quote

from bs4 import BeautifulSoup
from django.utils import timezone as tz

from bidding.models import BiddingInfo, FilterRule
from crawlers.base import BaseSiteCrawler, parse_date


class CebpCrawler(BaseSiteCrawler):
    code = 'cebpubservice'
    name = '中国招标投标公共服务平台'
    url = 'https://bulletin.cebpubservice.com/'

    fetch_detail_enabled = False  # 详情页有 VAPTCHA 人机验证，正文不可自动抓取

    BASE = 'https://bulletin.cebpubservice.com'
    LIST_URL = BASE + '/xxfbcmses/search/bulletin.html'
    DETAIL_URL = ('https://ctbpsp.com/#/bulletinDetail'
                  '?uuid={uuid}&inpvalue=&dataSource=0&tenderAgency=')

    # 栏目：88 招标公告 / 89 更正公告公示 / 90 中标结果公示 / 91 中标候选人公示 / 92 资格预审公告
    CATEGORY_ID = '88'
    DATES = '300'  # 与站内默认一致的发布时间范围

    request_delay = 2.0    # 正常请求间隔（秒），过快会触发软拦截
    retry_delay = 30.0     # 软拦截后退避时长（秒）
    max_retries = 3

    USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36')

    async def crawl(self, context):
        items = []
        seen = {}
        cutoff = self._site_cutoff()
        keywords = self._collect_keywords()
        if keywords:
            # 关键字检索结果为相关度排序（非按时间倒序），无法用 cutoff 提前停页，
            # 只做按条跳过，翻页至 max_pages。
            for kw in keywords:
                await self._crawl_list(context, items, seen, word=kw)
        else:
            # 全栏目列表按发布时间倒序，可遇 cutoff 提前停页。
            await self._crawl_list(context, items, seen, cutoff=cutoff)
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

    def _list_url(self, page_no, word=''):
        params = [
            f'searchDate={date.today().isoformat()}',
            f'dates={self.DATES}',
            f'categoryId={self.CATEGORY_ID}',
            'industryName=',
            'area=',
            'status=',
            'publishMedia=',
            'sourceInfo=',
            'showStatus=1',
            f'word={quote(word)}',
            f'page={page_no}',
        ]
        return self.LIST_URL + '?' + '&'.join(params)

    async def _crawl_list(self, context, items, seen, word='', cutoff=None):
        page_no = 1
        while page_no <= self.max_pages:
            url = self._list_url(page_no, word)
            html = await self._fetch(context, url)
            if not html:
                break
            page_items, total_pages = self._parse_page(html)
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
                    # 仅全栏目（按时间倒序）允许遇 cutoff 提前停页；
                    # 关键字检索（相关度排序）只跳过旧条目、继续翻页。
                    if word:
                        continue
                    reached_cutoff = True
                    break
                items.append(item)

            if reached_cutoff or page_no >= total_pages:
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
                    return ''
                text = await resp.text()
                # 服务器偶发返回「页面暂时找不到」软拦截页，退避重试
                if '找不到此页面' in text or '暂时找不到' in text:
                    continue
                return text
            except Exception as e:
                print(f'[cebpubservice] fetch error {url}: {e}', file=sys.stderr)
                continue
        return ''

    def _parse_page(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items = []
        for tr in soup.select('table.table_text tr'):
            item = self._parse_row(tr)
            if item:
                items.append(item)
        return items, self._parse_total_pages(soup)

    def _parse_total_pages(self, soup):
        pagination = soup.select_one('div.pagination')
        if pagination:
            label = pagination.select_one('label')
            if label:
                try:
                    return int(label.get_text(strip=True))
                except ValueError:
                    pass
        return self.max_pages

    def _parse_row(self, tr):
        try:
            a = tr.select_one('a[href*="urlOpen"]')
            if not a:
                return None
            title = a.get('title') or a.get_text(strip=True)
            href = a.get('href') or ''
            mm = re.search(r"urlOpen\('([0-9a-f]{32})'\)", href)
            if not title or not mm:
                return None
            m = mm.group(1)

            tds = tr.select('td')
            industry = ''
            region = ''
            if len(tds) >= 3:
                ind_span = tds[1].select_one('span[title]')
                if ind_span:
                    industry = ind_span.get('title', '').strip()
                reg_span = tds[2].select_one('span[title]')
                if reg_span:
                    region = reg_span.get('title', '').strip()

            source_channel = tds[3].get_text(strip=True) if len(tds) >= 4 else ''
            publish_date = parse_date(tds[4].get_text(strip=True)) if len(tds) >= 5 else None
            deadline = None
            if len(tds) >= 6:
                open_td = tds[5]
                raw_dl = open_td.get('id') or ''
                deadline = self._parse_deadline(raw_dl)

            return {
                'title': title,
                'source_url': self.DETAIL_URL.format(uuid=m),
                'source_unique_id': m,
                'project_no': '',
                'publish_date': publish_date,
                'purchaser': '',
                'region': region,
                'industry': industry,
                'budget_amount': None,
                'bid_amount': None,
                'deadline': deadline,
                'content': '',
                'contact_info': '',
                'attachment_url': '',
                'status': 'open',
                'raw_data': {
                    'source_channel': source_channel,
                    'category_id': self.CATEGORY_ID,
                },
            }
        except Exception as e:
            print(f'[cebpubservice] item parse error: {e}', file=sys.stderr)
            return None

    def _parse_deadline(self, raw):
        try:
            dt = datetime.strptime(raw.strip(), '%Y-%m-%d %H:%M:%S')
            if dt.year < 1900:
                return None
            # 站内时间为北京时间，转为带时区值（Asia/Shanghai）
            return tz.make_aware(dt)
        except (ValueError, AttributeError):
            return None
