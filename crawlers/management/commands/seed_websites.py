from django.core.management.base import BaseCommand

from bidding.models import BiddingWebsite

WEBSITES = [
    # 政府/公共类（无需登录，已实现爬虫）
    {'name': '中国政府采购网', 'code': 'ccgp', 'url': 'https://www.ccgp.gov.cn/cggg/zygg/', 'category': 'government', 'requires_login': False},
    {'name': '中国招标投标公共服务平台', 'code': 'cebpubservice', 'url': 'https://bulletin.cebpubservice.com/', 'category': 'government', 'requires_login': False},
    {'name': '全国公共资源交易平台', 'code': 'ggzy', 'url': 'https://www.ggzy.gov.cn/deal/dealList.html', 'category': 'government', 'requires_login': False},
    {'name': '中国采购与招标网', 'code': 'chinabidding', 'url': 'https://www.chinabidding.cn/search/search?keyword=', 'category': 'government', 'requires_login': False},
    {'name': '省级公共资源交易中心', 'code': 'province', 'url': '', 'category': 'province', 'requires_login': False},
    # 中国金融采购平台（国电投聚合站，匿名可抓列表+部分正文）
    {'name': '中国金融采购平台', 'code': 'zgjr', 'url': 'http://zgjr.gdtzb.com/', 'category': 'finance', 'requires_login': False},
    # 国电投电子商务平台（招标信息栏目，支持关键字过滤）
    {'name': '国电投电子商务平台', 'code': 'gdtzb', 'url': 'http://www.gdtzb.com/zb/', 'category': 'other', 'requires_login': False},
    # 金融集采类
    {'name': '中国金融集中采购网', 'code': 'cfin', 'url': 'http://www.cfcpn.com/jcw/sys/index/goUrl?url=modules/sys/login/list&column=cggg', 'category': 'finance', 'requires_login': False},
    # 以下为需登录平台，留框架桩
    {'name': '中招联合招标采购平台', 'code': 'cntcitc', 'url': '', 'category': 'finance', 'requires_login': True},
    {'name': '诚E招', 'code': 'chengezhao', 'url': 'https://www.chengezhao.com/cms/categories/%E4%B8%9A%E5%8A%A1%E5%85%AC%E5%91%8A/%E9%A1%B9%E7%9B%AE%E5%85%AC%E5%91%8A/', 'category': 'finance', 'requires_login': False},
    {'name': '农银e采', 'code': 'nongyinecai', 'url': 'https://jc.abchina.com.cn/puc/#/biddingPage', 'category': 'finance', 'requires_login': False},
    {'name': '中银智采', 'code': 'zhongyin', 'url': 'https://ctpch.fmscop.bankofchina.com/pcm/#/first-page/purchase1', 'category': 'finance', 'requires_login': False},
    {'name': '邮银易采', 'code': 'youyin', 'url': 'https://cg.psbc.com/cms/default/webfile/1ywgg2/index.html', 'category': 'finance', 'requires_login': False},
    {'name': '兴业银行集采平台', 'code': 'xingye', 'url': '', 'category': 'finance', 'requires_login': True},
    {'name': '中信金控采购共享平台', 'code': 'zhongxinjinkong', 'url': 'https://ebid.cfhc.citic/cms/default/webfile/ywgg1/index.html', 'category': 'finance', 'requires_login': False},
    {'name': '人保e采', 'code': 'renbaoecai', 'url': '', 'category': 'finance', 'requires_login': True},
    {'name': '平安招采平台', 'code': 'pingan', 'url': '', 'category': 'finance', 'requires_login': True},
    {'name': '龙集采', 'code': 'longjicai', 'url': 'https://ibuy.ccb.com/cms/index.html#/ccbbidzbzq', 'category': 'finance', 'requires_login': False},
]


class Command(BaseCommand):
    help = 'Seed BiddingWebsite records'

    def handle(self, *args, **options):
        created = 0
        updated = 0
        for data in WEBSITES:
            code = data.pop('code')
            _, was_created = BiddingWebsite.objects.update_or_create(
                code=code, defaults=data,
            )
            if was_created:
                created += 1
            else:
                updated += 1
        self.stdout.write(self.style.SUCCESS(
            f'Seeded websites: {created} created, {updated} updated'
        ))
