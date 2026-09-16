"""Registry of site crawlers, keyed by BiddingWebsite.code."""
from .ccgp import CcgpCrawler
from .cebpubservice import CebpCrawler
from .cfin import CfinCrawler
from .chengezhao import ChengezhaoCrawler
from .chinabidding import ChinaBiddingCrawler
from .gdtzb import GdtzbCrawler
from .ggzy import GgzyCrawler
from .longjicai import LongjicaiCrawler
from .nongyinecai import NongyinecaiCrawler
from .province import ProvinceCrawler
from .thtc import ThtcCrawler
from .youyin import YouyinCrawler
from .zgjr import ZgjrCrawler
from .zhongxinjinkong import ZhongxinjinkongCrawler
from .zhongyin import ZhongyinCrawler

CRAWLERS = {
    CcgpCrawler.code: CcgpCrawler,
    CebpCrawler.code: CebpCrawler,
    CfinCrawler.code: CfinCrawler,
    ChengezhaoCrawler.code: ChengezhaoCrawler,
    ChinaBiddingCrawler.code: ChinaBiddingCrawler,
    GdtzbCrawler.code: GdtzbCrawler,
    GgzyCrawler.code: GgzyCrawler,
    LongjicaiCrawler.code: LongjicaiCrawler,
    NongyinecaiCrawler.code: NongyinecaiCrawler,
    ProvinceCrawler.code: ProvinceCrawler,
    ThtcCrawler.code: ThtcCrawler,
    YouyinCrawler.code: YouyinCrawler,
    ZgjrCrawler.code: ZgjrCrawler,
    ZhongyinCrawler.code: ZhongyinCrawler,
    ZhongxinjinkongCrawler.code: ZhongxinjinkongCrawler,
}


def get_crawler(code: str):
    cls = CRAWLERS.get(code)
    return cls() if cls else None
