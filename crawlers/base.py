"""Base class for site crawlers.

Each site implements a subclass and is registered in `crawlers/sites/__init__.py`.
The crawl command looks up the crawler by its `code` (matching BiddingWebsite.code).
"""
import asyncio
import re
import sys


class BaseSiteCrawler:
    code = ''          # BiddingWebsite.code
    name = ''          # human-readable name
    url = ''           # entry point URL
    requires_login = False
    fetch_detail_enabled = True   # 是否进入详情页抓取正文/项目编号
    max_detail_fetch = 20         # 每次最多进入详情页的条数
    max_pages = 50                # 每个栏目页最多翻页数（防止死循环）
    detail_content_selector = None  # 详情页正文容器选择器；None 则用 body 全文
    detail_concurrency = 1        # 抓取正文的并发数；>1 仅对 context/page.request 类安全

    async def crawl(self, context):
        """Crawl the site and return a list of normalized dict items.

        Each item should contain keys matching BiddingInfo fields:
        title, source_url, source_unique_id, publish_date, purchaser, region,
        industry, budget_amount, bid_amount, deadline, content, contact_info,
        attachment_url, status, raw_data.
        """
        raise NotImplementedError

    def normalize(self, raw: dict) -> dict:
        """Normalize a raw item into a BiddingInfo-compatible dict."""
        return raw

    async def enrich_items(self, context, items):
        """Fetch detail pages for the first max_detail_fetch items to fill in
        content, project_no, contact_info and attachment_url."""
        if not self.fetch_detail_enabled or not items:
            return items

        page = await context.new_page()
        try:
            for item in items[:self.max_detail_fetch]:
                await self._enrich_detail(page, item)
        finally:
            await page.close()
        return items

    async def enrich_http(self, requester, items):
        """Fetch detail content for items via requester (context or page).request.

        Bounded by max_detail_fetch. When detail_concurrency > 1, requests run
        concurrently through an asyncio.Semaphore. Subclasses must implement
        ``_enrich_one(requester, item)`` using requester.request (plain HTTP),
        not page.goto / page.evaluate.
        """
        if not self.fetch_detail_enabled or not items:
            return
        batch = items[:self.max_detail_fetch]
        concurrency = getattr(self, 'detail_concurrency', 1) or 1
        if concurrency <= 1:
            for item in batch:
                await self._enrich_one(requester, item)
            return
        sem = asyncio.Semaphore(concurrency)

        async def _one(item):
            async with sem:
                await self._enrich_one(requester, item)

        await asyncio.gather(*(_one(item) for item in batch))

    async def _enrich_detail(self, page, item):
        url = item.get('source_url')
        if not url:
            return
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(1200)
            text = ''
            if self.detail_content_selector:
                el = await page.query_selector(self.detail_content_selector)
                if el:
                    text = (await el.inner_text()).strip()
            if not text:
                text = (await page.inner_text('body')).strip()
            if text:
                item['content'] = text
                if not item.get('project_no'):
                    item['project_no'] = parse_project_no(text)
                if not item.get('contact_info'):
                    item['contact_info'] = await self.extract_contact(page, text)
            if not item.get('attachment_url'):
                item['attachment_url'] = await _extract_attachment(page)
        except Exception as e:
            print(f'[detail] {url}: {e}', file=sys.stderr)

    async def extract_contact(self, page, text):
        """Extract contact info from the detail page.

        Default implementation falls back to keyword-based line matching on the
        body text. Sites with a well-defined contact block (e.g. ccgp's
        「联系人及联系方式」 header table) should override this to extract only
        that section.
        """
        return parse_contact_info(text)


def parse_amount(text):
    """Extract a numeric amount from text like '¥1,234.56万' -> Decimal (yuan)."""
    if not text:
        return None
    s = str(text).strip().replace(',', '').replace('，', '')
    m = re.search(r'([\d.]+)\s*(万|亿|万元|亿元)?', s)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2) or ''
    if '亿' in unit:
        value *= 100000000
    elif '万' in unit:
        value *= 10000
    return value


def parse_date(text):
    """Parse common Chinese date formats into YYYY-MM-DD string or None."""
    if not text:
        return None
    s = str(text).strip()
    # YYYY-MM-DD or YYYY/MM/DD or YYYY.MM.DD
    for fmt in (
        r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})',
    ):
        m = re.search(fmt, s)
        if m:
            y, mo, d = m.groups()
            return f'{y}-{int(mo):02d}-{int(d):02d}'
    # YYYY年MM月DD日
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', s)
    if m:
        y, mo, d = m.groups()
        return f'{y}-{int(mo):02d}-{int(d):02d}'
    return None


PROJECT_NO_PATTERNS = (
    r'采购项目编号[：:\s]\s*([A-Za-z0-9][A-Za-z0-9_\-/（）()]{2,60})',
    r'项目编号[：:\s]\s*([A-Za-z0-9][A-Za-z0-9_\-/（）()]{2,60})',
    r'项目编码[：:\s]\s*([A-Za-z0-9][A-Za-z0-9_\-/（）()]{2,60})',
    r'采购编号[：:\s]\s*([A-Za-z0-9][A-Za-z0-9_\-/（）()]{2,60})',
    r'招标编号[：:\s]\s*([A-Za-z0-9][A-Za-z0-9_\-/（）()]{2,60})',
    r'项目号[：:\s]\s*([A-Za-z0-9][A-Za-z0-9_\-/（）()]{2,60})',
)


def parse_project_no(text):
    """Extract a project number from announcement body text, or ''."""
    if not text:
        return ''
    for pattern in PROJECT_NO_PATTERNS:
        m = re.search(pattern, text)
        if m:
            no = m.group(1).strip().rstrip('，。,.、;； （()）')
            if len(no) >= 3:
                return no
    return ''


CONTACT_KEYWORDS = (
    '联系人', '联系电话', '电话', '手机', '传真',
    '邮箱', '电子邮箱', '联系地址', '地址', '招标人', '采购人',
    '代理机构', '代理联系人',
)


def parse_contact_info(text):
    """Extract lines likely containing contact details, or ''."""
    if not text:
        return ''
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(kw in s for kw in CONTACT_KEYWORDS):
            lines.append(s)
        if len(lines) >= 20:
            break
    return '\n'.join(lines)


_ATTACHMENT_EXTS = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.7z')


async def _extract_attachment(page):
    """Return the first attachment download URL found on the detail page."""
    anchors = await page.query_selector_all('a[href]')
    for a in anchors:
        href = await a.get_attribute('href')
        if href and href.lower().endswith(_ATTACHMENT_EXTS):
            return href
    return ''
