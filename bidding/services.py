from .models import FilterRule


def group_by_project(infos):
    """Group BiddingInfo by project_no.

    Returns a list of dicts: {'project_no', 'infos', 'latest'}.
    Items without a project_no each form their own single-item group.
    Groups are ordered by latest publish_date/created_at descending.
    """
    groups = []
    by_project = {}

    for info in infos:
        key = (info.project_no or '').strip()
        if key:
            by_project.setdefault(key, []).append(info)
        else:
            groups.append({'project_no': '', 'infos': [info], 'latest': info})

    for project_no, items in by_project.items():
        items.sort(key=_sort_key, reverse=True)
        groups.append({'project_no': project_no, 'infos': items, 'latest': items[0]})

    groups.sort(key=lambda g: _sort_key(g['latest']), reverse=True)
    return groups


def _sort_key(info):
    return info.publish_date or info.created_at.date()


def apply_filters(item: dict) -> bool:
    """Return True if item matches any active FilterRule.

    When no active rules exist, all items pass (keep everything).
    Within a rule, keywords/regions/industries are AND-ed; rules are OR-ed.
    """
    rules = FilterRule.objects.filter(is_active=True)
    if not rules.exists():
        return True

    for rule in rules:
        if _match_rule(rule, item):
            return True
    return False


def prefilter_item(title: str, region: str) -> bool:
    """Cheap pre-filter before fetching the detail page.

    Only drops items that are *definitively* excluded by list-visible fields:
    - an exclude keyword present in the title, or
    - a region that is known at list level and doesn't match the rule.
    Include-keywords are intentionally NOT required here — they often appear
    only in the body, which is fetched later and checked by apply_filters
    (title OR content). This keeps the pre-filter content-aware while still
    skipping items a rule can already reject from the list alone.
    """
    rules = FilterRule.objects.filter(is_active=True)
    if not rules.exists():
        return True

    title_lower = (title or '').lower()
    region_clean = (region or '').strip()

    for rule in rules:
        # Exclude keywords: present in title → this rule cannot match
        if any(kw and kw.lower() in title_lower for kw in rule.exclude_keyword_list()):
            continue
        # Region: reject only when the rule requires a region AND the list
        # provides a non-empty region that doesn't match. When region is
        # unknown at list level (''), defer to apply_filters after fetch.
        regions = rule.region_list()
        if regions and region_clean and not _region_match(region_clean, regions):
            continue
        return True
    return False


def _region_match(region: str, rule_regions) -> bool:
    """Loose region match: '北京市' vs '北京' both ways."""
    if not region:
        return False
    for r in rule_regions:
        if r and (r in region or region in r):
            return True
    return False


def _match_rule(rule: FilterRule, item: dict) -> bool:
    title = (item.get('title') or '').lower()
    content = (item.get('content') or '').lower()
    region = (item.get('region') or '').strip()
    industry = (item.get('industry') or '').strip()

    # Exclude keywords: if any present, rule does not match
    for kw in rule.exclude_keyword_list():
        kw = kw.lower()
        if kw and (kw in title or kw in content):
            return False

    # Include keywords: if specified, at least one must be present
    keywords = rule.keyword_list()
    if keywords:
        matched = False
        for kw in keywords:
            kw = kw.lower()
            if kw and (kw in title or kw in content):
                matched = True
                break
        if not matched:
            return False

    # Regions: if specified, region must match one of them
    regions = rule.region_list()
    if regions and not _region_match(region, regions):
        return False

    # Industries: if specified, industry must match one of them
    industries = rule.industry_list()
    if industries and industry not in industries:
        return False

    return True
