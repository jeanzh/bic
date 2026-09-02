"""中国政府采购网 crawler (www.ccgp.gov.cn)."""
import re
import sys
from urllib.parse import urljoin

from bidding.models import BiddingInfo
from bidding.services import prefilter_item
from crawlers.base import BaseSiteCrawler, parse_amount, parse_date, parse_contact_info


class CcgpCrawler(BaseSiteCrawler):
    code = 'ccgp'
    name = '中国政府采购网'
    url = 'https://www.ccgp.gov.cn/cggg/zygg/'
    detail_content_selector = 'div.vF_detail_content'
    max_detail_fetch = 300   # 详情页走 page.goto，成本高，仅覆盖最新条目兜底正文匹配

    LIST_URLS = [
        'https://www.ccgp.gov.cn/cggg/zygg/',
        'https://www.ccgp.gov.cn/cggg/dfgg/',
        'https://www.ccgp.gov.cn/zydwplcg/zy/zyzb/',
        'https://www.ccgp.gov.cn/zydwplcg/zz/zzzb/',
        'https://www.ccgp.gov.cn/xxgg/qtcgxx/index.htm',
    ]

    async def crawl(self, context):
        page = await context.new_page()
        items = []
        try:
            for list_url in self.LIST_URLS:
                # 每栏目独立截止日期；整站共用一个会漏抓更新较慢的栏目
                cutoff = self._section_cutoff(list_url)
                try:
                    page_no = 1
                    section_max = self.max_pages
                    while True:
                        url = list_url if page_no == 1 else urljoin(
                            list_url, f'index_{page_no - 1}.htm')
                        await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                        await page.wait_for_timeout(1500)
                        if page_no == 1:
                            # 以页面 Pager 声明的总页数为上限；zyzb/zzzb 只有 1 页，
                            # 超出后服务器会返回历史归档而非空页，不设上限会一路翻到 2013 年。
                            pager_size = await self._pager_size(page)
                            if pager_size:
                                section_max = min(self.max_pages, pager_size)
                        lis = await page.query_selector_all('ul.c_list_bid li, ul.ulst li')
                        if not lis:
                            break

                        reached_cutoff = False
                        for li in lis:
                            item = await self._parse_li(page, li, url)
                            if not item:
                                continue
                            # 列表按发布时间倒序，遇到早于本栏目截止日期的条目即停止翻页
                            if (cutoff and item.get('publish_date')
                                    and item['publish_date'] < cutoff):
                                reached_cutoff = True
                                break
                            # 预筛：标题关键词 + 区域命中才进详情页
                            if not prefilter_item(item['title'], item['region']):
                                continue
                            items.append(item)

                        if reached_cutoff:
                            break
                        page_no += 1
                        if page_no > section_max:
                            break
                except Exception as e:
                    print(f'[ccgp] list error {list_url}: {e}', file=sys.stderr)
        finally:
            await page.close()
        return await self.enrich_items(context, items)

    def _section_cutoff(self, list_url):
        """本栏目已入库数据的最新发布日期，按 source_url 前缀区分栏目。"""
        prefix = list_url.rsplit('/', 1)[0] + '/'
        latest = (BiddingInfo.objects
                  .filter(website__code=self.code,
                          source_url__startswith=prefix,
                          publish_date__isnull=False)
                  .order_by('-publish_date')
                  .values_list('publish_date', flat=True)
                  .first())
        return latest.isoformat() if latest else None

    async def _pager_size(self, page):
        """读取列表页 Pager({size:N,...}) 声明的总页数；无 Pager 返回 0。"""
        try:
            size = await page.evaluate(
                """() => {
                    for (const s of document.querySelectorAll('script')) {
                        const m = (s.textContent || '').match(/Pager\\(\\{[\\s\\S]*?\\}\\)/);
                        if (m) {
                            const sm = m[0].match(/size\\s*:\\s*(\\d+)/);
                            if (sm) return parseInt(sm[1], 10);
                        }
                    }
                    return 0;
                }"""
            )
            return int(size or 0)
        except Exception:
            return 0

    async def _parse_li(self, page, li, list_url):
        try:
            a = await li.query_selector('a')
            title = (await a.inner_text()).strip() if a else ''
            href = (await a.get_attribute('href')) if a else ''
            if not title or not href:
                return None

            source_url = urljoin(list_url, href)

            ems = await li.query_selector_all('em')
            if ems:
                # c_list_bid 结构：em 顺序 [公告类型, 发布时间, 地区, 采购人]
                em_texts = [(await e.inner_text()).strip() for e in ems]
                notice_type = em_texts[0] if len(em_texts) > 0 else ''
                publish_date = parse_date(em_texts[1]) if len(em_texts) > 1 else None
                region = em_texts[2] if len(em_texts) > 2 else ''
                purchaser = em_texts[3] if len(em_texts) > 3 else ''
                status = self._map_status(notice_type)
                raw_data = {'notice_type': notice_type, 'ems': em_texts}
            else:
                # ulst 结构：日期/地域/采购人 标签
                text = await li.inner_text()
                publish_date = parse_date(self._label(text, '日期'))
                region = self._label(text, '地域')
                purchaser = self._label(text, '采购人')
                status = self._map_status(title)
                raw_data = {'notice_type': '', 'text': text}

            return {
                'title': title,
                'source_url': source_url,
                'source_unique_id': href.rsplit('/', 1)[-1] or href,
                'project_no': '',
                'publish_date': publish_date,
                'purchaser': purchaser,
                'region': region,
                'industry': '',
                'budget_amount': None,
                'bid_amount': None,
                'deadline': None,
                'content': '',
                'contact_info': '',
                'attachment_url': '',
                'status': status,
                'raw_data': raw_data,
            }
        except Exception as e:
            print(f'[ccgp] item parse error: {e}', file=sys.stderr)
            return None

    @staticmethod
    def _label(text, label):
        m = re.search(label + r'[:：]\s*(.*?)(?=\s+(?:日期|地域|采购人)[:：]|$)', text, re.S)
        return m.group(1).strip() if m else ''

    def _map_status(self, notice_type):
        if any(k in notice_type for k in ('中标', '成交', '结果')):
            return 'awarded'
        if any(k in notice_type for k in ('废标', '流标', '终止')):
            return 'closed'
        return 'open'

    async def extract_contact(self, page, text):
        """Extract only the 「联系人及联系方式」 block from the detail header."""
        try:
            result = await page.evaluate(
                """() => {
                    const tables = document.querySelectorAll('table');
                    for (const t of tables) {
                        const trs = Array.from(t.querySelectorAll('tr'));
                        const idx = trs.findIndex(tr => tr.innerText.includes('联系人及联系方式'));
                        if (idx === -1) continue;
                        const out = [];
                        for (let i = idx; i < trs.length; i++) {
                            const tr = trs[i];
                            const tds = Array.from(tr.querySelectorAll('td'));
                            // Stop at the next section header after the first row.
                            if (i > idx) {
                                const b = tds[0] && tds[0].querySelector('b');
                                const first = (tds[0] && tds[0].innerText || '').trim();
                                if (tds.length === 1 && (b || first.endsWith('：'))) break;
                            }
                            const key = (tds[0] && tds[0].innerText || '').trim();
                            const val = tds.slice(1)
                                .map(td => td.innerText.trim())
                                .filter(Boolean)
                                .join(' ')
                                .trim();
                            if (key && val) {
                                out.push(key + '：' + val);
                            } else {
                                const line = tr.innerText.trim();
                                if (line) out.push(line);
                            }
                        }
                        return out.join('\\n');
                    }
                    return '';
                }"""
            )
            return (result or '').strip()
        except Exception as e:
            print(f'[ccgp] extract_contact error: {e}', file=sys.stderr)
            return parse_contact_info(text)

