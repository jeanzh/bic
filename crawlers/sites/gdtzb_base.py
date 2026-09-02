"""Shared base for gdtzb.com-family crawlers.

zgjr.gdtzb.com（中国金融采购平台）与 www.gdtzb.com（国电投电子商务平台）
的详情页都落在 www.gdtzb.com/g-zb-*.html，结构一致，故抽取公共的
WAF 等待与详情页解析逻辑。两者都带阿里云 WAF（acw_sc__v2 挑战）。
"""
import sys

from crawlers.base import BaseSiteCrawler, parse_date, parse_project_no


class GdtzbBase(BaseSiteCrawler):
    detail_content_selector = 'div.tender-content'

    async def _wait_content(self, page, selector, timeout_ms=30000):
        """等待 WAF 挑战通过后真实内容出现。"""
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def _enrich_detail(self, page, item):
        """抓详情页正文/地区/项目编号（匿名，敏感字段被掩码）。"""
        url = item.get('source_url')
        if not url:
            return
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            if not await self._wait_content(page, self.detail_content_selector):
                return

            content_el = await page.query_selector(self.detail_content_selector)
            text = (await content_el.inner_text()).strip() if content_el else ''
            if text:
                item['content'] = text
                if not item.get('project_no'):
                    item['project_no'] = parse_project_no(text)

            region = await self._extract_region(page)
            if region:
                item['region'] = region

            if not item.get('publish_date'):
                item['publish_date'] = await self._extract_publish_date(page)
        except Exception as e:
            print(f'[detail] {url}: {e}', file=sys.stderr)

    async def _extract_region(self, page):
        """从详情页 head-tips-table 读取「所属地区」。"""
        try:
            result = await page.evaluate(
                """() => {
                    const tds = Array.from(document.querySelectorAll('table.head-tips-table td'));
                    for (let i = 0; i < tds.length; i++) {
                        if ((tds[i].innerText || '').trim() === '所属地区') {
                            return (tds[i + 1] && tds[i + 1].innerText || '').trim();
                        }
                    }
                    return '';
                }"""
            )
            return (result or '').strip()
        except Exception:
            return ''

    async def _extract_publish_date(self, page):
        try:
            info = await page.query_selector('div.content-info')
            if info:
                return parse_date(await info.inner_text())
        except Exception:
            pass
        return None
