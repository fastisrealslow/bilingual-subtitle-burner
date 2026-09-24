"""Bounded source checks for loss impressions and explicit market phases."""
import re


def impression_error(title, cover, transcript):
    # Observed source7: “所有的股民感觉的都是亏钱”. A collective
    # impression is not a verified loss statistic. This intentionally covers
    # that construction, rather than claiming general semantic entailment.
    subjects = set(re.findall(
        r'(股民|投资者)[^。！？!?；;]{0,12}(?:感觉|觉得)[^。！？!?；;]{0,8}亏钱', transcript))
    for copy in (title, cover):
        for subject in subjects:
            if (re.search(re.escape(subject)+r'[^。！？!?；;]{0,12}亏钱', copy)
                    and not re.search(r'感觉|觉得|感受|体感', copy)):
                return '原话说的是股民感觉亏钱，不是核验过的亏损统计；标题和封面须保留感觉等范围，或改选同段其他有明确对象的原话，不得写成所有股民已实际亏钱'
    if re.search(r'(?:我的?|个人的?)感觉[^。！？!?；;]{0,14}亏钱的人多', transcript):
        for copy in (title, cover):
            if '亏钱的人多' in copy and not re.search(r'感觉|觉得|感受|体感', copy):
                return '亏钱的人多是嘉宾的个人感觉；标题和封面不能把个人感受变成全市场统计，保留感觉或改用其他完整原话'
    if re.search(r'(?:还|尚)(?:没有|没|未)进入牛市', transcript):
        assertion = r'(?:这|市场|当前|目前|现在)(?:还|仍然|仍)?(?:就)?(?:是|处于|处在)熊市'
        direct = any(re.search(assertion, s) and not re.search(r'不认为|不是|不能说|不等于|未必|没有说|[？?]', s)
                     for s in re.split(r'[。；;！!]', transcript))
        if not direct and any(re.search(assertion, copy) for copy in (title, cover)):
            return '还没有进入牛市不能改写成还是熊市；保留嘉宾实际说出的市场判断，不补出相反市场状态'
    return None

