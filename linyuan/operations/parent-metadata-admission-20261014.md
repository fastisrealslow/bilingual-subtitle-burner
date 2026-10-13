# Preserve exact API-backed parent evidence after catalog source relabeling

Real generated titles such as `林园 P1 studio_video_1786109908572` were rejected
because their record source became bilibili_search while collection_title,
page, cid, exact bvid and verified-duration metadata survived. The existing
parent-evidence route accepted only bilibili_collection and parent names
starting with 林园; it also missed actual date-prefixed interview titles.

Keep existing title/speaker, rights, source-role, duplicate and final media
gates. For the three Bilibili catalog aliases, accept parent metadata only when
exact URL page, bvid, page integer, positive cid, generated P title, original
generated ID, known duration, author and direct mother role all agree. Parent
must itself identify the target speaker and cannot be third-party/AI/noise.

Read-only fresh official view checks found two imported parents
with no_reprint=0 and matching exact cids: BV15au86KERp (3219s, cid40691764475),
BV1SRuP6WEZJ (2931s, cid40708014684). A date-prefixed true collection parent
BV1c4m5YBEq2 also has no_reprint=0; its pages require exact per-page duration,
not the combined3698s. These facts are not first-party or rights clearance,
not successful clips and not credit toward45%. Normal preflight/ASR/final
acceptance must still run, and explicit current bans remain terminal.

The two imported parents are blocked separately by retained old rejection keys
(BV15au86KERp has an archived failure record; BV1SRuP6WEZJ has an old rejection
without a detailed reason). This patch does NOT remove those records, clear
their lifetime blocks or falsely claim newly usable mothers. Any future archive
repair must independently justify and retain a bounded retry.

After the label fix, a read-only census found26 candidate records/four nominal
long mothers. Fresh exact view showed three newly exposed collection parents
explicitly prohibited (BV1JbkdBFEs1, BV1J9kfBzEZL, BV1MzkBBbEvG); register those
before rollout. Remaining BV1c4m5YBEq2 has page1=3626s and page2=72s. Combined
video duration3698s must not turn page2 into a long mother.
